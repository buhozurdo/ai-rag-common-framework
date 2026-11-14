"""Tests for Stateful Directed Graph Workflow Engine (FEAT-20)."""

from __future__ import annotations

import asyncio
from typing import Any
import pytest

from common_framework.agents.workflow import (
    END,
    START,
    StateGraph,
    WorkflowResult,
    WorkflowStep,
)


def test_fluent_api_chaining() -> None:
    """Test method chaining for graph creation."""
    graph = StateGraph()
    ret = (
        graph.add_node("step1", lambda s: {"val": 1})
        .add_node("step2", lambda s: {"val": 2})
        .set_entry_point("step1")
        .add_edge("step1", "step2")
        .add_edge("step2", END)
    )
    assert ret is graph


def test_reserved_node_name_validation() -> None:
    """Test using START or END as node name raises ValueError."""
    graph = StateGraph()
    with pytest.raises(ValueError, match="reserved sentinel node name"):
        graph.add_node(START, lambda s: s)

    with pytest.raises(ValueError, match="reserved sentinel node name"):
        graph.add_node(END, lambda s: s)


def test_validation_integrity() -> None:
    """Test graph structure validation catches missing entry points and invalid node references."""
    # 1. No entry point
    g1 = StateGraph().add_node("n1", lambda s: s)
    with pytest.raises(ValueError, match="requires an entry point"):
        g1.validate()

    # 2. Entry point not registered
    g2 = StateGraph().set_entry_point("missing")
    with pytest.raises(ValueError, match="is not registered"):
        g2.validate()

    # 3. Invalid edge source
    g3 = StateGraph().add_node("n1", lambda s: s).set_entry_point("n1").add_edge("missing_src", "n1")
    with pytest.raises(ValueError, match="Edge source 'missing_src' is not registered"):
        g3.validate()

    # 4. Invalid edge target
    g4 = StateGraph().add_node("n1", lambda s: s).set_entry_point("n1").add_edge("n1", "missing_dst")
    with pytest.raises(ValueError, match="Edge target 'missing_dst' from 'n1' is not registered"):
        g4.validate()

    # 5. Invalid conditional edge source
    g5 = StateGraph().add_node("n1", lambda s: s).set_entry_point("n1").add_conditional_edges("missing", lambda s: END)
    with pytest.raises(ValueError, match="Conditional edge source 'missing' is not registered"):
        g5.validate()

    # 6. Invalid conditional edge target in path_map
    g6 = (
        StateGraph()
        .add_node("n1", lambda s: s)
        .set_entry_point("n1")
        .add_conditional_edges("n1", lambda s: "bad", {"bad": "missing_target"})
    )
    with pytest.raises(ValueError, match="Conditional edge target 'missing_target' from 'n1' is not registered"):
        g6.validate()


@pytest.mark.asyncio
async def test_linear_workflow_execution() -> None:
    """Test a straightforward linear 3-step workflow."""
    def step_a(state: dict[str, Any]) -> dict[str, Any]:
        return {"a": 1, "log": state.get("log", []) + ["A"]}

    async def step_b(state: dict[str, Any]) -> dict[str, Any]:
        return {"b": 2, "log": state["log"] + ["B"]}

    def step_c(state: dict[str, Any]) -> dict[str, Any]:
        return {"c": state["a"] + state["b"], "log": state["log"] + ["C"]}

    graph = (
        StateGraph()
        .add_node("node_a", step_a)
        .add_node("node_b", step_b)
        .add_node("node_c", step_c)
        .add_edge(START, "node_a")
        .add_edge("node_a", "node_b")
        .add_edge("node_b", "node_c")
        .add_edge("node_c", END)
    )

    result = await graph.run({"initial": True})
    assert result.completed is True
    assert result.iterations == 3
    assert result.state["initial"] is True
    assert result.state["a"] == 1
    assert result.state["b"] == 2
    assert result.state["c"] == 3
    assert result.state["log"] == ["A", "B", "C"]

    assert len(result.steps) == 3
    assert [s.node for s in result.steps] == ["node_a", "node_b", "node_c"]
    assert all(s.duration_ms >= 0.0 for s in result.steps)


@pytest.mark.asyncio
async def test_conditional_branching_and_path_map() -> None:
    """Test dynamic routing through conditional edges and path_map."""
    def classify(state: dict[str, Any]) -> dict[str, Any]:
        score = state.get("score", 0)
        return {"category": "high" if score >= 80 else "low"}

    def router(state: dict[str, Any]) -> str:
        return state["category"]

    def handle_high(state: dict[str, Any]) -> dict[str, Any]:
        return {"action": "approved"}

    def handle_low(state: dict[str, Any]) -> dict[str, Any]:
        return {"action": "rejected"}

    graph = (
        StateGraph()
        .add_node("classify", classify)
        .add_node("high_flow", handle_high)
        .add_node("low_flow", handle_low)
        .set_entry_point("classify")
        .add_conditional_edges(
            "classify",
            router,
            {"high": "high_flow", "low": "low_flow"},
        )
        .add_edge("high_flow", END)
        .add_edge("low_flow", END)
    )

    # Path 1: high score
    res_high = await graph.run({"score": 95})
    assert res_high.state["action"] == "approved"
    assert [s.node for s in res_high.steps] == ["classify", "high_flow"]

    # Path 2: low score
    res_low = await graph.run({"score": 40})
    assert res_low.state["action"] == "rejected"
    assert [s.node for s in res_low.steps] == ["classify", "low_flow"]


@pytest.mark.asyncio
async def test_cyclic_workflow_and_termination() -> None:
    """Test iterative cyclic workflow stopping when condition is fulfilled."""
    def counter_node(state: dict[str, Any]) -> dict[str, Any]:
        count = state.get("count", 0) + 1
        return {"count": count}

    def should_continue(state: dict[str, Any]) -> str:
        if state["count"] >= 3:
            return END
        return "counter"

    graph = (
        StateGraph()
        .add_node("counter", counter_node)
        .set_entry_point("counter")
        .add_conditional_edges("counter", should_continue)
    )

    result = await graph.run({"count": 0})
    assert result.completed is True
    assert result.iterations == 3
    assert result.state["count"] == 3
    assert len(result.steps) == 3


@pytest.mark.asyncio
async def test_infinite_loop_max_steps_guard() -> None:
    """Test runaway loops are terminated by max_steps with completed=False."""
    def loop_node(state: dict[str, Any]) -> dict[str, Any]:
        return {"tick": state.get("tick", 0) + 1}

    graph = (
        StateGraph()
        .add_node("loop", loop_node)
        .set_entry_point("loop")
        .add_edge("loop", "loop")
    )

    result = await graph.run({"tick": 0}, max_steps=5)
    assert result.completed is False
    assert result.iterations == 5
    assert result.state["tick"] == 5
    assert len(result.steps) == 5


@pytest.mark.asyncio
async def test_terminal_node_implicit_end() -> None:
    """Test a terminal node with no outgoing edges transitions to END."""
    def terminal_step(state: dict[str, Any]) -> dict[str, Any]:
        return {"finished": True}

    graph = (
        StateGraph()
        .add_node("final_step", terminal_step)
        .set_entry_point("final_step")
    )

    result = await graph.run({})
    assert result.completed is True
    assert result.iterations == 1
    assert result.state["finished"] is True


@pytest.mark.asyncio
async def test_checkpoint_callback() -> None:
    """Test state checkpoint callback invocation at each step."""
    snapshots: list[tuple[str, dict[str, Any]]] = []

    def checkpoint_logger(node: str, state: dict[str, Any]) -> None:
        snapshots.append((node, dict(state)))

    graph = (
        StateGraph()
        .add_node("n1", lambda s: {"step1": True})
        .add_node("n2", lambda s: {"step2": True})
        .set_entry_point("n1")
        .add_edge("n1", "n2")
        .add_edge("n2", END)
    )

    result = await graph.run({}, checkpoint_callback=checkpoint_logger)
    assert result.completed is True
    assert len(snapshots) == 2
    assert snapshots[0] == ("n1", {"step1": True})
    assert snapshots[1] == ("n2", {"step1": True, "step2": True})


def test_run_sync_execution() -> None:
    """Test synchronous execution wrapper run_sync()."""
    graph = (
        StateGraph()
        .add_node("sync_node", lambda s: {"sync": "ok"})
        .set_entry_point("sync_node")
        .add_edge("sync_node", END)
    )

    res = graph.run_sync({"input": "data"})
    assert isinstance(res, WorkflowResult)
    assert res.completed is True
    assert res.state == {"input": "data", "sync": "ok"}
    assert res.iterations == 1


def test_run_sync_inside_running_loop() -> None:
    """Test run_sync() executes cleanly via thread pool inside an active loop."""
    graph = (
        StateGraph()
        .add_node("step", lambda s: {"in_loop": True})
        .set_entry_point("step")
        .add_edge("step", END)
    )

    async def caller() -> WorkflowResult:
        return graph.run_sync()

    res = asyncio.run(caller())
    assert res.completed is True
    assert res.state["in_loop"] is True
