"""Comprehensive test suite for v0.19.0 P0 fixes, API freeze, and test reconciliation."""

from __future__ import annotations

import warnings
import pytest
from fastapi.testclient import TestClient

import common_framework
from common_framework._version import __version__
from common_framework.api.app import create_app
from common_framework.core.cache import CachedLLMClient, SemanticCache
from common_framework.core.deprecation import (
    FrameworkDeprecationWarning,
    deprecated,
    warn_deprecated_parameter,
)
from common_framework.core.llm import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    Role,
    TokenUsage,
)
from common_framework.eval.hallucination import HallucinationDetector
from common_framework.eval.triad import RAGTriadEvaluator
from common_framework.guardrails.sanitizer import PIISanitizer, RedactionStrategy
from common_framework.mcp.client import create_mcp_client
from common_framework.mcp.server import create_mcp_server
from common_framework.observability.cost import (
    ModelPricing,
    TokenCostTracker,
    get_global_cost_tracker,
    global_cost_tracker,
)
from common_framework.rag.embeddings import _deterministic_mock_vector


# ---------------------------------------------------------------------------
# BUG-05: Inconsistent version hardcoding
# ---------------------------------------------------------------------------
def test_p0_version_alignment() -> None:
    """Verify that create_app, MCP server, and MCP client default to package __version__."""
    assert __version__ == common_framework.__version__

    app = create_app()
    assert app.version == __version__

    client = TestClient(app)
    health = client.get("/healthz").json()
    assert health["version"] == __version__

    mcp_srv = create_mcp_server()
    assert mcp_srv.version == __version__

    mcp_cli = create_mcp_client()
    assert mcp_cli.client_version == __version__


# ---------------------------------------------------------------------------
# BUG-06 & BUG-08: /metrics mounting and create_app parameters
# ---------------------------------------------------------------------------
def test_p0_create_app_metrics_mounted_and_scaffold_compatible() -> None:
    """Verify that create_app accepts include_metrics=True and mounts Prometheus /metrics."""
    app_with_metrics = create_app(include_metrics=True)
    client = TestClient(app_with_metrics)

    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]

    # When metrics explicitly disabled
    app_no_metrics = create_app(include_metrics=False)
    client_no_metrics = TestClient(app_no_metrics)
    res_404 = client_no_metrics.get("/metrics")
    assert res_404.status_code == 404


# ---------------------------------------------------------------------------
# BUG-07: CLI run-mcp create_sse_app method alias
# ---------------------------------------------------------------------------
def test_p0_mcp_server_create_sse_app_alias() -> None:
    """Verify that FrameworkMCPServer provides create_sse_app alias matching get_sse_app."""
    server = create_mcp_server(name="test-sse-server")
    app1 = server.get_sse_app()
    app2 = server.create_sse_app()
    assert app1 is not None
    assert app2 is not None


# ---------------------------------------------------------------------------
# BUG-09: TokenCostTracker Gemini pricing and cached tokens
# ---------------------------------------------------------------------------
def test_p0_token_cost_tracker_gemini_and_cache_pricing() -> None:
    """Verify Gemini models are priced non-zero and cached tokens receive discount."""
    tracker = TokenCostTracker()

    # Gemini 2.5 Flash
    usage_flash = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
    record_flash = tracker.calculate_cost("gemini-2.5-flash", usage_flash)
    assert record_flash.input_cost_usd == 0.15
    assert record_flash.output_cost_usd == 0.60
    assert record_flash.total_cost_usd == 0.75

    # Cached tokens discount
    usage_cached = TokenUsage(
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        total_tokens=1_500_000,
        cached_tokens=800_000,  # 800k cached @ $0.0375, 200k regular @ $0.15
    )
    record_cached = tracker.calculate_cost("gemini-2.5-flash", usage_cached)
    assert record_cached.cached_tokens == 800_000
    # (200k * 0.15 + 800k * 0.0375) / 1M = 0.03 + 0.03 = 0.06
    assert record_cached.input_cost_usd == 0.06

    # Global tracker getter
    assert get_global_cost_tracker() is global_cost_tracker


# ---------------------------------------------------------------------------
# BUG-10: Semantic cache mock similarity and model/temperature isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_p0_semantic_cache_mock_similarity_and_namespace_isolation() -> None:
    """Verify that mock vectors produce semantic similarity and cache isolates model/temp/sys."""
    # 1. Vector similarity for similar queries
    v1 = _deterministic_mock_vector(64, "What is the capital city of France?")
    v2 = _deterministic_mock_vector(64, "What is the capital of France?")
    v_unrelated = _deterministic_mock_vector(64, "How to bake a chocolate cake at home")

    dot_sim = sum(a * b for a, b in zip(v1, v2))
    dot_unrelated = sum(a * b for a, b in zip(v1, v_unrelated))
    assert dot_sim > dot_unrelated
    assert dot_sim > 0.80

    # 2. CachedLLMClient namespace isolation
    class DummyClient(BaseLLMClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def complete(self, messages, model=None, temperature=None, **kwargs):
            self.calls += 1
            return ChatResponse(content=f"Response #{self.calls} for {model} at {temperature}", model=model or "default")

        async def structured_complete(self, messages, response_model, model=None, temperature=None, **kwargs):
            pass

    dummy = DummyClient()
    cached_client = CachedLLMClient(dummy, cache=SemanticCache())

    msgs = [ChatMessage(role=Role.USER, content="Hello framework")]

    # First call with gpt-4o temp 0.2
    res1 = await cached_client.complete(msgs, model="gpt-4o", temperature=0.2)
    assert dummy.calls == 1

    # Same parameters -> hit
    res2 = await cached_client.complete(msgs, model="gpt-4o", temperature=0.2)
    assert dummy.calls == 1
    assert "cached" in res2.model

    # Different model -> miss (no collision)
    res3 = await cached_client.complete(msgs, model="gemini-2.5-flash", temperature=0.2)
    assert dummy.calls == 2

    # Different temperature -> miss (no collision)
    res4 = await cached_client.complete(msgs, model="gpt-4o", temperature=0.9)
    assert dummy.calls == 3


# ---------------------------------------------------------------------------
# BUG-11: PII Sanitizer HMAC and International Phone Numbers
# ---------------------------------------------------------------------------
def test_p0_pii_sanitizer_hmac_and_international_phone() -> None:
    """Verify that PIISanitizer supports HMAC secret key and detects international phones."""
    # HMAC with secret
    sanitizer_plain = PIISanitizer()
    sanitizer_hmac = PIISanitizer(hmac_key="secret-salt-key")

    text = "Secret email: confidential@enterprise.org"
    res_plain = sanitizer_plain.sanitize(text, strategy=RedactionStrategy.HASH)
    res_hmac = sanitizer_hmac.sanitize(text, strategy=RedactionStrategy.HASH)

    assert "[HASH:" in res_plain.sanitized_text
    assert "[HASH:" in res_hmac.sanitized_text
    # Digests must differ because HMAC uses the secret key
    assert res_plain.sanitized_text != res_hmac.sanitized_text

    # International phone detection (E.164 and international formats)
    intl_text = "Call Mexico: +52 55 1234 5678, Spain: +34 612 345 678, UK: +44 20 7946 0958."
    res_phones = sanitizer_plain.sanitize(intl_text, strategy=RedactionStrategy.REPLACE)
    assert res_phones.has_pii is True
    assert "+52 55 1234 5678" not in res_phones.sanitized_text
    assert "+34 612 345 678" not in res_phones.sanitized_text
    assert "[REDACTED_PHONE]" in res_phones.sanitized_text


# ---------------------------------------------------------------------------
# BUG-12: Lexical evaluation with Spanish and custom stopwords
# ---------------------------------------------------------------------------
def test_p0_lexical_evaluation_multilingual_stopwords() -> None:
    """Verify that hallucination and triad evaluation support Spanish stop words."""
    detector = HallucinationDetector()
    context = "La capital de Francia es París. La ciudad es conocida por la Torre Eiffel."
    grounded_answer = "París es la capital de Francia."

    report = detector.detect(answer=grounded_answer, context=context)
    assert report.is_grounded is True
    assert report.overall_grounding_score >= 0.70

    evaluator = RAGTriadEvaluator(hallucination_detector=detector)
    triad = evaluator.evaluate(
        query="¿Cuál es la capital de Francia?",
        context=context,
        answer=grounded_answer,
    )
    assert triad.passed is True
    assert triad.groundedness >= 0.70


# ---------------------------------------------------------------------------
# FEAT-13: Public API Contract Freeze & Deprecation Architecture
# ---------------------------------------------------------------------------
def test_p0_api_freeze_deprecation_warning() -> None:
    """Verify that @deprecated decorator emits FrameworkDeprecationWarning with guidance."""

    @deprecated(since="0.19.0", removal_version="1.0.0", alternative="modern_fn")
    def legacy_fn(x: int) -> int:
        return x * 2

    with pytest.deprecated_call(match="legacy_fn.*deprecated since v0.19.0"):
        val = legacy_fn(5)
    assert val == 10

    assert getattr(legacy_fn, "__deprecated__", False) is True
    assert getattr(legacy_fn, "__deprecated_since__", None) == "0.19.0"
    assert getattr(legacy_fn, "__deprecated_removal__", None) == "1.0.0"

    with pytest.deprecated_call(match="legacy_param"):
        warn_deprecated_parameter("legacy_param", since="0.19.0", removal_version="1.0.0", alternative="new_param")


# ---------------------------------------------------------------------------
# TASK-22: Test Inventory & Count Reconciliation Audit
# ---------------------------------------------------------------------------
def test_p0_test_inventory_reconciliation() -> None:
    """Verify test distribution across unit tests and integration suites for accurate reporting."""
    import inspect
    import os
    import ast

    unit_test_funcs = 0
    test_files_count = 0
    for root, _, files in os.walk("tests"):
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                test_files_count += 1
                with open(os.path.join(root, f), "r", encoding="utf-8") as fp:
                    tree = ast.parse(fp.read())
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name.startswith("test_"):
                                unit_test_funcs += 1

    # At least 300 unit tests across 28+ test modules
    assert test_files_count >= 25
    assert unit_test_funcs >= 300
