"""Configuration Doctor & Environment Diagnostics (TASK-19)."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from common_framework.core.config import Settings, get_settings


@dataclass
class DiagnosticCheck:
    """Individual system or configuration check result."""

    name: str
    status: str  # "PASS", "WARN", "FAIL"
    message: str
    recommendation: Optional[str] = None


@dataclass
class DoctorReport:
    """Complete diagnostic health report."""

    checks: list[DiagnosticCheck] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(c.status == "FAIL" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "WARN" for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        passed = sum(1 for c in self.checks if c.status == "PASS")
        warns = sum(1 for c in self.checks if c.status == "WARN")
        fails = sum(1 for c in self.checks if c.status == "FAIL")
        return {
            "summary": {
                "total": len(self.checks),
                "passed": passed,
                "warnings": warns,
                "failures": fails,
                "healthy": fails == 0,
            },
            "checks": [asdict(c) for c in self.checks],
        }

    def format_text(self) -> str:
        lines: list[str] = [
            "=" * 60,
            " AI RAG Common Framework — Configuration & Health Doctor",
            "=" * 60,
        ]
        status_symbols = {
            "PASS": "[\033[32mPASS\033[0m]" if sys.stdout.isatty() else "[PASS]",
            "WARN": "[\033[33mWARN\033[0m]" if sys.stdout.isatty() else "[WARN]",
            "FAIL": "[\033[31mFAIL\033[0m]" if sys.stdout.isatty() else "[FAIL]",
        }

        for check in self.checks:
            sym = status_symbols.get(check.status, f"[{check.status}]")
            lines.append(f"{sym} {check.name}: {check.message}")
            if check.recommendation:
                lines.append(f"       Recommendation: {check.recommendation}")

        summary = self.to_dict()["summary"]
        lines.append("-" * 60)
        lines.append(
            f"Summary: {summary['passed']} passed, {summary['warnings']} warnings, "
            f"{summary['failures']} failures (Total: {summary['total']})"
        )
        if summary["failures"] == 0:
            lines.append("Status: HEALTHY — Ready for AI workflows.")
        else:
            lines.append("Status: ATTENTION REQUIRED — Fix critical failures above.")
        lines.append("=" * 60)
        return "\n".join(lines)


def _check_python_version() -> DiagnosticCheck:
    major, minor = sys.version_info.major, sys.version_info.minor
    if major == 3 and minor >= 11:
        return DiagnosticCheck(
            name="Python Version",
            status="PASS",
            message=f"Python {major}.{minor}.{sys.version_info.micro} detected (>= 3.11 required)",
        )
    return DiagnosticCheck(
        name="Python Version",
        status="FAIL",
        message=f"Python {major}.{minor} detected. Minimum required is 3.11",
        recommendation="Upgrade Python to version 3.11 or higher.",
    )


def _check_settings(settings: Settings) -> DiagnosticCheck:
    try:
        env = getattr(settings, "framework_env", "development")
        provider = getattr(settings, "default_provider", "gemini")
        return DiagnosticCheck(
            name="Settings Schema",
            status="PASS",
            message=f"Settings loaded successfully (env='{env}', default_provider='{provider}')",
        )
    except Exception as exc:
        return DiagnosticCheck(
            name="Settings Schema",
            status="FAIL",
            message=f"Failed to load framework settings: {exc}",
            recommendation="Review environment variables and configuration files.",
        )


def _check_api_keys(settings: Settings) -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []

    # Check Gemini
    gemini_key = settings.gemini_api_key.get_secret_value() if getattr(settings, "gemini_api_key", None) else ""
    if gemini_key:
        masked = f"{gemini_key[:4]}...{gemini_key[-4:]}" if len(gemini_key) > 8 else "***"
        checks.append(DiagnosticCheck(
            name="Google Gemini API Key",
            status="PASS",
            message=f"Configured ({masked})",
        ))
    else:
        checks.append(DiagnosticCheck(
            name="Google Gemini API Key",
            status="WARN",
            message="Not configured",
            recommendation="Set GEMINI_API_KEY if calling Google Gemini models.",
        ))

    # Check OpenAI
    openai_key = settings.openai_api_key.get_secret_value() if getattr(settings, "openai_api_key", None) else ""
    if openai_key and not openai_key.startswith("sk-placeholder"):
        masked = f"{openai_key[:4]}...{openai_key[-4:]}" if len(openai_key) > 8 else "***"
        checks.append(DiagnosticCheck(
            name="OpenAI API Key",
            status="PASS",
            message=f"Configured ({masked})",
        ))
    else:
        checks.append(DiagnosticCheck(
            name="OpenAI API Key",
            status="WARN",
            message="Not configured or placeholder value detected",
            recommendation="Set OPENAI_API_KEY if calling OpenAI models.",
        ))

    # Check Anthropic
    anthropic_key = settings.anthropic_api_key.get_secret_value() if getattr(settings, "anthropic_api_key", None) else ""
    if anthropic_key and not anthropic_key.startswith("sk-ant-placeholder"):
        masked = f"{anthropic_key[:6]}...{anthropic_key[-4:]}" if len(anthropic_key) > 10 else "***"
        checks.append(DiagnosticCheck(
            name="Anthropic API Key",
            status="PASS",
            message=f"Configured ({masked})",
        ))
    else:
        checks.append(DiagnosticCheck(
            name="Anthropic API Key",
            status="WARN",
            message="Not configured",
            recommendation="Set ANTHROPIC_API_KEY if calling Claude models.",
        ))

    # Check Cohere
    cohere_key = getattr(settings, "cohere_api_key", None)
    cohere_val = cohere_key.get_secret_value() if cohere_key else ""
    if cohere_val:
        checks.append(DiagnosticCheck(
            name="Cohere API Key",
            status="PASS",
            message="Configured",
        ))
    else:
        checks.append(DiagnosticCheck(
            name="Cohere API Key",
            status="WARN",
            message="Not configured",
            recommendation="Set COHERE_API_KEY if using Cohere cross-encoder rerankers.",
        ))

    return checks


def _check_local_endpoint(settings: Settings, check_network: bool) -> DiagnosticCheck:
    base_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
    if not check_network:
        return DiagnosticCheck(
            name="Local LLM / Ollama Reachability",
            status="PASS",
            message=f"Network check skipped for {base_url}",
        )

    try:
        with httpx.Client(timeout=1.0) as client:
            resp = client.get(f"{base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                return DiagnosticCheck(
                    name="Local LLM / Ollama Reachability",
                    status="PASS",
                    message=f"Reachable at {base_url} (HTTP 200)",
                )
            return DiagnosticCheck(
                name="Local LLM / Ollama Reachability",
                status="WARN",
                message=f"Endpoint responded with HTTP {resp.status_code} at {base_url}",
                recommendation="Verify Ollama/LocalAI server health.",
            )
    except Exception:
        return DiagnosticCheck(
            name="Local LLM / Ollama Reachability",
            status="WARN",
            message=f"No local service detected at {base_url}",
            recommendation="If using local models, start Ollama with 'ollama serve'.",
        )


def _check_storage_permissions() -> DiagnosticCheck:
    test_path = Path(".doctor_test_write.tmp")
    try:
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink()
        return DiagnosticCheck(
            name="Filesystem Write Access",
            status="PASS",
            message="Working directory is writable for vector persistence and cache",
        )
    except Exception as exc:
        return DiagnosticCheck(
            name="Filesystem Write Access",
            status="FAIL",
            message=f"Cannot write to working directory: {exc}",
            recommendation="Ensure the process has write permissions in the working directory.",
        )


def _check_optional_dependencies() -> list[DiagnosticCheck]:
    checks: list[DiagnosticCheck] = []
    
    # FastAPI & Uvicorn (REST API)
    has_fastapi = importlib.util.find_spec("fastapi") is not None
    has_uvicorn = importlib.util.find_spec("uvicorn") is not None
    if has_fastapi and has_uvicorn:
        checks.append(DiagnosticCheck(
            name="HTTP REST API Dependencies",
            status="PASS",
            message="fastapi and uvicorn installed",
        ))
    else:
        checks.append(DiagnosticCheck(
            name="HTTP REST API Dependencies",
            status="WARN",
            message=f"Missing: {'fastapi ' if not has_fastapi else ''}{'uvicorn' if not has_uvicorn else ''}",
            recommendation="Install with 'pip install ai-rag-common-framework[api]'.",
        ))

    # MCP Protocol
    has_mcp = importlib.util.find_spec("mcp") is not None
    if has_mcp:
        checks.append(DiagnosticCheck(
            name="Model Context Protocol (MCP)",
            status="PASS",
            message="mcp package installed",
        ))
    else:
        checks.append(DiagnosticCheck(
            name="Model Context Protocol (MCP)",
            status="WARN",
            message="mcp package not installed",
            recommendation="Install with 'pip install ai-rag-common-framework[mcp]'.",
        ))

    return checks


def run_doctor(
    settings: Optional[Settings] = None,
    check_network: bool = True,
) -> DoctorReport:
    """Run full configuration and environmental diagnostic checks."""
    effective_settings = settings or get_settings()
    report = DoctorReport()

    # 1. Python version
    report.checks.append(_check_python_version())

    # 2. Settings validation
    report.checks.append(_check_settings(effective_settings))

    # 3. API Keys
    report.checks.extend(_check_api_keys(effective_settings))

    # 4. Local Ollama/LLM reachability
    report.checks.append(_check_local_endpoint(effective_settings, check_network=check_network))

    # 5. Storage / Filesystem
    report.checks.append(_check_storage_permissions())

    # 6. Optional dependencies
    report.checks.extend(_check_optional_dependencies())

    return report
