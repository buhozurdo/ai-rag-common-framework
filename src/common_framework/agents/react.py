"""Lightweight Autonomous ReAct (Reasoning + Acting) Agent Loop Engine (FEAT-04 / TASK-14)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator, Callable, Optional, Sequence, Union
from pydantic import BaseModel, Field

from common_framework.agents.hitl import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    AutoApprovalHook,
    BaseApprovalHook,
)
from common_framework.agents.memory import BaseMemory, ConversationBufferMemory
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    LLMFactory,
    Role,
    TokenUsage,
)
from common_framework.tools.base import Tool, ToolExecutionError
from common_framework.tools.registry import ToolNotFoundError, ToolRegistry, global_tool_registry

logger = logging.getLogger("common_framework.agents.react")

DEFAULT_REACT_SYSTEM_PROMPT = """You are an intelligent, autonomous agent designed to solve problems step by step.
You have access to the following tools:

{tools_description}

Use the following format strictly:

Thought: Consider what you should do next based on the user request and past observations.
Action: the tool name to call (must be one of the available tools: [{tool_names}]).
Action Input: a valid JSON object containing the exact keyword arguments for the tool.
Observation: the result of executing the tool will be placed here.

When you have gathered enough information to completely answer the user request, or if no tools are needed, provide:
Thought: I now have the complete answer to the request.
Final Answer: your final, comprehensive answer to the user.

Rules:
1. Always output exactly one Thought and either one Action/Action Input pair OR Final Answer in each response.
2. Action Input MUST be a valid JSON dictionary (e.g. {{"query": "example", "top_k": 3}}).
3. Do not invent tools not listed above.
"""


class AgentStep(BaseModel):
    """A single reasoning and execution step in the ReAct loop."""

    thought: str = Field(description="Internal reasoning explanation")
    action: Optional[str] = Field(default=None, description="Name of the tool executed")
    action_input: dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the tool")
    observation: Optional[str] = Field(default=None, description="Result returned by tool execution")
    step_index: int = Field(default=0, description="1-indexed iteration counter")


class AgentResponse(BaseModel):
    """Final outcome returned by the ReAct agent."""

    output: str = Field(description="Final answer text delivered to the user")
    steps: list[AgentStep] = Field(default_factory=list, description="Ordered trace of thought-action-observation steps")
    iterations: int = Field(default=0, description="Total number of reasoning cycles executed")
    success: bool = Field(default=True, description="Whether the agent reached a Final Answer within max_iterations")
    token_usage: TokenUsage = Field(default_factory=TokenUsage, description="Cumulative token usage across all steps")


class ReActAgent:
    """Lightweight ReAct agent coordinating LLM completions with dynamic tool executions."""

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        tools: Optional[Union[list[Tool], ToolRegistry]] = None,
        memory: Optional[BaseMemory] = None,
        approval_hook: Optional[BaseApprovalHook] = None,
        max_iterations: int = 8,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.llm_client = llm_client or LLMFactory.create()
        if isinstance(tools, ToolRegistry):
            self.registry = tools
        elif isinstance(tools, list):
            self.registry = ToolRegistry()
            for t in tools:
                self.registry.register(t)
        else:
            self.registry = global_tool_registry

        self.memory = memory or ConversationBufferMemory(max_messages=12)
        self.approval_hook = approval_hook or AutoApprovalHook()
        self.max_iterations = max_iterations
        self.system_prompt_template = system_prompt or DEFAULT_REACT_SYSTEM_PROMPT

    def _build_tools_description(self) -> tuple[str, str]:
        all_tools = self.registry.list_tools()
        if not all_tools:
            return "No tools available.", ""

        tool_descs = []
        for t in all_tools:
            params = json.dumps(t.parameters_schema.get("properties", {}))
            approval_tag = " [REQUIRES HUMAN APPROVAL]" if getattr(t, "requires_approval", False) else ""
            tool_descs.append(f"- {t.name}{approval_tag}: {t.description}\n  Parameters: {params}")

        names = ", ".join([t.name for t in all_tools])
        return "\n".join(tool_descs), names

    def _parse_output(self, text: str) -> tuple[Optional[str], Optional[str], Optional[dict[str, Any]], Optional[str]]:
        """Extract Thought, Action, Action Input, and Final Answer from LLM response."""
        # 1. Check for Final Answer
        final_match = re.search(r"Final Answer:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
        if final_match:
            thought_part = text[: final_match.start()].strip()
            thought = re.sub(r"^Thought:\s*", "", thought_part, flags=re.IGNORECASE).strip()
            final_answer = final_match.group(1).strip()
            return thought or "Task completed.", None, None, final_answer

        # 2. Extract Thought
        thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|\Z)", text, re.DOTALL | re.IGNORECASE)
        thought = thought_match.group(1).strip() if thought_match else text.strip()

        # 3. Extract Action
        action_match = re.search(r"Action:\s*([a-zA-Z0-9_\-\.]+)", text, re.IGNORECASE)
        action = action_match.group(1).strip() if action_match else None

        # 4. Extract Action Input
        action_input: Optional[dict[str, Any]] = None
        input_match = re.search(r"Action Input:\s*(\{.*\}|\[.*\]|\".*\"|'.*')", text, re.DOTALL | re.IGNORECASE)
        if input_match:
            raw_input = input_match.group(1).strip()
            try:
                parsed = json.loads(raw_input)
                action_input = parsed if isinstance(parsed, dict) else {"input": parsed}
            except json.JSONDecodeError:
                # Handle single unquoted string or simple value
                action_input = {"input": raw_input}
        elif action:
            # Empty dictionary default
            action_input = {}

        return thought, action, action_input, None

    async def run(
        self,
        query: str,
        context: Optional[dict[str, Any]] = None,
    ) -> AgentResponse:
        """Execute the full autonomous reasoning loop until a Final Answer is reached."""
        steps: list[AgentStep] = []
        cumulative_usage = TokenUsage()

        async for step, final_resp in self._execute_loop(query, context):
            if step is not None:
                steps.append(step)
            if final_resp is not None:
                final_resp.steps = steps
                return final_resp

        # Reached max iterations without final answer
        fallback_answer = (
            f"Agent completed {len(steps)} steps but reached max iteration limit ({self.max_iterations}) "
            "without finalizing. Please inspect step trace."
        )
        return AgentResponse(
            output=fallback_answer,
            steps=steps,
            iterations=len(steps),
            success=False,
            token_usage=cumulative_usage,
        )

    async def run_stream(
        self,
        query: str,
        context: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[AgentStep]:
        """Stream each executed step in real-time as it occurs."""
        async for step, _ in self._execute_loop(query, context):
            if step is not None:
                yield step

    async def _execute_loop(
        self,
        query: str,
        context: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[tuple[Optional[AgentStep], Optional[AgentResponse]]]:
        tools_desc, tool_names = self._build_tools_description()
        system_content = self.system_prompt_template.format(
            tools_description=tools_desc,
            tool_names=tool_names,
        )

        history = await self.memory.get_messages()
        conversation: list[ChatMessage] = [
            ChatMessage(role=Role.SYSTEM, content=system_content),
            *history,
            ChatMessage(role=Role.USER, content=query),
        ]

        scratchpad: list[str] = []
        cumulative_usage = TokenUsage()

        for iteration in range(1, self.max_iterations + 1):
            # Assemble current conversation with working scratchpad
            current_messages = list(conversation)
            if scratchpad:
                current_messages.append(
                    ChatMessage(
                        role=Role.ASSISTANT,
                        content="\n".join(scratchpad),
                    )
                )

            # Generate LLM thought and next action
            resp = await self.llm_client.complete(current_messages)
            if resp.usage:
                cumulative_usage.prompt_tokens += resp.usage.prompt_tokens
                cumulative_usage.completion_tokens += resp.usage.completion_tokens
                cumulative_usage.total_tokens += resp.usage.total_tokens

            thought, action, action_input, final_answer = self._parse_output(resp.content)

            # Case 1: Final answer reached
            if final_answer is not None:
                # Persist completed turn to memory
                await self.memory.add_message(ChatMessage(role=Role.USER, content=query))
                await self.memory.add_message(ChatMessage(role=Role.ASSISTANT, content=final_answer))

                yield None, AgentResponse(
                    output=final_answer,
                    iterations=iteration,
                    success=True,
                    token_usage=cumulative_usage,
                )
                return

            # Case 2: Tool execution step
            if not action:
                # LLM produced neither action nor final answer; prompt it to conclude or act
                scratchpad.append(f"Thought: {thought}\nObservation: Please specify an Action or Final Answer.")
                continue

            # Verify tool existence
            tool_obj = self.registry.get(action)
            action_args = action_input or {}
            observation: str = ""

            if not tool_obj:
                observation = f"Error: Tool '{action}' is not found. Available tools: [{tool_names}]."
            else:
                # Human-in-the-Loop check
                requires_hitl = getattr(tool_obj, "requires_approval", False)
                if requires_hitl and self.approval_hook:
                    review = await self.approval_hook.review(
                        ApprovalRequest(
                            tool_name=action,
                            arguments=action_args,
                            context=context,
                        )
                    )
                    if review.decision == ApprovalDecision.REJECTED:
                        observation = f"Execution blocked: {review.reason or 'Rejected by human operator.'}"
                    elif review.decision == ApprovalDecision.MODIFIED:
                        action_args = review.modified_arguments or action_args
                        try:
                            result = await self.registry.execute(action, **action_args)
                            observation = str(result)
                        except Exception as exc:
                            observation = f"Error executing tool '{action}': {exc}"
                    else:
                        try:
                            result = await self.registry.execute(action, **action_args)
                            observation = str(result)
                        except Exception as exc:
                            observation = f"Error executing tool '{action}': {exc}"
                else:
                    try:
                        result = await self.registry.execute(action, **action_args)
                        observation = str(result)
                    except Exception as exc:
                        observation = f"Error executing tool '{action}': {exc}"

            step = AgentStep(
                thought=thought,
                action=action,
                action_input=action_args,
                observation=observation,
                step_index=iteration,
            )

            # Record step in scratchpad
            step_text = (
                f"Thought: {thought}\n"
                f"Action: {action}\n"
                f"Action Input: {json.dumps(action_args)}\n"
                f"Observation: {observation}"
            )
            scratchpad.append(step_text)

            yield step, None

    def run_sync(
        self,
        query: str,
        context: Optional[dict[str, Any]] = None,
    ) -> AgentResponse:
        """Synchronous wrapper for run."""
        return asyncio.run(self.run(query, context=context))
