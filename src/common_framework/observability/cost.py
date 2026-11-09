"""Token Usage & Cost Attribution Engine (FEAT-07)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

from common_framework.core.llm import TokenUsage


class ModelPricing(BaseModel):
    """Cost configuration in USD per 1,000,000 tokens."""

    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    cached_input_cost_per_million: Optional[float] = None


class CostRecord(BaseModel):
    """Detailed attribution of a single LLM invocation cost."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Standard reference pricing tables (USD per 1M tokens)
DEFAULT_PRICING_TABLE: dict[str, ModelPricing] = {
    # OpenAI Models
    "gpt-4o": ModelPricing(input_cost_per_million=2.50, output_cost_per_million=10.00),
    "gpt-4o-mini": ModelPricing(input_cost_per_million=0.15, output_cost_per_million=0.60),
    "gpt-4-turbo": ModelPricing(input_cost_per_million=10.00, output_cost_per_million=30.00),
    "o1": ModelPricing(input_cost_per_million=15.00, output_cost_per_million=60.00),
    "o1-mini": ModelPricing(input_cost_per_million=3.00, output_cost_per_million=12.00),
    "text-embedding-3-small": ModelPricing(input_cost_per_million=0.02, output_cost_per_million=0.0),
    "text-embedding-3-large": ModelPricing(input_cost_per_million=0.13, output_cost_per_million=0.0),
    # Anthropic Models
    "claude-3-5-sonnet": ModelPricing(input_cost_per_million=3.00, output_cost_per_million=15.00),
    "claude-3-5-sonnet-20241022": ModelPricing(input_cost_per_million=3.00, output_cost_per_million=15.00),
    "claude-3-5-haiku": ModelPricing(input_cost_per_million=1.00, output_cost_per_million=5.00),
    "claude-3-5-haiku-20241022": ModelPricing(input_cost_per_million=1.00, output_cost_per_million=5.00),
    "claude-3-opus": ModelPricing(input_cost_per_million=15.00, output_cost_per_million=75.00),
    # Cohere Models
    "rerank-v3.5": ModelPricing(input_cost_per_million=2.00, output_cost_per_million=0.0),
    "embed-multilingual-v3.0": ModelPricing(input_cost_per_million=0.10, output_cost_per_million=0.0),
    # Local & Mock Models (Zero Cost)
    "mock": ModelPricing(input_cost_per_million=0.0, output_cost_per_million=0.0),
    "ollama": ModelPricing(input_cost_per_million=0.0, output_cost_per_million=0.0),
}


class TokenCostTracker:
    """Thread-safe ledger recording token consumption and computing real-time dollar costs."""

    def __init__(
        self,
        custom_pricing: Optional[dict[str, ModelPricing]] = None,
    ) -> None:
        self.pricing: dict[str, ModelPricing] = dict(DEFAULT_PRICING_TABLE)
        if custom_pricing:
            self.pricing.update(custom_pricing)

        self._records: list[CostRecord] = []
        self._lock = asyncio.Lock()

    def register_pricing(self, model: str, pricing: ModelPricing) -> None:
        """Register or override pricing for a specific model name."""
        self.pricing[model.lower().strip()] = pricing

    def get_pricing(self, model: str) -> ModelPricing:
        """Retrieve model pricing or return zero-cost fallback."""
        m = model.lower().strip()
        if m in self.pricing:
            return self.pricing[m]
        # Match prefix (e.g. gpt-4o-2024-08-06 matches gpt-4o)
        for key, p in self.pricing.items():
            if m.startswith(key):
                return p
        return ModelPricing(input_cost_per_million=0.0, output_cost_per_million=0.0)

    def calculate_cost(
        self,
        model: str,
        usage: TokenUsage,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CostRecord:
        """Compute the dollar cost of a token usage payload without storing it."""
        pricing = self.get_pricing(model)
        input_cost = (usage.prompt_tokens / 1_000_000.0) * pricing.input_cost_per_million
        output_cost = (usage.completion_tokens / 1_000_000.0) * pricing.output_cost_per_million
        total_cost = input_cost + output_cost

        return CostRecord(
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens or (usage.prompt_tokens + usage.completion_tokens),
            input_cost_usd=round(input_cost, 6),
            output_cost_usd=round(output_cost, 6),
            total_cost_usd=round(total_cost, 6),
            metadata=dict(metadata or {}),
        )

    async def record_usage(
        self,
        model: str,
        usage: TokenUsage,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CostRecord:
        """Compute dollar cost and record invocation into historical ledger."""
        record = self.calculate_cost(model, usage, metadata=metadata)
        async with self._lock:
            self._records.append(record)
        return record

    def record_usage_sync(
        self,
        model: str,
        usage: TokenUsage,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CostRecord:
        """Synchronous record wrapper."""
        record = self.calculate_cost(model, usage, metadata=metadata)
        self._records.append(record)
        return record

    async def get_summary(
        self,
        tag: Optional[str] = None,
        tag_value: Optional[str] = None,
    ) -> dict[str, Any]:
        """Aggregate token counts and dollar expenses across records."""
        async with self._lock:
            records = list(self._records)

        if tag and tag_value:
            records = [r for r in records if r.metadata.get(tag) == tag_value]

        total_prompt_tokens = sum(r.prompt_tokens for r in records)
        total_completion_tokens = sum(r.completion_tokens for r in records)
        total_tokens = sum(r.total_tokens for r in records)
        total_cost_usd = sum(r.total_cost_usd for r in records)

        by_model: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.model not in by_model:
                by_model[r.model] = {"tokens": 0, "cost_usd": 0.0, "invocations": 0}
            by_model[r.model]["tokens"] += r.total_tokens
            by_model[r.model]["cost_usd"] += r.total_cost_usd
            by_model[r.model]["invocations"] += 1

        for m_data in by_model.values():
            m_data["cost_usd"] = round(m_data["cost_usd"], 6)

        return {
            "total_invocations": len(records),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost_usd, 6),
            "by_model": by_model,
        }

    async def reset(self) -> None:
        """Clear ledger records."""
        async with self._lock:
            self._records.clear()


# Global default tracker singleton
global_cost_tracker = TokenCostTracker()
