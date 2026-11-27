"""Unit and integration tests for stateful CircuitBreaker and LLM resilience (FEAT-45)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

import common_framework
from common_framework.core import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    ResilientLLMClient,
)
from common_framework.core.config import Settings
from common_framework.core.exceptions import (
    AllProvidersFailedError,
    ConfigurationError,
    LLMProviderError,
    RateLimitError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    FallbackLLMClient,
    LLMFactory,
    Role,
    TokenUsage,
)


class FakeClock:
    """Deterministic monotonic clock for testing cooldown and failure windows."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class AnswerSchema(BaseModel):
    answer: str


class ScriptedLLMClient(BaseLLMClient):
    """Test LLM client driven by a scripted sequence of responses or exceptions."""

    def __init__(
        self,
        outcomes: list[Any] | None = None,
        provider_name: str = "scripted",
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__()
        self.outcomes = list(outcomes or [])
        self.provider_name = provider_name
        self.delay_seconds = delay_seconds
        self.call_count = 0

    def _next_outcome(self) -> Any:
        self.call_count += 1
        if self.outcomes:
            return self.outcomes.pop(0)
        return f"ok-{self.call_count}"

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        item = self._next_outcome()
        if isinstance(item, Exception):
            raise item
        return ChatResponse(
            content=str(item),
            model=model or "scripted-model",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
        )

    async def structured_complete(
        self,
        messages: list[ChatMessage],
        response_model: type[Any],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        item = self._next_outcome()
        if isinstance(item, Exception):
            raise item
        return response_model(answer=str(item))

    async def complete_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ):
        item = self._next_outcome()
        if isinstance(item, Exception):
            raise item
        if item == "MID_STREAM_FAIL":
            yield ChatStreamChunk(delta="partial", model=model or "scripted-model")
            raise LLMProviderError("mid-stream disconnect", status_code=502, provider=self.provider_name)
        yield ChatStreamChunk(delta=str(item), model=model or "scripted-model", finish_reason="stop")


class TestCircuitBreakerStateMachine:
    @pytest.mark.asyncio
    async def test_closed_to_open_and_fast_fail(self):
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="openai",
            config=CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=10.0),
            clock=clock,
        )
        calls = 0

        async def failing_op() -> str:
            nonlocal calls
            calls += 1
            raise LLMProviderError("503 Unavailable", status_code=503, provider="openai")

        for _ in range(3):
            with pytest.raises(LLMProviderError):
                await breaker.call(failing_op)

        assert calls == 3
        assert breaker.state is CircuitState.OPEN

        # Next call must fail fast without invoking failing_op
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await breaker.call(failing_op)
        assert calls == 3
        assert exc_info.value.status_code == 503
        assert exc_info.value.retry_after == pytest.approx(10.0)
        assert breaker.stats().total_rejected == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_and_recovery_to_closed(self):
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="gemini",
            config=CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout_seconds=5.0,
                backoff_multiplier=2.0,
                max_recovery_timeout_seconds=15.0,
                success_threshold=2,
            ),
            clock=clock,
        )

        async def fail() -> None:
            raise RateLimitError("429", provider="gemini")

        async def succeed() -> str:
            return "healthy"

        # Trip #1 -> cooldown = 5s
        for _ in range(2):
            with pytest.raises(RateLimitError):
                await breaker.call(fail)
        assert breaker.stats().current_cooldown_seconds == pytest.approx(5.0)
        assert breaker.stats().open_count == 1

        # Advance 5s -> HALF_OPEN, probe fails -> Trip #2 -> cooldown = 10s
        clock.advance(5.0)
        assert breaker.state is CircuitState.HALF_OPEN
        with pytest.raises(RateLimitError):
            await breaker.call(fail)
        assert breaker.state is CircuitState.OPEN
        assert breaker.stats().open_count == 2
        assert breaker.stats().current_cooldown_seconds == pytest.approx(10.0)

        # Advance 10s -> HALF_OPEN, probe fails -> Trip #3 -> capped at 15s (not 20s)
        clock.advance(10.0)
        with pytest.raises(RateLimitError):
            await breaker.call(fail)
        assert breaker.stats().open_count == 3
        assert breaker.stats().current_cooldown_seconds == pytest.approx(15.0)

        # Still open before 15s
        clock.advance(14.0)
        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call(succeed)

        # Advance remaining 1s -> HALF_OPEN, requires 2 consecutive successes to CLOSE
        clock.advance(1.0)
        assert await breaker.call(succeed) == "healthy"
        assert breaker.state is CircuitState.HALF_OPEN
        assert await breaker.call(succeed) == "healthy"
        assert breaker.state is CircuitState.CLOSED
        assert breaker.stats().open_count == 0
        assert breaker.stats().consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failure_window_memory_expires_old_failures(self):
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="windowed",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                failure_window_seconds=10.0,
                recovery_timeout_seconds=5.0,
            ),
            clock=clock,
        )

        async def fail() -> None:
            raise httpx.ConnectTimeout("timeout")

        # 2 failures at t=100, t=102
        with pytest.raises(httpx.ConnectTimeout):
            await breaker.call(fail)
        clock.advance(2.0)
        with pytest.raises(httpx.ConnectTimeout):
            await breaker.call(fail)
        assert breaker.state is CircuitState.CLOSED
        assert breaker.stats().recent_failures == 2

        # Advance past 10s window -> earlier failures expire
        clock.advance(11.0)
        with pytest.raises(httpx.ConnectTimeout):
            await breaker.call(fail)
        assert breaker.state is CircuitState.CLOSED
        assert breaker.stats().recent_failures == 1

        # Two more rapid failures within window -> trips OPEN
        clock.advance(1.0)
        with pytest.raises(httpx.ConnectTimeout):
            await breaker.call(fail)
        clock.advance(1.0)
        with pytest.raises(httpx.ConnectTimeout):
            await breaker.call(fail)
        assert breaker.state is CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_non_provider_and_excluded_exceptions_do_not_trip(self):
        breaker = CircuitBreaker(
            name="filter-check",
            config=CircuitBreakerConfig(failure_threshold=2),
        )

        async def bad_validation() -> None:
            raise StructuredOutputError("Invalid JSON schema")

        async def value_err() -> None:
            raise ValueError("Caller bug")

        for _ in range(5):
            with pytest.raises(StructuredOutputError):
                await breaker.call(bad_validation)
            with pytest.raises(ValueError):
                await breaker.call(value_err)

        assert breaker.state is CircuitState.CLOSED
        assert breaker.stats().total_failures == 0

    @pytest.mark.asyncio
    async def test_half_open_probe_concurrency_limit_and_reset(self):
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="probe-limit",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=5.0,
                half_open_max_calls=1,
            ),
            clock=clock,
        )

        async def fail() -> None:
            raise LLMProviderError("down", status_code=500)

        with pytest.raises(LLMProviderError):
            await breaker.call(fail)
        assert breaker.state is CircuitState.OPEN

        clock.advance(5.0)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_probe() -> str:
            entered.set()
            await release.wait()
            return "recovered"

        probe_task = asyncio.create_task(breaker.call(slow_probe))
        await entered.wait()

        # Second concurrent call in HALF_OPEN must be rejected while probe is in flight
        with pytest.raises(CircuitBreakerOpenError, match="probe slots are full"):
            await breaker.call(slow_probe)

        release.set()
        assert await probe_task == "recovered"
        assert breaker.state is CircuitState.CLOSED

        # Verify manual reset clears failure state
        with pytest.raises(LLMProviderError):
            await breaker.call(fail)
        assert breaker.state is CircuitState.OPEN
        breaker.reset()
        assert breaker.state is CircuitState.CLOSED
        assert breaker.stats().open_count == 0


class TestResilientLLMAndFallbackIntegration:
    @pytest.mark.asyncio
    async def test_resilient_llm_client_protects_complete_structured_and_stream(self):
        clock = FakeClock()
        client = ScriptedLLMClient(
            outcomes=[
                LLMProviderError("err1", status_code=502),
                "MID_STREAM_FAIL",
                "structured-ok",
                "stream-ok",
            ],
            provider_name="anthropic",
        )
        breaker = CircuitBreaker(
            name="anthropic",
            config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=10.0),
            clock=clock,
        )
        resilient = ResilientLLMClient(client=client, breaker=breaker)
        msgs = [ChatMessage(role=Role.USER, content="hi")]

        with pytest.raises(LLMProviderError):
            await resilient.complete(msgs)
        assert breaker.state is CircuitState.CLOSED

        with pytest.raises(LLMProviderError):
            async for _chunk in resilient.complete_stream(msgs):
                pass
        assert breaker.state is CircuitState.OPEN

        # Fast-fail on structured_complete while OPEN
        with pytest.raises(CircuitBreakerOpenError):
            await resilient.structured_complete(msgs, AnswerSchema)
        assert client.call_count == 2

        # Advance cooldown and verify recovery via structured_complete and streaming
        clock.advance(10.0)
        parsed = await resilient.structured_complete(msgs, AnswerSchema)
        assert parsed.answer == "structured-ok"
        assert breaker.state is CircuitState.CLOSED

        chunks = [c.delta async for c in resilient.complete_stream(msgs)]
        assert "".join(chunks) == "stream-ok"

    @pytest.mark.asyncio
    async def test_fallback_llm_client_skips_open_circuit_candidate(self):
        clock = FakeClock()
        primary = ScriptedLLMClient(
            outcomes=[
                LLMProviderError("p-down-1", status_code=503, provider="primary"),
                LLMProviderError("p-down-2", status_code=503, provider="primary"),
                "primary-recovered",
            ],
            provider_name="primary",
        )
        secondary = ScriptedLLMClient(
            outcomes=["sec-1", "sec-2", "sec-3"],
            provider_name="secondary",
        )

        cb_primary = CircuitBreaker(
            name="primary",
            config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=15.0),
            clock=clock,
        )
        cb_secondary = CircuitBreaker(
            name="secondary",
            config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=15.0),
            clock=clock,
        )

        fallback = FallbackLLMClient(
            clients=[primary, secondary],
            circuit_breakers=[cb_primary, cb_secondary],
        )
        msgs = [ChatMessage(role=Role.USER, content="hello")]

        # Calls 1 and 2 fail on primary -> trips primary OPEN, failover to secondary
        r1 = await fallback.complete(msgs)
        r2 = await fallback.complete(msgs)
        assert r1.content == "sec-1"
        assert r2.content == "sec-2"
        assert primary.call_count == 2
        assert cb_primary.state is CircuitState.OPEN

        # Call 3 skips primary completely (primary.call_count stays 2!) and hits secondary
        r3 = await fallback.complete(msgs)
        assert r3.content == "sec-3"
        assert primary.call_count == 2
        assert r3.fallback_attempts[0]["exception_type"] == "CircuitBreakerOpenError"

        # Advance cooldown -> primary probes and recovers
        clock.advance(15.0)
        r4 = await fallback.complete(msgs)
        assert r4.content == "primary-recovered"
        assert primary.call_count == 3
        assert cb_primary.state is CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_fallback_mismatched_circuit_breakers_raises_configuration_error(self):
        c1 = ScriptedLLMClient(provider_name="p1")
        with pytest.raises(ConfigurationError, match="length must match"):
            FallbackLLMClient(clients=[c1], circuit_breakers=[])

    def test_factory_and_top_level_exports(self):
        cfg = Settings(openai_api_key="sk-test")
        client = LLMFactory.create(
            provider="openai",
            settings=cfg,
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3),
        )
        assert isinstance(client, ResilientLLMClient)
        assert client.breaker.config.failure_threshold == 3

        fb = LLMFactory.create_with_fallbacks(
            providers=["openai", "ollama"],
            settings=cfg,
            enable_circuit_breaker=True,
        )
        stats = fb.circuit_breaker_stats()
        assert len(stats) == 2
        assert stats[0].state is CircuitState.CLOSED

        assert common_framework.CircuitBreaker is CircuitBreaker
        assert common_framework.CircuitBreakerOpenError is CircuitBreakerOpenError
        assert common_framework.ResilientLLMClient is ResilientLLMClient

    @pytest.mark.asyncio
    async def test_fallback_all_open_raises_all_providers_failed(self):
        c1 = ScriptedLLMClient(outcomes=[LLMProviderError("down", status_code=503)], provider_name="p1")
        fb = FallbackLLMClient(
            clients=[c1],
            circuit_breaker_config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=30.0),
        )
        msgs = [ChatMessage(role=Role.USER, content="hi")]

        with pytest.raises(AllProvidersFailedError):
            await fb.complete(msgs)
        # Second call fails fast on open circuit
        with pytest.raises(AllProvidersFailedError) as exc_info:
            await fb.complete(msgs)
        assert exc_info.value.errors[0]["exception_type"] == "CircuitBreakerOpenError"
