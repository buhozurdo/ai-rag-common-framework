"""Suite 5: MCP Protocol (Tool Execution).

Validates that tools exported by the framework adhere to the official Model Context
Protocol (MCP) standard promoted by Anthropic. Simulates JSON-RPC 2.0 payloads sent by
an IDE or client (e.g., Cursor, Claude Desktop, Antigravity) to invoke 'search_legal_corpus'
routing requests into RAGEngine.
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
    """Validation tests for MCP protocol and tool schema compliance."""

    @pytest.mark.asyncio
    async def test_mcp_dynamic_schema_generation(self) -> None:
        """Verify MCP server exposes dynamic tool schema generated from Pydantic types adhering to MCP spec."""
        server = build_legal_mcp_server()
        tools = await server.list_tools()

        # 1. Verify presence of exported tool
        tool_names = [t.name for t in tools]
        assert "search_legal_corpus" in tool_names, (
            f"Tool 'search_legal_corpus' was not exported in MCP tools listing: {tool_names}"
        )

        target_tool = next(t for t in tools if t.name == "search_legal_corpus")
        assert "precedents" in target_tool.description.lower()

        # 2. Validate dynamically generated JSON Schema
        schema = target_tool.input_schema
        assert schema.get("type") == "object"
        properties = schema.get("properties", {})

        # Verify required parameters and types
        assert "query" in properties, "Parameter 'query' is missing from input_schema."
        assert properties["query"]["type"] == "string"

        assert "jurisdiction" in properties, "Parameter 'jurisdiction' is missing from input_schema."
        assert properties["jurisdiction"].get("default") == "federal"

        assert "top_k" in properties, "Parameter 'top_k' is missing from input_schema."
        assert properties["top_k"]["type"] == "integer"
        assert properties["top_k"].get("default") == 3

        assert "query" in schema.get("required", [])

    @pytest.mark.asyncio
    async def test_json_rpc_tool_execution_routes_to_rag_engine(self) -> None:
        """Verify JSON-RPC 2.0 tool execution routes to RAGEngine and formats standard CallToolResult."""
        # 1. Mock RAGEngine with controlled synthetic results
        mock_rag_engine = AsyncMock(spec=RAGEngine)
        mock_rag_engine.retrieve = AsyncMock(
            return_value=[
                SearchResult(
                    document=Document(
                        id="ART-CONST-16",
                        content="No person shall be disturbed in their person except by virtue of written order.",
                        metadata={"jurisdiction": "federal"},
                    ),
                    score=0.96,
                ),
                SearchResult(
                    document=Document(
                        id="PRECEDENTE-SCJN-2024",
                        content="Trade secret protection enjoys heightened constitutional scrutiny.",
                        metadata={"jurisdiction": "federal"},
                    ),
                    score=0.91,
                ),
            ]
        )

        server = build_legal_mcp_server(rag_engine=mock_rag_engine)

        # 2. Standard JSON-RPC 2.0 payload per Anthropic MCP specification
        client_jsonrpc_request = {
            "jsonrpc": "2.0",
            "id": "cursor-agent-call-9912",
            "method": "tools/call",
            "params": {
                "name": "search_legal_corpus",
                "arguments": {
                    "query": "trade secret protection and constitutional guarantees",
                    "jurisdiction": "federal",
                    "top_k": 2,
                },
            },
        }

        # 3. Dispatch through MCP server
        jsonrpc_response = await dispatch_jsonrpc_tool_call(server, client_jsonrpc_request)

        # 4. Validate request was routed with correct arguments to RAGEngine
        mock_rag_engine.retrieve.assert_called_once_with(
            query="trade secret protection and constitutional guarantees",
            top_k=2,
            filter_metadata={"jurisdiction": "federal"},
        )

        # 5. Validate standard Anthropic MCP response format
        assert jsonrpc_response["jsonrpc"] == "2.0"
        assert jsonrpc_response["id"] == "cursor-agent-call-9912"
        assert "result" in jsonrpc_response
        result_data = jsonrpc_response["result"]

        assert result_data["isError"] is False
        assert len(result_data["content"]) >= 1

        first_block = result_data["content"][0]
        assert first_block["type"] == "text"
        output_text = first_block["text"]

        # Validate generated RAG citations
        assert "[ART-CONST-16]" in output_text
        assert "Relevance: 0.96" in output_text
        assert "[PRECEDENTE-SCJN-2024]" in output_text
        assert "Trade secret protection" in output_text

    @pytest.mark.asyncio
    async def test_json_rpc_tool_execution_empty_results_graceful_handling(self) -> None:
        """Verify MCP server gracefully handles empty search results without raising exceptions."""
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
                    "query": "nonexistent term in indexed corpus",
                    "jurisdiction": "international",
                    "top_k": 5,
                },
            },
        }

        response = await dispatch_jsonrpc_tool_call(server, request_payload)
        assert response["result"]["isError"] is False
        text = response["result"]["content"][0]["text"]
        assert "No relevant legal precedents found" in text


if __name__ == "__main__":
    pytest.main(["-v", __file__])
