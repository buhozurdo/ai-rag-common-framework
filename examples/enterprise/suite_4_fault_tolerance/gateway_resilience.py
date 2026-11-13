"""Resilient Gateway application exhibiting framework exception translation into HTTP RFC standards."""

from __future__ import annotations

import logging
from typing import Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel, Field

from common_framework.api import create_app
from common_framework.core.llm import BaseLLMClient, ChatMessage, LLMFactory, Role

logger = logging.getLogger("examples.gateway.resilience")


class TextGenerationRequest(BaseModel):
    """Payload requesting LLM text generation."""

    prompt: str = Field(..., min_length=1, description="Prompt text to send to LLM")
    model: Optional[str] = Field(default=None, description="Model override")


class DocumentExtractionSchema(BaseModel):
    """Target Pydantic schema for structured document extraction."""

    entity_name: str
    tax_id: str
    annual_revenue: float


def create_resilient_gateway(llm_client: Optional[BaseLLMClient] = None) -> FastAPI:
    """Build a FastAPI gateway configured with framework exception handlers."""
    app = create_app(
        title="Resilient Enterprise LLM Gateway",
        version="1.0.0",
        description="Gateway testing Python exception interception and HTTP translation.",
    )

    @app.post("/api/v1/chat/completions", summary="Generate chat completion")
    async def chat_completion(payload: TextGenerationRequest) -> dict[str, Any]:
        client = llm_client or LLMFactory.create()
        messages = [ChatMessage(role=Role.USER, content=payload.prompt)]
        resp = await client.complete(messages=messages, model=payload.model)
        return {"content": resp.content, "model": resp.model}

    @app.post("/api/v1/extract/structured", summary="Extract structured entity")
    async def extract_structured(payload: TextGenerationRequest) -> DocumentExtractionSchema:
        client = llm_client or LLMFactory.create()
        messages = [ChatMessage(role=Role.USER, content=payload.prompt)]
        return await client.structured_complete(
            messages=messages,
            response_model=DocumentExtractionSchema,
            model=payload.model,
        )

    return app
