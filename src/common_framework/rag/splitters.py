from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Iterable, Iterator, Optional

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
        self._split_cache: dict[tuple[int, int], list[str]] = {}

    @abstractmethod
    def split_text_stream(self, text: str) -> Iterator[str]:
        """Lazily split a raw text string into a stream of chunks."""
        pass

    def split_text(self, text: str) -> list[str]:
        """Split a raw text string into a list of chunks."""
        if not text:
            return []

        cache_key = (len(text), hash(text))
        if hasattr(self, "_split_cache"):
            cached = self._split_cache.get(cache_key)
            if cached is not None:
                return list(cached)
        else:
            self._split_cache = {}

        chunks = list(self.split_text_stream(text))
        if len(self._split_cache) >= 128:
            self._split_cache.pop(next(iter(self._split_cache)))
        self._split_cache[cache_key] = chunks
        return list(chunks)

    def split_documents_stream(self, documents: Iterable[Document]) -> Iterator[Document]:
        """Lazily split a stream/iterable of documents into chunked Document objects."""
        for doc in documents:
            for idx, chunk in enumerate(self.split_text(doc.content)):
                meta = dict(doc.metadata)
                meta["parent_id"] = doc.id
                meta["chunk_index"] = idx
                yield Document(content=chunk, metadata=meta)

    def split_documents(self, documents: list[Document]) -> list[Document]:
        """Split a list of documents into chunked Document objects, preserving metadata."""
        return list(self.split_documents_stream(documents))


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

    def split_text_stream(self, text: str) -> Iterator[str]:
        if not text:
            return

        if self.separator == "":
            step = max(1, self.chunk_size - self.chunk_overlap)
            for i in range(0, len(text), step):
                yield text[i : i + self.chunk_size]
            return

        splits = text.split(self.separator)
        current_chunk: list[str] = []
        current_length = 0
        sep_len_val = len(self.separator)

        for piece in splits:
            piece_len = len(piece)
            sep_len = sep_len_val if current_chunk else 0

            if current_length + sep_len + piece_len <= self.chunk_size:
                current_chunk.append(piece)
                current_length += sep_len + piece_len
            else:
                if current_chunk:
                    yield self.separator.join(current_chunk)
                    if self.chunk_overlap > 0:
                        overlap_len = 0
                        start_idx = len(current_chunk)
                        for i in range(len(current_chunk) - 1, -1, -1):
                            item_len = len(current_chunk[i])
                            needed = item_len if start_idx == len(current_chunk) else (item_len + sep_len_val)
                            if overlap_len + needed <= self.chunk_overlap:
                                overlap_len += needed
                                start_idx = i
                            else:
                                break

                        while start_idx < len(current_chunk):
                            lead_sep = sep_len_val if start_idx < len(current_chunk) else 0
                            if overlap_len + lead_sep + piece_len > self.chunk_size:
                                overlap_len -= len(current_chunk[start_idx])
                                start_idx += 1
                                if start_idx < len(current_chunk):
                                    overlap_len -= sep_len_val
                            else:
                                break

                        current_chunk = current_chunk[start_idx:]
                        current_length = overlap_len
                    else:
                        current_chunk = []
                        current_length = 0

                sep_len = sep_len_val if current_chunk else 0
                current_chunk.append(piece)
                current_length += sep_len + piece_len

        if current_chunk:
            yield self.separator.join(current_chunk)


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

    def _split_stream(self, text: str, separators: list[str]) -> Iterator[str]:
        if not text:
            return

        # Find the first separator present in the text
        active_sep = ""
        sep_idx = len(separators) - 1
        for i, sep in enumerate(separators):
            if sep == "":
                active_sep = ""
                sep_idx = i
                break
            if sep in text:
                active_sep = sep
                sep_idx = i
                break

        # Fast path for character splitting when no other separator matches
        if active_sep == "":
            step = max(1, self.chunk_size - self.chunk_overlap)
            for i in range(0, len(text), step):
                chunk = text[i : i + self.chunk_size].strip()
                if chunk:
                    yield chunk
            return

        splits = text.split(active_sep)
        next_separators = separators[sep_idx + 1 :]
        has_next = bool(next_separators)
        active_sep_len = len(active_sep)

        # Merge splits into chunks adhering to chunk_size and chunk_overlap
        current_chunk: list[str] = []
        current_length = 0

        for piece in splits:
            piece_len = len(piece)
            if piece_len > self.chunk_size and has_next:
                # Flush any accumulated text before processing the oversized piece to maintain order
                if current_chunk:
                    chunk_text = active_sep.join(current_chunk).strip()
                    if chunk_text:
                        yield chunk_text
                    current_chunk = []
                    current_length = 0

                # Sub-split oversized piece recursively via generator streaming
                yield from self._split_stream(piece, next_separators)
                continue

            sep_len = active_sep_len if current_chunk else 0

            if current_length + sep_len + piece_len <= self.chunk_size:
                current_chunk.append(piece)
                current_length += sep_len + piece_len
            else:
                if current_chunk:
                    chunk_text = active_sep.join(current_chunk).strip()
                    if chunk_text:
                        yield chunk_text
                    # Handle overlap by taking tail elements from previous chunk via slice
                    if self.chunk_overlap > 0:
                        overlap_len = 0
                        start_idx = len(current_chunk)
                        for i in range(len(current_chunk) - 1, -1, -1):
                            item_len = len(current_chunk[i])
                            needed = item_len if start_idx == len(current_chunk) else (item_len + active_sep_len)
                            if overlap_len + needed <= self.chunk_overlap:
                                overlap_len += needed
                                start_idx = i
                            else:
                                break

                        while start_idx < len(current_chunk):
                            lead_sep = active_sep_len if start_idx < len(current_chunk) else 0
                            if overlap_len + lead_sep + piece_len > self.chunk_size:
                                overlap_len -= len(current_chunk[start_idx])
                                start_idx += 1
                                if start_idx < len(current_chunk):
                                    overlap_len -= active_sep_len
                            else:
                                break

                        current_chunk = current_chunk[start_idx:]
                        current_length = overlap_len
                    else:
                        current_chunk = []
                        current_length = 0

                sep_len = active_sep_len if current_chunk else 0
                current_chunk.append(piece)
                current_length += sep_len + piece_len

        if current_chunk:
            chunk_text = active_sep.join(current_chunk).strip()
            if chunk_text:
                yield chunk_text

    def _split(self, text: str, separators: list[str]) -> list[str]:
        return list(self._split_stream(text, separators))

    def split_text_stream(self, text: str) -> Iterator[str]:
        return self._split_stream(text, self.separators)
