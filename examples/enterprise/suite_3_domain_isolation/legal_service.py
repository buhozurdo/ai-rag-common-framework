"""Domain service demonstrating structured Pydantic outputs and isolated LLM interactions."""

from __future__ import annotations

import logging
from typing import Any, Optional
from fastapi import FastAPI, status
from pydantic import BaseModel, Field

from common_framework.api import create_app
from common_framework.core.llm import BaseLLMClient, ChatMessage, LLMFactory, Role

logger = logging.getLogger("examples.domain.legal")


class LegalSummary(BaseModel):
    """Pydantic schema representing a deterministic structured legal verdict."""

    case_id: str = Field(..., description="Unique legal dossier identifier")
    verdict: str = Field(..., description="Legal outcome assessment: FAVORABLE, UNFAVORABLE, or PENDING")
    key_arguments: list[str] = Field(..., description="Core legal principles supporting the verdict")
    risk_level: str = Field(..., description="Assessed risk level: LOW, MEDIUM, or CRITICAL")
    confidence_score: float = Field(default=0.95, ge=0.0, le=1.0, description="Confidence certainty score")


class LegalAnalysisRequest(BaseModel):
    """Payload to request automated legal text summarization."""

    case_id: str = Field(..., description="Reference case identifier")
    document_text: str = Field(..., min_length=10, description="Raw text of the legal contract or lawsuit")


class LegalAnalysisService:
    """Enterprise domain service executing structured legal analysis via LLM."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None) -> None:
        # If client is not explicitly provided, lazily resolve from LLMFactory
        self._explicit_client = llm_client

    @property
    def client(self) -> BaseLLMClient:
        return self._explicit_client or LLMFactory.create()

    async def analyze_case(self, case_id: str, document_text: str) -> LegalSummary:
        """Call LLM structured completion and return the validated LegalSummary model."""
        messages = [
            ChatMessage(
                role=Role.SYSTEM,
                content="You are an expert corporate legal counsel. Analyze the document and output a strict LegalSummary JSON.",
            ),
            ChatMessage(
                role=Role.USER,
                content=f"Case ID: {case_id}\n\nDocument Text:\n{document_text}",
            ),
        ]

        summary: LegalSummary = await self.client.structured_complete(
            messages=messages,
            response_model=LegalSummary,
        )
        logger.info("Successfully produced structured LegalSummary for case %s.", case_id)
        return summary


def create_legal_gateway(service: Optional[LegalAnalysisService] = None) -> FastAPI:
    """Build FastAPI gateway exposing the domain legal analysis service."""
    active_service = service or LegalAnalysisService()

    app = create_app(
        title="Enterprise Legal RAG Gateway",
        version="1.0.0",
        description="Domain isolation example for testing RAG and LLM components.",
    )

    @app.post(
        "/api/v1/legal/analyze",
        response_model=LegalSummary,
        status_code=status.HTTP_200_OK,
        summary="Analyze legal document into structured LegalSummary",
    )
    async def analyze_document(payload: LegalAnalysisRequest) -> LegalSummary:
        return await active_service.analyze_case(
            case_id=payload.case_id,
            document_text=payload.document_text,
        )

    return app
