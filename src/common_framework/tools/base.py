"""Universal Tool abstraction with automatic schema generation and validation."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, Coroutine, Optional, Union, get_type_hints
from pydantic import BaseModel, create_model
from pydantic.fields import FieldInfo

from common_framework.core.exceptions import FrameworkError


class ToolExecutionError(FrameworkError):
    """Raised when an error occurs during tool execution."""

    def __init__(self, tool_name: str, message: str, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(f"Error executing tool '{tool_name}': {message}", details)
        self.tool_name = tool_name


class Tool:
    """A callable tool with strict Pydantic validation and auto-generated JSON schema."""

    def __init__(
        self,
        fn: Callable[..., Any],
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        self.fn = fn
        self.name = name or fn.__name__
        self.description = (
            description or inspect.getdoc(fn) or f"Tool executing {self.name}"
        ).strip()
        self.is_async = inspect.iscoroutinefunction(fn)
        self._args_model = self._create_args_model()
        self._parameters_schema = self._args_model.model_json_schema()
        # Clean up JSON schema title to keep it clean
        self._parameters_schema.pop("title", None)

    def _create_args_model(self) -> type[BaseModel]:
        """Dynamically create a Pydantic model representing the function arguments."""
        sig = inspect.signature(self.fn)
        try:
            type_hints = get_type_hints(self.fn)
        except Exception:
            type_hints = {}

        fields: dict[str, tuple[Any, Any]] = {}

        for param_name, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue

            param_type = type_hints.get(param_name, Any)
            if param.default is inspect.Parameter.empty:
                # Required field
                fields[param_name] = (param_type, ...)
            else:
                # Optional field with default value
                fields[param_name] = (param_type, param.default)

        model_name = f"{self.name.capitalize()}Input"
        return create_model(model_name, **fields)

    @property
    def parameters_schema(self) -> dict[str, Any]:
        """Return the JSON schema representing tool input parameters."""
        return self._parameters_schema

    def to_openai_schema(self) -> dict[str, Any]:
        """Format the tool schema for OpenAI/compatible function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._parameters_schema,
            },
        }

    async def execute(self, **kwargs: Any) -> Any:
        """Validate input arguments against schema and execute the underlying function."""
        try:
            validated = self._args_model.model_validate(kwargs)
            validated_kwargs = validated.model_dump()
        except Exception as exc:
            raise ToolExecutionError(
                self.name,
                f"Input validation error: {exc}",
                details={"inputs": kwargs},
            ) from exc

        try:
            if self.is_async:
                return await self.fn(**validated_kwargs)
            return self.fn(**validated_kwargs)
        except Exception as exc:
            raise ToolExecutionError(
                self.name,
                str(exc),
                details={"inputs": validated_kwargs},
            ) from exc

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Allow calling the tool directly as a callable object."""
        if args:
            # Bind positional args to kwargs
            sig = inspect.signature(self.fn)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return await self.execute(**bound.arguments)
        return await self.execute(**kwargs)

    def __repr__(self) -> str:
        return f"<Tool name={self.name!r} async={self.is_async}>"


def tool(
    fn_or_name: Union[Callable[..., Any], str, None] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Any:
    """Decorator to declare a function as a reusable Tool.

    Can be used as:
        @tool
        def my_tool(x: int) -> int: ...

    Or:
        @tool(name="custom_name", description="Custom description")
        def my_tool(x: int) -> int: ...
    """
    if callable(fn_or_name):
        return Tool(fn_or_name)

    custom_name = name or (fn_or_name if isinstance(fn_or_name, str) else None)

    def decorator(fn: Callable[..., Any]) -> Tool:
        return Tool(fn, name=custom_name, description=description)

    return decorator
