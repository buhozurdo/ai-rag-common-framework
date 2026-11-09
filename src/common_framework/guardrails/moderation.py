"""Prompt Injection, Jailbreak Detection, and Guardrail Moderation engine (FEAT-10)."""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from common_framework.guardrails.sanitizer import PIISanitizer, SanitizationResult


class InjectionThreatLevel(str, Enum):
    """Severity classification for prompt injection or policy violations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardrailViolation(BaseModel):
    """Specific guardrail rule or safety boundary breached."""

    category: str
    message: str
    threat_level: InjectionThreatLevel
    snippet: Optional[str] = None


class GuardrailResult(BaseModel):
    """Aggregate safety assessment of candidate user input or model output."""

    is_safe: bool
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    violations: list[GuardrailViolation] = Field(default_factory=list)
    sanitized_text: str

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


# Hardened heuristic regex patterns targeting common prompt injection and jailbreak vectors
_INJECTION_PATTERNS = [
    # 1. Direct instruction overrides
    (
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\s+(all\s+)?(previous|prior|above|system)\s+(instructions|directives|prompts|rules|guidelines)\b",
            re.IGNORECASE,
        ),
        "Instruction override attempt: command instructing model to discard system directives.",
        InjectionThreatLevel.CRITICAL,
        0.95,
    ),
    # 2. Persona hijacking and jailbreak modes
    (
        re.compile(
            r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+(DAN|unfiltered|jailbroken|evil|unrestricted|god\s+mode|developer\s+mode)\b",
            re.IGNORECASE,
        ),
        "Persona hijacking attempt: instructing model to assume an unrestricted or rogue persona.",
        InjectionThreatLevel.HIGH,
        0.85,
    ),
    # 3. Delimiter breakout injections (breaking out of context tags)
    (
        re.compile(
            r"(</context>|</document>|</system>|\[/INST\]|<\|im_end\|>|<\|endoftext\|>)",
            re.IGNORECASE,
        ),
        "Delimiter breakout injection: payload contains boundary tags attempting to break context enclosure.",
        InjectionThreatLevel.CRITICAL,
        0.90,
    ),
    # 4. System prompt extraction probes
    (
        re.compile(
            r"\b(repeat|reveal|print|output|display|show|quote)\s+(your\s+)?(system\s+prompt|initial\s+prompt|internal\s+instructions|hidden\s+rules)\b",
            re.IGNORECASE,
        ),
        "System prompt extraction probe: attempting to exfiltrate secret instructions.",
        InjectionThreatLevel.MEDIUM,
        0.60,
    ),
    # 5. Roleplay escape hooks
    (
        re.compile(
            r"\b(from\s+now\s+on\s+you\s+will|never\s+say\s+no|do\s+not\s+refuse\s+any\s+request)\b",
            re.IGNORECASE,
        ),
        "Roleplay restriction bypass attempt.",
        InjectionThreatLevel.HIGH,
        0.75,
    ),
]


class PromptGuardrail:
    """Enterprise guardrail evaluator screening inputs and outputs for PII and adversarial injections."""

    def __init__(
        self,
        sanitizer: Optional[PIISanitizer] = None,
        block_on_critical: bool = True,
        max_acceptable_risk: float = 0.70,
    ) -> None:
        self.sanitizer = sanitizer or PIISanitizer()
        self.block_on_critical = block_on_critical
        self.max_acceptable_risk = max_acceptable_risk

    def evaluate(self, text: str, redact_pii: bool = True) -> GuardrailResult:
        """Evaluate text for prompt injections and sensitive PII."""
        if not text:
            return GuardrailResult(is_safe=True, risk_score=0.0, sanitized_text=text)

        violations: list[GuardrailViolation] = []
        max_threat_score = 0.0

        # 1. Prompt Injection Scanning
        for pattern, description, threat_level, score in _INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                max_threat_score = max(max_threat_score, score)
                violations.append(
                    GuardrailViolation(
                        category="prompt_injection",
                        message=description,
                        threat_level=threat_level,
                        snippet=match.group(0),
                    )
                )

        # 2. PII Sanitization
        sanitized_text = text
        if redact_pii:
            pii_result = self.sanitizer.sanitize(text)
            sanitized_text = pii_result.sanitized_text
            if pii_result.has_pii:
                types_found = {e.entity_type.value for e in pii_result.entities_found}
                violations.append(
                    GuardrailViolation(
                        category="pii_detected",
                        message=f"PII entities detected and redacted: {', '.join(sorted(types_found))}",
                        threat_level=InjectionThreatLevel.MEDIUM,
                        snippet=None,
                    )
                )
                max_threat_score = max(max_threat_score, 0.40)

        # Determine safety
        is_safe = True
        if self.block_on_critical:
            has_critical = any(v.threat_level == InjectionThreatLevel.CRITICAL for v in violations)
            if has_critical:
                is_safe = False

        if max_threat_score > self.max_acceptable_risk:
            is_safe = False

        return GuardrailResult(
            is_safe=is_safe,
            risk_score=round(max_threat_score, 2),
            violations=violations,
            sanitized_text=sanitized_text,
        )
