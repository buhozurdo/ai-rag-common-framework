"""Central registry for managing, discovering, and executing tools."""

from __future__ import annotations

from typing import Any, Callable, Optional
from common_framework.core.exceptions import FrameworkError
from common_framework.tools.base import Tool, tool


class ToolNotFoundError(FrameworkError):
    """Raised when a requested tool does not exist in the registry."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool '{tool_name}' is not registered.")
        self.tool_name = tool_name


class ToolRegistry:
    """Registry container storing and executing tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, target: Tool | Callable[..., Any]) -> Tool:
        """Register a Tool instance or a raw function into the registry."""
        tool_instance = target if isinstance(target, Tool) else tool(target)
        self._tools[tool_instance.name] = tool_instance
        return tool_instance

    def unregister(self, name: str) -> Optional[Tool]:
        """Remove a tool from the registry."""
        return self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        """Retrieve a registered tool by name."""
        if name not in self._tools:
            raise ToolNotFoundError(name)
        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        """Return all registered tools as an array of OpenAI function definitions."""
        return [t.to_openai_schema() for t in self._tools.values()]

    async def execute(self, tool_name: str, **kwargs: Any) -> Any:
        """Execute a registered tool by name with provided arguments."""
        t = self.get(tool_name)
        return await t.execute(**kwargs)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        names = list(self._tools.keys())
        return f"<ToolRegistry tools={names}>"


# Global singleton registry
global_tool_registry = ToolRegistry()
