"""Automated Bug Search, Static Analysis, and Pre-Completion Implementation Guardrail.

Provides comprehensive scanning, vulnerability detection, and automated verification
before completing any code implementation or releasing agent artifacts.
"""

from __future__ import annotations

import ast
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union
from pydantic import BaseModel, Field


class AuditSeverity(str, Enum):
    """Severity classification for detected defects and vulnerabilities."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class BugFinding(BaseModel):
    """Detailed audit finding produced by code inspection."""

    id: str = Field(description="Unique finding identifier, e.g. BUG-SSRF-01")
    rule_id: str = Field(description="Category rule identifier")
    title: str = Field(description="Summary title of the defect")
    severity: AuditSeverity = Field(description="Severity classification")
    file_path: str = Field(default="", description="Path to the file where defect was found")
    line_number: int = Field(default=0, description="1-indexed line number")
    description: str = Field(description="Detailed explanation of the flaw and exploitability")
    remediation: str = Field(description="Concrete steps or code pattern required to fix")
    snippet: Optional[str] = Field(default=None, description="Source code snippet exhibiting the flaw")


class VerificationResult(BaseModel):
    """Outcome of pre-completion verification."""

    passed: bool = Field(description="Whether the code or response passed all audit checks")
    findings: list[BugFinding] = Field(default_factory=list, description="List of detected defects")
    reason: Optional[str] = Field(default=None, description="Summary failure reason if rejected")
    summary: dict[str, int] = Field(default_factory=dict, description="Counts by severity")


# ---------------------------------------------------------------------------
# Core Static Analysis Rules
# ---------------------------------------------------------------------------

class BugAuditGuardrail:
    """Static analysis and heuristic code inspection engine detecting known vulnerability patterns."""

    def __init__(
        self,
        strict: bool = False,
        blocked_severities: Optional[Sequence[AuditSeverity]] = None,
    ) -> None:
        """Initialize audit guardrail.

        Args:
            strict: If True, any finding (including medium/low) fails verification.
            blocked_severities: Specific severities that cause verification failure.
                Defaults to [AuditSeverity.CRITICAL, AuditSeverity.HIGH].
        """
        self.strict = strict
        self.blocked_severities = set(
            blocked_severities or [AuditSeverity.CRITICAL, AuditSeverity.HIGH]
        )

    def scan_code(self, code: str, file_path: str = "", is_python: bool = True) -> list[BugFinding]:
        """Analyze a string of source code against all built-in audit rules."""
        findings: list[BugFinding] = []
        if not code.strip():
            return findings

        lines = code.split("\n")

        # 1. SSRF: requests/httpx calls with unvalidated host/IP
        for idx, line in enumerate(lines, start=1):
            if re.search(r"\b(httpx|requests)\.(get|post|put|delete|patch|request)\s*\(", line):
                # Check if base_url validation is missing in nearby scope
                if "validate_provider_base_url" not in code and "socket.getaddrinfo" not in code:
                    findings.append(
                        BugFinding(
                            id=f"AUDIT-SSRF-{idx}",
                            rule_id="RULE-SEC-SSRF",
                            title="Direct HTTP request without SSRF / DNS rebinding validation",
                            severity=AuditSeverity.HIGH,
                            file_path=file_path,
                            line_number=idx,
                            description="Direct outbound HTTP request detected without provider base URL validation or socket DNS resolution.",
                            remediation="Validate URL using validate_provider_base_url() or resolve DNS via socket.getaddrinfo() before dispatch.",
                            snippet=line.strip(),
                        )
                    )

        # 2. Silent Exception Swallowing (except Exception: pass)
        for idx, line in enumerate(lines, start=1):
            if re.search(r"except\s+(?:Exception|BaseException)?\s*:\s*(?:pass|\.\.\.)\s*(?:#.*)?$", line.strip()):
                findings.append(
                    BugFinding(
                        id=f"AUDIT-EXC-SWALLOW-{idx}",
                        rule_id="RULE-CODE-SILENT-EXC",
                        title="Silent exception swallowing with bare pass",
                        severity=AuditSeverity.MEDIUM,
                        file_path=file_path,
                        line_number=idx,
                        description="Exceptions are silently discarded without logging, metrics recording, or fallback handling.",
                        remediation="Log exception at logger.warning() / logger.debug() or raise informative FrameworkError.",
                        snippet=line.strip(),
                    )
                )

        # 3. Unbounded Pydantic String/List in API Routes (DoS Vector)
        if "fastapi" in code or "APIRouter" in code or "BaseModel" in code:
            for idx, line in enumerate(lines, start=1):
                # Pattern: Field(..., min_length=1) without max_length
                if "Field(" in line and "min_length=" in line and "max_length" not in line:
                    findings.append(
                        BugFinding(
                            id=f"AUDIT-DOS-UNBOUNDED-{idx}",
                            rule_id="RULE-SEC-DOS-PAYLOAD",
                            title="Unbounded Field length without max_length in request schema",
                            severity=AuditSeverity.HIGH,
                            file_path=file_path,
                            line_number=idx,
                            description="Pydantic Field specifies minimum length but lacks max_length constraint, exposing API to Memory/Payload DoS.",
                            remediation="Add max_length constraint, e.g. Field(..., min_length=1, max_length=10000).",
                            snippet=line.strip(),
                        )
                    )

        # 4. Unanchored FINAL_ANSWER protocol in Supervisor/Agent reasoning
        if "bug_audit.py" not in file_path and "FINAL_ANSWER:" in code and 'if "FINAL_ANSWER:" in' in code:
            for idx, line in enumerate(lines, start=1):
                if 'if "FINAL_ANSWER:" in' in line:
                    findings.append(
                        BugFinding(
                            id=f"AUDIT-AGENT-FINAL-ANSWER-{idx}",
                            rule_id="RULE-AGENT-PROTOCOL",
                            title="Unanchored substring check for FINAL_ANSWER",
                            severity=AuditSeverity.MEDIUM,
                            file_path=file_path,
                            line_number=idx,
                            description="Simple substring match for 'FINAL_ANSWER:' causes premature agent termination if mentioned in reasoning thoughts.",
                            remediation="Anchor match using regex: re.search(r'(?:^|\\n)FINAL_ANSWER:\\s*(.+)', content).",
                            snippet=line.strip(),
                        )
                    )

        # 5. Path Traversal: Path.resolve() without is_relative_to check
        if "Path(" in code and ".resolve()" in code:
            for idx, line in enumerate(lines, start=1):
                if ".resolve()" in line and "filepath" in line.lower() and "is_relative_to" not in code:
                    findings.append(
                        BugFinding(
                            id=f"AUDIT-PATH-TRAVERSAL-{idx}",
                            rule_id="RULE-SEC-PATH-TRAVERSAL",
                            title="Path resolution without containment verification (is_relative_to)",
                            severity=AuditSeverity.HIGH,
                            file_path=file_path,
                            line_number=idx,
                            description="Path is resolved without asserting is_relative_to() against base directory, permitting path traversal.",
                            remediation="Assert candidate.is_relative_to(base_dir) before reading or writing files.",
                            snippet=line.strip(),
                        )
                    )

        # 6. Syntax / AST Parse Verification (only for Python code)
        if is_python:
            try:
                ast.parse(code)
            except SyntaxError as syn_err:
                findings.append(
                    BugFinding(
                        id=f"AUDIT-SYNTAX-ERR-{syn_err.lineno or 1}",
                        rule_id="RULE-SYNTAX",
                        title="Python syntax error in source implementation",
                        severity=AuditSeverity.CRITICAL,
                        file_path=file_path,
                        line_number=syn_err.lineno or 1,
                        description=f"SyntaxError: {syn_err.msg}",
                        remediation="Fix syntax error before deploying or testing code.",
                        snippet=str(syn_err.text or "").strip(),
                    )
                )

        return findings

    def scan_file(self, file_path: Union[str, Path]) -> list[BugFinding]:
        """Read and scan a single file from the filesystem."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return []
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as exc:
            return [
                BugFinding(
                    id="AUDIT-READ-ERR",
                    rule_id="RULE-FILE-READ",
                    title=f"Could not read file {p.name}",
                    severity=AuditSeverity.LOW,
                    file_path=str(p),
                    line_number=1,
                    description=str(exc),
                    remediation="Ensure file exists and has valid UTF-8 encoding.",
                )
            ]
        return self.scan_code(content, file_path=str(p))

    def scan_directory(
        self,
        root_path: Union[str, Path],
        extensions: tuple[str, ...] = (".py",),
        recursive: bool = True,
    ) -> list[BugFinding]:
        """Scan all matching files within directory."""
        root = Path(root_path).resolve()
        all_findings: list[BugFinding] = []
        if not root.is_dir():
            return all_findings

        pattern = "**/*" if recursive else "*"
        for path in root.glob(pattern):
            if path.is_file() and path.suffix in extensions:
                # Skip virtual environments and hidden directories
                if any(part.startswith(".") or part in (".venv", "venv", "__pycache__", "build", "dist") for part in path.parts):
                    continue
                all_findings.extend(self.scan_file(path))

        return all_findings

    def pre_completion_check(
        self,
        code_or_diff: str,
        strict: Optional[bool] = None,
        is_python: bool = True,
    ) -> VerificationResult:
        """Run verification on code or implementation output before completing."""
        is_strict = strict if strict is not None else self.strict
        findings = self.scan_code(code_or_diff, is_python=is_python)

        counts: dict[str, int] = {}
        blocking_findings: list[BugFinding] = []

        for f in findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
            if is_strict or f.severity in self.blocked_severities:
                blocking_findings.append(f)

        passed = len(blocking_findings) == 0
        reason = None
        if not passed:
            top_issue = blocking_findings[0]
            reason = (
                f"Pre-completion audit failed with {len(blocking_findings)} blocking issue(s). "
                f"Primary defect: [{top_issue.id}] {top_issue.title} (line {top_issue.line_number}): {top_issue.remediation}"
            )

        return VerificationResult(
            passed=passed,
            findings=findings,
            reason=reason,
            summary=counts,
        )


class PreCompletionGuardrail:
    """Pluggable agent guardrail validating final responses and code blocks before completion."""

    def __init__(self, audit_engine: Optional[BugAuditGuardrail] = None) -> None:
        self.engine = audit_engine or BugAuditGuardrail()

    async def verify(self, output: str, **context: Any) -> VerificationResult:
        """Verify an agent output or code implementation before finalizing."""
        # Extract python code blocks if present
        code_blocks = re.findall(r"```(?:python)?\s*(.*?)\s*```", output, re.DOTALL)
        if code_blocks:
            merged_code = "\n\n".join(b.strip() for b in code_blocks if b.strip())
            return self.engine.pre_completion_check(merged_code, is_python=True)

        looks_like_code = any(
            re.search(rf"\b{kw}\b", output)
            for kw in ("def", "class", "import", "async def", "return")
        )
        return self.engine.pre_completion_check(output, is_python=bool(looks_like_code))
