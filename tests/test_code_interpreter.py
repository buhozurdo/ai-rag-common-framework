"""Tests for Ephemeral Code Interpreter & Tool Sandboxing (FEAT-21)."""

from __future__ import annotations

import asyncio
import os
import pytest

from common_framework.tools.base import Tool
from common_framework.tools.interpreter import (
    CodeInterpreter,
    ExecutionResult,
    code_interpreter_tool,
    default_code_interpreter,
)


@pytest.mark.asyncio
async def test_basic_code_execution() -> None:
    """Test standard Python code execution and stdout capture."""
    interpreter = CodeInterpreter(timeout=5.0)
    code = (
        "import math\n"
        "res = math.sqrt(144)\n"
        "print(f'Result={int(res)}')\n"
    )
    result = await interpreter.run(code)
    assert result.success is True
    assert result.exit_code == 0
    assert result.timed_out is False
    assert "Result=12" in result.stdout
    assert result.stderr == ""
    assert result.output == result.stdout
    assert result.duration_ms > 0.0


@pytest.mark.asyncio
async def test_syntax_error_and_stderr_capture() -> None:
    """Test syntax error produces stderr and non-zero exit code."""
    interpreter = CodeInterpreter(timeout=5.0)
    code = "def invalid_python(: print('fail')"
    result = await interpreter.run(code)
    assert result.success is False
    assert result.exit_code != 0
    assert result.timed_out is False
    assert "SyntaxError" in result.stderr
    assert result.output == result.stderr


@pytest.mark.asyncio
async def test_runtime_exception_capture() -> None:
    """Test runtime exception produces traceback in stderr."""
    interpreter = CodeInterpreter(timeout=5.0)
    code = "raise ValueError('Custom error message')"
    result = await interpreter.run(code)
    assert result.success is False
    assert result.exit_code != 0
    assert "ValueError: Custom error message" in result.stderr


@pytest.mark.asyncio
async def test_execution_timeout() -> None:
    """Test long-running loops or sleep commands trigger timeout."""
    interpreter = CodeInterpreter(timeout=0.4)
    code = (
        "import time\n"
        "time.sleep(2.0)\n"
        "print('Should not reach here')\n"
    )
    result = await interpreter.run(code)
    assert result.timed_out is True
    assert result.success is False
    assert result.exit_code == -1
    assert "Should not reach here" not in result.stdout


@pytest.mark.asyncio
async def test_output_truncation() -> None:
    """Test outputs exceeding max_output_length are cleanly truncated."""
    interpreter = CodeInterpreter(timeout=5.0, max_output_length=200)
    code = "print('A' * 1000)"
    result = await interpreter.run(code)
    assert result.success is True
    assert result.truncated is True
    assert len(result.stdout) < 1000
    assert "... [output truncated]" in result.stdout


@pytest.mark.asyncio
async def test_environment_scrubbing() -> None:
    """Test sensitive environment variables are stripped from the sandbox."""
    os.environ["SECRET_API_TOKEN"] = "super-secret-token"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    os.environ["DATABASE_PASSWORD"] = "dbpass123"

    try:
        interpreter = CodeInterpreter(scrub_environment=True)
        code = (
            "import os\n"
            "keys = list(os.environ.keys())\n"
            "print('HAS_SECRET:', any('SECRET' in k.upper() for k in keys))\n"
            "print('HAS_API_KEY:', any('API_KEY' in k.upper() for k in keys))\n"
            "print('HAS_PASS:', any('PASSWORD' in k.upper() for k in keys))\n"
        )
        result = await interpreter.run(code)
        assert result.success is True
        assert "HAS_SECRET: False" in result.stdout
        assert "HAS_API_KEY: False" in result.stdout
        assert "HAS_PASS: False" in result.stdout
    finally:
        os.environ.pop("SECRET_API_TOKEN", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("DATABASE_PASSWORD", None)


@pytest.mark.asyncio
async def test_custom_environment_injection() -> None:
    """Test custom environment variables are provided to the sandbox."""
    interpreter = CodeInterpreter(
        scrub_environment=True,
        custom_env={"APP_ENV": "sandbox_testing", "BENCHMARK_FLAG": "42"},
    )
    code = (
        "import os\n"
        "print('APP_ENV=' + os.environ.get('APP_ENV', ''))\n"
        "print('FLAG=' + os.environ.get('BENCHMARK_FLAG', ''))\n"
    )
    result = await interpreter.run(code)
    assert result.success is True
    assert "APP_ENV=sandbox_testing" in result.stdout
    assert "FLAG=42" in result.stdout


@pytest.mark.asyncio
async def test_ephemeral_filesystem_isolation() -> None:
    """Test child process creates files in an ephemeral scratch dir that is removed."""
    interpreter = CodeInterpreter(timeout=5.0)
    code = (
        "import os\n"
        "with open('temp_output.txt', 'w') as f:\n"
        "    f.write('ephemeral data')\n"
        "print('CWD:', os.getcwd())\n"
        "print('EXISTS:', os.path.exists('temp_output.txt'))\n"
    )
    result = await interpreter.run(code)
    assert result.success is True
    assert "EXISTS: True" in result.stdout

    # Extract scratch directory path from stdout
    cwd_line = [line for line in result.stdout.splitlines() if line.startswith("CWD:")][0]
    scratch_path = cwd_line.split("CWD:", 1)[1].strip()
    # Verify the scratch directory was completely deleted after context manager exited
    assert not os.path.exists(scratch_path)


def test_run_sync_execution() -> None:
    """Test synchronous execution wrapper run_sync()."""
    interpreter = CodeInterpreter(timeout=5.0)
    result = interpreter.run_sync("print('Sync execution successful')")
    assert isinstance(result, ExecutionResult)
    assert result.success is True
    assert "Sync execution successful" in result.stdout


def test_run_sync_inside_running_loop() -> None:
    """Test run_sync() executes safely via ThreadPoolExecutor inside active loop."""
    interpreter = CodeInterpreter(timeout=5.0)

    async def in_loop() -> ExecutionResult:
        return interpreter.run_sync("print('Inside active loop')")

    res = asyncio.run(in_loop())
    assert res.success is True
    assert "Inside active loop" in res.stdout


@pytest.mark.asyncio
async def test_as_tool_integration() -> None:
    """Test exporting CodeInterpreter as a standard framework Tool."""
    interpreter = CodeInterpreter(timeout=5.0)
    tool_inst = interpreter.as_tool(name="calc_py")

    assert isinstance(tool_inst, Tool)
    assert tool_inst.name == "calc_py"

    tool_res = await tool_inst.execute(code="print(2 + 2)")
    assert isinstance(tool_res, dict)
    assert tool_res["success"] is True
    assert tool_res["exit_code"] == 0
    assert "4" in tool_res["stdout"]
    assert tool_res["timed_out"] is False


def test_default_instances() -> None:
    """Test default module-level instances."""
    assert isinstance(default_code_interpreter, CodeInterpreter)
    assert isinstance(code_interpreter_tool, Tool)
    assert code_interpreter_tool.name == "code_interpreter"
