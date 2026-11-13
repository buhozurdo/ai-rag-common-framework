"""Suite 3: Domain Isolation (RAG & LLM Mocks).

Validates orchestrator business logic in complete isolation without incurring API token
expenses or relying on external network latencies and fluctuations.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from common_framework.core.llm import BaseLLMClient, LLMFactory
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document
from common_framework.rag.splitters import RecursiveTextSplitter
from common_framework.rag.vectorstore import InMemoryVectorStore
from suite_3_domain_isolation.legal_service import (
    LegalSummary,
    create_legal_gateway,
)


class TestRAGIngestionMock:
    """Isolated ingestion and chunking tests using a VectorStore mock."""

    @pytest.mark.asyncio
    async def test_isolated_ingest_with_vector_store_mock(self) -> None:
        """Verify RAGEngine splits documents and calls add_documents with expected metadata (parent_id, chunk_index)."""
        # 1. Create VectorStore mock
        mock_vector_store = AsyncMock(spec=InMemoryVectorStore)
        mock_vector_store.add_documents = AsyncMock(return_value=["chunk-id-1", "chunk-id-2"])

        # 2. Inject into RAGEngine with small splitter to force multiple chunks
        custom_splitter = RecursiveTextSplitter(chunk_size=120, chunk_overlap=20)
        engine = RAGEngine(
            vector_store=mock_vector_store,
            default_splitter=custom_splitter,
        )

        parent_doc_id = "legal-contract-2026-dossier"
        long_content = (
            "CLAUSE ONE: Confidentiality of information and intellectual property. "
            "All parties agree to safeguard commercial secrets subject to indemnification. "
            "CLAUSE TWO: Effective term stipulated for thirty-six continuous months. "
            "Any extension must be agreed upon in writing between designated legal representatives."
        )
        sample_doc = Document(
            id=parent_doc_id,
            content=long_content,
            metadata={"source_file": "NDA_Corp.pdf", "jurisdiction": "federal"},
        )

        # 3. Invoke ingest_documents
        ingested_ids = await engine.ingest_documents([sample_doc], collection="legal_contracts")

        # 4. Validate add_documents was called exactly once for the batch
        assert mock_vector_store.add_documents.call_count == 1, (
            "add_documents was not called or was called an unexpected number of times."
        )

        # 5. Inspect arguments passed to add_documents
        call_args, call_kwargs = mock_vector_store.add_documents.call_args
        stored_chunks: list[Document] = call_args[0]
        target_collection: str = call_kwargs.get("collection", "")

        assert target_collection == "legal_contracts"
        assert len(stored_chunks) >= 2, f"Expected multiple chunks, got {len(stored_chunks)}"

        # 6. Validate mandatory metadata on each chunk (parent_id, chunk_index)
        for idx, chunk in enumerate(stored_chunks):
            assert chunk.metadata.get("parent_id") == parent_doc_id, (
                f"Chunk #{idx} lost the parent_id of the root document."
            )
            assert chunk.metadata.get("chunk_index") == idx, (
                f"Chunk #{idx} missing expected sequential chunk_index."
            )
            assert chunk.metadata.get("source_file") == "NDA_Corp.pdf", (
                "Original user metadata was lost during chunking."
            )

        assert ingested_ids == ["chunk-id-1", "chunk-id-2"]


class TestDeterministicLLMOutput:
    """Deterministic output tests using patch on LLMFactory.create()."""

    def test_deterministic_legal_summary_llm_mock(self) -> None:
        """Verify LLM structured completion returns validated LegalSummary model matching Gateway response."""
        # 1. Create prefabricated LegalSummary object (typed Pydantic model)
        prefabricated_summary = LegalSummary(
            case_id="CORP-LITIGATION-883",
            verdict="FAVORABLE",
            key_arguments=[
                "Timely compliance with notice period per Clause 5.1",
                "Absence of verifiable willful misconduct in bond execution",
                "Applicable precedent from the Civil Appeals Tribunal",
            ],
            risk_level="LOW",
            confidence_score=0.98,
        )

        # 2. Configure BaseLLMClient mock
        mock_llm_client = AsyncMock(spec=BaseLLMClient)
        mock_llm_client.structured_complete = AsyncMock(return_value=prefabricated_summary)

        # 3. Intercept LLMFactory.create with patch
        with patch("common_framework.core.llm.LLMFactory.create", return_value=mock_llm_client) as mock_factory:
            app = create_legal_gateway()

            with TestClient(app) as client:
                payload = {
                    "case_id": "CORP-LITIGATION-883",
                    "document_text": (
                        "Contractual dispute lawsuit filed by counterparty alleging breach... "
                        "Plea filed establishing timely performance of all contractual obligations."
                    ),
                }

                # Call Gateway
                response = client.post("/api/v1/legal/analyze", json=payload)

                # HTTP validations
                assert response.status_code == 200, f"Gateway error: {response.text}"
                data = response.json()

                # Validate LLMFactory.create was invoked
                mock_factory.assert_called()

                # Validate structured_complete received LegalSummary model
                mock_llm_client.structured_complete.assert_called_once()
                call_kwargs = mock_llm_client.structured_complete.call_args.kwargs
                assert call_kwargs.get("response_model") is LegalSummary

                # Validate payload content matching mock
                assert data["case_id"] == "CORP-LITIGATION-883"
                assert data["verdict"] == "FAVORABLE"
                assert data["risk_level"] == "LOW"
                assert len(data["key_arguments"]) == 3
                assert data["confidence_score"] == 0.98


if __name__ == "__main__":
    pytest.main(["-v", __file__])
