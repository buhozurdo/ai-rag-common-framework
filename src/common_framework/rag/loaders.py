"""Native document loaders and ingestion utilities for RAG pipelines.

Zero-bloat, stdlib-first document loaders supporting Text, Markdown, CSV,
HTML, Directories, and optional PDF parsing.
"""

from __future__ import annotations

import csv
import html.parser
import io
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence, Union

from common_framework.rag.models import Document


class BaseLoader(ABC):
    """Abstract base class for all document loaders."""

    @abstractmethod
    def load(self) -> list[Document]:
        """Load documents synchronously into memory."""
        raise NotImplementedError

    def load_stream(self) -> Iterator[Document]:
        """Yield documents iteratively for memory-efficient streaming."""
        yield from self.load()


class TextLoader(BaseLoader):
    """Zero-dependency loader for plain text files with robust encoding fallback."""

    def __init__(
        self,
        file_path: Union[str, Path],
        encoding: str = "utf-8",
        autodetect_encoding: bool = True,
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.encoding = encoding
        self.autodetect_encoding = autodetect_encoding

    def _read_content(self) -> str:
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")

        encodings = [self.encoding]
        if self.autodetect_encoding and self.encoding != "utf-8":
            encodings.append("utf-8")
        if self.autodetect_encoding:
            encodings.extend(["utf-8-sig", "latin-1", "cp1252"])

        for enc in encodings:
            try:
                with open(self.file_path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue

        # Final binary fallback with replacement
        with open(self.file_path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")

    def load(self) -> list[Document]:
        text = self._read_content()
        size = self.file_path.stat().st_size if self.file_path.exists() else len(text)
        metadata: dict[str, Any] = {
            "source": str(self.file_path),
            "filename": self.file_path.name,
            "extension": self.file_path.suffix.lower(),
            "file_size": size,
        }
        return [Document(content=text, metadata=metadata)]


class MarkdownLoader(TextLoader):
    """Markdown document loader with frontmatter metadata extraction and header parsing."""

    def __init__(
        self,
        file_path: Union[str, Path],
        encoding: str = "utf-8",
        extract_frontmatter: bool = True,
        extract_title: bool = True,
    ) -> None:
        super().__init__(file_path=file_path, encoding=encoding)
        self.extract_frontmatter = extract_frontmatter
        self.extract_title = extract_title

    def _parse_frontmatter(self, text: str) -> tuple[dict[str, Any], str]:
        """Extract simple key-value YAML frontmatter if delimited by '---'."""
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].strip() == "---":
            fm_lines: list[str] = []
            content_start = -1
            for idx, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    content_start = idx + 1
                    break
                fm_lines.append(line)

            if content_start != -1:
                fm_dict: dict[str, Any] = {}
                for f_line in fm_lines:
                    if ":" in f_line:
                        key, val = f_line.split(":", 1)
                        fm_dict[key.strip()] = val.strip().strip("\"'")
                body = "\n".join(lines[content_start:])
                return fm_dict, body

        return {}, text

    def load(self) -> list[Document]:
        raw_text = self._read_content()
        metadata: dict[str, Any] = {
            "source": str(self.file_path),
            "filename": self.file_path.name,
            "extension": self.file_path.suffix.lower(),
            "file_size": len(raw_text),
        }

        body = raw_text
        if self.extract_frontmatter:
            fm, body = self._parse_frontmatter(raw_text)
            metadata.update(fm)

        if self.extract_title and "title" not in metadata:
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    metadata["title"] = stripped[2:].strip()
                    break

        return [Document(content=body.strip(), metadata=metadata)]


class CSVLoader(BaseLoader):
    """CSV document loader converting rows into structured document records."""

    def __init__(
        self,
        file_path: Union[str, Path],
        source_column: Optional[str] = None,
        metadata_columns: Optional[Sequence[str]] = None,
        delimiter: str = ",",
        encoding: str = "utf-8",
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.source_column = source_column
        self.metadata_columns = set(metadata_columns) if metadata_columns else None
        self.delimiter = delimiter
        self.encoding = encoding

    def load(self) -> list[Document]:
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")

        documents: list[Document] = []
        with open(self.file_path, "r", encoding=self.encoding, errors="replace") as f:
            reader = csv.DictReader(f, delimiter=self.delimiter)
            for row_idx, row in enumerate(reader, start=1):
                # Clean row items
                row_dict = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}

                if self.source_column and self.source_column in row_dict:
                    content = row_dict[self.source_column]
                else:
                    content = "\n".join(f"{k}: {v}" for k, v in row_dict.items())

                meta: dict[str, Any] = {
                    "source": str(self.file_path),
                    "filename": self.file_path.name,
                    "row": row_idx,
                }

                if self.metadata_columns:
                    for k, v in row_dict.items():
                        if k in self.metadata_columns and k != self.source_column:
                            meta[k] = v
                else:
                    for k, v in row_dict.items():
                        if k != self.source_column:
                            meta[k] = v

                documents.append(Document(content=content, metadata=meta))

        return documents


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Stdlib HTML parser that extracts clean visible text and title."""

    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []
        self._title_pieces: list[str] = []
        self._in_title = False
        self._in_skip_tag = False
        self._skip_tags = {"script", "style", "noscript", "svg", "head"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self._skip_tags:
            self._in_skip_tag = True
        elif tag_lower == "title":
            self._in_title = True
        elif tag_lower in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self._skip_tags:
            self._in_skip_tag = False
        elif tag_lower == "title":
            self._in_title = False
        elif tag_lower in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_pieces.append(data)
        elif not self._in_skip_tag:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        lines = [line.strip() for line in raw.splitlines()]
        # Collapse multiple blank lines
        cleaned_lines = []
        for line in lines:
            if line:
                cleaned_lines.append(line)
            elif cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
        return "\n".join(cleaned_lines).strip()

    def get_title(self) -> str:
        return "".join(self._title_pieces).strip()


class HTMLLoader(BaseLoader):
    """Zero-dependency HTML document loader utilizing stdlib html.parser."""

    def __init__(
        self,
        file_path: Union[str, Path],
        encoding: str = "utf-8",
        extract_title: bool = True,
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.encoding = encoding
        self.extract_title = extract_title

    def load(self) -> list[Document]:
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")

        with open(self.file_path, "r", encoding=self.encoding, errors="replace") as f:
            raw_html = f.read()

        parser = _HTMLTextExtractor()
        parser.feed(raw_html)
        text = parser.get_text()
        title = parser.get_title()

        metadata: dict[str, Any] = {
            "source": str(self.file_path),
            "filename": self.file_path.name,
            "extension": self.file_path.suffix.lower(),
            "file_size": len(raw_html),
        }
        if self.extract_title and title:
            metadata["title"] = title

        return [Document(content=text, metadata=metadata)]


class DirectoryLoader(BaseLoader):
    """Recursive directory loader discovering and parsing files via extension matching."""

    _DEFAULT_LOADERS: dict[str, type[BaseLoader]] = {
        ".txt": TextLoader,
        ".text": TextLoader,
        ".log": TextLoader,
        ".md": MarkdownLoader,
        ".markdown": MarkdownLoader,
        ".csv": CSVLoader,
        ".html": HTMLLoader,
        ".htm": HTMLLoader,
    }

    def __init__(
        self,
        dir_path: Union[str, Path],
        glob: str = "**/*",
        recursive: bool = True,
        custom_loaders: Optional[dict[str, type[BaseLoader]]] = None,
        exclude_patterns: Optional[Sequence[str]] = None,
    ) -> None:
        self.dir_path = Path(dir_path).resolve()
        self.glob = glob
        self.recursive = recursive
        self.loaders = dict(self._DEFAULT_LOADERS)
        if custom_loaders:
            self.loaders.update(custom_loaders)
        self.exclude_patterns = exclude_patterns or [".git", "__pycache__", "node_modules", ".venv"]

    def _is_excluded(self, path: Path) -> bool:
        path_str = str(path)
        for pattern in self.exclude_patterns:
            if pattern in path_str:
                return True
        return False

    def load(self) -> list[Document]:
        if not self.dir_path.exists() or not self.dir_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {self.dir_path}")

        documents: list[Document] = []
        search_iter = self.dir_path.glob(self.glob) if not self.recursive else self.dir_path.rglob(self.glob)

        for file_path in sorted(search_iter):
            if not file_path.is_file() or self._is_excluded(file_path):
                continue

            ext = file_path.suffix.lower()
            loader_cls = self.loaders.get(ext)
            if loader_cls is not None:
                try:
                    loader = loader_cls(file_path)
                    documents.extend(loader.load())
                except Exception:
                    # Continue loading other valid files
                    continue

        return documents


class PDFLoader(BaseLoader):
    """Optional PDF document loader relying on 'pypdf' if installed."""

    def __init__(
        self,
        file_path: Union[str, Path],
        extract_pages: bool = True,
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.extract_pages = extract_pages

    def load(self) -> list[Document]:
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")

        try:
            import pypdf
        except ImportError as exc:
            raise ImportError(
                "PDFLoader requires 'pypdf' to be installed. "
                "Install it using: pip install pypdf"
            ) from exc

        documents: list[Document] = []
        reader = pypdf.PdfReader(str(self.file_path))
        num_pages = len(reader.pages)

        if self.extract_pages:
            for page_num, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                metadata: dict[str, Any] = {
                    "source": str(self.file_path),
                    "filename": self.file_path.name,
                    "page": page_num,
                    "total_pages": num_pages,
                }
                documents.append(Document(content=text.strip(), metadata=metadata))
        else:
            full_text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            metadata = {
                "source": str(self.file_path),
                "filename": self.file_path.name,
                "total_pages": num_pages,
            }
            documents.append(Document(content=full_text.strip(), metadata=metadata))

        return documents
