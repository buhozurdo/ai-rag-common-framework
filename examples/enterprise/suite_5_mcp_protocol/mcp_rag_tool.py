"""MCP Server adapter exposing RAGEngine as an Anthropic-compliant Model Context Protocol tool."""

from __future__ import annotations

import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

from common_framework.mcp.server import FrameworkMCPServer, create_mcp_server
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document, SearchResult

logger = logging.getLogger("examples.mcp.rag_tool")


class LegalSearchArgs(BaseModel):
    """Pydantic schema defining dynamic arguments for the search_legal_corpus MCP tool."""

    query: str = Field(..., description="Legal query string, precedent or statutory provision to locate")
    jurisdiction: str = Field(default="federal", description="Target jurisdiction filter: federal, state, international")
    top_k: int = Field(default=3, ge=1, le=20, description="Maximum number of relevant legal citations to retrieve")


def build_legal_mcp_server(rag_engine: Optional[RAGEngine] = None) -> FrameworkMCPServer:
    """Build and configure a FrameworkMCPServer exporting RAG retrieval as an official MCP tool."""
    engine = rag_engine or RAGEngine()
    server = create_mcp_server(
        name="legal-knowledge-mcp",
        version="1.0.0",
        instructions="MCP server providing RAG legal search over jurisprudence and contracts.",
    )

    @server.tool(
        name="search_legal_corpus",
        description="Search precedents, jurisprudence, and statutory articles within the indexed legal corpus.",
    )
    async def search_legal_corpus(
        query: str,
        jurisdiction: str = "federal",
        top_k: int = 3,
    ) -> str:
        """Execute semantic RAG retrieval and format citations for the calling agent (Cursor / Claude)."""
        logger.info("Executing MCP tool search_legal_corpus: query=%r, jurisdiction=%r, top_k=%d", query, jurisdiction, top_k)
        filter_meta = {"jurisdiction": jurisdiction} if jurisdiction else None
        results = await engine.retrieve(query=query, top_k=top_k, filter_metadata=filter_meta)

        if not results:
            return "No relevant legal precedents found for the specified query."

        formatted_citations = []
        for r in results:
            doc_id = r.document.id
            score = r.score
            content = r.document.content.strip()
            formatted_citations.append(f"[{doc_id}] (Relevance: {score:.2f})\n{content}")

        return "\n\n---\n\n".join(formatted_citations)

    return server


async def dispatch_jsonrpc_tool_call(
    server: FrameworkMCPServer,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Simulate the dispatch of a JSON-RPC 2.0 tool call from Cursor / Claude Desktop to the MCP server."""
    req_id = payload.get("id", "req-default")
    method = payload.get("method")
    params = payload.get("params", {})

    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method {method} not supported"},
        }

    tool_name = params.get("name")
    tool_args = params.get("arguments", {})

    # Direct invocation through MCPServer
    result = await server.underlying_server.call_tool(tool_name, tool_args)

    content_list = []
    for item in result.content:
        content_list.append({"type": getattr(item, "type", "text"), "text": getattr(item, "text", str(item))})

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": content_list,
            "isError": getattr(result, "is_error", False) or getattr(result, "isError", False),
        },
    }
