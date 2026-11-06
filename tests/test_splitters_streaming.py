"""Tests for lazy streaming text and document splitting."""

import inspect
from typing import Iterator
import pytest

from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document
from common_framework.rag.splitters import CharacterTextSplitter, RecursiveTextSplitter


def test_character_splitter_is_generator():
    """Verify split_text_stream returns a generator/iterator without eager materialization."""
    splitter = CharacterTextSplitter(separator="\n\n", chunk_size=20, chunk_overlap=0)
    stream = splitter.split_text_stream("Paragraph 1\n\nParagraph 2\n\nParagraph 3")

    assert inspect.isgenerator(stream) or isinstance(stream, Iterator)
    first_chunk = next(stream)
    assert first_chunk == "Paragraph 1"
    remaining = list(stream)
    assert remaining == ["Paragraph 2", "Paragraph 3"]


def test_recursive_splitter_is_generator():
    """Verify RecursiveTextSplitter.split_text_stream returns a generator/iterator."""
    splitter = RecursiveTextSplitter(chunk_size=40, chunk_overlap=10)
    text = "Short intro.\n\nLong body paragraph with multiple sentences.\n\nConclusion."
    stream = splitter.split_text_stream(text)

    assert inspect.isgenerator(stream) or isinstance(stream, Iterator)
    chunks = list(stream)
    assert len(chunks) >= 2


def test_character_and_recursive_streaming_equivalence():
    """Verify that split_text(t) == list(split_text_stream(t)) across splitters."""
    text = (
        "Heading 1: Architecture\n\n"
        "AI Common Framework is designed to be lightweight, modular, and extensible.\n\n"
        "It supports MCP servers, RAG pipelines, and API gateways.\n\n"
        "Heading 2: Features\n\n"
        "Features include hybrid search, fallbacks, and streaming."
    )

    char_splitter = CharacterTextSplitter(separator="\n\n", chunk_size=80, chunk_overlap=20)
    assert char_splitter.split_text(text) == list(char_splitter.split_text_stream(text))

    rec_splitter = RecursiveTextSplitter(chunk_size=80, chunk_overlap=20)
    assert rec_splitter.split_text(text) == list(rec_splitter.split_text_stream(text))


def test_document_streaming():
    """Verify split_documents_stream yields Document objects with correct provenance metadata."""
    docs = [
        Document(id="doc-1", content="Alpha 1\n\nAlpha 2", metadata={"source": "alpha"}),
        Document(id="doc-2", content="Beta 1\n\nBeta 2", metadata={"source": "beta"}),
    ]

    splitter = CharacterTextSplitter(separator="\n\n", chunk_size=10, chunk_overlap=0)
    stream = splitter.split_documents_stream(docs)

    assert inspect.isgenerator(stream) or isinstance(stream, Iterator)
    chunked = list(stream)
    assert len(chunked) == 4

    assert chunked[0].content == "Alpha 1"
    assert chunked[0].metadata["parent_id"] == "doc-1"
    assert chunked[0].metadata["chunk_index"] == 0
    assert chunked[0].metadata["source"] == "alpha"

    assert chunked[1].content == "Alpha 2"
    assert chunked[1].metadata["parent_id"] == "doc-1"
    assert chunked[1].metadata["chunk_index"] == 1

    assert chunked[2].content == "Beta 1"
    assert chunked[2].metadata["parent_id"] == "doc-2"
    assert chunked[2].metadata["chunk_index"] == 0

    assert chunked[3].content == "Beta 2"
    assert chunked[3].metadata["parent_id"] == "doc-2"
    assert chunked[3].metadata["chunk_index"] == 1


def test_empty_text_streaming():
    """Verify empty text yields zero chunks without error."""
    char_splitter = CharacterTextSplitter()
    assert list(char_splitter.split_text_stream("")) == []

    rec_splitter = RecursiveTextSplitter()
    assert list(rec_splitter.split_text_stream("")) == []


@pytest.mark.asyncio
async def test_rag_engine_ingest_streaming_generator_with_batching():
    """Verify RAGEngine.ingest_documents accepts a generator of documents and batches them."""
    topics = [
        "Python", "JavaScript", "Rust", "Golang", "Kotlin",
        "Swift", "C++", "Quantum Computing", "Biology", "Astronomy",
    ]

    def doc_generator():
        for i, topic in enumerate(topics):
            yield Document(id=f"stream-doc-{i}", content=f"Unique encyclopedic article detailing {topic} in depth.")

    engine = RAGEngine()
    splitter = CharacterTextSplitter(chunk_size=100, chunk_overlap=0)

    # Ingest using batch_size=3
    added_ids = await engine.ingest_documents(
        documents=doc_generator(),
        collection="streaming_test",
        splitter=splitter,
        batch_size=3,
    )

    assert len(added_ids) == len(topics)
    assert await engine.vector_store.count("streaming_test") == len(topics)

    # Verify retrieval works on ingested docs
    results = await engine.retrieve("Quantum Computing", top_k=2, collection="streaming_test")
    assert len(results) >= 1
    assert any("Quantum Computing" in r.document.content for r in results)
