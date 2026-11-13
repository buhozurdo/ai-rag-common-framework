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
from common_framework.agents.supervisor import (
    AgentBus,
    AgentSupervisor,
    DelegationStep,
    SupervisorResponse,
)

__all__ = [
    "ReActAgent",
    "AgentStep",
    "AgentResponse",
    "AgentSupervisor",
    "AgentBus",
    "DelegationStep",
    "SupervisorResponse",
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
