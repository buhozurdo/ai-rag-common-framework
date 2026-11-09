"""Comprehensive tests for Observability, Telemetry, and Cost Accounting (EPIC-05 / FEAT-07 / FEAT-08 / TASK-17)."""

import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common_framework.core.llm import TokenUsage
from common_framework.observability.cost import (
    ModelPricing,
    TokenCostTracker,
)
from common_framework.observability.metrics import (
    MetricFamily,
    PrometheusRegistry,
    create_metrics_router,
)
from common_framework.observability.tracing import (
    InMemorySpanExporter,
    TraceContext,
    get_current_trace_context,
    trace_span,
)


# ---------------------------------------------------------------------------
# Token Cost Attribution Tests (FEAT-07)
# ---------------------------------------------------------------------------


def test_token_cost_calculation():
    tracker = TokenCostTracker()

    # 1,000,000 prompt tokens + 1,000,000 completion tokens on gpt-4o
    # Pricing: $2.50 input / $10.00 output
    usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    record = tracker.calculate_cost("gpt-4o", usage)

    assert record.input_cost_usd == 2.50
    assert record.output_cost_usd == 10.00
    assert record.total_cost_usd == 12.50

    # Test gpt-4o-mini ($0.15 input / $0.60 output per 1M)
    usage_mini = TokenUsage(prompt_tokens=100_000, completion_tokens=50_000)
    record_mini = tracker.calculate_cost("gpt-4o-mini", usage_mini)
    assert record_mini.input_cost_usd == 0.015
    assert record_mini.output_cost_usd == 0.030
    assert record_mini.total_cost_usd == 0.045

    # Test zero-cost model (ollama / mock)
    record_ollama = tracker.calculate_cost("ollama/llama3", TokenUsage(prompt_tokens=5000, completion_tokens=2000))
    assert record_ollama.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_token_cost_tracker_summary_and_aggregation():
    tracker = TokenCostTracker()

    await tracker.record_usage(
        "gpt-4o",
        TokenUsage(prompt_tokens=10_000, completion_tokens=5_000),
        metadata={"user_id": "usr-1", "session": "s1"},
    )
    await tracker.record_usage(
        "gpt-4o",
        TokenUsage(prompt_tokens=20_000, completion_tokens=10_000),
        metadata={"user_id": "usr-1", "session": "s2"},
    )
    await tracker.record_usage(
        "claude-3-5-sonnet",
        TokenUsage(prompt_tokens=50_000, completion_tokens=10_000),
        metadata={"user_id": "usr-2", "session": "s3"},
    )

    # Global summary
    summary = await tracker.get_summary()
    assert summary["total_invocations"] == 3
    assert summary["total_prompt_tokens"] == 80_000
    assert summary["total_completion_tokens"] == 25_000
    assert summary["total_cost_usd"] > 0
    assert "gpt-4o" in summary["by_model"]
    assert "claude-3-5-sonnet" in summary["by_model"]

    # Filtered by user tag
    user1_summary = await tracker.get_summary(tag="user_id", tag_value="usr-1")
    assert user1_summary["total_invocations"] == 2
    assert user1_summary["total_prompt_tokens"] == 30_000


def test_custom_pricing_registration():
    tracker = TokenCostTracker()
    tracker.register_pricing(
        "custom-model",
        ModelPricing(input_cost_per_million=5.0, output_cost_per_million=20.0),
    )
    record = tracker.calculate_cost("custom-model", TokenUsage(prompt_tokens=200_000, completion_tokens=100_000))
    assert record.input_cost_usd == 1.0
    assert record.output_cost_usd == 2.0
    assert record.total_cost_usd == 3.0


# ---------------------------------------------------------------------------
# Prometheus Metrics Tests (FEAT-08)
# ---------------------------------------------------------------------------


def test_prometheus_registry_and_rendering():
    registry = PrometheusRegistry()
    family = registry.register("test_metric_counter", "A test counter", "counter")

    family.inc(1.0, labels={"env": "prod", "handler": "chat"})
    family.inc(2.0, labels={"env": "prod", "handler": "chat"})
    family.inc(5.0, labels={"env": "dev", "handler": "chat"})

    rendered = registry.generate_exposition_text()
    assert "# HELP test_metric_counter A test counter" in rendered
    assert "# TYPE test_metric_counter counter" in rendered
    assert 'test_metric_counter{env="prod",handler="chat"} 3.0' in rendered
    assert 'test_metric_counter{env="dev",handler="chat"} 5.0' in rendered


def test_fastapi_metrics_endpoint():
    registry = PrometheusRegistry()
    registry.get("rag_llm_requests_total").inc(12.0, labels={"model": "gpt-4o", "provider": "openai", "status": "ok"})

    app = FastAPI()
    router = create_metrics_router(registry)
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "rag_llm_requests_total" in response.text
    assert "12.0" in response.text


# ---------------------------------------------------------------------------
# Tracing & Distributed Context Propagation Tests (FEAT-08 / TASK-17)
# ---------------------------------------------------------------------------


def test_w3c_trace_context_parsing_and_injection():
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    span_id = "00f067aa0ba902b7"
    raw_header = f"00-{trace_id}-{span_id}-01"

    ctx = TraceContext.from_traceparent(raw_header)
    assert ctx is not None
    assert ctx.trace_id == trace_id
    assert ctx.span_id == span_id
    assert ctx.sampled is True
    assert ctx.to_traceparent() == raw_header

    # Inbound header extraction
    headers = {"Authorization": "Bearer test", "Traceparent": raw_header}
    extracted = TraceContext.extract(headers)
    assert extracted is not None
    assert extracted.trace_id == trace_id

    # Outbound header injection
    injected = extracted.inject({"Content-Type": "application/json"})
    assert "traceparent" in injected
    assert injected["traceparent"] == raw_header


@pytest.mark.asyncio
async def test_trace_span_nested_context_propagation():
    exporter = InMemorySpanExporter()

    async with trace_span("parent_operation", attributes={"source": "test"}, exporter=exporter) as parent_span:
        assert parent_span.name == "parent_operation"
        parent_ctx = get_current_trace_context()
        assert parent_ctx is not None
        assert parent_ctx.span_id == parent_span.span_id

        # Nested child span
        async with trace_span("child_vector_search", attributes={"top_k": 5}, exporter=exporter) as child_span:
            child_ctx = get_current_trace_context()
            assert child_ctx is not None
            # Child must inherit parent trace_id
            assert child_ctx.trace_id == parent_ctx.trace_id
            # Child's parent_span_id must equal parent's span_id
            assert child_span.parent_span_id == parent_span.span_id

    # Verify exporter captured both spans
    assert len(exporter.spans) == 2
    child_exported = exporter.spans[0]
    parent_exported = exporter.spans[1]

    assert child_exported.name == "child_vector_search"
    assert child_exported.duration_ms is not None
    assert child_exported.status == "OK"

    assert parent_exported.name == "parent_operation"
    assert parent_exported.status == "OK"
    assert parent_exported.duration_ms >= child_exported.duration_ms


def test_trace_span_error_capture():
    exporter = InMemorySpanExporter()

    with pytest.raises(ValueError, match="Boom"):
        with trace_span("failing_operation", exporter=exporter):
            raise ValueError("Boom")

    assert len(exporter.spans) == 1
    failed_span = exporter.spans[0]
    assert failed_span.name == "failing_operation"
    assert failed_span.status == "ERROR"
    assert failed_span.error_message == "Boom"
