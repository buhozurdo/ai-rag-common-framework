"""Stateful circuit breaker resilience engine with failure memory and exponential backoff."""

from __future__ import annotations

import asyncio
from collections import deque
from enum import Enum
import logging
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from common_framework.core.exceptions import (
    CircuitBreakerOpenError,
    LLMProviderError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
)

logger = logging.getLogger("common_framework.resilience")

R = TypeVar("R")

DEFAULT_FAILURE_EXCEPTIONS: tuple[type[Exception], ...] = (
    RateLimitError,
    LLMProviderError,
    httpx.RequestError,
    TimeoutError,
    asyncio.TimeoutError,
)


class CircuitState(str, Enum):
    """Operational states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConfig(BaseModel):
    """Configuration parameters for a stateful CircuitBreaker."""

    model_config = ConfigDict(frozen=True)

    failure_threshold: int = Field(
        default=5,
        ge=1,
        description="Failures required to trip the circuit from CLOSED to OPEN",
    )
    recovery_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Base cooldown duration in seconds before transitioning OPEN -> HALF_OPEN",
    )
    backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        description="Exponential backoff multiplier applied on consecutive OPEN transitions",
    )
    max_recovery_timeout_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description="Maximum capped cooldown duration in seconds under exponential backoff",
    )
    half_open_max_calls: int = Field(
        default=1,
        ge=1,
        description="Maximum concurrent probe calls permitted in HALF_OPEN state",
    )
    success_threshold: int = Field(
        default=1,
        ge=1,
        description="Consecutive successful probes in HALF_OPEN required to close the circuit",
    )
    failure_window_seconds: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Optional sliding time window in seconds for counting failures in CLOSED state",
    )


class CircuitBreakerStats(BaseModel):
    """Snapshot of circuit breaker state, failure memory, and counters."""

    name: str
    state: CircuitState
    consecutive_failures: int = 0
    recent_failures: int = 0
    open_count: int = 0
    half_open_in_flight: int = 0
    half_open_successes: int = 0
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_rejected: int = 0
    current_cooldown_seconds: float = 0.0
    retry_after_seconds: Optional[float] = None
    last_failure_error: Optional[str] = None


class CircuitBreaker:
    """Stateful circuit breaker with failure memory, exponential backoff, and automatic recovery."""

    def __init__(
        self,
        name: str = "default",
        config: Optional[CircuitBreakerConfig] = None,
        failure_exceptions: Optional[tuple[type[Exception], ...]] = None,
        excluded_exceptions: tuple[type[Exception], ...] = (StructuredOutputError,),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.failure_exceptions = failure_exceptions or DEFAULT_FAILURE_EXCEPTIONS
        self.excluded_exceptions = excluded_exceptions
        self._clock = clock

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_timestamps: deque[float] = deque()
        self._consecutive_failures: int = 0
        self._open_count: int = 0
        self._next_attempt_at: Optional[float] = None
        self._current_cooldown: float = 0.0
        self._half_open_in_flight: int = 0
        self._half_open_successes: int = 0

        self._total_calls: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0
        self._total_rejected: int = 0
        self._last_failure_error: Optional[str] = None

        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _prune_failure_window(self, now: float) -> None:
        window = self.config.failure_window_seconds
        if window is None:
            return
        cutoff = now - window
        while self._failure_timestamps and self._failure_timestamps[0] < cutoff:
            self._failure_timestamps.popleft()

    def _refresh_state(self, now: float) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._next_attempt_at is not None
            and now >= self._next_attempt_at
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_in_flight = 0
            self._half_open_successes = 0
            logger.info("CircuitBreaker '%s' transitioned OPEN -> HALF_OPEN.", self.name)

    def _trip_open(self, now: float) -> None:
        self._open_count += 1
        exponent = max(0, self._open_count - 1)
        cooldown = min(
            self.config.max_recovery_timeout_seconds,
            self.config.recovery_timeout_seconds * (self.config.backoff_multiplier ** exponent),
        )
        self._current_cooldown = cooldown
        self._next_attempt_at = now + cooldown
        self._state = CircuitState.OPEN
        self._half_open_in_flight = 0
        self._half_open_successes = 0
        logger.warning(
            "CircuitBreaker '%s' tripped to OPEN (trip #%d, cooldown=%.2fs, last_error=%s).",
            self.name,
            self._open_count,
            cooldown,
            self._last_failure_error,
        )

    def _is_counted_failure(self, exc: Exception) -> bool:
        if isinstance(exc, CircuitBreakerOpenError):
            return False
        if self.excluded_exceptions and isinstance(exc, self.excluded_exceptions):
            return False
        return isinstance(exc, self.failure_exceptions)

    @property
    def state(self) -> CircuitState:
        """Return current circuit state, transitioning OPEN -> HALF_OPEN if cooldown elapsed."""
        self._refresh_state(self._clock())
        return self._state

    def stats(self) -> CircuitBreakerStats:
        """Return a consistent snapshot of breaker state and counters."""
        now = self._clock()
        self._refresh_state(now)
        self._prune_failure_window(now)
        retry_after: Optional[float] = None
        if self._state is CircuitState.OPEN and self._next_attempt_at is not None:
            retry_after = max(0.0, self._next_attempt_at - now)
        recent = (
            len(self._failure_timestamps)
            if self.config.failure_window_seconds is not None
            else self._consecutive_failures
        )
        return CircuitBreakerStats(
            name=self.name,
            state=self._state,
            consecutive_failures=self._consecutive_failures,
            recent_failures=recent,
            open_count=self._open_count,
            half_open_in_flight=self._half_open_in_flight,
            half_open_successes=self._half_open_successes,
            total_calls=self._total_calls,
            total_successes=self._total_successes,
            total_failures=self._total_failures,
            total_rejected=self._total_rejected,
            current_cooldown_seconds=self._current_cooldown,
            retry_after_seconds=retry_after,
            last_failure_error=self._last_failure_error,
        )

    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED and clear failure memory."""
        self._state = CircuitState.CLOSED
        self._failure_timestamps.clear()
        self._consecutive_failures = 0
        self._open_count = 0
        self._next_attempt_at = None
        self._current_cooldown = 0.0
        self._half_open_in_flight = 0
        self._half_open_successes = 0
        self._last_failure_error = None

    async def acquire(self) -> bool:
        """Check whether a call may proceed. Returns True if acquired as a HALF_OPEN probe."""
        async with self._get_lock():
            now = self._clock()
            self._refresh_state(now)

            if self._state is CircuitState.OPEN:
                self._total_rejected += 1
                retry_after = max(0.0, (self._next_attempt_at or now) - now)
                raise CircuitBreakerOpenError(
                    message=f"Circuit breaker '{self.name}' is OPEN; retry after {retry_after:.2f}s",
                    provider=self.name,
                    retry_after=retry_after,
                    breaker_name=self.name,
                )

            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight >= self.config.half_open_max_calls:
                    self._total_rejected += 1
                    raise CircuitBreakerOpenError(
                        message=f"Circuit breaker '{self.name}' is HALF_OPEN and probe slots are full",
                        provider=self.name,
                        retry_after=0.0,
                        breaker_name=self.name,
                    )
                self._half_open_in_flight += 1
                self._total_calls += 1
                return True

            self._total_calls += 1
            return False

    async def release_probe(self) -> None:
        """Release an in-flight HALF_OPEN probe slot on cancellation or non-failure abort."""
        async with self._get_lock():
            if self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1

    async def record_success(self, is_probe: bool = False) -> None:
        """Record a successful call and close the circuit if recovery criteria are met."""
        async with self._get_lock():
            self._total_successes += 1
            if is_probe and self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1

            if self._state is CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._failure_timestamps.clear()
                    self._open_count = 0
                    self._next_attempt_at = None
                    self._current_cooldown = 0.0
                    self._half_open_in_flight = 0
                    self._half_open_successes = 0
                    logger.info("CircuitBreaker '%s' recovered HALF_OPEN -> CLOSED.", self.name)
            elif self._state is CircuitState.CLOSED:
                self._consecutive_failures = 0

    async def record_failure(self, exc: Exception, is_probe: bool = False) -> None:
        """Record a call exception, tripping OPEN when threshold is exceeded."""
        async with self._get_lock():
            if is_probe and self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1

            if not self._is_counted_failure(exc):
                return

            now = self._clock()
            self._total_failures += 1
            self._consecutive_failures += 1
            self._last_failure_error = f"{type(exc).__name__}: {exc}"

            if self._state is CircuitState.HALF_OPEN:
                self._trip_open(now)
                return

            if self._state is CircuitState.CLOSED:
                if self.config.failure_window_seconds is not None:
                    self._failure_timestamps.append(now)
                    self._prune_failure_window(now)
                    failure_count = len(self._failure_timestamps)
                else:
                    failure_count = self._consecutive_failures

                if failure_count >= self.config.failure_threshold:
                    self._trip_open(now)

    async def call(
        self,
        fn: Callable[..., Awaitable[R]],
        *args: Any,
        **kwargs: Any,
    ) -> R:
        """Execute an async callable protected by this circuit breaker."""
        is_probe = await self.acquire()
        completed = False
        try:
            result = await fn(*args, **kwargs)
            await self.record_success(is_probe=is_probe)
            completed = True
            return result
        except Exception as exc:
            await self.record_failure(exc, is_probe=is_probe)
            completed = True
            raise
        finally:
            if is_probe and not completed:
                await self.release_probe()

    execute = call


class ResilientLLMClient(BaseLLMClient):
    """Wrapper protecting any BaseLLMClient with a stateful CircuitBreaker."""

    def __init__(
        self,
        client: BaseLLMClient,
        breaker: Optional[CircuitBreaker] = None,
        config: Optional[CircuitBreakerConfig] = None,
        name: Optional[str] = None,
    ) -> None:
        super().__init__(settings=client.settings)
        self.underlying_client = client
        provider_label = name or getattr(client, "provider_name", client.__class__.__name__)
        self.breaker = breaker or CircuitBreaker(name=provider_label, config=config)

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Alias for breaker attribute."""
        return self.breaker

    async def aclose(self) -> None:
        """Close the wrapped client's HTTP resources."""
        await self.underlying_client.aclose()

    async def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        return await self.breaker.call(
            self.underlying_client.complete,
            messages=messages,
            model=model,
            temperature=temperature,
            **kwargs,
        )

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        return await self.breaker.call(
            self.underlying_client.structured_complete,
            messages=messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
            **kwargs,
        )

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ):
        is_probe = await self.breaker.acquire()
        finished = False
        try:
            async for chunk in self.underlying_client.complete_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                **kwargs,
            ):
                yield chunk
            await self.breaker.record_success(is_probe=is_probe)
            finished = True
        except Exception as exc:
            await self.breaker.record_failure(exc, is_probe=is_probe)
            finished = True
            raise
        finally:
            if is_probe and not finished:
                await self.breaker.release_probe()
