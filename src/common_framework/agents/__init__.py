"""Autonomous Agents module for common framework."""

from common_framework.agents.hitl import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    AutoApprovalHook,
    BaseApprovalHook,
    CallableApprovalHook,
    QueueApprovalHook,
)
from common_framework.agents.memory import (
    BaseMemory,
    ConversationBufferMemory,
    VectorMemory,
)
from common_framework.agents.react import (
    AgentResponse,
    AgentStep,
    ReActAgent,
)

__all__ = [
    "ReActAgent",
    "AgentStep",
    "AgentResponse",
    "BaseMemory",
    "ConversationBufferMemory",
    "VectorMemory",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResponse",
    "BaseApprovalHook",
    "AutoApprovalHook",
    "CallableApprovalHook",
    "QueueApprovalHook",
]
