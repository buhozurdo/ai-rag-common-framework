"""Statistical and Semantic Hallucination Detection Heuristics (TASK-18)."""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Union
from pydantic import BaseModel, Field

from common_framework.rag.bm25 import tokenize
from common_framework.rag.models import Document


# Common English stop words for informational density filtering
_STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "could", "did", "do", "does", "doing", "down",
    "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hers", "herself", "him", "himself", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me",
    "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on",
    "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out",
    "over", "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "with", "would", "you", "your", "yours", "yourself", "yourselves",
}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _stem(word: str) -> str:
    """Lightweight suffix stripper for morphological matching."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ing"):
        w = w[:-3]
        if len(w) >= 3 and w[-1] == w[-2]:
            w = w[:-1]
    elif len(w) > 3 and w.endswith("ed"):
        w = w[:-2]
        if len(w) >= 3 and w[-1] == w[-2]:
            w = w[:-1]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


class SentenceClaim(BaseModel):
    """Grounding assessment for an individual claim/sentence in generated text."""

    sentence: str
    grounding_score: float = Field(ge=0.0, le=1.0)
    is_supported: bool
    matched_terms: list[str] = Field(default_factory=list)


class HallucinationReport(BaseModel):
    """Comprehensive factual grounding report across all statements in an answer."""

    answer: str
    overall_grounding_score: float = Field(ge=0.0, le=1.0)
    is_grounded: bool
    claims: list[SentenceClaim] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)

    @property
    def hallucination_rate(self) -> float:
        """Percentage of claims in the answer that lack source attribution."""
        if not self.claims:
            return 0.0
        return round(len(self.unsupported_claims) / len(self.claims), 3)


class HallucinationDetector:
    """Heuristic grounding verification engine detecting unsupported statements."""

    def __init__(
        self,
        min_sentence_grounding: float = 0.40,
        min_overall_grounding: float = 0.60,
    ) -> None:
        self.min_sentence_grounding = min_sentence_grounding
        self.min_overall_grounding = min_overall_grounding

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

    def _split_into_sentences(self, text: str) -> list[str]:
        cleaned = text.strip()
        if not cleaned:
            return []
        sentences = _SENTENCE_SPLIT_RE.split(cleaned)
        return [s.strip() for s in sentences if len(s.strip()) > 3]

    def _extract_content_tokens(self, text: str) -> list[str]:
        tokens = tokenize(text.lower())
        return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]

    def _evaluate_sentence(
        self,
        sentence: str,
        context_tokens_set: set[str],
        context_stems_set: set[str],
        context_lower: str,
    ) -> SentenceClaim:
        s_lower = sentence.lower()

        # Exact subphrase match bonus
        if len(s_lower) > 15 and s_lower in context_lower:
            return SentenceClaim(
                sentence=sentence,
                grounding_score=1.0,
                is_supported=True,
                matched_terms=["[EXACT_PHRASE_MATCH]"],
            )

        content_tokens = self._extract_content_tokens(sentence)
        if not content_tokens:
            # Sentence without content words (e.g. "Yes.", "Thank you.") is neutral
            return SentenceClaim(
                sentence=sentence,
                grounding_score=1.0,
                is_supported=True,
                matched_terms=[],
            )

        matched: list[str] = []
        for t in content_tokens:
            stemmed = _stem(t)
            if t in context_tokens_set or stemmed in context_stems_set:
                matched.append(t)
            elif len(stemmed) >= 3 and any(
                cs.startswith(stemmed[:3]) for cs in context_stems_set if len(cs) >= 3
            ):
                matched.append(t)

        coverage = len(matched) / len(content_tokens)

        # Bigram match bonus
        bigram_bonus = 0.0
        if len(content_tokens) >= 2:
            bigrams = [f"{content_tokens[i]} {content_tokens[i+1]}" for i in range(len(content_tokens) - 1)]
            bigrams_matched = sum(1 for bg in bigrams if bg in context_lower)
            bigram_bonus = (bigrams_matched / len(bigrams)) * 0.25

        score = min(1.0, coverage + bigram_bonus)
        is_supported = score >= self.min_sentence_grounding

        return SentenceClaim(
            sentence=sentence,
            grounding_score=round(score, 3),
            is_supported=is_supported,
            matched_terms=matched,
        )

    def detect(
        self,
        answer: str,
        context: Union[str, Sequence[str], Sequence[Document]],
    ) -> HallucinationReport:
        """Evaluate an answer against provided context chunks for hallucinations."""
        raw_context = self._extract_context_text(context)
        context_lower = raw_context.lower()
        context_tokens = set(tokenize(context_lower))
        context_stems = {_stem(ct) for ct in context_tokens}

        sentences = self._split_into_sentences(answer)
        if not sentences:
            return HallucinationReport(
                answer=answer,
                overall_grounding_score=1.0,
                is_grounded=True,
                claims=[],
                unsupported_claims=[],
            )

        claims: list[SentenceClaim] = []
        unsupported: list[str] = []
        scores: list[float] = []

        for sent in sentences:
            claim = self._evaluate_sentence(sent, context_tokens, context_stems, context_lower)
            claims.append(claim)
            scores.append(claim.grounding_score)
            if not claim.is_supported:
                unsupported.append(sent)

        mean_score = sum(scores) / len(scores) if scores else 0.0
        is_grounded = mean_score >= self.min_overall_grounding and len(unsupported) == 0

        return HallucinationReport(
            answer=answer,
            overall_grounding_score=round(mean_score, 3),
            is_grounded=is_grounded,
            claims=claims,
            unsupported_claims=unsupported,
        )
