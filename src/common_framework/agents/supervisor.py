"""Multi-Agent Supervisor and Delegation Pattern.

Orchestrator-Workers hierarchy routing subtasks to specialized agents with a shared state bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Coroutine, Optional, Union
from pydantic import BaseModel, Field

from common_framework.core.llm import BaseLLMClient, ChatMessage, LLMFactory, Role

logger = logging.getLogger("common_framework.agents.supervisor")


class DelegationStep(BaseModel):
    """Record of a single delegation step executed by a worker agent."""

    step: int
    agent_name: str
    task: str
    result: str


class SupervisorResponse(BaseModel):
    """Final output of the Multi-Agent Supervisor execution."""

    final_answer: str
    iterations: int
    delegations: list[DelegationStep] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)


class AgentBus:
    """Shared state bus enabling worker agents and supervisor to share intermediate data."""

    def __init__(self, initial_state: Optional[dict[str, Any]] = None) -> None:
        self._data: dict[str, Any] = dict(initial_state or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


class AgentSupervisor:
    """Orchestrator routing complex user tasks to specialized worker agents."""

    def __init__(
        self,
        agents: dict[str, tuple[str, Any]],
        llm_client: Optional[BaseLLMClient] = None,
        bus: Optional[AgentBus] = None,
        max_steps: int = 5,
        system_prompt: Optional[str] = None,
    ) -> None:
        """Initialize supervisor with registered worker agents.

        Args:
            agents: Mapping of agent_name -> (description, agent_instance_or_callable).
            llm_client: LLM client for routing decisions. Defaults to LLMFactory.create().
            bus: Shared state bus. Defaults to a new empty AgentBus.
            max_steps: Maximum delegation rounds before forcing final conclusion.
            system_prompt: Optional override for supervisor decision prompt.
        """
        self.agents = agents
        self.llm = llm_client or LLMFactory.create()
        self.bus = bus or AgentBus()
        self.max_steps = max_steps
        self.system_prompt = system_prompt

    def _build_system_prompt(self) -> str:
        if self.system_prompt:
            return self.system_prompt

        agent_desc = "\n".join(
            f"- {name}: {desc}" for name, (desc, _) in self.agents.items()
        )
        return (
            "You are a Multi-Agent Supervisor orchestrating specialized workers to solve a complex user task.\n"
            f"Available workers:\n{agent_desc}\n\n"
            "On each turn, inspect previous findings and output EXACTLY one of the following formats:\n"
            "1. To delegate work:\n"
            "DELEGATE: <agent_name> | <specific task instructions for this worker>\n\n"
            "2. When the goal is completed or no more delegation is needed:\n"
            "FINAL_ANSWER: <complete final response to the user's task>"
        )

    async def _execute_worker(self, name: str, task: str) -> str:
        """Invoke a registered worker agent (supporting ReActAgent, async, or sync callable)."""
        _, worker = self.agents[name]

        # Case 1: ReActAgent or object with async run()
        if hasattr(worker, "run") and callable(worker.run):
            res = worker.run(task)
            if asyncio.iscoroutine(res):
                res = await res
            return getattr(res, "output", getattr(res, "answer", str(res)))

        # Case 2: Async callable
        if callable(worker):
            if asyncio.iscoroutinefunction(worker):
                return str(await worker(task))
            return str(worker(task))

        return str(worker)

    async def run(self, task: str) -> SupervisorResponse:
        """Run the supervisor orchestration loop asynchronously."""
        delegations: list[DelegationStep] = []
        history_log: list[str] = []

        sys_prompt = self._build_system_prompt()

        for step_idx in range(1, self.max_steps + 1):
            bus_state_str = json.dumps(self.bus.to_dict(), default=str)
            delegation_history_str = "\n".join(history_log) or "(None yet)"

            user_msg = (
                f"Overall Goal: {task}\n\n"
                f"Shared State Bus: {bus_state_str}\n\n"
                f"Delegation History:\n{delegation_history_str}\n\n"
                "Decide next action (DELEGATE or FINAL_ANSWER):"
            )

            messages = [
                ChatMessage(role=Role.SYSTEM, content=sys_prompt),
                ChatMessage(role=Role.USER, content=user_msg),
            ]

            llm_res = await self.llm.complete(messages)
            content = llm_res.content.strip()

            # Check for FINAL_ANSWER
            if "FINAL_ANSWER:" in content:
                final_answer = content.split("FINAL_ANSWER:", 1)[1].strip()
                return SupervisorResponse(
                    final_answer=final_answer,
                    iterations=step_idx,
                    delegations=delegations,
                    state=self.bus.to_dict(),
                )

            # Check for DELEGATE
            delegate_match = re.search(r"DELEGATE:\s*([^|\n]+?)\s*\|\s*(.+)", content, re.DOTALL)
            if delegate_match:
                agent_name = delegate_match.group(1).strip()
                subtask = delegate_match.group(2).strip()

                if agent_name in self.agents:
                    try:
                        worker_output = await self._execute_worker(agent_name, subtask)
                    except Exception as exc:
                        worker_output = f"Error executing worker {agent_name}: {exc}"

                    # Update shared state and logs
                    self.bus.set(agent_name, worker_output)
                    delegations.append(
                        DelegationStep(
                            step=step_idx,
                            agent_name=agent_name,
                            task=subtask,
                            result=worker_output,
                        )
                    )
                    history_log.append(f"Step {step_idx}: [{agent_name}] -> {worker_output}")
                    continue
                else:
                    history_log.append(
                        f"Step {step_idx}: Failed - worker '{agent_name}' not found. Available: {list(self.agents.keys())}"
                    )
                    continue

            # Fallback when LLM did not follow format
            history_log.append(f"Step {step_idx}: Output did not follow DELEGATE or FINAL_ANSWER format.")

        # Reached max steps without explicit FINAL_ANSWER; conclude with last findings
        return SupervisorResponse(
            final_answer=f"Reached maximum steps ({self.max_steps}). Summary of completed work: {json.dumps(self.bus.to_dict(), default=str)}",
            iterations=self.max_steps,
            delegations=delegations,
            state=self.bus.to_dict(),
        )

    def run_sync(self, task: str) -> SupervisorResponse:
        """Synchronous convenience wrapper for run()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(self.run(task))).result()
        return asyncio.run(self.run(task))
