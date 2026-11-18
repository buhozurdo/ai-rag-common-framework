"""Unit tests for BugAuditGuardrail and PreCompletionGuardrail (Pre-Implementation Verification)."""

import asyncio
from pathlib import Path
import pytest

from common_framework.guardrails.bug_audit import (
    AuditSeverity,
    BugAuditGuardrail,
    BugFinding,
    PreCompletionGuardrail,
    VerificationResult,
)
from common_framework.guardrails.sanitizer import PIISanitizer, PIIType, RedactionStrategy
from common_framework.agents.react import ReActAgent
from common_framework.agents.supervisor import AgentSupervisor, AgentBus
from common_framework.core.llm import BaseLLMClient, ChatResponse, TokenUsage
from common_framework.tools.base import tool


class DummyLLM(BaseLLMClient):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self.responses = list(responses)
        self.call_count = 0

    async def complete(self, messages, **kwargs):
        resp = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return ChatResponse(content=resp, model="mock-model", usage=TokenUsage())

    async def structured_complete(self, messages, response_model, **kwargs):
        pass


def test_bug_audit_guardrail_detects_syntax_errors():
    """Verify BugAuditGuardrail detects invalid Python syntax."""
    bad_code = "def invalid_func(\n  print('missing closing paren'"
    engine = BugAuditGuardrail()
    findings = engine.scan_code(bad_code)

    assert len(findings) >= 1
    syntax_findings = [f for f in findings if f.rule_id == "RULE-SYNTAX"]
    assert len(syntax_findings) == 1
    assert syntax_findings[0].severity == AuditSeverity.CRITICAL


def test_bug_audit_guardrail_detects_unbounded_dos_fields():
    """Verify BugAuditGuardrail detects Pydantic fields without max_length."""
    vulnerable_code = """
from pydantic import BaseModel, Field

class BadPayload(BaseModel):
    query: str = Field(..., min_length=1)
"""
    engine = BugAuditGuardrail()
    findings = engine.scan_code(vulnerable_code)

    dos_findings = [f for f in findings if f.rule_id == "RULE-SEC-DOS-PAYLOAD"]
    assert len(dos_findings) == 1
    assert dos_findings[0].severity == AuditSeverity.HIGH
    assert "max_length" in dos_findings[0].remediation


def test_bug_audit_guardrail_pre_completion_check():
    """Verify pre_completion_check blocks on critical and high severity defects."""
    vulnerable_code = """
import requests

def fetch_data(user_url):
    return requests.get(user_url)
"""
    engine = BugAuditGuardrail()
    res = engine.pre_completion_check(vulnerable_code)
    assert res.passed is False
    assert "Pre-completion audit failed" in res.reason


@pytest.mark.asyncio
async def test_react_agent_with_pre_completion_guardrail_revises_answer():
    """Verify ReActAgent rejects invalid final answer and revises when completion_guardrail fails."""
    # Turn 0: LLM returns insecure code block in final answer
    # Turn 1: LLM receives guardrail rejection in scratchpad and returns secure code
    llm = DummyLLM([
        "Thought: Done.\nFinal Answer: ```python\ndef run(\n```",  # Syntax error
        "Thought: I noticed the previous code had syntax error, fixing.\nFinal Answer: All clear and valid.",
    ])

    guardrail = PreCompletionGuardrail()
    agent = ReActAgent(
        llm_client=llm,
        tools=[],
        completion_guardrail=guardrail,
        max_iterations=5,
    )

    resp = await agent.run("Generate function")
    assert resp.success is True
    assert "All clear and valid" in resp.output


@pytest.mark.asyncio
async def test_supervisor_with_pre_completion_guardrail_revises_answer():
    """Verify AgentSupervisor rejects final answer and revises when completion_guardrail fails."""
    llm = DummyLLM([
        "FINAL_ANSWER: ```python\ndef broken(\n```",
        "FINAL_ANSWER: The implementation is completed and verified.",
    ])

    guardrail = PreCompletionGuardrail()
    supervisor = AgentSupervisor(
        agents={"worker": ("Dummy worker", lambda t: "ok")},
        llm_client=llm,
        completion_guardrail=guardrail,
        max_steps=5,
    )

    resp = await supervisor.run("Build module")
    assert "completed and verified" in resp.final_answer
    assert resp.iterations == 2


def test_pii_sanitizer_salted_hash_and_phone_coverage():
    """Verify PIISanitizer uses salted HMAC and matches 7-digit local phone numbers."""
    sanitizer = PIISanitizer()
    text = "Call me at 555-1234 or visit 192.168.1.1."
    res = sanitizer.sanitize(text, strategy=RedactionStrategy.HASH)

    assert "555-1234" not in res.sanitized_text
    assert "[HASH:" in res.sanitized_text

    # Verify 7-digit phone entity was detected
    entities = sanitizer.find_entities(text)
    phone_entities = [e for e in entities if e.entity_type == PIIType.PHONE]
    assert len(phone_entities) == 1
    assert phone_entities[0].value == "555-1234"


def test_agent_bus_records_history_and_prevents_clobbering():
    """Verify AgentBus records history per key across multiple turns."""
    bus = AgentBus()
    bus.set("researcher", "Initial findings: market is growing.")
    bus.set("researcher", "Updated findings: competitor launched product.")

    # Current value is the latest
    assert "competitor" in bus.get("researcher")

    # History retains all intermediate outputs
    history = bus.get_history("researcher")
    assert len(history) == 2
    assert "growing" in history[0]
    assert "competitor" in history[1]
