"""Comprehensive unit tests for native Document Loaders (RAG milestone v0.20.0)."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from common_framework.rag.loaders import (
    BaseLoader,
    CSVLoader,
    DirectoryLoader,
    HTMLLoader,
    MarkdownLoader,
    PDFLoader,
    TextLoader,
)
from common_framework.rag.models import Document
from common_framework.rag.engine import RAGEngine
from common_framework.core.llm import BaseLLMClient, ChatResponse, TokenUsage


class DummyLLM(BaseLLMClient):
    async def complete(self, messages, **kwargs):
        return ChatResponse(content="Dummy answer", model="dummy", usage=TokenUsage())

    async def structured_complete(self, messages, response_model, **kwargs):
        pass


def test_text_loader_basic(tmp_path: Path):
    file = tmp_path / "sample.txt"
    file.write_text("Hello World! This is plain text content.", encoding="utf-8")

    loader = TextLoader(file)
    docs = loader.load()

    assert len(docs) == 1
    assert "Hello World!" in docs[0].content
    assert docs[0].metadata["filename"] == "sample.txt"
    assert docs[0].metadata["extension"] == ".txt"
    assert docs[0].metadata["file_size"] > 0


def test_text_loader_non_existent():
    loader = TextLoader("non_existent_file.txt")
    with pytest.raises(FileNotFoundError):
        loader.load()


def test_markdown_loader_with_frontmatter(tmp_path: Path):
    file = tmp_path / "guide.md"
    content = """---
title: Getting Started
author: Alice
category: docs
---
# Welcome to Framework

This is the main markdown body text.
"""
    file.write_text(content, encoding="utf-8")

    loader = MarkdownLoader(file)
    docs = loader.load()

    assert len(docs) == 1
    doc = docs[0]
    assert doc.metadata["title"] == "Getting Started"
    assert doc.metadata["author"] == "Alice"
    assert doc.metadata["category"] == "docs"
    assert "Welcome to Framework" in doc.content
    assert "---" not in doc.content


def test_markdown_loader_without_frontmatter(tmp_path: Path):
    file = tmp_path / "readme.md"
    content = """# Architecture Overview

Here is the system architecture overview without frontmatter.
"""
    file.write_text(content, encoding="utf-8")

    loader = MarkdownLoader(file)
    docs = loader.load()

    assert len(docs) == 1
    assert docs[0].metadata["title"] == "Architecture Overview"
    assert "Here is the system architecture" in docs[0].content


def test_csv_loader_row_documents(tmp_path: Path):
    file = tmp_path / "products.csv"
    csv_content = """id,product,price,category
101,Laptop,1200,Electronics
102,Desk Chair,250,Furniture
103,Coffee Mug,15,Kitchenware
"""
    file.write_text(csv_content, encoding="utf-8")

    loader = CSVLoader(file, source_column="product", metadata_columns=["price", "category"])
    docs = loader.load()

    assert len(docs) == 3
    assert docs[0].content == "Laptop"
    assert docs[0].metadata["row"] == 1
    assert docs[0].metadata["price"] == "1200"
    assert docs[0].metadata["category"] == "Electronics"

    assert docs[1].content == "Desk Chair"
    assert docs[1].metadata["row"] == 2


def test_csv_loader_all_columns_content(tmp_path: Path):
    file = tmp_path / "data.csv"
    csv_content = """name,age
Bob,30
Charlie,25
"""
    file.write_text(csv_content, encoding="utf-8")

    loader = CSVLoader(file)
    docs = loader.load()

    assert len(docs) == 2
    assert "name: Bob" in docs[0].content
    assert "age: 30" in docs[0].content


def test_html_loader_clean_text_and_title(tmp_path: Path):
    file = tmp_path / "page.html"
    html_content = """<!DOCTYPE html>
<html>
<head>
    <title>Framework Documentation</title>
    <style>body { font-family: sans-serif; }</style>
    <script>alert('bad');</script>
</head>
<body>
    <h1>Welcome to AI RAG</h1>
    <p>This is a paragraph with <a href="#">link</a>.</p>
    <ul>
        <li>Fast execution</li>
        <li>Zero bloat</li>
    </ul>
</body>
</html>"""
    file.write_text(html_content, encoding="utf-8")

    loader = HTMLLoader(file)
    docs = loader.load()

    assert len(docs) == 1
    doc = docs[0]
    assert doc.metadata["title"] == "Framework Documentation"
    assert "alert('bad')" not in doc.content
    assert "Welcome to AI RAG" in doc.content
    assert "Fast execution" in doc.content
    assert "Zero bloat" in doc.content


def test_directory_loader_recursive(tmp_path: Path):
    # Setup nested folder structure
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    sub_dir = docs_dir / "sub"
    sub_dir.mkdir()

    (docs_dir / "file1.txt").write_text("Content of text 1", encoding="utf-8")
    (docs_dir / "file2.md").write_text("# Doc 2\nContent of markdown 2", encoding="utf-8")
    (sub_dir / "file3.html").write_text("<html><title>Page 3</title><body>HTML 3</body></html>", encoding="utf-8")
    (docs_dir / "ignored.pyc").write_text("binary", encoding="utf-8")

    loader = DirectoryLoader(docs_dir, recursive=True)
    docs = loader.load()

    assert len(docs) == 3
    filenames = {d.metadata["filename"] for d in docs}
    assert filenames == {"file1.txt", "file2.md", "file3.html"}


def test_pdf_loader_missing_dependency(tmp_path: Path):
    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content")
    loader = PDFLoader(dummy_pdf)
    with patch.dict(sys.modules, {"pypdf": None}):
        with pytest.raises(ImportError, match="pypdf"):
            loader.load()


def test_pdf_loader_file_not_found(tmp_path: Path):
    loader = PDFLoader(tmp_path / "non_existent.pdf")
    with patch.dict(sys.modules, {"pypdf": MagicMock()}):
        with pytest.raises(FileNotFoundError, match="File not found"):
            loader.load()


@pytest.mark.asyncio
async def test_rag_engine_ingest_loader(tmp_path: Path):
    file = tmp_path / "kb.md"
    file.write_text("# Knowledge Base\nVector databases allow fast semantic search.", encoding="utf-8")

    engine = RAGEngine(llm_client=DummyLLM())
    loader = MarkdownLoader(file)

    added_ids = await engine.ingest_loader(loader, collection="test_kb")
    assert len(added_ids) >= 1

    results = await engine.retrieve("semantic search", collection="test_kb")
    assert len(results) >= 1
    assert "Vector databases allow fast semantic search" in results[0].document.content
