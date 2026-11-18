"""Stateful Directed Graph Workflow Engine.

Lightweight graph-based orchestrator coordinating multi-step cyclic and acyclic agent workflows with state persistence.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import inspect
import time
import uuid
from typing import Any, Callable, Coroutine, Optional, Union, TYPE_CHECKING
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from common_framework.agents.checkpoints import BaseCheckpointSaver

START = "__start__"
END = "__end__"


class WorkflowStep(BaseModel):
    """Execution step record representing a completed graph node."""

    node: str
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class WorkflowResult(BaseModel):
    """Final outcome returned by StateGraph execution."""

    state: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStep] = Field(default_factory=list)
    iterations: int = 0
    completed: bool = True


NodeCallable = Callable[[dict[str, Any]], Union[dict[str, Any], Coroutine[Any, Any, dict[str, Any]]]]
RouterCallable = Callable[[dict[str, Any]], Union[str, Coroutine[Any, Any, str]]]


class StateGraph:
    """Stateful directed graph coordinating node executions and routing transitions."""

    def __init__(self, state_schema: Optional[type[BaseModel]] = None) -> None:
        self.state_schema = state_schema
        self._nodes: dict[str, NodeCallable] = {}
        self._edges: dict[str, str] = {}
        self._conditional_edges: dict[str, tuple[RouterCallable, Optional[dict[str, str]]]] = {}
        self._entry_point: Optional[str] = None

    def add_node(self, name: str, fn: NodeCallable) -> StateGraph:
        """Register a state-transforming node function (sync or async)."""
        if name in (START, END):
            raise ValueError(f"Cannot use reserved sentinel node name '{name}'.")
        self._nodes[name] = fn
        return self

    def set_entry_point(self, node: str) -> StateGraph:
        """Define the first node to execute when the workflow starts."""
        self._entry_point = node
        return self

    def add_edge(self, source: str, target: str) -> StateGraph:
        """Register a direct transition from source node to target node."""
        if source == START:
            self._entry_point = target
        else:
            self._edges[source] = target
        return self

    def add_conditional_edges(
        self,
        source: str,
        router: RouterCallable,
        path_map: Optional[dict[str, str]] = None,
    ) -> StateGraph:
        """Register dynamic branching logic from source node based on state."""
        self._conditional_edges[source] = (router, path_map)
        return self

    def validate(self) -> None:
        """Validate graph integrity, entry point, and node references."""
        if not self._entry_point:
            raise ValueError("StateGraph requires an entry point. Call set_entry_point() or add_edge(START, node).")
        if self._entry_point not in self._nodes:
            raise ValueError(f"Entry point '{self._entry_point}' is not registered in graph nodes.")

        for src, dst in self._edges.items():
            if src not in self._nodes:
                raise ValueError(f"Edge source '{src}' is not registered in graph nodes.")
            if dst != END and dst not in self._nodes:
                raise ValueError(f"Edge target '{dst}' from '{src}' is not registered in graph nodes.")

        for src, (_, path_map) in self._conditional_edges.items():
            if src not in self._nodes:
                raise ValueError(f"Conditional edge source '{src}' is not registered in graph nodes.")
            if src in self._edges:
                raise ValueError(f"Conflicting edges: node '{src}' has both a static edge and a conditional edge.")
            if path_map:
                for target in path_map.values():
                    if target != END and target not in self._nodes:
                        raise ValueError(f"Conditional edge target '{target}' from '{src}' is not registered.")

    async def _invoke_callable(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Safely invoke sync or async callables without blocking the event loop."""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, fn, *args)
        if asyncio.iscoroutine(res):
            return await res
        return res

    async def run(
        self,
        initial_state: Optional[dict[str, Any]] = None,
        max_steps: int = 50,
        checkpoint_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
        checkpointer: Optional[Any] = None,
        thread_id: Optional[str] = None,
    ) -> WorkflowResult:
        """Execute the workflow asynchronously from entry point to END with optional checkpointing."""
        self.validate()

        effective_thread_id = thread_id or str(uuid.uuid4())
        state: dict[str, Any] = {}

        # Resume from checkpoint if thread_id has an existing checkpoint and no initial_state provided
        if checkpointer and thread_id:
            latest_chk = checkpointer.get(thread_id)
            if latest_chk is not None:
                state.update(latest_chk.state)

        if initial_state:
            state.update(initial_state)

        steps: list[WorkflowStep] = []
        current_node: Optional[str] = self._entry_point
        iterations = 0

        while current_node and current_node != END and iterations < max_steps:
            iterations += 1
            node_fn = self._nodes.get(current_node)
            if not node_fn:
                raise ValueError(f"Workflow execution reached unregistered node '{current_node}'.")

            t0 = time.perf_counter()
            # Deepcopy state to prevent uncontrolled in-place mutation and retroactively mutating snapshots
            node_output = await self._invoke_callable(node_fn, copy.deepcopy(state) if state else {})
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if isinstance(node_output, dict):
                state.update(node_output)

            # Record step snapshot with deep copy
            step_record = WorkflowStep(
                node=current_node,
                state_snapshot=copy.deepcopy(state),
                duration_ms=round(elapsed_ms, 3),
            )
            steps.append(step_record)

            if checkpointer:
                checkpointer.save(
                    thread_id=effective_thread_id,
                    node=current_node,
                    step=iterations,
                    state=copy.deepcopy(state),
                )

            if checkpoint_callback:
                await self._invoke_callable(checkpoint_callback, current_node, copy.deepcopy(state))

            # Determine next node transition
            if current_node in self._conditional_edges:
                router, path_map = self._conditional_edges[current_node]
                next_key = await self._invoke_callable(router, copy.deepcopy(state))
                if path_map and next_key in path_map:
                    current_node = path_map[next_key]
                else:
                    current_node = str(next_key)
            elif current_node in self._edges:
                current_node = self._edges[current_node]
            else:
                # Terminal node with no outgoing edges defaults to END
                current_node = END

        completed = (current_node == END)
        return WorkflowResult(
            state=state,
            steps=steps,
            iterations=iterations,
            completed=completed,
        )

    def run_sync(
        self,
        initial_state: Optional[dict[str, Any]] = None,
        max_steps: int = 50,
        checkpoint_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
        checkpointer: Optional[Any] = None,
        thread_id: Optional[str] = None,
    ) -> WorkflowResult:
        """Synchronous wrapper for run() supporting execution within running loops."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(
                        self.run(
                            initial_state=initial_state,
                            max_steps=max_steps,
                            checkpoint_callback=checkpoint_callback,
                            checkpointer=checkpointer,
                            thread_id=thread_id,
                        )
                    )
                ).result()
        return asyncio.run(
            self.run(
                initial_state=initial_state,
                max_steps=max_steps,
                checkpoint_callback=checkpoint_callback,
                checkpointer=checkpointer,
                thread_id=thread_id,
            )
        )
