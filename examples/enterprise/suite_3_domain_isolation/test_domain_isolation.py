"""Suite 3: Aislamiento del Dominio (RAG & LLM Mocks).

Valida la lógica de negocio del orquestador de forma completamente aislada,
sin incurrir en gasto de tokens de API ni depender de latencias o fluctuaciones de red externa.
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
    """Pruebas de ingesta y fragmentación aisladas usando un Mock de VectorStore."""

    @pytest.mark.asyncio
    async def test_isolated_ingest_with_vector_store_mock(self) -> None:
        """Objetivo: Inyectar un Mock de InMemoryVectorStore dentro de RAGEngine. Llamar a la

        función de ingesta con una lista de documentos. Verificar que el motor fragmentó
        los documentos y llamó al método add_documents del Vector Store con la estructura
        de metadatos esperada (parent_id, chunk_index).
        """
        # 1. Crear el Mock de VectorStore
        mock_vector_store = AsyncMock(spec=InMemoryVectorStore)
        mock_vector_store.add_documents = AsyncMock(return_value=["chunk-id-1", "chunk-id-2"])

        # 2. Inyectar en RAGEngine con un splitter pequeño para forzar múltiples fragmentos
        custom_splitter = RecursiveTextSplitter(chunk_size=120, chunk_overlap=20)
        engine = RAGEngine(
            vector_store=mock_vector_store,
            default_splitter=custom_splitter,
        )

        parent_doc_id = "legal-contract-2026-dossier"
        long_content = (
            "CLÁUSULA PRIMERA: De la confidencialidad de la información y propiedad intelectual. "
            "Todas las partes acuerdan resguardar los secretos comerciales bajo pena de indemnización. "
            "CLÁUSULA SEGUNDA: Del plazo de vigencia estipulado en treinta y seis meses continuos. "
            "Cualquier prórroga deberá convenirse por escrito entre los apoderados legales."
        )
        sample_doc = Document(
            id=parent_doc_id,
            content=long_content,
            metadata={"source_file": "NDA_Corp.pdf", "jurisdiction": "federal"},
        )

        # 3. Invocar ingest_documents
        ingested_ids = await engine.ingest_documents([sample_doc], collection="legal_contracts")

        # 4. Validar que add_documents fue invocado exactamente una vez para el batch
        assert mock_vector_store.add_documents.call_count == 1, (
            "add_documents no fue llamado o se llamó un número inesperado de veces."
        )

        # 5. Inspeccionar argumentos pasados a add_documents
        call_args, call_kwargs = mock_vector_store.add_documents.call_args
        stored_chunks: list[Document] = call_args[0]
        target_collection: str = call_kwargs.get("collection", "")

        assert target_collection == "legal_contracts"
        assert len(stored_chunks) >= 2, f"Se esperaban múltiples chunks, pero se obtuvieron {len(stored_chunks)}"

        # 6. Validar metadatos obligatorios en cada fragmento (parent_id, chunk_index)
        for idx, chunk in enumerate(stored_chunks):
            assert chunk.metadata.get("parent_id") == parent_doc_id, (
                f"El fragmento #{idx} perdió el parent_id del documento raíz."
            )
            assert chunk.metadata.get("chunk_index") == idx, (
                f"El fragmento #{idx} no tiene el chunk_index secuencial esperado."
            )
            assert chunk.metadata.get("source_file") == "NDA_Corp.pdf", (
                "Se perdieron los metadatos de usuario originales al fragmentar."
            )

        assert ingested_ids == ["chunk-id-1", "chunk-id-2"]


class TestDeterministicLLMOutput:
    """Pruebas de salida determinista mediante patch a LLMFactory.create()."""

    def test_deterministic_legal_summary_llm_mock(self) -> None:
        """Objetivo: Intervenir LLMFactory.create() (usando unittest.mock.patch) para que el cliente

        devuelva un objeto LegalSummary (Pydantic) prefabricado en lugar de hacer una llamada
        HTTP a OpenAI o Gemini. Validar que el servicio procesa correctamente el modelo Pydantic
        retornado y que la respuesta final del Gateway coincide exactamente con esta estructura.
        """
        # 1. Crear el objeto LegalSummary prefabricado (modelo Pydantic tipado)
        prefabricated_summary = LegalSummary(
            case_id="CORP-LITIGATION-883",
            verdict="FAVORABLE",
            key_arguments=[
                "Cumplimiento fehaciente del plazo de notificación según Cláusula 5.1",
                "Ausencia de dolo comprobable en la ejecución de la fianza",
                "Precedente aplicable del Tribunal Colegiado en Materia Civil",
            ],
            risk_level="LOW",
            confidence_score=0.98,
        )

        # 2. Configurar el Mock de BaseLLMClient
        mock_llm_client = AsyncMock(spec=BaseLLMClient)
        mock_llm_client.structured_complete = AsyncMock(return_value=prefabricated_summary)

        # 3. Intervenir LLMFactory.create con patch
        with patch("common_framework.core.llm.LLMFactory.create", return_value=mock_llm_client) as mock_factory:
            app = create_legal_gateway()

            with TestClient(app) as client:
                payload = {
                    "case_id": "CORP-LITIGATION-883",
                    "document_text": (
                        "Demanda de controversia contractual interpuesta por la contraparte alegando incumplimiento... "
                        "Se presenta contestación demostrando cumplimiento puntual de obligaciones."
                    ),
                }

                # Llamar al Gateway
                response = client.post("/api/v1/legal/analyze", json=payload)

                # Validaciones HTTP
                assert response.status_code == 200, f"Error en Gateway: {response.text}"
                data = response.json()

                # Validar que LLMFactory.create fue invocado
                mock_factory.assert_called()

                # Validar que structured_complete recibió el modelo LegalSummary
                mock_llm_client.structured_complete.assert_called_once()
                call_kwargs = mock_llm_client.structured_complete.call_args[1]
                assert call_kwargs.get("response_model") is LegalSummary

                # Validar determinismo absoluto en la respuesta del Gateway
                assert data["case_id"] == "CORP-LITIGATION-883"
                assert data["verdict"] == "FAVORABLE"
                assert data["risk_level"] == "LOW"
                assert data["confidence_score"] == 0.98
                assert len(data["key_arguments"]) == 3
                assert data["key_arguments"][0] == "Cumplimiento fehaciente del plazo de notificación según Cláusula 5.1"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
