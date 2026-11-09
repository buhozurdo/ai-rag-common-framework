"""Short-term and long-term conversation memory buffers (FEAT-05 / TASK-15)."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional

from common_framework.core.llm import ChatMessage, Role
from common_framework.rag.embeddings import BaseEmbeddingProvider, EmbeddingFactory
from common_framework.rag.models import Document
from common_framework.rag.vectorstore import BaseVectorStore, InMemoryVectorStore


class BaseMemory(ABC):
    """Abstract interface for agent conversation memory."""

    @abstractmethod
    async def add_message(self, message: ChatMessage) -> None:
        """Append a message to the conversation memory."""
        pass

    @abstractmethod
    async def get_messages(self) -> list[ChatMessage]:
        """Retrieve stored conversation messages."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear memory buffer."""
        pass


class ConversationBufferMemory(BaseMemory):
    """In-memory sliding window buffer for conversational history."""

    def __init__(self, max_messages: Optional[int] = None) -> None:
        self.max_messages = max_messages
        self._messages: list[ChatMessage] = []
        self._lock = asyncio.Lock()

    async def add_message(self, message: ChatMessage) -> None:
        async with self._lock:
            self._messages.append(message)
            self._trim()

    def add_message_sync(self, message: ChatMessage) -> None:
        self._messages.append(message)
        self._trim()

    def add_user_message(self, content: str) -> None:
        self.add_message_sync(ChatMessage(role=Role.USER, content=content))

    def add_assistant_message(self, content: str) -> None:
        self.add_message_sync(ChatMessage(role=Role.ASSISTANT, content=content))

    def add_system_message(self, content: str) -> None:
        self.add_message_sync(ChatMessage(role=Role.SYSTEM, content=content))

    def _trim(self) -> None:
        """Trim messages to max_messages while preserving the initial system prompt if present."""
        if self.max_messages is None or len(self._messages) <= self.max_messages:
            return

        has_system = len(self._messages) > 0 and self._messages[0].role == Role.SYSTEM
        if has_system:
            system_msg = self._messages[0]
            # Keep system prompt + the last (max_messages - 1) messages
            kept = self._messages[-(self.max_messages - 1) :]
            self._messages = [system_msg] + kept
        else:
            self._messages = self._messages[-self.max_messages :]

    async def get_messages(self) -> list[ChatMessage]:
        async with self._lock:
            return list(self._messages)

    async def clear(self) -> None:
        async with self._lock:
            self._messages.clear()


class VectorMemory(ConversationBufferMemory):
    """Hybrid memory combining a short-term conversational buffer with persistent RAG retrieval."""

    def __init__(
        self,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        collection: str = "agent_memory",
        max_messages: Optional[int] = 10,
    ) -> None:
        super().__init__(max_messages=max_messages)
        self.vector_store = vector_store or InMemoryVectorStore()
        self.embedding_provider = embedding_provider or EmbeddingFactory.create("mock")
        self.collection = collection

    async def save_interaction(
        self,
        user_input: str,
        assistant_output: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Index a completed turn into the vector store for long-term associative recall."""
        await self.add_message(ChatMessage(role=Role.USER, content=user_input))
        await self.add_message(ChatMessage(role=Role.ASSISTANT, content=assistant_output))

        content = f"User: {user_input}\nAssistant: {assistant_output}"
        meta = dict(metadata or {})
        meta["type"] = "interaction"

        emb = await self.embedding_provider.embed_query(content)
        doc = Document(content=content, metadata=meta, embedding=emb)
        added_ids = await self.vector_store.add_documents([doc], collection=self.collection)
        return added_ids[0] if added_ids else ""

    async def recall(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[Document]:
        """Retrieve relevant past interactions from long-term memory."""
        query_emb = await self.embedding_provider.embed_query(query)
        results = await self.vector_store.similarity_search(
            query_embedding=query_emb,
            top_k=top_k,
            collection=self.collection,
        )
        return [r.document for r in results]

    async def get_relevant_context(self, query: str, top_k: int = 3) -> str:
        """Format retrieved memories into a structured context snippet for the agent prompt."""
        docs = await self.recall(query, top_k=top_k)
        if not docs:
            return ""

        snippets = [f"- {d.content.strip()}" for d in docs]
        return "\n".join(snippets)
