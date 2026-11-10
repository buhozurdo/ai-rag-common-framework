"""Evaluation and Hallucination Detection module for AI RAG Common Framework."""

from common_framework.eval.hallucination import (
    HallucinationDetector,
    HallucinationReport,
    SentenceClaim,
)
from common_framework.eval.triad import (
    RAGTriadEvaluator,
    RAGTriadScore,
)

__all__ = [
    "SentenceClaim",
    "HallucinationReport",
    "HallucinationDetector",
    "RAGTriadScore",
    "RAGTriadEvaluator",
]
