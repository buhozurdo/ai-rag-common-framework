"""Example: Modular RAG Engine with In-Memory Vector Store and MCP integration (v0.3.0-alpha)."""

import asyncio
from common_framework import (
    Document,
    InMemoryVectorStore,
    MockEmbeddingProvider,
    RAGEngine,
    RecursiveTextSplitter,
    create_mcp_server,
)


async def main() -> None:
    print("=== AI RAG Common Framework - RAG Demo ===\n")

    # 1. Initialize RAG Engine components (zero external databases needed!)
    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider(dimension=32)
    splitter = RecursiveTextSplitter(chunk_size=120, chunk_overlap=20)

    engine = RAGEngine(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        default_splitter=splitter,
    )

    # 2. Ingest sample knowledge base documents
    sample_docs = [
        Document(
            id="doc-arch",
            content=(
                "The AI RAG Common Framework uses a hexagonal modular architecture. "
                "It separates core LLM abstractions, MCP tooling, and vector storage into isolated modules."
            ),
            metadata={"category": "architecture", "author": "BuhoZurdo"},
        ),
        Document(
            id="doc-mcp",
            content=(
                "Model Context Protocol (MCP) is an open protocol standard that lets LLMs "
                "securely communicate with external data sources, prompts, and tools via stdio and SSE."
            ),
            metadata={"category": "protocols", "author": "Anthropic"},
        ),
    ]

    print("Ingesting documents into vector store...")
    chunk_ids = await engine.ingest_documents(sample_docs)
    print(f"Ingested {len(chunk_ids)} chunks into vector store.\n")

    # 3. Retrieve relevant context
    query = "What is the architecture of the framework?"
    print(f"Query: {query!r}")
    results = await engine.retrieve(query, top_k=1)
    for r in results:
        print(f"Top Result (Score: {r.score:.4f}):\n{r.document.content}\n")

    # 4. Export RAG as an MCP Tool in one line!
    rag_tool = engine.as_tool(
        name="search_project_docs",
        description="Search internal project architectural documentation.",
    )
    print(f"Generated RAG Tool: {rag_tool.name} (Schema parameters: {list(rag_tool.parameters_schema.get('properties', {}).keys())})")

    # 5. Attach RAG Tool to MCP Server
    mcp_server = create_mcp_server(name="rag-mcp-service", version="0.3.0-alpha")
    mcp_server.add_tool(rag_tool)
    tools = await mcp_server.list_tools()
    print(f"MCP Server Tools: {[t.name for t in tools]}")


if __name__ == "__main__":
    asyncio.run(main())
