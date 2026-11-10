"""Comprehensive tests for RAG Triad Evaluation and Hallucination Detection (EPIC-06 / FEAT-09 / TASK-18)."""

import pytest

from common_framework.eval.hallucination import (
    HallucinationDetector,
    HallucinationReport,
)
from common_framework.eval.triad import (
    RAGTriadEvaluator,
    RAGTriadScore,
)
from common_framework.rag.models import Document


# ---------------------------------------------------------------------------
# Hallucination Detection Tests (TASK-18)
# ---------------------------------------------------------------------------


def test_hallucination_detector_grounded_answer():
    detector = HallucinationDetector()
    context = (
        "PostgreSQL is a powerful, open-source object-relational database system "
        "with over 35 years of active development. It uses SQL language combined with many features."
    )
    answer = (
        "PostgreSQL is an open-source object-relational database system. "
        "It features over 35 years of active development and uses the SQL language."
    )

    report: HallucinationReport = detector.detect(answer=answer, context=context)

    assert report.is_grounded is True
    assert report.overall_grounding_score >= 0.70
    assert len(report.unsupported_claims) == 0
    assert report.hallucination_rate == 0.0


def test_hallucination_detector_unsupported_claims():
    detector = HallucinationDetector()
    context = "The Apollo 11 mission landed astronauts on the Moon in July 1969."
    # Fabricated / hallucinated claim
    answer = (
        "The Apollo 11 mission landed astronauts on the Moon in July 1969. "
        "The spacecraft was piloted by Albert Einstein and landed on Mars in 1985."
    )

    report = detector.detect(answer=answer, context=context)

    assert report.is_grounded is False
    assert len(report.unsupported_claims) >= 1
    assert any("Mars" in c or "Einstein" in c for c in report.unsupported_claims)
    assert report.hallucination_rate > 0.0


def test_hallucination_detector_document_objects():
    detector = HallucinationDetector()
    docs = [
        Document(content="FastAPI is built on top of Starlette and Pydantic.", id="doc1"),
        Document(content="It provides native asynchronous support with high performance.", id="doc2"),
    ]
    answer = "FastAPI is built on Starlette and Pydantic with native asynchronous support."

    report = detector.detect(answer=answer, context=docs)
    assert report.is_grounded is True
    assert report.overall_grounding_score > 0.60


def test_hallucination_detector_empty_inputs():
    detector = HallucinationDetector()
    assert detector.detect(answer="", context="Some context").is_grounded is True
    assert detector.detect(answer="Some answer", context="").is_grounded is False


# ---------------------------------------------------------------------------
# RAG Triad Evaluation Tests (FEAT-09)
# ---------------------------------------------------------------------------


def test_rag_triad_evaluator_ideal_interaction():
    evaluator = RAGTriadEvaluator()
    query = "What framework is FastAPI built on top of?"
    context = "FastAPI is a modern web framework built on top of Starlette and Pydantic."
    answer = "FastAPI is built on top of Starlette and Pydantic."

    score: RAGTriadScore = evaluator.evaluate(query=query, context=context, answer=answer)

    assert score.passed is True
    assert score.context_relevance > 0.70
    assert score.groundedness > 0.70
    assert score.answer_relevance > 0.60
    assert score.composite_score >= 0.70
    assert score.details["is_grounded"] is True


def test_rag_triad_evaluator_irrelevant_context():
    evaluator = RAGTriadEvaluator()
    query = "How do I configure OAuth2 in FastAPI?"
    # Retrieved context is totally irrelevant (e.g. biology)
    context = "Photosynthesis is the process used by plants to convert light energy into chemical energy."
    answer = "OAuth2 is configured in FastAPI using security utilities."

    score = evaluator.evaluate(query=query, context=context, answer=answer)

    # Context relevance should be near 0
    assert score.context_relevance < 0.20
    # Because context didn't support the answer, groundedness fails
    assert score.groundedness < 0.40
    assert score.passed is False


def test_rag_triad_evaluator_irrelevant_answer():
    evaluator = RAGTriadEvaluator()
    query = "What is the capital of Canada?"
    context = "Ottawa is the capital city of Canada, located in Ontario."
    # Answer completely ignores the question
    answer = "The weather today in Tokyo is sunny and pleasant."

    score = evaluator.evaluate(query=query, context=context, answer=answer)

    assert score.answer_relevance < 0.15
    assert score.passed is False


def test_rag_triad_batch_evaluation():
    evaluator = RAGTriadEvaluator()
    records = [
        {
            "query": "Who created Python?",
            "context": "Python was created by Guido van Rossum and released in 1991.",
            "answer": "Guido van Rossum created Python in 1991.",
        },
        {
            "query": "What is Docker?",
            "context": "Docker is a platform for developing, shipping, and running applications in containers.",
            "answer": "Docker runs applications inside isolated containers.",
        },
        {
            "query": "Explain photosynthesis.",
            "context": "SQLite is a C-language library that implements a small, fast SQL database engine.",
            "answer": "Photosynthesis converts light into chemical sugar.",
        },
    ]

    batch_summary = evaluator.evaluate_batch(records)

    assert batch_summary["count"] == 3
    assert len(batch_summary["scores"]) == 3
    # 2 passed, 1 failed due to irrelevant context
    assert 0.60 <= batch_summary["pass_rate"] <= 0.70
    assert batch_summary["mean_composite_score"] > 0.50
