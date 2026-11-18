"""Ephemeral Code Interpreter & Tool Sandboxing (FEAT-21).

Provides secure, isolated Python execution environments for agents and tools with timeouts and secret scrubbing.
"""

from __future__ import annotations

import asyncio
import ast
import concurrent.futures
import os
import sys
import tempfile
import time
from typing import Any, Callable, Optional
from pydantic import BaseModel, Field

from common_framework.tools.base import Tool


class ExecutionResult(BaseModel):
    """Result of code execution in the sandboxed environment."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    duration_ms: float = 0.0
    truncated: bool = False

    @property
    def output(self) -> str:
        """Convenience accessor returning stdout, or stderr if stdout is empty."""
        return self.stdout if self.stdout.strip() else self.stderr

    @property
    def success(self) -> bool:
        """Whether execution finished normally with exit code 0 and without timeout."""
        return self.exit_code == 0 and not self.timed_out


# Sensitive environment key substrings to scrub from the child process
SENSITIVE_ENV_SUBSTRINGS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "AUTH",
    "CREDENTIAL",
    "PRIVATE",
    "ACCESS",
)

# Standard minimal system variables to preserve for Python to execute cleanly
SAFE_SYSTEM_VARS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "PYTHONPATH",
    "PYTHONHOME",
    "COMSPEC",
    "LANG",
    "LC_ALL",
)


# Security policy: modules and process execution primitives blocked in safe mode
DANGEROUS_MODULES = frozenset({"subprocess", "pty", "multiprocessing", "ctypes", "socket"})
DANGEROUS_ATTRS = frozenset(
    {"system", "popen", "spawnl", "spawnv", "spawnle", "spawnve", "spawnlp", "spawnvp", "kill", "fork"}
)


class CodeInterpreter:
    """Isolated Python code interpreter running code in an ephemeral sandbox."""

    def __init__(
        self,
        timeout: float = 10.0,
        max_output_length: int = 10000,
        scrub_environment: bool = True,
        safe_mode: bool = True,
        custom_env: Optional[dict[str, str]] = None,
        base_dir: Optional[str] = None,
    ) -> None:
        """Initialize the code interpreter sandbox.

        Args:
            timeout: Maximum execution duration in seconds before killing the process.
            max_output_length: Maximum allowed characters in stdout/stderr before truncation.
            scrub_environment: Whether to strip sensitive environment variables (API keys, secrets).
            safe_mode: Whether to statically enforce AST security rules blocking dangerous primitives.
            custom_env: Optional dictionary of environment variables to inject.
            base_dir: Optional directory where ephemeral scratch folders should be created.
        """
        self.timeout = timeout
        self.max_output_length = max_output_length
        self.scrub_environment = scrub_environment
        self.safe_mode = safe_mode
        self.custom_env = dict(custom_env or {})
        self.base_dir = base_dir

    def _validate_code_ast(self, code: str) -> Optional[str]:
        """Verify code does not import dangerous system modules or invoke dangerous process primitives."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Let standard Python runtime handle and report syntax error
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in DANGEROUS_MODULES:
                        return f"Disallowed import '{alias.name}'. Module is blocked in safe sandbox mode."
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in DANGEROUS_MODULES:
                        return f"Disallowed import from '{node.module}'. Module is blocked in safe sandbox mode."
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr in DANGEROUS_ATTRS:
                    return f"Disallowed invocation '{node.func.attr}()'. Process and system execution calls are blocked in safe sandbox mode."
        return None

    def _prepare_environment(self) -> dict[str, str]:
        """Construct sanitized environment variables for child execution."""
        if not self.scrub_environment:
            env = dict(os.environ)
        else:
            env = {}
            for k, v in os.environ.items():
                k_upper = k.upper()
                if k_upper in SAFE_SYSTEM_VARS or not any(s in k_upper for s in SENSITIVE_ENV_SUBSTRINGS):
                    env[k] = v

        # Apply custom environment overrides
        env.update(self.custom_env)
        # Ensure child process stdout is unbuffered utf-8
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _truncate_output(self, text: str) -> tuple[str, bool]:
        """Truncate output if it exceeds max_output_length."""
        if len(text) <= self.max_output_length:
            return text, False
        truncated_text = text[: self.max_output_length] + "\n... [output truncated]"
        return truncated_text, True

    async def run(self, code: str) -> ExecutionResult:
        """Execute Python code asynchronously in an ephemeral subprocess sandbox."""
        if self.safe_mode:
            violation = self._validate_code_ast(code)
            if violation:
                return ExecutionResult(
                    stdout="",
                    stderr=f"SecurityError: {violation}",
                    exit_code=1,
                    timed_out=False,
                    duration_ms=0.0,
                )

        env = self._prepare_environment()
        t0 = time.perf_counter()

        with tempfile.TemporaryDirectory(dir=self.base_dir, prefix="agy_sandbox_") as scratch_dir:
            script_path = os.path.join(scratch_dir, "script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=scratch_dir,
                    env=env,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=self.timeout,
                    )
                    timed_out = False
                    exit_code = proc.returncode if proc.returncode is not None else 0
                except asyncio.TimeoutError:
                    timed_out = True
                    exit_code = -1
                    try:
                        proc.kill()
                        stdout_bytes, stderr_bytes = await proc.communicate()
                    except Exception:
                        stdout_bytes, stderr_bytes = b"", b""

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return ExecutionResult(
                    stdout="",
                    stderr=f"Sandbox execution error: {exc}",
                    exit_code=-1,
                    timed_out=False,
                    duration_ms=round(elapsed_ms, 3),
                )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        clean_stdout, stdout_trunc = self._truncate_output(stdout_str)
        clean_stderr, stderr_trunc = self._truncate_output(stderr_str)

        return ExecutionResult(
            stdout=clean_stdout,
            stderr=clean_stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=round(elapsed_ms, 3),
            truncated=(stdout_trunc or stderr_trunc),
        )

    def run_sync(self, code: str) -> ExecutionResult:
        """Synchronously execute Python code, handling active event loops safely."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(self.run(code))).result()
        return asyncio.run(self.run(code))

    def as_tool(
        self,
        name: str = "code_interpreter",
        description: Optional[str] = None,
    ) -> Tool:
        """Export this code interpreter as a standard framework Tool."""
        tool_desc = description or (
            "Execute Python code in an isolated, sandboxed environment. "
            "Returns stdout, stderr, and exit status. "
            "Use this tool for data analysis, mathematical calculations, and running scripts."
        )

        async def execute_code(code: str) -> dict[str, Any]:
            """Execute Python code in the sandbox.

            Args:
                code: Valid Python code string to execute.
            """
            result = await self.run(code)
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output": result.output,
                "exit_code": result.exit_code,
                "success": result.success,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
            }

        return Tool(fn=execute_code, name=name, description=tool_desc)


# Module-level default instance and tool factory
default_code_interpreter = CodeInterpreter()
code_interpreter_tool = default_code_interpreter.as_tool()
