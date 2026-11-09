"""Human-in-the-Loop (HITL) tool approval hooks and decision protocols (FEAT-06 / TASK-16)."""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Optional, Union
from pydantic import BaseModel, Field


class ApprovalDecision(str, Enum):
    """Possible outcomes for a sensitive tool invocation review."""

    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


class ApprovalRequest(BaseModel):
    """Payload provided to human reviewer or automated governance hook."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional auxiliary context (e.g. agent thought, user id, session id)",
    )


class ApprovalResponse(BaseModel):
    """Decision returned by the approval hook."""

    decision: ApprovalDecision = ApprovalDecision.APPROVED
    reason: Optional[str] = None
    modified_arguments: Optional[dict[str, Any]] = None

    @classmethod
    def approve(cls, reason: Optional[str] = None) -> ApprovalResponse:
        return cls(decision=ApprovalDecision.APPROVED, reason=reason)

    @classmethod
    def reject(cls, reason: str = "Execution rejected by human operator") -> ApprovalResponse:
        return cls(decision=ApprovalDecision.REJECTED, reason=reason)

    @classmethod
    def modify(cls, modified_arguments: dict[str, Any], reason: Optional[str] = None) -> ApprovalResponse:
        return cls(
            decision=ApprovalDecision.MODIFIED,
            modified_arguments=modified_arguments,
            reason=reason,
        )


class BaseApprovalHook(ABC):
    """Abstract interface for reviewing tool execution requests."""

    @abstractmethod
    async def review(self, request: ApprovalRequest) -> ApprovalResponse:
        """Review tool invocation and return an ApprovalResponse decision."""
        pass

    def review_sync(self, request: ApprovalRequest) -> ApprovalResponse:
        """Synchronous wrapper for review."""
        return asyncio.run(self.review(request))


class AutoApprovalHook(BaseApprovalHook):
    """Default non-blocking hook that approves all tool execution requests."""

    async def review(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse.approve(reason="Auto-approved by policy")


class CallableApprovalHook(BaseApprovalHook):
    """Hook delegating decisions to a custom sync or async callable."""

    def __init__(
        self,
        handler: Callable[
            [ApprovalRequest],
            Union[ApprovalResponse, bool, str],
        ],
    ) -> None:
        self.handler = handler
        self.is_async = inspect.iscoroutinefunction(handler)

    async def review(self, request: ApprovalRequest) -> ApprovalResponse:
        if self.is_async:
            result = await self.handler(request)
        else:
            result = await asyncio.to_thread(self.handler, request)

        if isinstance(result, ApprovalResponse):
            return result
        if isinstance(result, bool):
            return ApprovalResponse.approve() if result else ApprovalResponse.reject()
        if isinstance(result, str):
            if result.lower() in ("approve", "approved", "yes", "y"):
                return ApprovalResponse.approve()
            return ApprovalResponse.reject(reason=result)

        return ApprovalResponse.reject(reason=f"Invalid approval handler response: {result!r}")


class QueueApprovalHook(BaseApprovalHook):
    """Asynchronous queue-based hook for external UI/Human approval workflows."""

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout
        self.pending_requests: asyncio.Queue[tuple[ApprovalRequest, asyncio.Future[ApprovalResponse]]] = (
            asyncio.Queue()
        )

    async def review(self, request: ApprovalRequest) -> ApprovalResponse:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        await self.pending_requests.put((request, future))

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            return ApprovalResponse.reject(
                reason=f"Human approval timed out after {self.timeout}s"
            )

    async def resolve_next(
        self,
        decision: ApprovalDecision,
        reason: Optional[str] = None,
        modified_arguments: Optional[dict[str, Any]] = None,
    ) -> tuple[ApprovalRequest, ApprovalResponse]:
        """Resolve the oldest pending approval request in the queue."""
        request, future = await self.pending_requests.get()
        response = ApprovalResponse(
            decision=decision,
            reason=reason,
            modified_arguments=modified_arguments,
        )
        if not future.done():
            future.set_result(response)
        return request, response
