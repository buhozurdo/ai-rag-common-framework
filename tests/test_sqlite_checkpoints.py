"""Comprehensive unit tests for SQLite Checkpoint Saver and StateGraph persistence (Workflow milestone v0.21.0)."""

from pathlib import Path
import pytest

from common_framework.agents.checkpoints import Checkpoint, SQLiteCheckpointSaver
from common_framework.agents.workflow import END, START, StateGraph


def test_sqlite_checkpoint_saver_crud(tmp_path: Path):
    db_file = tmp_path / "test_checkpoints.db"
    saver = SQLiteCheckpointSaver(str(db_file))

    # Save checkpoints for thread 1
    chk1 = saver.save(thread_id="t1", node="fetch", step=1, state={"count": 1})
    assert chk1.thread_id == "t1"
    assert chk1.node == "fetch"
    assert chk1.state == {"count": 1}

    chk2 = saver.save(thread_id="t1", node="process", step=2, state={"count": 2, "status": "ok"})

    # Get latest
    latest = saver.get("t1")
    assert latest is not None
    assert latest.checkpoint_id == chk2.checkpoint_id
    assert latest.node == "process"
    assert latest.state["count"] == 2

    # Get by specific ID
    fetched1 = saver.get("t1", checkpoint_id=chk1.checkpoint_id)
    assert fetched1 is not None
    assert fetched1.node == "fetch"

    # List all in chronological order
    all_chks = saver.list("t1")
    assert len(all_chks) == 2
    assert all_chks[0].step == 1
    assert all_chks[1].step == 2

    # Non-existent thread
    assert saver.get("non_existent") is None
    assert saver.list("non_existent") == []

    saver.close()


@pytest.mark.asyncio
async def test_stategraph_with_sqlite_checkpointer():
    saver = SQLiteCheckpointSaver(":memory:")

    def step_a(state: dict) -> dict:
        return {"val": state.get("val", 0) + 10}

    def step_b(state: dict) -> dict:
        return {"val": state["val"] * 2}

    graph = (
        StateGraph()
        .add_node("a", step_a)
        .add_node("b", step_b)
        .add_edge(START, "a")
        .add_edge("a", "b")
        .add_edge("b", END)
    )

    result = await graph.run(
        initial_state={"val": 5},
        checkpointer=saver,
        thread_id="thread-xyz",
    )

    assert result.completed is True
    assert result.state["val"] == 30  # (5 + 10) * 2 = 30

    # Verify checkpoints were persisted
    chks = saver.list("thread-xyz")
    assert len(chks) == 2
    assert chks[0].node == "a"
    assert chks[0].state["val"] == 15
    assert chks[1].node == "b"
    assert chks[1].state["val"] == 30

    saver.close()


@pytest.mark.asyncio
async def test_stategraph_resume_from_checkpoint():
    saver = SQLiteCheckpointSaver(":memory:")

    # Pre-seed state at checkpoint
    saver.save(
        thread_id="resumed-session",
        node="step1",
        step=1,
        state={"user": "John", "step1_done": True},
    )

    def step2(state: dict) -> dict:
        assert state.get("step1_done") is True
        return {"step2_done": True}

    graph = (
        StateGraph()
        .add_node("step2", step2)
        .add_edge(START, "step2")
        .add_edge("step2", END)
    )

    # Run without initial_state, should automatically resume from latest checkpoint
    result = await graph.run(
        checkpointer=saver,
        thread_id="resumed-session",
    )

    assert result.completed is True
    assert result.state["step1_done"] is True
    assert result.state["step2_done"] is True
    assert result.state["user"] == "John"

    saver.close()
