"""Text splitting and chunking strategies."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Optional

from common_framework.rag.models import Document


class BaseTextSplitter(ABC):
    """Abstract base class for all text splitters."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be non-negative, got {chunk_overlap}")
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be strictly less than chunk_size ({chunk_size})"
            )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @abstractmethod
    def split_text(self, text: str) -> list[str]:
        """Split a raw text string into a list of chunks."""
        pass

    def split_documents(self, documents: list[Document]) -> list[Document]:
        """Split a list of documents into chunked Document objects, preserving metadata."""
        chunked_docs: list[Document] = []
        for doc in documents:
            chunks = self.split_text(doc.content)
            for idx, chunk in enumerate(chunks):
                meta = dict(doc.metadata)
                meta["parent_id"] = doc.id
                meta["chunk_index"] = idx
                chunked_docs.append(Document(content=chunk, metadata=meta))
        return chunked_docs


class CharacterTextSplitter(BaseTextSplitter):
    """Splits text using a single character/delimiter."""

    def __init__(
        self,
        separator: str = "\n\n",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> None:
        super().__init__(chunk_size, chunk_overlap)
        self.separator = separator

    def split_text(self, text: str) -> list[str]:
        if not text:
            return []

        splits = text.split(self.separator) if self.separator != "" else list(text)
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        for piece in splits:
            piece_len = len(piece)
            sep_len = len(self.separator) if current_chunk else 0

            if current_length + sep_len + piece_len <= self.chunk_size:
                current_chunk.append(piece)
                current_length += sep_len + piece_len
            else:
                if current_chunk:
                    chunks.append(self.separator.join(current_chunk))
                    if self.chunk_overlap > 0:
                        overlap_elements: list[str] = []
                        overlap_len = 0
                        for item in reversed(current_chunk):
                            item_sep = len(self.separator) if overlap_elements else 0
                            if overlap_len + item_sep + len(item) <= self.chunk_overlap:
                                overlap_elements.insert(0, item)
                                overlap_len += item_sep + len(item)
                            else:
                                break

                        # Trim overlap elements from the left if combined with piece they exceed chunk_size
                        new_sep_len = len(self.separator) if overlap_elements else 0
                        while overlap_elements and (overlap_len + new_sep_len + piece_len > self.chunk_size):
                            popped = overlap_elements.pop(0)
                            overlap_len -= len(popped)
                            if overlap_elements:
                                overlap_len -= len(self.separator)
                            new_sep_len = len(self.separator) if overlap_elements else 0

                        current_chunk = list(overlap_elements)
                        current_length = overlap_len
                    else:
                        current_chunk = []
                        current_length = 0

                sep_len = len(self.separator) if current_chunk else 0
                current_chunk.append(piece)
                current_length += sep_len + piece_len

        if current_chunk:
            chunks.append(self.separator.join(current_chunk))

        return chunks


class RecursiveTextSplitter(BaseTextSplitter):
    """Recursively splits text by prioritizing natural semantic boundaries:

    paragraph breaks ('\\n\\n'), newlines ('\\n'), spaces (' '), and characters ('').
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: Optional[list[str]] = None,
    ) -> None:
        super().__init__(chunk_size, chunk_overlap)
        self.separators = separators or ["\n\n", "\n", " ", ""]

    def _split(self, text: str, separators: list[str]) -> list[str]:
        if not text:
            return []

        # Find the first separator present in the text
        active_sep = separators[-1]
        for sep in separators:
            if sep == "":
                active_sep = ""
                break
            if sep in text:
                active_sep = sep
                break

        splits = text.split(active_sep) if active_sep != "" else list(text)

        # Merge splits into chunks adhering to chunk_size and chunk_overlap
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0
        next_separators = separators[separators.index(active_sep) + 1 :] if active_sep in separators else []

        for piece in splits:
            if len(piece) > self.chunk_size and next_separators:
                # Sub-split oversized piece recursively
                sub_chunks = self._split(piece, next_separators)
                for sub in sub_chunks:
                    chunks.append(sub)
                continue

            piece_len = len(piece)
            sep_len = len(active_sep) if current_chunk else 0

            if current_length + sep_len + piece_len <= self.chunk_size:
                current_chunk.append(piece)
                current_length += sep_len + piece_len
            else:
                if current_chunk:
                    chunk_text = active_sep.join(current_chunk).strip()
                    if chunk_text:
                        chunks.append(chunk_text)
                    # Handle overlap by taking tail elements from previous chunk
                    overlap_elements: list[str] = []
                    overlap_len = 0
                    for item in reversed(current_chunk):
                        if overlap_len + len(item) <= self.chunk_overlap:
                            overlap_elements.insert(0, item)
                            overlap_len += len(item)
                        else:
                            break
                    current_chunk = overlap_elements
                    current_length = sum(len(x) for x in current_chunk)

                current_chunk.append(piece)
                current_length += len(piece)

        if current_chunk:
            chunk_text = active_sep.join(current_chunk).strip()
            if chunk_text:
                chunks.append(chunk_text)

        return chunks

    def split_text(self, text: str) -> list[str]:
        return self._split(text, self.separators)
