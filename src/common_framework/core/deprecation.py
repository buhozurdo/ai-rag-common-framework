"""Public API Contract Freeze and Deprecation Warning System (FEAT-13)."""

from __future__ import annotations

import functools
import inspect
import warnings
from typing import Any, Callable, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class FrameworkDeprecationWarning(FutureWarning):
    """Warning category emitted when accessing deprecated framework features."""
    pass


def deprecated(
    since: str,
    removal_version: str,
    alternative: Optional[str] = None,
    details: Optional[str] = None,
) -> Callable[[F], F]:
    """Decorator to mark public classes, functions, or methods as deprecated.

    Emits a `FrameworkDeprecationWarning` when the decorated object is invoked.

    Args:
        since: The version where deprecation began (e.g. "0.19.0").
        removal_version: The version where the item will be completely removed (e.g. "1.0.0").
        alternative: Recommended modern replacement function or class.
        details: Optional contextual explanation or migration instructions.

    Usage:
        @deprecated(since="0.19.0", removal_version="1.0.0", alternative="new_function")
        def legacy_function():
            ...
    """

    def decorator(target: F) -> F:
        message = (
            f"'{target.__name__}' is deprecated since v{since} and will be removed in v{removal_version}."
        )
        if alternative:
            message += f" Use '{alternative}' instead."
        if details:
            message += f" Details: {details}"

        if inspect.isclass(target):
            orig_init = target.__init__

            @functools.wraps(orig_init)
            def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
                warnings.warn(message, category=FrameworkDeprecationWarning, stacklevel=2)
                orig_init(self, *args, **kwargs)

            target.__init__ = new_init  # type: ignore[misc]
            target.__deprecated__ = True  # type: ignore[attr-defined]
            target.__deprecated_since__ = since  # type: ignore[attr-defined]
            target.__deprecated_removal__ = removal_version  # type: ignore[attr-defined]
            return target

        if inspect.iscoroutinefunction(target):
            @functools.wraps(target)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                warnings.warn(message, category=FrameworkDeprecationWarning, stacklevel=2)
                return await target(*args, **kwargs)

            async_wrapper.__deprecated__ = True  # type: ignore[attr-defined]
            async_wrapper.__deprecated_since__ = since  # type: ignore[attr-defined]
            async_wrapper.__deprecated_removal__ = removal_version  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(target)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(message, category=FrameworkDeprecationWarning, stacklevel=2)
            return target(*args, **kwargs)

        wrapper.__deprecated__ = True  # type: ignore[attr-defined]
        wrapper.__deprecated_since__ = since  # type: ignore[attr-defined]
        wrapper.__deprecated_removal__ = removal_version  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def warn_deprecated_parameter(
    param_name: str,
    since: str,
    removal_version: str,
    alternative: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """Emit a deprecation warning for an obsolete parameter passed to a function."""
    msg = f"Parameter '{param_name}' is deprecated since v{since} and will be removed in v{removal_version}."
    if alternative:
        msg += f" Use parameter '{alternative}' instead."
    if details:
        msg += f" Details: {details}"
    warnings.warn(msg, category=FrameworkDeprecationWarning, stacklevel=3)
