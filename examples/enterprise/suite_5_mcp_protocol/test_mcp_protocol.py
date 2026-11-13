"""Suite 5: Protocolo MCP (Tool Execution).

Valida que las herramientas exportadas por el framework respondan bajo el estándar oficial
del Model Context Protocol (MCP) promovido por Anthropic. Simula el payload JSON-RPC 2.0
que un IDE o cliente (como Cursor, Claude Desktop o Antigravity) enviaría para invocar
la herramienta 'search_legal_corpus' enrutando la petición hacia RAGEngine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document, SearchResult
from suite_5_mcp_protocol.mcp_rag_tool import (
    build_legal_mcp_server,
    dispatch_jsonrpc_tool_call,
)


class TestMCPToolExecutionProtocol:
    """Pruebas de validación del protocolo MCP y esquema de herramientas."""

    @pytest.mark.asyncio
    async def test_mcp_dynamic_schema_generation(self) -> None:
        """Objetivo: Validar que el servidor MCP expone dinámicamente el esquema de la herramienta

        generado a partir de los tipos y modelos Pydantic, cumpliendo con la especificación MCP.
        """
        server = build_legal_mcp_server()
        tools = await server.list_tools()

        # 1. Verificar presencia de la herramienta exportada
        tool_names = [t.name for t in tools]
        assert "search_legal_corpus" in tool_names, (
            f"La herramienta 'search_legal_corpus' no fue exportada al listado MCP: {tool_names}"
        )

        target_tool = next(t for t in tools if t.name == "search_legal_corpus")
        assert "precedentes" in target_tool.description.lower()

        # 2. Validar esquema JSON Schema generado dinámicamente
        schema = target_tool.input_schema
        assert schema.get("type") == "object"
        properties = schema.get("properties", {})

        # Verificar parámetros requeridos y tipos
        assert "query" in properties, "El parámetro 'query' no está definido en input_schema."
        assert properties["query"]["type"] == "string"

        assert "jurisdiction" in properties, "El parámetro 'jurisdiction' no está en input_schema."
        assert properties["jurisdiction"].get("default") == "federal"

        assert "top_k" in properties, "El parámetro 'top_k' no está en input_schema."
        assert properties["top_k"]["type"] == "integer"
        assert properties["top_k"].get("default") == 3

        assert "query" in schema.get("required", [])

    @pytest.mark.asyncio
    async def test_json_rpc_tool_execution_routes_to_rag_engine(self) -> None:
        """Objetivo: Simular el payload JSON-RPC que un cliente (como Cursor o Claude Desktop)

        enviaría al servidor MCP para invocar 'search_legal_corpus'. Comprobar que el servidor
        enruta la petición hacia la función interna de RAGEngine y empaqueta el resultado
        en el formato estándar de respuesta MCP (TextContent y CallToolResult).
        """
        # 1. Mock de RAGEngine con resultados sintéticos controlados
        mock_rag_engine = AsyncMock(spec=RAGEngine)
        mock_rag_engine.retrieve = AsyncMock(
            return_value=[
                SearchResult(
                    document=Document(
                        id="ART-CONST-16",
                        content="Nadie puede ser molestado en su persona sino en virtud de mandamiento escrito.",
                        metadata={"jurisdiction": "federal"},
                    ),
                    score=0.96,
                ),
                SearchResult(
                    document=Document(
                        id="PRECEDENTE-SCJN-2024",
                        content="El secreto profesional y comercial goza de protección reforzada bajo el artículo 16 constitucional.",
                        metadata={"jurisdiction": "federal"},
                    ),
                    score=0.91,
                ),
            ]
        )

        server = build_legal_mcp_server(rag_engine=mock_rag_engine)

        # 2. Payload JSON-RPC 2.0 estándar según especificación Anthropic MCP
        client_jsonrpc_request = {
            "jsonrpc": "2.0",
            "id": "cursor-agent-call-9912",
            "method": "tools/call",
            "params": {
                "name": "search_legal_corpus",
                "arguments": {
                    "query": "protección del secreto comercial y garantías",
                    "jurisdiction": "federal",
                    "top_k": 2,
                },
            },
        }

        # 3. Despachar a través del servidor MCP
        jsonrpc_response = await dispatch_jsonrpc_tool_call(server, client_jsonrpc_request)

        # 4. Validar que la petición fue enrutada con los argumentos correctos hacia RAGEngine
        mock_rag_engine.retrieve.assert_called_once_with(
            query="protección del secreto comercial y garantías",
            top_k=2,
            filter_metadata={"jurisdiction": "federal"},
        )

        # 5. Validar formato estándar de respuesta Anthropic MCP
        assert jsonrpc_response["jsonrpc"] == "2.0"
        assert jsonrpc_response["id"] == "cursor-agent-call-9912"
        assert "result" in jsonrpc_response
        result_data = jsonrpc_response["result"]

        assert result_data["isError"] is False
        assert len(result_data["content"]) >= 1

        first_block = result_data["content"][0]
        assert first_block["type"] == "text"
        output_text = first_block["text"]

        # Validar que contiene las citas generadas por RAG
        assert "[ART-CONST-16]" in output_text
        assert "Relevancia: 0.96" in output_text
        assert "[PRECEDENTE-SCJN-2024]" in output_text
        assert "protección reforzada" in output_text

    @pytest.mark.asyncio
    async def test_json_rpc_tool_execution_empty_results_graceful_handling(self) -> None:
        """Verifica que el servidor MCP maneja elegantemente búsquedas sin resultados sin lanzar excepciones."""
        mock_rag_engine = AsyncMock(spec=RAGEngine)
        mock_rag_engine.retrieve = AsyncMock(return_value=[])

        server = build_legal_mcp_server(rag_engine=mock_rag_engine)

        request_payload = {
            "jsonrpc": "2.0",
            "id": "req-empty-check",
            "method": "tools/call",
            "params": {
                "name": "search_legal_corpus",
                "arguments": {
                    "query": "término completamente inexistente en el corpus",
                    "jurisdiction": "international",
                    "top_k": 5,
                },
            },
        }

        response = await dispatch_jsonrpc_tool_call(server, request_payload)
        assert response["result"]["isError"] is False
        text = response["result"]["content"][0]["text"]
        assert "No se encontraron precedentes" in text


if __name__ == "__main__":
    pytest.main(["-v", __file__])
