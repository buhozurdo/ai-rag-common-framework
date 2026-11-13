"""Tests for Multi-Agent Supervisor and Delegation Pattern (FEAT-19)."""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from common_framework.agents.react import AgentResponse, AgentStep, ReActAgent
from common_framework.agents.supervisor import (
    AgentBus,
    AgentSupervisor,
    DelegationStep,
    SupervisorResponse,
)
from common_framework.core.llm import BaseLLMClient, ChatMessage, ChatResponse, Role


class MockScriptedLLMClient(BaseLLMClient):
    """Mock LLM client returning a predefined sequence of responses."""

    def __init__(self, responses: List[str]) -> None:
        super().__init__()
        self.model = "mock-supervisor-model"
        self.responses = list(responses)
        self.call_count = 0
        self.recorded_messages: List[List[ChatMessage]] = []

    async def complete(self, messages: List[ChatMessage], **kwargs: Any) -> ChatResponse:
        self.recorded_messages.append(messages)
        if self.call_count < len(self.responses):
            content = self.responses[self.call_count]
        else:
            content = self.responses[-1]
        self.call_count += 1
        return ChatResponse(content=content, model=self.model)

    async def structured_complete(self, messages: List[ChatMessage], response_model: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class MockStreamingLLM(MockScriptedLLMClient):
    pass


def test_agent_bus_operations() -> None:
    """Test AgentBus get, set, initial state, and to_dict methods."""
    bus = AgentBus({"env": "test", "counter": 0})
    assert bus.get("env") == "test"
    assert bus.get("missing", "default_val") == "default_val"

    bus.set("counter", 1)
    bus.set("status", "ok")

    data = bus.to_dict()
    assert data == {"env": "test", "counter": 1, "status": "ok"}
    # Verify mutations on dictionary copy do not affect internal bus state
    data["env"] = "modified"
    assert bus.get("env") == "test"


@pytest.mark.asyncio
async def test_supervisor_direct_final_answer() -> None:
    """Test supervisor completing immediately when no delegation is required."""
    llm = MockScriptedLLMClient(["FINAL_ANSWER: 42 is the ultimate answer."])
    supervisor = AgentSupervisor(agents={}, llm_client=llm)

    response = await supervisor.run("What is the meaning of life?")
    assert response.final_answer == "42 is the ultimate answer."
    assert response.iterations == 1
    assert len(response.delegations) == 0
    assert response.state == {}


@pytest.mark.asyncio
async def test_supervisor_delegation_multi_step() -> None:
    """Test multi-step delegation across specialized worker agents."""
    script = [
        "DELEGATE: researcher | Search documentation for Python async best practices",
        "DELEGATE: coder | Write an async gather example based on findings",
        "FINAL_ANSWER: Here is the verified async code and explanation.",
    ]
    llm = MockScriptedLLMClient(script)

    async def mock_researcher(task: str) -> str:
        return f"Research findings for: {task}"

    def mock_coder(task: str) -> str:
        return "async def run(): await asyncio.gather()"

    agents = {
        "researcher": ("Web and documentation searcher", mock_researcher),
        "coder": ("Senior Python programmer", mock_coder),
    }

    supervisor = AgentSupervisor(agents=agents, llm_client=llm)
    response = await supervisor.run("Build an async pipeline")

    assert response.final_answer == "Here is the verified async code and explanation."
    assert response.iterations == 3
    assert len(response.delegations) == 2

    # Step 1: researcher
    assert response.delegations[0].step == 1
    assert response.delegations[0].agent_name == "researcher"
    assert "Research findings" in response.delegations[0].result

    # Step 2: coder
    assert response.delegations[1].step == 2
    assert response.delegations[1].agent_name == "coder"
    assert "async def run():" in response.delegations[1].result

    # Bus state verification
    assert "researcher" in response.state
    assert "coder" in response.state


@pytest.mark.asyncio
async def test_supervisor_with_react_agent_worker() -> None:
    """Test delegating to a ReActAgent instance with run() method."""
    mock_react_agent = MagicMock()

    async def mock_run(task: str) -> AgentResponse:
        return AgentResponse(
            output="ReAct agent solved the task",
            steps=[AgentStep(thought="Thinking", action="tool", action_input={"query": "x"}, observation="y")],
            iterations=1,
        )

    mock_react_agent.run = mock_run

    llm = MockScriptedLLMClient([
        "DELEGATE: react-worker | Perform multi-step reasoning",
        "FINAL_ANSWER: Completed using ReAct agent.",
    ])

    supervisor = AgentSupervisor(
        agents={"react-worker": ("Specialized reasoner", mock_react_agent)},
        llm_client=llm,
    )

    response = await supervisor.run("Solve complex logic")
    assert response.final_answer == "Completed using ReAct agent."
    assert len(response.delegations) == 1
    assert response.delegations[0].result == "ReAct agent solved the task"
    assert response.state["react-worker"] == "ReAct agent solved the task"


@pytest.mark.asyncio
async def test_supervisor_unknown_agent_handling() -> None:
    """Test supervisor handling delegation to a non-existent worker agent."""
    script = [
        "DELEGATE: non_existent | Do something impossible",
        "FINAL_ANSWER: Handled missing agent gracefully.",
    ]
    llm = MockScriptedLLMClient(script)

    supervisor = AgentSupervisor(
        agents={"valid_worker": ("Does valid work", lambda t: "ok")},
        llm_client=llm,
    )

    response = await supervisor.run("Run with bad agent name")
    assert response.final_answer == "Handled missing agent gracefully."
    assert response.iterations == 2
    assert len(response.delegations) == 0


@pytest.mark.asyncio
async def test_supervisor_worker_exception_handling() -> None:
    """Test supervisor resiliently capturing worker exceptions."""
    def failing_worker(task: str) -> str:
        raise ValueError("Database connection failed")

    script = [
        "DELEGATE: db_agent | Fetch data",
        "FINAL_ANSWER: Failed gracefully due to db error.",
    ]
    llm = MockScriptedLLMClient(script)

    supervisor = AgentSupervisor(
        agents={"db_agent": ("Database fetcher", failing_worker)},
        llm_client=llm,
    )

    response = await supervisor.run("Fetch records")
    assert response.final_answer == "Failed gracefully due to db error."
    assert len(response.delegations) == 1
    assert "Error executing worker db_agent: Database connection failed" in response.delegations[0].result
    assert "Error executing worker db_agent" in response.state["db_agent"]


@pytest.mark.asyncio
async def test_supervisor_unrecognized_format_and_max_steps() -> None:
    """Test fallback when output format is invalid and loop reaches max_steps."""
    # LLM outputs non-conforming responses continuously
    script = [
        "I am thinking about the problem...",
        "Still thinking about it...",
    ]
    llm = MockScriptedLLMClient(script)

    supervisor = AgentSupervisor(
        agents={"dummy": ("Dummy", lambda t: "ok")},
        llm_client=llm,
        max_steps=2,
    )

    response = await supervisor.run("Perform task")
    assert "Reached maximum steps (2)" in response.final_answer
    assert response.iterations == 2
    assert len(response.delegations) == 0


def test_supervisor_custom_system_prompt_and_defaults() -> None:
    """Test custom system prompt configuration and default prompt generation."""
    custom_prompt = "You are a custom orchestrator."
    llm = MockScriptedLLMClient(["FINAL_ANSWER: Done"])

    supervisor = AgentSupervisor(
        agents={"w1": ("Worker one", "static result")},
        llm_client=llm,
        system_prompt=custom_prompt,
    )
    assert supervisor._build_system_prompt() == custom_prompt

    # Test default system prompt includes workers description
    supervisor_default = AgentSupervisor(
        agents={"w1": ("Worker one", "static result")},
        llm_client=llm,
    )
    prompt = supervisor_default._build_system_prompt()
    assert "w1: Worker one" in prompt
    assert "DELEGATE:" in prompt
    assert "FINAL_ANSWER:" in prompt


def test_supervisor_run_sync_execution() -> None:
    """Test synchronous convenience wrapper run_sync()."""
    llm = MockScriptedLLMClient([
        "DELEGATE: sync_worker | Run task synchronously",
        "FINAL_ANSWER: Sync run successful.",
    ])

    supervisor = AgentSupervisor(
        agents={"sync_worker": ("Synchronous worker", lambda t: f"Executed: {t}")},
        llm_client=llm,
    )

    response = supervisor.run_sync("Sync test task")
    assert isinstance(response, SupervisorResponse)
    assert response.final_answer == "Sync run successful."
    assert len(response.delegations) == 1
    assert response.delegations[0].result == "Executed: Run task synchronously"


def test_supervisor_run_sync_inside_running_loop() -> None:
    """Test run_sync() correctly executes via thread pool when called in an active event loop."""
    llm = MockScriptedLLMClient(["FINAL_ANSWER: Executed inside running loop."])
    supervisor = AgentSupervisor(agents={}, llm_client=llm)

    async def inside_loop() -> SupervisorResponse:
        return supervisor.run_sync("Task inside loop")

    res = asyncio.run(inside_loop())
    assert res.final_answer == "Executed inside running loop."
