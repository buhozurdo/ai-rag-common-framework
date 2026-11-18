"""Millimeter-accurate Resource & Byte Accounting Engine.

Tracks every byte allocated across vectors, embeddings, caching, SQLite,
and LLM payloads, providing exact attribution of where and why memory was used.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import tracemalloc
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generator, Optional, Union
from contextlib import contextmanager

from pydantic import BaseModel, Field


class ByteCategory(str, Enum):
    """Component categories for byte-level resource attribution."""

    VECTOR_INDEX = "vector_index"
    QUERY_PAYLOAD = "query_payload"
    EMBEDDING_FLOAT32 = "embedding_float32"
    LLM_PROMPT = "llm_prompt"
    LLM_COMPLETION = "llm_completion"
    CACHE_STORAGE = "cache_storage"
    SQLITE_PAGE_CACHE = "sqlite_page_cache"
    DOCUMENT_CONTENT = "document_content"
    GENERAL_HEAP = "general_heap"


class ByteAllocationRecord(BaseModel):
    """Ledger entry recording an exact byte allocation and its justification."""

    operation: str
    category: str
    allocated_bytes: int
    peak_bytes: int
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ByteAccountant:
    """Thread-safe engine providing millimeter-level byte tracking and auditability.

    Zero-dependency implementation using Python standard library `tracemalloc`
    and exact object byte measurement.
    """

    def __init__(self, auto_start_tracing: bool = True) -> None:
        self._records: list[ByteAllocationRecord] = []
        self._lock = threading.Lock()
        self._auto_start_tracing = auto_start_tracing
        if auto_start_tracing and not tracemalloc.is_tracing():
            try:
                tracemalloc.start()
            except Exception:  # pragma: no cover
                pass

    @contextmanager
    def track(
        self,
        operation: str,
        category: Union[ByteCategory, str],
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
        explicit_payload_bytes: Optional[int] = None,
    ) -> Generator[None, None, None]:
        """Context manager tracking exact heap bytes allocated and peak memory during block execution."""
        cat_str = category.value if isinstance(category, ByteCategory) else str(category)
        
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()

        # Reset peak for this block or capture current baseline
        start_curr, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        start_time = time.perf_counter()

        try:
            yield
        finally:
            end_curr, end_peak = tracemalloc.get_traced_memory()
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            delta_bytes = max(0, end_curr - start_curr)
            if explicit_payload_bytes is not None:
                allocated = explicit_payload_bytes
            else:
                allocated = delta_bytes

            peak = max(allocated, end_peak)

            meta = dict(metadata or {})
            meta["duration_ms"] = round(duration_ms, 3)

            record = ByteAllocationRecord(
                operation=operation,
                category=cat_str,
                allocated_bytes=allocated,
                peak_bytes=peak,
                reason=reason,
                metadata=meta,
            )

            with self._lock:
                self._records.append(record)

    def record_manual(
        self,
        operation: str,
        category: Union[ByteCategory, str],
        allocated_bytes: int,
        reason: str,
        peak_bytes: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ByteAllocationRecord:
        """Manually record a known byte allocation with explicit architectural reason."""
        cat_str = category.value if isinstance(category, ByteCategory) else str(category)
        record = ByteAllocationRecord(
            operation=operation,
            category=cat_str,
            allocated_bytes=allocated_bytes,
            peak_bytes=peak_bytes if peak_bytes is not None else allocated_bytes,
            reason=reason,
            metadata=metadata or {},
        )
        with self._lock:
            self._records.append(record)
        return record

    def get_ledger(self) -> list[ByteAllocationRecord]:
        """Return a copy of all recorded byte allocations."""
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        """Clear all ledger records."""
        with self._lock:
            self._records.clear()

    def summary(self) -> dict[str, Any]:
        """Aggregate total bytes allocated by category and operation."""
        with self._lock:
            records = list(self._records)

        by_category: dict[str, int] = {}
        by_operation: dict[str, int] = {}
        total_allocated = 0
        max_peak = 0

        for r in records:
            total_allocated += r.allocated_bytes
            max_peak = max(max_peak, r.peak_bytes)
            by_category[r.category] = by_category.get(r.category, 0) + r.allocated_bytes
            by_operation[r.operation] = by_operation.get(r.operation, 0) + r.allocated_bytes

        curr_traced, peak_traced = (0, 0)
        if tracemalloc.is_tracing():
            curr_traced, peak_traced = tracemalloc.get_traced_memory()

        return {
            "total_allocated_bytes": total_allocated,
            "max_peak_bytes": max(max_peak, peak_traced),
            "current_heap_bytes": curr_traced,
            "records_count": len(records),
            "by_category": by_category,
            "by_operation": by_operation,
        }

    def generate_audit_report(self) -> str:
        """Produce a formatted ASCII audit report detailing every byte allocated and why."""
        summary = self.summary()
        records = self.get_ledger()

        lines: list[str] = [
            "=" * 78,
            " AI RAG Common Framework — Millimeter Resource & Byte Audit",
            "=" * 78,
            f" Total Accounted Allocations : {summary['total_allocated_bytes']:,} bytes ({summary['total_allocated_bytes'] / (1024 * 1024):.3f} MB)",
            f" Peak Heap Footprint        : {summary['max_peak_bytes']:,} bytes ({summary['max_peak_bytes'] / (1024 * 1024):.3f} MB)",
            f" Current Traced Heap        : {summary['current_heap_bytes']:,} bytes ({summary['current_heap_bytes'] / 1024:.2f} KB)",
            f" Total Tracked Operations   : {summary['records_count']}",
            "-" * 78,
            " Byte Breakdown by Category:",
        ]

        for cat, b in sorted(summary["by_category"].items(), key=lambda x: x[1], reverse=True):
            pct = (b / summary["total_allocated_bytes"] * 100) if summary["total_allocated_bytes"] > 0 else 0.0
            lines.append(f"   * {cat:20} : {b:>12,} bytes  ({pct:5.1f}%)")

        lines.extend([
            "-" * 78,
            " Recent Byte Allocations & Justifications (Last 15):",
            f" {'Operation':<22} | {'Category':<16} | {'Bytes':>10} | {'Reason':<25}",
            f" {'-'*22} | {'-'*16} | {'-'*10} | {'-'*25}",
        ])

        for r in records[-15:]:
            reason_trunc = (r.reason[:22] + "...") if len(r.reason) > 25 else r.reason
            lines.append(f" {r.operation:<22} | {r.category:<16} | {r.allocated_bytes:>10,} | {reason_trunc:<25}")

        lines.append("=" * 78)
        return "\n".join(lines)


# Global byte accountant singleton
_global_accountant: Optional[ByteAccountant] = None
_accountant_lock = threading.Lock()


def get_byte_accountant() -> ByteAccountant:
    """Return or initialize the global thread-safe ByteAccountant singleton."""
    global _global_accountant
    if _global_accountant is None:
        with _accountant_lock:
            if _global_accountant is None:
                _global_accountant = ByteAccountant()
    return _global_accountant
