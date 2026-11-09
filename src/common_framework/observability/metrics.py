"""Prometheus Metrics Exporter and Telemetry Registry (FEAT-08)."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional
from fastapi import APIRouter, Response


class MetricFamily:
    """Represents a set of Prometheus metric samples with labels."""

    def __init__(self, name: str, docstring: str, metric_type: str = "counter") -> None:
        self.name = name
        self.docstring = docstring
        self.metric_type = metric_type
        # Maps tuple(sorted(label_items)) -> float
        self._samples: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, labels: Optional[dict[str, str]] = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._samples[key] = self._samples.get(key, 0.0) + value

    def set(self, value: float, labels: Optional[dict[str, str]] = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._samples[key] = value

    def get(self, labels: Optional[dict[str, str]] = None) -> float:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            return self._samples.get(key, 0.0)

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.docstring}",
            f"# TYPE {self.name} {self.metric_type}",
        ]
        with self._lock:
            for label_tuple, val in self._samples.items():
                if label_tuple:
                    label_str = ",".join(f'{k}="{v}"' for k, v in label_tuple)
                    lines.append(f"{self.name}{{{label_str}}} {val}")
                else:
                    lines.append(f"{self.name} {val}")
        return lines


class PrometheusRegistry:
    """Central registry maintaining Prometheus metric families."""

    def __init__(self) -> None:
        self._metrics: dict[str, MetricFamily] = {}
        self._lock = threading.Lock()
        self._init_core_metrics()

    def _init_core_metrics(self) -> None:
        # HTTP Metrics
        self.register("rag_http_requests_total", "Total incoming HTTP API requests", "counter")
        self.register("rag_http_request_duration_seconds", "Histogram of HTTP request durations", "gauge")

        # LLM Metrics
        self.register("rag_llm_requests_total", "Total LLM client invocations", "counter")
        self.register("rag_llm_tokens_total", "Total prompt and completion tokens processed", "counter")
        self.register("rag_llm_cost_usd_total", "Accumulated monetary cost of LLM tokens in USD", "counter")

        # RAG & Vectorstore Metrics
        self.register("rag_vector_searches_total", "Total vector similarity and hybrid searches", "counter")
        self.register("rag_vector_search_duration_seconds", "Latency of vector search operations", "gauge")

        # Semantic Cache Metrics
        self.register("rag_semantic_cache_hits_total", "Count of cache hits in SemanticCache", "counter")
        self.register("rag_semantic_cache_misses_total", "Count of cache misses in SemanticCache", "counter")

    def register(self, name: str, docstring: str, metric_type: str = "counter") -> MetricFamily:
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = MetricFamily(name, docstring, metric_type)
            return self._metrics[name]

    def get(self, name: str) -> Optional[MetricFamily]:
        return self._metrics.get(name)

    def generate_exposition_text(self) -> str:
        """Render all metrics in Prometheus text format (version 0.0.4)."""
        output: list[str] = []
        with self._lock:
            families = list(self._metrics.values())

        for family in families:
            rendered = family.render()
            if len(rendered) > 2:  # Only output if samples exist
                output.extend(rendered)

        return "\n".join(output) + ("\n" if output else "")

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()
            self._init_core_metrics()


# Global Prometheus registry singleton
global_prometheus_registry = PrometheusRegistry()


def create_metrics_router(registry: Optional[PrometheusRegistry] = None) -> APIRouter:
    """Create a FastAPI router exposing the GET /metrics Prometheus scraping endpoint."""
    router = APIRouter(tags=["Observability"])
    reg = registry or global_prometheus_registry

    @router.get("/metrics", response_class=Response)
    def metrics_endpoint() -> Response:
        body = reg.generate_exposition_text()
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    return router
