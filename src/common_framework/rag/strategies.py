"""Multi-Query & Hypothetical Document Embeddings (HyDE) Strategies (FEAT-17)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence, TYPE_CHECKING

from common_framework.core.llm import BaseLLMClient, ChatMessage, LLMFactory, Role
from common_framework.rag.models import Document, SearchResult

if TYPE_CHECKING:
    from common_framework.rag.engine import RAGEngine

logger = logging.getLogger("common_framework.rag.strategies")


def multi_list_rrf(
    ranked_lists: Sequence[Sequence[SearchResult]],
    k: int = 60,
    weights: Optional[Sequence[float]] = None,
    top_k: int = 4,
) -> list[SearchResult]:
    """Fuse multiple ranked result lists using Generalized Reciprocal Rank Fusion (RRF).

    Formula:
        RRF_score(d) = sum( weight_i / (k + rank_i(d)) ) for all lists i containing d.

    Args:
        ranked_lists: Sequence of SearchResult lists, one per query variation or retrieval pipeline.
        k: Smoothing constant to balance top versus lower rank importance (default: 60).
        weights: Optional importance weights for each list. Defaults to uniform 1.0 weights.
        top_k: Number of fused results to return.

    Returns:
        Deduplicated and score-normalized SearchResults sorted by descending fused score.

    Raises:
        ValueError: If k < 1 or weights are invalid.
    """
    if k < 1:
        raise ValueError(f"RRF smoothing parameter k must be >= 1, got {k}")

    valid_lists = [lst for lst in ranked_lists if lst]
    if not valid_lists:
        return []

    num_lists = len(ranked_lists)
    if weights is None:
        effective_weights = [1.0] * num_lists
    else:
        if len(weights) != num_lists:
            raise ValueError(f"Length of weights ({len(weights)}) must match ranked_lists ({num_lists})")
        if any(w < 0.0 for w in weights):
            raise ValueError("All RRF weights must be non-negative")
        if all(w == 0.0 for w in weights):
            raise ValueError("At least one list weight must be positive")
        effective_weights = list(weights)

    rrf_scores: dict[str, float] = {}
    raw_scores: dict[str, float] = {}
    doc_map: dict[str, Document] = {}

    for list_idx, result_list in enumerate(ranked_lists):
        w = effective_weights[list_idx]
        if w <= 0.0:
            continue
        for rank, item in enumerate(result_list, start=1):
            doc_id = item.document.id
            doc_map[doc_id] = item.document
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (w / (k + rank))
            raw_scores[doc_id] = raw_scores.get(doc_id, 0.0) + (w * item.score)

    if not rrf_scores:
        return []

    # Maximum possible score is if a document were ranked #1 across all positive-weight lists
    max_possible = sum(w / (k + 1) for w in effective_weights if w > 0.0)
    if max_possible <= 0.0:
        return []

    fused_items: list[tuple[float, float, Document]] = []
    for doc_id, raw_score in rrf_scores.items():
        normalized_score = min(1.0, max(0.0, raw_score / max_possible))
        fused_items.append((normalized_score, raw_scores.get(doc_id, 0.0), doc_map[doc_id]))

    fused_items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [SearchResult(document=doc, score=round(score, 4)) for score, _, doc in fused_items[:top_k]]


class BaseRetrievalStrategy(ABC):
    """Abstract base class for advanced multi-stage query retrieval strategies."""

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        engine: RAGEngine,
        top_k: int = 4,
        collection: str = "default",
        filter_metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Execute the advanced retrieval strategy using the provided RAGEngine."""
        pass

    def retrieve_sync(
        self,
        query: str,
        engine: RAGEngine,
        top_k: int = 4,
        collection: str = "default",
        filter_metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Synchronously execute the retrieval strategy."""
        try:
            asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    self.retrieve(
                        query=query,
                        engine=engine,
                        top_k=top_k,
                        collection=collection,
                        filter_metadata=filter_metadata,
                        **kwargs,
                    ),
                ).result()
        except RuntimeError:
            return asyncio.run(
                self.retrieve(
                    query=query,
                    engine=engine,
                    top_k=top_k,
                    collection=collection,
                    filter_metadata=filter_metadata,
                    **kwargs,
                )
            )


_MULTI_QUERY_PROMPT_DEFAULT = """You are an AI language model assistant. Your task is to generate {num_queries} different versions or perspectives of the given user query to retrieve relevant documents from a vector database. By generating multiple perspectives on the user query, your goal is to help overcome distance-metric and phrasing limitations.

Provide these alternative queries separated by newlines, with no introductory or concluding remarks.
Original query: {query}"""


class MultiQueryStrategy(BaseRetrievalStrategy):
    """Multi-query retrieval strategy expanding queries into multiple viewpoints and fusing with RRF."""

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        num_queries: int = 3,
        prompt_template: Optional[str] = None,
        include_original: bool = True,
        rrf_k: int = 60,
    ) -> None:
        self.llm_client = llm_client
        self.num_queries = max(1, num_queries)
        self.prompt_template = prompt_template or _MULTI_QUERY_PROMPT_DEFAULT
        self.include_original = include_original
        self.rrf_k = rrf_k

    def _get_llm(self, engine: RAGEngine) -> BaseLLMClient:
        return self.llm_client or engine.llm_client or LLMFactory.create()

    async def generate_queries(self, query: str, llm: BaseLLMClient) -> list[str]:
        """Generate multiple distinct query reformulations using the LLM."""
        formatted_prompt = self.prompt_template.format(
            num_queries=self.num_queries,
            query=query,
        )
        messages = [
            ChatMessage(
                role=Role.USER,
                content=formatted_prompt,
            )
        ]

        try:
            response = await llm.complete(messages, temperature=0.7)
            raw_lines = response.content.strip().splitlines()
            cleaned_queries: list[str] = []
            for line in raw_lines:
                clean = re.sub(r"^\s*(?:\d+[\.\)]|[-*•])\s*", "", line).strip()
                if clean and len(clean) > 3 and clean.lower() != query.lower():
                    cleaned_queries.append(clean)

            # Limit to requested number
            variations = cleaned_queries[: self.num_queries]
        except Exception as exc:
            logger.warning("Failed to generate multi-query variations via LLM: %s", exc)
            variations = []

        queries: list[str] = []
        if self.include_original:
            queries.append(query)
        for var in variations:
            if var not in queries:
                queries.append(var)

        # Fallback to single original query if no variations produced
        return queries if queries else [query]

    async def retrieve(
        self,
        query: str,
        engine: RAGEngine,
        top_k: int = 4,
        collection: str = "default",
        filter_metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Run parallel retrievals across query variations and fuse with RRF."""
        llm = self._get_llm(engine)
        queries = await self.generate_queries(query, llm)

        # Perform parallel searches across all query variants (passing strategy=None to prevent recursion)
        tasks = [
            engine.retrieve(
                query=q,
                top_k=top_k * 2,
                collection=collection,
                filter_metadata=filter_metadata,
                strategy=None,
                **kwargs,
            )
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks, return_exceptions=False)

        # Fuse ranked lists
        fused = multi_list_rrf(all_results, k=self.rrf_k, top_k=top_k)

        # If engine has a reranker, apply cross-encoder reranking over the candidate set
        if engine.reranker is not None and fused:
            docs = [r.document for r in fused]
            return await engine.reranker.rerank(query=query, documents=docs, top_k=top_k)

        return fused[:top_k]


_HYDE_PROMPT_DEFAULT = """Please write a short, highly informative passage that answers the following question.
Write directly and factually as if this passage is an excerpt from an authoritative document or textbook.
Do not mention that this passage is hypothetical or fabricated.

Question: {query}
Passage:"""


class HyDEStrategy(BaseRetrievalStrategy):
    """Hypothetical Document Embeddings (HyDE) strategy.

    Synthesizes a hypothetical answer document to place the query representation
    in the document vector space, bridging the query-passage vocabulary gap.
    """

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        prompt_template: Optional[str] = None,
        include_original_query: bool = True,
        hypothetical_weight: float = 0.7,
        original_weight: float = 0.3,
        rrf_k: int = 60,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_template = prompt_template or _HYDE_PROMPT_DEFAULT
        self.include_original_query = include_original_query
        self.hypothetical_weight = hypothetical_weight
        self.original_weight = original_weight
        self.rrf_k = rrf_k

    def _get_llm(self, engine: RAGEngine) -> BaseLLMClient:
        return self.llm_client or engine.llm_client or LLMFactory.create()

    async def generate_hypothetical_document(self, query: str, llm: BaseLLMClient) -> str:
        """Generate a hypothetical document passage answering the query."""
        formatted_prompt = self.prompt_template.format(query=query)
        messages = [
            ChatMessage(
                role=Role.USER,
                content=formatted_prompt,
            )
        ]
        try:
            response = await llm.complete(messages, temperature=0.3)
            doc_text = response.content.strip()
            return doc_text if doc_text else query
        except Exception as exc:
            logger.warning("Failed to generate hypothetical document via LLM: %s", exc)
            return query

    async def retrieve(
        self,
        query: str,
        engine: RAGEngine,
        top_k: int = 4,
        collection: str = "default",
        filter_metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Embed hypothetical passage and retrieve documents matching document-space distribution."""
        llm = self._get_llm(engine)
        hypo_doc = await self.generate_hypothetical_document(query, llm)

        candidate_k = top_k * 2 if self.include_original_query or engine.reranker is not None else top_k

        # Retrieve using hypothetical document (executing dense or hybrid search without strategy recursion)
        hypo_results = await engine.retrieve(
            query=hypo_doc,
            top_k=candidate_k,
            collection=collection,
            filter_metadata=filter_metadata,
            strategy=None,
            **kwargs,
        )

        if not self.include_original_query:
            if engine.reranker is not None and hypo_results:
                docs = [r.document for r in hypo_results]
                return await engine.reranker.rerank(query=query, documents=docs, top_k=top_k)
            return hypo_results[:top_k]

        # Retrieve using original query as baseline
        original_results = await engine.retrieve(
            query=query,
            top_k=candidate_k,
            collection=collection,
            filter_metadata=filter_metadata,
            strategy=None,
            **kwargs,
        )

        # Fuse hypothetical search and original query search using weighted RRF
        fused = multi_list_rrf(
            ranked_lists=[hypo_results, original_results],
            weights=[self.hypothetical_weight, self.original_weight],
            k=self.rrf_k,
            top_k=top_k,
        )

        if engine.reranker is not None and fused:
            docs = [r.document for r in fused]
            return await engine.reranker.rerank(query=query, documents=docs, top_k=top_k)

        return fused[:top_k]
