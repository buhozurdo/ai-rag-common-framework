"""Zero-dependency, high-throughput PII Sanitization and Redaction engine (FEAT-10)."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Optional, Sequence
from pydantic import BaseModel, Field


class PIIType(str, Enum):
    """Categorization of supported Personal Identifiable Information (PII) types."""

    EMAIL = "email"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    SSN = "ssn"
    IP_ADDRESS = "ip_address"
    API_KEY = "api_key"


class RedactionStrategy(str, Enum):
    """Strategies for masking or redacting detected sensitive information."""

    REPLACE = "replace"  # Replaces with a typed placeholder, e.g. [REDACTED_EMAIL]
    MASK = "mask"        # Partially masks the characters, e.g. ****-****-****-1234
    HASH = "hash"        # Replaces with an HMAC/SHA-256 pseudonym, e.g. [HASH:a1b2c3d4]


class PIIEntity(BaseModel):
    """Details of a detected sensitive entity within text."""

    entity_type: PIIType
    value: str
    start: int
    end: int
    replacement: str


class SanitizationResult(BaseModel):
    """Outcome of a text sanitization pass."""

    original_text: str
    sanitized_text: str
    entities_found: list[PIIEntity] = Field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return len(self.entities_found) > 0


# Pre-compiled, hardened regex patterns for ultra-low latency parsing
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b",
    re.IGNORECASE,
)

_PHONE_RE = re.compile(
    r"(?:(?:\+?1\s*(?:[.-]\s*)?)?(?:\(\s*([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9])\s*\)|([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9]))\s*(?:[.-]\s*)?)?([2-9]1[02-9]|[2-9][02-9]1|[2-9][02-9]{2})\s*(?:[.-]\s*)?([0-9]{4})(?:\s*(?:#|x\.?|ext\.?|extension)\s*(\d+))?",
    re.IGNORECASE,
)

_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d{4}[ -]?){3}\d{4}\b|\b\d{15,16}\b"
)

_SSN_RE = re.compile(
    r"\b(?!000|666|9\d{2})\d{3}[ -]?(?!00)\d{2}[ -]?(?!0000)\d{4}\b"
)

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

_API_KEY_RE = re.compile(
    r"\b(?:sk-[a-zA-Z0-9_-]{20,}|sk-ant-[a-zA-Z0-9_-]{20,}|ghp_[a-zA-Z0-9]{36}|AIza[0-9A-Za-z-_]{35}|Bearer\s+[a-zA-Z0-9_\-\.]{25,})\b"
)


def _luhn_checksum_valid(card_number_str: str) -> bool:
    """Validate credit card numbers using the standard Luhn modulo 10 algorithm."""
    digits = [int(c) for c in card_number_str if c.isdigit()]
    if len(digits) not in (13, 14, 15, 16, 19):
        return False

    checksum = 0
    reverse_digits = digits[::-1]
    for idx, d in enumerate(reverse_digits):
        if idx % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


class PIISanitizer:
    """High-throughput PII detection and redaction processor."""

    def __init__(
        self,
        enabled_types: Optional[Sequence[PIIType]] = None,
        default_strategy: RedactionStrategy = RedactionStrategy.REPLACE,
    ) -> None:
        self.enabled_types = set(enabled_types or list(PIIType))
        self.default_strategy = default_strategy

    def _generate_replacement(
        self,
        entity_type: PIIType,
        raw_val: str,
        strategy: RedactionStrategy,
    ) -> str:
        if strategy == RedactionStrategy.REPLACE:
            return f"[REDACTED_{entity_type.value.upper()}]"

        if strategy == RedactionStrategy.HASH:
            digest = hashlib.sha256(raw_val.encode("utf-8")).hexdigest()[:8]
            return f"[HASH:{digest}]"

        if strategy == RedactionStrategy.MASK:
            if entity_type == PIIType.CREDIT_CARD:
                clean_digits = "".join(c for c in raw_val if c.isdigit())
                last_four = clean_digits[-4:] if len(clean_digits) >= 4 else "0000"
                return f"****-****-****-{last_four}"

            if entity_type == PIIType.EMAIL:
                parts = raw_val.split("@")
                if len(parts) == 2 and len(parts[0]) > 1:
                    masked_user = parts[0][0] + "***"
                    return f"{masked_user}@{parts[1]}"
                return "[MASKED_EMAIL]"

            if entity_type == PIIType.SSN:
                clean = "".join(c for c in raw_val if c.isdigit())
                last_four = clean[-4:] if len(clean) >= 4 else "0000"
                return f"***-**-{last_four}"

            if entity_type == PIIType.PHONE:
                clean = "".join(c for c in raw_val if c.isdigit())
                last_four = clean[-4:] if len(clean) >= 4 else "0000"
                return f"***-***-{last_four}"

            if entity_type == PIIType.API_KEY:
                return f"{raw_val[:4]}...[MASKED_KEY]"

            # Generic fallback mask
            return "*" * len(raw_val)

        return f"[REDACTED_{entity_type.value.upper()}]"

    def find_entities(
        self,
        text: str,
        strategy: Optional[RedactionStrategy] = None,
    ) -> list[PIIEntity]:
        """Locate all PII entities present within text without modifying it."""
        if not text:
            return []

        active_strategy = strategy or self.default_strategy
        entities: list[PIIEntity] = []

        # 1. API Keys
        if PIIType.API_KEY in self.enabled_types:
            for match in _API_KEY_RE.finditer(text):
                val = match.group(0)
                entities.append(
                    PIIEntity(
                        entity_type=PIIType.API_KEY,
                        value=val,
                        start=match.start(),
                        end=match.end(),
                        replacement=self._generate_replacement(PIIType.API_KEY, val, active_strategy),
                    )
                )

        # 2. Emails
        if PIIType.EMAIL in self.enabled_types:
            for match in _EMAIL_RE.finditer(text):
                val = match.group(0)
                entities.append(
                    PIIEntity(
                        entity_type=PIIType.EMAIL,
                        value=val,
                        start=match.start(),
                        end=match.end(),
                        replacement=self._generate_replacement(PIIType.EMAIL, val, active_strategy),
                    )
                )

        # 3. Credit Cards (with Luhn validation filter)
        if PIIType.CREDIT_CARD in self.enabled_types:
            for match in _CREDIT_CARD_RE.finditer(text):
                val = match.group(0)
                if _luhn_checksum_valid(val):
                    entities.append(
                        PIIEntity(
                            entity_type=PIIType.CREDIT_CARD,
                            value=val,
                            start=match.start(),
                            end=match.end(),
                            replacement=self._generate_replacement(PIIType.CREDIT_CARD, val, active_strategy),
                        )
                    )

        # 4. SSN
        if PIIType.SSN in self.enabled_types:
            for match in _SSN_RE.finditer(text):
                val = match.group(0)
                entities.append(
                    PIIEntity(
                        entity_type=PIIType.SSN,
                        value=val,
                        start=match.start(),
                        end=match.end(),
                        replacement=self._generate_replacement(PIIType.SSN, val, active_strategy),
                    )
                )

        # 5. IPv4
        if PIIType.IP_ADDRESS in self.enabled_types:
            for match in _IPV4_RE.finditer(text):
                val = match.group(0)
                entities.append(
                    PIIEntity(
                        entity_type=PIIType.IP_ADDRESS,
                        value=val,
                        start=match.start(),
                        end=match.end(),
                        replacement=self._generate_replacement(PIIType.IP_ADDRESS, val, active_strategy),
                    )
                )

        # 6. Phone Numbers
        if PIIType.PHONE in self.enabled_types:
            for match in _PHONE_RE.finditer(text):
                val = match.group(0).strip()
                # Ensure minimum digit count to avoid matching simple numbers
                digits = [c for c in val if c.isdigit()]
                if 10 <= len(digits) <= 15:
                    entities.append(
                        PIIEntity(
                            entity_type=PIIType.PHONE,
                            value=val,
                            start=match.start(),
                            end=match.end(),
                            replacement=self._generate_replacement(PIIType.PHONE, val, active_strategy),
                        )
                    )

        # Deduplicate and sort spans by start index
        entities.sort(key=lambda e: (e.start, -e.end))
        non_overlapping: list[PIIEntity] = []
        last_end = -1
        for e in entities:
            if e.start >= last_end:
                non_overlapping.append(e)
                last_end = e.end

        return non_overlapping

    def sanitize(
        self,
        text: str,
        strategy: Optional[RedactionStrategy] = None,
    ) -> SanitizationResult:
        """Scan text and return sanitized text with all sensitive entities redacted."""
        if not text:
            return SanitizationResult(original_text=text, sanitized_text=text, entities_found=[])

        entities = self.find_entities(text, strategy=strategy)
        if not entities:
            return SanitizationResult(original_text=text, sanitized_text=text, entities_found=[])

        # Replace spans in reverse order to keep indices accurate
        out_chars = list(text)
        for ent in reversed(entities):
            out_chars[ent.start : ent.end] = list(ent.replacement)

        sanitized = "".join(out_chars)
        return SanitizationResult(
            original_text=text,
            sanitized_text=sanitized,
            entities_found=entities,
        )
