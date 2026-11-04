"""Unit tests for RAG components: Splitters, Embeddings, Vector Stores, and RAG Engine."""

import math
import httpx
import pytest

from common_framework.core.llm import ChatMessage, OpenAICompatibleClient, Role
from common_framework.mcp.server import create_mcp_server
from common_framework.rag.embeddings import EmbeddingFactory, MockEmbeddingProvider
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document
from common_framework.rag.splitters import CharacterTextSplitter, RecursiveTextSplitter
from common_framework.rag.vectorstore import InMemoryVectorStore, cosine_similarity
from common_framework.tools.base import Tool


def test_character_splitter():
    """Verify CharacterTextSplitter splits by delimiter adhering to chunk size."""
    text = "Paragraph 1\n\nParagraph 2\n\nParagraph 3"
    splitter = CharacterTextSplitter(separator="\n\n", chunk_size=15, chunk_overlap=0)
    chunks = splitter.split_text(text)
    assert len(chunks) == 3
    assert chunks[0] == "Paragraph 1"
    assert chunks[1] == "Paragraph 2"
    assert chunks[2] == "Paragraph 3"


def test_recursive_splitter_and_documents():
    """Verify RecursiveTextSplitter and document metadata tracking."""
    text = "Machine learning is a subset of AI.\n\nDeep learning uses neural networks.\n\nTransformers revolutionized NLP."
    splitter = RecursiveTextSplitter(chunk_size=40, chunk_overlap=10)
    doc = Document(id="doc-original-1", content=text, metadata={"topic": "ai"})

    chunks = splitter.split_documents([doc])
    assert len(chunks) >= 2
    for idx, c in enumerate(chunks):
        assert c.metadata["parent_id"] == "doc-original-1"
        assert c.metadata["chunk_index"] == idx
        assert c.metadata["topic"] == "ai"


@pytest.mark.asyncio
async def test_mock_embedding_provider():
    """Verify MockEmbeddingProvider generates normalized deterministic vectors."""
    provider = MockEmbeddingProvider(dimension=32)
    vec1 = await provider.embed_query("Artificial Intelligence")
    vec2 = await provider.embed_query("Artificial Intelligence")
    vec3 = await provider.embed_query("Cooking Recipes")

    assert len(vec1) == 32
    assert vec1 == vec2  # Deterministic!
    assert vec1 != vec3

    norm = math.sqrt(sum(x * x for x in vec1))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)


def test_cosine_similarity():
    """Verify cosine similarity calculation."""
    v1 = [1.0, 0.0]
    v2 = [1.0, 0.0]
    v3 = [0.0, 1.0]

    assert math.isclose(cosine_similarity(v1, v2), 1.0, rel_tol=1e-5)
    assert math.isclose(cosine_similarity(v1, v3), 0.0, rel_tol=1e-5)


@pytest.mark.asyncio
async def test_in_memory_vectorstore():
    """Verify InMemoryVectorStore operations."""
    store = InMemoryVectorStore()
    assert await store.count() == 0

    doc1 = Document(id="d1", content="Python RAG framework", embedding=[1.0, 0.0])
    doc2 = Document(id="d2", content="JavaScript frontend", embedding=[0.0, 1.0])

    await store.add_documents([doc1, doc2])
    assert await store.count() == 2

    # Query closest to doc1
    results = await store.similarity_search([0.9, 0.1], top_k=1)
    assert len(results) == 1
    assert results[0].document.id == "d1"
    assert results[0].score > 0.8

    # Delete
    await store.delete(["d1"])
    assert await store.count() == 1
    assert store.get("d1") is None


@pytest.mark.asyncio
async def test_rag_engine_end_to_end():
    """Verify end-to-end ingestion, retrieval, and query synthesis."""
    # 1. Setup mock LLM HTTP client
    def mock_llm_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Model Context Protocol (MCP) enables LLMs to access external tools and data.",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65},
            },
        )

    transport = httpx.MockTransport(mock_llm_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        llm = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        embedding_provider = MockEmbeddingProvider(dimension=16)
        vector_store = InMemoryVectorStore()

        engine = RAGEngine(
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            llm_client=llm,
        )

        # 2. Ingest texts
        texts = [
            "Model Context Protocol (MCP) is an open standard designed by Anthropic for connecting AI models to data sources and tools.",
            "FastAPI is a modern web framework for building REST APIs with Python based on standard type hints.",
        ]
        doc_ids = await engine.ingest_texts(texts, metadatas=[{"source": "mcp-doc"}, {"source": "fastapi-doc"}])
        assert len(doc_ids) >= 2

        # 3. Retrieve
        retrieved = await engine.retrieve("What is MCP?", top_k=1)
        assert len(retrieved) == 1
        assert "Model Context Protocol" in retrieved[0].document.content

        # 4. Synthesize query
        response = await engine.query("Explain MCP", top_k=1)
        assert "Model Context Protocol (MCP)" in response.answer
        assert len(response.sources) == 1
        assert response.usage.total_tokens == 65


@pytest.mark.asyncio
async def test_rag_engine_as_tool_and_mcp_integration():
    """Verify exporting RAGEngine as a Tool and mounting into MCP Server."""
    engine = RAGEngine()
    await engine.ingest_texts(["The capital of France is Paris."])

    # Convert RAG engine to tool
    search_tool = engine.as_tool(name="kb_search", description="Search internal knowledge base")
    assert isinstance(search_tool, Tool)
    assert search_tool.name == "kb_search"

    # Execute tool directly
    tool_output = await search_tool(query="Paris capital")
    assert "results" in tool_output
    assert len(tool_output["results"]) > 0
    assert "Paris" in tool_output["results"][0]["content"]

    # Register into MCP server
    mcp_server = create_mcp_server(name="rag-mcp-demo")
    mcp_server.add_tool(search_tool)

    mcp_tools = await mcp_server.list_tools()
    assert "kb_search" in [t.name for t in mcp_tools]
