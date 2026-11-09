"""Distributed Context Propagation and OpenTelemetry Tracing (FEAT-08 / TASK-17)."""

from __future__ import annotations

import contextvars
import os
import secrets
import time
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any, Optional
from pydantic import BaseModel, Field

# Context variable preserving active trace across async coroutines
_current_context: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar(
    "_current_trace_context", default=None
)


class TraceContext(BaseModel):
    """W3C-compliant distributed trace context container."""

    trace_id: str = Field(default_factory=lambda: secrets.token_hex(16))  # 32 hex chars
    span_id: str = Field(default_factory=lambda: secrets.token_hex(8))    # 16 hex chars
    parent_span_id: Optional[str] = None
    sampled: bool = True

    def to_traceparent(self) -> str:
        """Format as W3C traceparent header: 00-{trace_id}-{span_id}-{flags}."""
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    @classmethod
    def from_traceparent(cls, header: str) -> Optional[TraceContext]:
        """Parse W3C traceparent header string."""
        if not header or not isinstance(header, str):
            return None

        parts = header.strip().split("-")
        if len(parts) != 4 or parts[0] != "00":
            return None

        trace_id = parts[1]
        span_id = parts[2]
        flags = parts[3]

        if len(trace_id) != 32 or len(span_id) != 16:
            return None

        sampled = flags == "01"
        return cls(trace_id=trace_id, span_id=span_id, sampled=sampled)

    def inject(self, headers: dict[str, str]) -> dict[str, str]:
        """Inject trace context into an outbound HTTP headers dictionary."""
        out = dict(headers)
        out["traceparent"] = self.to_traceparent()
        return out

    @classmethod
    def extract(cls, headers: dict[str, str]) -> Optional[TraceContext]:
        """Extract trace context from incoming headers dictionary (case-insensitive)."""
        lower = {k.lower(): v for k, v in headers.items()}
        tp = lower.get("traceparent")
        if tp:
            return cls.from_traceparent(tp)
        return None


class Span(BaseModel):
    """Representation of an observed unit of work."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    status: str = "OK"  # "OK" or "ERROR"
    error_message: Optional[str] = None


class InMemorySpanExporter:
    """Ring buffer retaining recent completed spans for inspection and telemetry export."""

    def __init__(self, max_spans: int = 1000) -> None:
        self.max_spans = max_spans
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)
        if len(self.spans) > self.max_spans:
            self.spans.pop(0)

    def clear(self) -> None:
        self.spans.clear()

    def get_by_trace(self, trace_id: str) -> list[Span]:
        return [s for s in self.spans if s.trace_id == trace_id]


global_span_exporter = InMemorySpanExporter()


class trace_span(AbstractContextManager, AbstractAsyncContextManager):
    """Dual sync and async context manager recording execution spans and propagating trace context."""

    def __init__(
        self,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
        exporter: Optional[InMemorySpanExporter] = None,
    ) -> None:
        self.name = name
        self.attributes = dict(attributes or {})
        self.exporter = exporter or global_span_exporter
        self.parent_context: Optional[TraceContext] = None
        self.current_context: Optional[TraceContext] = None
        self.token: Optional[contextvars.Token] = None
        self.start_time: float = 0.0
        self.span: Optional[Span] = None

    def _enter(self) -> Span:
        self.start_time = time.perf_counter()
        self.parent_context = _current_context.get()

        if self.parent_context:
            self.current_context = TraceContext(
                trace_id=self.parent_context.trace_id,
                span_id=secrets.token_hex(8),
                parent_span_id=self.parent_context.span_id,
                sampled=self.parent_context.sampled,
            )
        else:
            self.current_context = TraceContext()

        self.token = _current_context.set(self.current_context)
        self.span = Span(
            name=self.name,
            trace_id=self.current_context.trace_id,
            span_id=self.current_context.span_id,
            parent_span_id=self.current_context.parent_span_id,
            start_time=self.start_time,
            attributes=self.attributes,
        )
        return self.span

    def _exit(self, exc_type: Any, exc_val: Any) -> None:
        end_time = time.perf_counter()
        if self.span:
            self.span.end_time = end_time
            self.span.duration_ms = round((end_time - self.start_time) * 1000.0, 3)

            if exc_type is not None:
                self.span.status = "ERROR"
                self.span.error_message = str(exc_val)
            else:
                self.span.status = "OK"

            self.exporter.export(self.span)

        if self.token is not None:
            _current_context.reset(self.token)

    def __enter__(self) -> Span:
        return self._enter()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._exit(exc_type, exc_val)

    async def __aenter__(self) -> Span:
        return self._enter()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._exit(exc_type, exc_val)


def get_current_trace_context() -> Optional[TraceContext]:
    """Return the active ambient TraceContext if inside a trace_span."""
    return _current_context.get()
