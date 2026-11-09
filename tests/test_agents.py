"""Comprehensive tests for Autonomous ReAct Agent Framework and Human-in-the-Loop (EPIC-04)."""

import asyncio
import pytest
from typing import Any, Optional

from common_framework.agents.hitl import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    AutoApprovalHook,
    CallableApprovalHook,
    QueueApprovalHook,
)
from common_framework.agents.memory import (
    ConversationBufferMemory,
    VectorMemory,
)
from common_framework.agents.react import (
    AgentResponse,
    AgentStep,
    ReActAgent,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    Role,
    TokenUsage,
)
from common_framework.rag.embeddings import MockEmbeddingProvider
from common_framework.rag.vectorstore import InMemoryVectorStore
from common_framework.tools.base import Tool, tool
from common_framework.tools.registry import ToolRegistry


class ScriptedMockLLMClient(BaseLLMClient):
    """Mock LLM returning pre-scripted responses sequentially for agent testing."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.call_count = 0
        self.history: list[list[ChatMessage]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.history.append(messages)
        if self.call_count < len(self.responses):
            resp_text = self.responses[self.call_count]
        else:
            resp_text = "Thought: No more responses.\nFinal Answer: Done."
        self.call_count += 1
        return ChatResponse(
            content=resp_text,
            model="mock-agent-llm",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=15, total_tokens=35),
        )

    async def complete_stream(self, *args: Any, **kwargs: Any):
        raise NotImplementedError()

    async def structured_complete(self, response_model: Any, messages: list[ChatMessage], **kwargs: Any) -> Any:
        raise NotImplementedError()


# ---------------------------------------------------------------------------
# Memory Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_buffer_memory_sliding_window():
    memory = ConversationBufferMemory(max_messages=4)

    memory.add_system_message("System prompt: You are helpful.")
    memory.add_user_message("User message 1")
    memory.add_assistant_message("Assistant message 1")
    memory.add_user_message("User message 2")
    memory.add_assistant_message("Assistant message 2")

    msgs = await memory.get_messages()
    # Should maintain max_messages (4) while preserving system message at index 0
    assert len(msgs) == 4
    assert msgs[0].role == Role.SYSTEM
    assert msgs[0].content == "System prompt: You are helpful."
    assert msgs[1].content == "Assistant message 1"
    assert msgs[2].content == "User message 2"
    assert msgs[3].content == "Assistant message 2"

    await memory.clear()
    assert await memory.get_messages() == []


@pytest.mark.asyncio
async def test_vector_memory_interaction_and_recall():
    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    memory = VectorMemory(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        collection="test_agent_memory",
    )

    doc_id = await memory.save_interaction(
        user_input="What is the capital of France?",
        assistant_output="The capital of France is Paris.",
    )
    assert doc_id

    recalled = await memory.recall(query="Paris capital France", top_k=1)
    assert len(recalled) == 1
    assert "Paris" in recalled[0].content

    context_snippet = await memory.get_relevant_context("France capital")
    assert "Paris" in context_snippet


# ---------------------------------------------------------------------------
# Human-in-the-Loop (HITL) Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_approval_hook():
    hook = AutoApprovalHook()
    req = ApprovalRequest(tool_name="delete_database", arguments={"db": "prod"})
    resp = await hook.review(req)
    assert resp.decision == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_callable_approval_hook_custom_logic():
    def custom_review(req: ApprovalRequest) -> ApprovalResponse:
        if req.arguments.get("amount", 0) > 1000:
            return ApprovalResponse.reject("Exceeds transfer limit of $1000")
        if req.arguments.get("amount", 0) == 500:
            return ApprovalResponse.modify({"amount": 400}, reason="Adjusted for taxes")
        return ApprovalResponse.approve()

    hook = CallableApprovalHook(custom_review)

    # Approve
    r1 = await hook.review(ApprovalRequest(tool_name="wire", arguments={"amount": 100}))
    assert r1.decision == ApprovalDecision.APPROVED

    # Reject
    r2 = await hook.review(ApprovalRequest(tool_name="wire", arguments={"amount": 5000}))
    assert r2.decision == ApprovalDecision.REJECTED
    assert "limit" in r2.reason

    # Modify
    r3 = await hook.review(ApprovalRequest(tool_name="wire", arguments={"amount": 500}))
    assert r3.decision == ApprovalDecision.MODIFIED
    assert r3.modified_arguments == {"amount": 400}


@pytest.mark.asyncio
async def test_queue_approval_hook_resolution_and_timeout():
    hook = QueueApprovalHook(timeout=0.1)

    # Test timeout
    req = ApprovalRequest(tool_name="reboot_server")
    timed_out_resp = await hook.review(req)
    assert timed_out_resp.decision == ApprovalDecision.REJECTED
    assert "timed out" in timed_out_resp.reason

    # Test manual async resolution
    hook_fast = QueueApprovalHook(timeout=5.0)

    async def simulate_human_review():
        await asyncio.sleep(0.05)
        await hook_fast.resolve_next(
            decision=ApprovalDecision.APPROVED,
            reason="Human supervisor confirmed",
        )

    task = asyncio.create_task(simulate_human_review())
    resp = await hook_fast.review(ApprovalRequest(tool_name="scale_service"))
    await task

    assert resp.decision == ApprovalDecision.APPROVED
    assert resp.reason == "Human supervisor confirmed"


# ---------------------------------------------------------------------------
# ReAct Agent Execution Tests
# ---------------------------------------------------------------------------


@tool(name="calculator", description="Computes mathematical expressions.")
def calculator_tool(expression: str) -> str:
    # Safe simple eval for testing
    if expression == "25 * 4":
        return "100"
    if expression == "100 + 50":
        return "150"
    return "0"


@tool(name="database_delete", description="Deletes database records.", requires_approval=True)
def delete_records_tool(table: str) -> str:
    return f"Table {table} deleted."


@pytest.mark.asyncio
async def test_react_agent_multi_step_reasoning():
    scripted_steps = [
        # Iteration 1: Agent decides to use calculator for 25 * 4
        "Thought: I need to calculate 25 * 4 first.\n"
        "Action: calculator\n"
        'Action Input: {"expression": "25 * 4"}',
        # Iteration 2: Agent takes intermediate 100 and adds 50
        "Thought: Now I need to add 50 to 100.\n"
        "Action: calculator\n"
        'Action Input: {"expression": "100 + 50"}',
        # Iteration 3: Final Answer
        "Thought: I now have the final calculation.\n"
        "Final Answer: The total computed value is 150.",
    ]

    llm = ScriptedMockLLMClient(scripted_steps)
    registry = ToolRegistry()
    registry.register(calculator_tool)

    agent = ReActAgent(llm_client=llm, tools=registry, max_iterations=5)

    response: AgentResponse = await agent.run("Calculate (25 * 4) + 50")

    assert response.success is True
    assert "150" in response.output
    assert len(response.steps) == 2
    assert response.steps[0].action == "calculator"
    assert response.steps[0].observation == "100"
    assert response.steps[1].action == "calculator"
    assert response.steps[1].observation == "150"
    assert response.token_usage.total_tokens > 0


@pytest.mark.asyncio
async def test_react_agent_streaming_steps():
    scripted_steps = [
        'Thought: Compute 25 * 4.\nAction: calculator\nAction Input: {"expression": "25 * 4"}',
        "Thought: Done.\nFinal Answer: Result is 100.",
    ]
    llm = ScriptedMockLLMClient(scripted_steps)
    registry = ToolRegistry()
    registry.register(calculator_tool)

    agent = ReActAgent(llm_client=llm, tools=registry)

    streamed_steps: list[AgentStep] = []
    async for step in agent.run_stream("What is 25 * 4?"):
        streamed_steps.append(step)

    assert len(streamed_steps) == 1
    assert streamed_steps[0].action == "calculator"
    assert streamed_steps[0].observation == "100"


@pytest.mark.asyncio
async def test_react_agent_hitl_rejection():
    scripted_steps = [
        'Thought: I will delete the users table.\nAction: database_delete\nAction Input: {"table": "users"}',
        "Thought: The human rejected deletion, so I must abort.\nFinal Answer: Operation was aborted by user approval.",
    ]

    llm = ScriptedMockLLMClient(scripted_steps)
    registry = ToolRegistry()
    registry.register(delete_records_tool)

    # Approval hook that rejects table deletion
    def reject_all_deletions(req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse.reject(reason="Drop/delete operations are strictly forbidden on production.")

    agent = ReActAgent(
        llm_client=llm,
        tools=registry,
        approval_hook=CallableApprovalHook(reject_all_deletions),
    )

    response = await agent.run("Please delete the users table")

    assert response.success is True
    assert len(response.steps) == 1
    assert response.steps[0].action == "database_delete"
    assert "Execution blocked" in response.steps[0].observation
    assert "strictly forbidden" in response.steps[0].observation
    assert "aborted" in response.output.lower()


def test_react_agent_sync_execution():
    scripted_steps = [
        "Thought: Simple query.\nFinal Answer: 42 is the answer.",
    ]
    llm = ScriptedMockLLMClient(scripted_steps)
    agent = ReActAgent(llm_client=llm, tools=[])
    resp = agent.run_sync("What is the meaning of life?")
    assert resp.success is True
    assert "42" in resp.output
