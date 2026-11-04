"""End-to-end Retrieval-Augmented Generation (RAG) orchestrator."""

from __future__ import annotations

import logging
from typing import Any, Optional

from common_framework.core.llm import BaseLLMClient, ChatMessage, LLMFactory, Role
from common_framework.rag.embeddings import BaseEmbeddingProvider, EmbeddingFactory
from common_framework.rag.models import Document, RAGResponse, SearchResult
from common_framework.rag.splitters import BaseTextSplitter, RecursiveTextSplitter
from common_framework.rag.vectorstore import BaseVectorStore, InMemoryVectorStore
from common_framework.tools.base import Tool, tool

logger = logging.getLogger("common_framework.rag")

DEFAULT_RAG_SYSTEM_PROMPT = (
    "You are an accurate, grounded assistant. Answer the user's question using ONLY the provided context.\n"
    "If the answer cannot be determined from the context, say 'I cannot find the answer in the provided documents.'\n"
    "Cite relevant sources with [Source: {doc_id}] when possible."
)


class RAGEngine:
    """Orchestrator combining document splitting, embeddings, vector storage, and LLM synthesis."""

    def __init__(
        self,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        llm_client: Optional[BaseLLMClient] = None,
        default_splitter: Optional[BaseTextSplitter] = None,
    ) -> None:
        self.vector_store = vector_store or InMemoryVectorStore()
        self.embedding_provider = embedding_provider or EmbeddingFactory.create("mock")
        self.llm_client = llm_client or LLMFactory.create()
        self.default_splitter = default_splitter or RecursiveTextSplitter()

    async def ingest_documents(
        self,
        documents: list[Document],
        splitter: Optional[BaseTextSplitter] = None,
    ) -> list[str]:
        """Split documents, compute vector embeddings, and store them in the vector store."""
        active_splitter = splitter or self.default_splitter
        chunks = active_splitter.split_documents(documents) if active_splitter else documents

        if not chunks:
            return []

        texts = [c.content for c in chunks]
        embeddings = await self.embedding_provider.embed_documents(texts)

        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb

        added_ids = await self.vector_store.add_documents(chunks)
        logger.info("Ingested %d document chunks into vector store.", len(added_ids))
        return added_ids

    async def ingest_texts(
        self,
        texts: list[str],
        metadatas: Optional[list[dict[str, Any]]] = None,
        splitter: Optional[BaseTextSplitter] = None,
    ) -> list[str]:
        """Convenience method to ingest raw text strings."""
        docs: list[Document] = []
        for idx, text in enumerate(texts):
            meta = metadatas[idx] if metadatas and idx < len(metadatas) else {}
            docs.append(Document(content=text, metadata=meta))
        return await self.ingest_documents(docs, splitter=splitter)

    async def retrieve(self, query: str, top_k: int = 4) -> list[SearchResult]:
        """Perform similarity search and return relevant document chunks."""
        query_emb = await self.embedding_provider.embed_query(query)
        return await self.vector_store.similarity_search(query_emb, top_k=top_k)

    async def query(
        self,
        query: str,
        top_k: int = 4,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> RAGResponse:
        """Retrieve relevant context and synthesize an answer using the LLM."""
        results = await self.retrieve(query, top_k=top_k)

        # Build context string
        context_parts = []
        for r in results:
            doc_id = r.document.metadata.get("parent_id", r.document.id)
            context_parts.append(f"[Source: {doc_id}]\n{r.document.content}")

        context_str = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant context found."

        sys_prompt = system_prompt or DEFAULT_RAG_SYSTEM_PROMPT
        user_message_content = (
            f"Context Information:\n{context_str}\n\n"
            f"User Question: {query}\n\n"
            "Answer:"
        )

        messages = [
            ChatMessage(role=Role.SYSTEM, content=sys_prompt),
            ChatMessage(role=Role.USER, content=user_message_content),
        ]

        llm_response = await self.llm_client.complete(
            messages=messages,
            model=model,
            temperature=temperature if temperature is not None else 0.0,
            **kwargs,
        )

        return RAGResponse(
            answer=llm_response.content,
            sources=results,
            model=llm_response.model,
            usage=llm_response.usage,
        )

    def as_tool(
        self,
        name: str = "search_knowledge_base",
        description: str = "Search the indexed knowledge base and retrieve relevant information for answering queries.",
        top_k: int = 4,
    ) -> Tool:
        """Export this RAG engine as a callable Tool compatible with MCP servers and agent workflows."""
        engine = self

        @tool(name=name, description=description)
        async def rag_tool(query: str) -> dict[str, Any]:
            results = await engine.retrieve(query, top_k=top_k)
            snippets = [
                {
                    "content": r.document.content,
                    "score": round(r.score, 4),
                    "metadata": r.document.metadata,
                }
                for r in results
            ]
            return {"query": query, "results": snippets}

        return rag_tool
