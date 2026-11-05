"""Okapi BM25 sparse keyword search and Reciprocal Rank Fusion (RRF)."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Optional

from common_framework.rag.models import Document, SearchResult


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric terms."""
    return re.findall(r"\b\w+\b", text.lower())


class BM25Index:
    """Zero-dependency pure-Python implementation of Okapi BM25 for sparse keyword search."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._documents: dict[str, Document] = {}
        self._doc_lengths: dict[str, int] = {}
        self._doc_frequencies: Counter[str] = Counter()
        self._term_frequencies: dict[str, Counter[str]] = {}
        self._avg_doc_len: float = 0.0

    def add_documents(self, documents: list[Document]) -> None:
        """Index a batch of documents for BM25 keyword scoring."""
        for doc in documents:
            tokens = tokenize(doc.content)
            self._documents[doc.id] = doc
            self._doc_lengths[doc.id] = len(tokens)

            tf = Counter(tokens)
            self._term_frequencies[doc.id] = tf

            # Update document frequency for unique terms in this doc
            for term in set(tokens):
                self._doc_frequencies[term] += 1

        total_length = sum(self._doc_lengths.values())
        self._avg_doc_len = total_length / len(self._doc_lengths) if self._doc_lengths else 0.0

    def delete(self, document_ids: list[str]) -> None:
        """Remove documents from the BM25 index."""
        for doc_id in document_ids:
            if doc_id in self._documents:
                doc = self._documents.pop(doc_id)
                self._doc_lengths.pop(doc_id, None)
                tf = self._term_frequencies.pop(doc_id, Counter())
                for term in tf:
                    self._doc_frequencies[term] -= 1
                    if self._doc_frequencies[term] <= 0:
                        del self._doc_frequencies[term]

        total_length = sum(self._doc_lengths.values())
        self._avg_doc_len = total_length / len(self._doc_lengths) if self._doc_lengths else 0.0

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Score indexed documents against query terms using Okapi BM25."""
        if not self._documents or not query:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        num_docs = len(self._documents)
        scores: dict[str, float] = {}

        for term in query_tokens:
            df = self._doc_frequencies.get(term, 0)
            if df == 0:
                continue

            # Standard IDF with smoothing to prevent negative weights
            idf = math.log((num_docs - df + 0.5) / (df + 0.5) + 1.0)

            for doc_id, doc in self._documents.items():
                if filter_metadata:
                    if not all(doc.metadata.get(k) == v for k, v in filter_metadata.items()):
                        continue

                tf = self._term_frequencies[doc_id].get(term, 0)
                if tf == 0:
                    continue

                doc_len = self._doc_lengths.get(doc_id, 0)
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / (self._avg_doc_len or 1.0)))
                term_score = idf * (tf * (self.k1 + 1.0)) / (denom or 1.0)
                scores[doc_id] = scores.get(doc_id, 0.0) + term_score

        if not scores:
            return []

        # Normalize BM25 scores to [0.0, 1.0] relative to max score
        max_score = max(scores.values()) or 1.0
        results: list[SearchResult] = []
        for doc_id, raw_score in scores.items():
            normalized = min(1.0, max(0.0, raw_score / max_score))
            results.append(SearchResult(document=self._documents[doc_id], score=normalized))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]


def reciprocal_rank_fusion(
    dense_results: list[SearchResult],
    sparse_results: list[SearchResult],
    k: int = 60,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
    top_k: int = 4,
) -> list[SearchResult]:
    """Combine dense and sparse search rankings using Reciprocal Rank Fusion (RRF).

    Formula: RRF_score(d) = sum( weight / (k + rank(d)) )
    """
    rrf_scores: dict[str, float] = {}
    doc_map: dict[str, Document] = {}

    # Accumulate dense rankings
    for rank, res in enumerate(dense_results, start=1):
        doc_id = res.document.id
        doc_map[doc_id] = res.document
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (dense_weight / (k + rank))

    # Accumulate sparse (BM25) rankings
    for rank, res in enumerate(sparse_results, start=1):
        doc_id = res.document.id
        doc_map[doc_id] = res.document
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (sparse_weight / (k + rank))

    if not rrf_scores:
        return []

    # Normalize RRF scores relative to theoretical maximum possible score
    max_possible = (dense_weight / (k + 1)) + (sparse_weight / (k + 1))
    fused: list[SearchResult] = []

    for doc_id, raw_rrf in rrf_scores.items():
        normalized_score = min(1.0, max(0.0, raw_rrf / max_possible))
        fused.append(SearchResult(document=doc_map[doc_id], score=normalized_score))

    fused.sort(key=lambda x: x.score, reverse=True)
    return fused[:top_k]
