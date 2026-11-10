"""Lightweight RAG Triad Evaluation Suite (FEAT-09)."""

from __future__ import annotations

from typing import Any, Optional, Sequence, Union
from pydantic import BaseModel, Field

from common_framework.eval.hallucination import HallucinationDetector, _STOP_WORDS
from common_framework.rag.bm25 import tokenize
from common_framework.rag.models import Document


class RAGTriadScore(BaseModel):
    """Evaluation result assessing Context Relevance, Groundedness, and Answer Relevance."""

    context_relevance: float = Field(ge=0.0, le=1.0, description="How relevant retrieved context is to the query")
    groundedness: float = Field(ge=0.0, le=1.0, description="How faithfully the answer is supported by context")
    answer_relevance: float = Field(ge=0.0, le=1.0, description="How directly the answer answers the user query")
    composite_score: float = Field(ge=0.0, le=1.0, description="Weighted aggregate quality score")
    passed: bool = Field(description="Whether composite score meets the passing threshold")
    details: dict[str, Any] = Field(default_factory=dict)


class RAGTriadEvaluator:
    """Zero-dependency evaluation engine assessing RAG generation quality and faithfulness."""

    def __init__(
        self,
        hallucination_detector: Optional[HallucinationDetector] = None,
        pass_threshold: float = 0.60,
        weights: Optional[tuple[float, float, float]] = None,
    ) -> None:
        self.detector = hallucination_detector or HallucinationDetector()
        self.pass_threshold = pass_threshold
        # Weights for (context_relevance, groundedness, answer_relevance)
        self.weights = weights or (0.30, 0.40, 0.30)

    def _extract_context_text(
        self,
        context: Union[str, Sequence[str], Sequence[Document]],
    ) -> str:
        if isinstance(context, str):
            return context
        texts: list[str] = []
        for item in context:
            if isinstance(item, Document):
                texts.append(item.content)
            elif isinstance(item, str):
                texts.append(item)
        return " ".join(texts)

    def _calculate_context_relevance(self, query: str, context_text: str) -> float:
        """Evaluate content term coverage of query terms in the retrieved context."""
        all_q_tokens = tokenize(query.lower())
        if not all_q_tokens or not context_text:
            return 0.0

        content_q = [t for t in all_q_tokens if t not in _STOP_WORDS]
        active_q = content_q if content_q else all_q_tokens

        ctx_lower = context_text.lower()
        ctx_tokens = set(tokenize(ctx_lower))

        matched = [t for t in active_q if t in ctx_tokens]
        token_coverage = len(matched) / len(active_q)

        phrase_bonus = 0.0
        if len(query.strip()) > 10 and query.lower().strip() in ctx_lower:
            phrase_bonus = 0.25

        return min(1.0, round(token_coverage + phrase_bonus, 3))

    def _calculate_answer_relevance(self, query: str, answer: str) -> float:
        """Evaluate whether the answer directly aligns with the query keywords and intent."""
        all_q = tokenize(query.lower())
        all_a = tokenize(answer.lower())

        if not all_q or not all_a:
            return 0.0

        content_q = set(t for t in all_q if t not in _STOP_WORDS)
        content_a = set(t for t in all_a if t not in _STOP_WORDS)

        active_q = content_q if content_q else set(all_q)
        active_a = content_a if content_a else set(all_a)

        overlap = active_q.intersection(active_a)
        score = len(overlap) / len(active_q)

        # Penalize extremely terse answers (<3 words) for detailed questions
        if len(answer.split()) < 3 and len(query.split()) > 4:
            score *= 0.5

        return min(1.0, round(score, 3))

    def evaluate(
        self,
        query: str,
        context: Union[str, Sequence[str], Sequence[Document]],
        answer: str,
    ) -> RAGTriadScore:
        """Run the complete RAG Triad assessment on a single generation interaction."""
        ctx_text = self._extract_context_text(context)

        # 1. Context Relevance
        context_rel = self._calculate_context_relevance(query, ctx_text)

        # 2. Groundedness (Faithfulness) via HallucinationDetector
        report = self.detector.detect(answer=answer, context=ctx_text)
        groundedness = report.overall_grounding_score

        # 3. Answer Relevance
        answer_rel = self._calculate_answer_relevance(query, answer)

        # Weighted composite score
        w_ctx, w_grnd, w_ans = self.weights
        composite = (w_ctx * context_rel) + (w_grnd * groundedness) + (w_ans * answer_rel)
        composite = round(composite, 3)

        passed = composite >= self.pass_threshold and report.is_grounded

        return RAGTriadScore(
            context_relevance=context_rel,
            groundedness=groundedness,
            answer_relevance=answer_rel,
            composite_score=composite,
            passed=passed,
            details={
                "unsupported_claims": report.unsupported_claims,
                "hallucination_rate": report.hallucination_rate,
                "is_grounded": report.is_grounded,
            },
        )

    def evaluate_batch(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate a batch of evaluation records and produce aggregate benchmark metrics."""
        scores: list[RAGTriadScore] = []
        for rec in records:
            score = self.evaluate(
                query=rec["query"],
                context=rec.get("context", ""),
                answer=rec["answer"],
            )
            scores.append(score)

        if not scores:
            return {
                "count": 0,
                "pass_rate": 0.0,
                "mean_context_relevance": 0.0,
                "mean_groundedness": 0.0,
                "mean_answer_relevance": 0.0,
                "mean_composite_score": 0.0,
                "scores": [],
            }

        n = len(scores)
        passed_count = sum(1 for s in scores if s.passed)

        return {
            "count": n,
            "pass_rate": round(passed_count / n, 3),
            "mean_context_relevance": round(sum(s.context_relevance for s in scores) / n, 3),
            "mean_groundedness": round(sum(s.groundedness for s in scores) / n, 3),
            "mean_answer_relevance": round(sum(s.answer_relevance for s in scores) / n, 3),
            "mean_composite_score": round(sum(s.composite_score for s in scores) / n, 3),
            "scores": scores,
        }
