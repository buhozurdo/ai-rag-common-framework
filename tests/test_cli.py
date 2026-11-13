"""Tests for Unified CLI, REPL Playground, and Environment Doctor (EPIC-07)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from common_framework import (
    BaseLLMClient,
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    CLIPlaygroundREPL,
    Document,
    InMemoryVectorStore,
    MockEmbeddingProvider,
    RAGEngine,
    Settings,
    TokenUsage,
    ToolRegistry,
    __version__,
    cli_main,
    run_doctor,
    scaffold_project,
    tool,
)


class DummyLLM(BaseLLMClient):
    async def complete(self, messages, **kwargs):
        return ChatResponse(
            content="Olympus City is the capital of Mars.",
            model="dummy-model",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=8, total_tokens=18),
        )

    async def complete_stream(self, messages, **kwargs):
        yield ChatStreamChunk(delta="Olympus City is the capital of Mars.", model="dummy-model")

    async def structured_complete(self, messages, response_model, **kwargs):
        raise NotImplementedError


def test_cli_version(capsys):
    ret = cli_main(["version"])
    assert ret == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out
    assert "Python" in captured.out


def test_cli_help(capsys):
    ret = cli_main([])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Available subcommands" in captured.out or "usage:" in captured.out


def test_doctor_diagnostic_suite():
    settings = Settings(
        framework_env="test",
        openai_api_key="sk-test-valid-key-for-testing",
        anthropic_api_key="sk-ant-test-valid-key",
    )
    report = run_doctor(settings=settings, check_network=False)
    assert len(report.checks) >= 5
    assert not report.has_failures

    data = report.to_dict()
    assert data["summary"]["total"] >= 5
    assert data["summary"]["failures"] == 0
    assert data["summary"]["healthy"] is True

    text = report.format_text()
    assert "AI RAG Common Framework" in text
    assert "HEALTHY" in text


def test_doctor_cli_command(capsys):
    ret = cli_main(["doctor", "--no-network"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Configuration & Health Doctor" in captured.out

    # Test JSON output flag
    ret_json = cli_main(["doctor", "--json", "--no-network"])
    assert ret_json == 0
    json_captured = capsys.readouterr()
    parsed = json.loads(json_captured.out)
    assert "summary" in parsed
    assert "checks" in parsed


def test_scaffold_project(tmp_path: Path):
    dest = tmp_path / "new_rag_app"
    created = scaffold_project(target_dir=dest, project_name="new-rag-app")

    assert len(created) == 3
    assert (dest / "main.py").is_file()
    assert (dest / ".env.example").is_file()
    assert (dest / "README.md").is_file()

    content = (dest / "main.py").read_text(encoding="utf-8")
    assert "create_app" in content
    assert "RAGEngine" in content

    # Running again on existing folder shouldn't overwrite or duplicate
    recreated = scaffold_project(target_dir=dest, project_name="new-rag-app")
    assert len(recreated) == 0


def test_cli_init_command(tmp_path: Path, capsys):
    target_dir = str(tmp_path / "cli_scaffold")
    ret = cli_main(["init", "sample-app", "--dir", target_dir])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Successfully bootstrapped project 'sample-app'" in captured.out
    assert Path(target_dir, "main.py").exists()


def test_repl_commands_and_tool_invocation():
    # Setup test tool
    registry = ToolRegistry()

    @tool(name="calc_add", description="Add two integers.")
    def calc_add(x: int, y: int) -> int:
        return x + y

    registry.register(calc_add)

    # Setup test RAG engine with mock LLM
    dummy_llm = DummyLLM()
    vector_store = InMemoryVectorStore()
    embedding_provider = MockEmbeddingProvider()
    rag_engine = RAGEngine(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        llm_client=dummy_llm,
    )

    outputs: list[str] = []

    def mock_output(text: str) -> None:
        outputs.append(text)

    repl = CLIPlaygroundREPL(
        llm_client=dummy_llm,
        rag_engine=rag_engine,
        tool_registry=registry,
        output_func=mock_output,
    )

    # 1. /help
    assert repl.handle_line("/help") is True
    assert any("Available commands" in o for o in outputs)

    # 2. /tools
    assert repl.handle_line("/tools") is True
    assert any("calc_add" in o for o in outputs)

    # 3. /status
    assert repl.handle_line("/status") is True
    assert any("Status:" in o for o in outputs)

    # 4. /call with JSON
    assert repl.handle_line('/call calc_add {"x": 15, "y": 25}') is True
    assert any("[Result (calc_add)]: 40" in o for o in outputs)

    # 5. /call with kwargs
    assert repl.handle_line("/call calc_add x=10 y=30") is True
    assert any("[Result (calc_add)]: 40" in o for o in outputs)

    # 6. /ingest and /query
    assert repl.handle_line("/ingest The capital of Mars is Olympus City.") is True
    assert any("Successfully ingested document" in o for o in outputs)

    assert repl.handle_line("/query What is the capital of Mars?") is True
    assert any("[RAG Response]:" in o for o in outputs)

    # 7. /exit
    assert repl.handle_line("/exit") is False
    assert outputs[-1] == "Goodbye!"


def test_repl_interactive_run():
    inputs = iter(["/help", "/tools", "/exit"])
    outputs: list[str] = []

    repl = CLIPlaygroundREPL(
        input_func=lambda prompt: next(inputs),
        output_func=lambda text: outputs.append(text),
    )
    repl.run()
    assert any("Starting AI RAG Interactive REPL" in o for o in outputs)
    assert any("Goodbye!" in o for o in outputs)


def test_cli_run_server_and_run_mcp_dispatch():
    with patch("uvicorn.run") as mock_uvicorn:
        ret = cli_main(["run-server", "--host", "0.0.0.0", "--port", "9000"])
        assert ret == 0
        mock_uvicorn.assert_called_once_with("main:app", host="0.0.0.0", port=9000, reload=False)

    with patch("common_framework.mcp.server.create_mcp_server") as mock_create_mcp:
        mock_server = MagicMock()
        mock_create_mcp.return_value = mock_server

        ret_mcp = cli_main(["run-mcp", "--name", "test-mcp", "--transport", "stdio"])
        assert ret_mcp == 0
        mock_server.run_stdio.assert_called_once()

        # SSE transport dispatch
        ret_sse = cli_main(["run-mcp", "--name", "test-mcp", "--transport", "sse", "--port", "8080"])
        assert ret_sse == 0
        mock_server.run_sse.assert_called_once_with(host="127.0.0.1", port=8080)


def test_repl_edge_cases_and_llm_prompts():
    """Verify REPL edge cases, parameter error paths, doctor command, and LLM prompt processing."""
    outputs: list[str] = []
    repl = CLIPlaygroundREPL(
        llm_client=None,
        rag_engine=None,
        output_func=lambda msg: outputs.append(msg),
    )

    # 1. Unknown command
    assert repl.handle_line("/unknown_command") is True
    assert any("Unknown command '/unknown_command'" in o for o in outputs)

    # 2. Query & ingest without RAGEngine
    repl.handle_line("/query Test question")
    assert any("No RAGEngine configured" in o for o in outputs)

    repl.handle_line("/ingest Some text")
    assert any("No RAGEngine configured" in o for o in outputs)

    # 3. Call without tool name
    repl.handle_line("/call")
    assert any("Usage: /call <tool_name>" in o for o in outputs)

    # 4. Call with invalid JSON
    repl.handle_line("/call some_tool {bad json}")
    assert any("Invalid JSON parameters:" in o for o in outputs)

    # 5. Call missing tool
    repl.handle_line("/call nonexistent_tool key=123")
    assert any("Tool Execution Error:" in o for o in outputs)

    # 6. LLM prompt without configured client
    repl.handle_line("Explain quantum computing")
    assert any("No LLM client configured" in o for o in outputs)

    # 7. Doctor command
    with patch("common_framework.cli.repl.run_doctor") as mock_doc:
        mock_doc.return_value.format_text.return_value = "All systems operational"
        repl.handle_line("/doctor")
        assert any("All systems operational" in o for o in outputs)

    # 8. Configure LLM client and verify prompt exchange
    llm = DummyLLM()
    llm.complete_sync = MagicMock(return_value=ChatResponse(content="Response from LLM", model="test-m"))
    repl.llm_client = llm

    repl.handle_line("What is the capital of Mars?")
    assert any("Response from LLM" in o for o in outputs)
    assert len(repl.history) == 2
    assert repl.history[0].content == "What is the capital of Mars?"
    assert repl.history[1].content == "Response from LLM"

    # 9. LLM exception path
    llm.complete_sync.side_effect = RuntimeError("Provider down")
    repl.handle_line("Another question")
    assert any("LLM Error: Provider down" in o for o in outputs)

    # 10. REPL KeyboardInterrupt handled cleanly
    interrupt_repl = CLIPlaygroundREPL(
        input_func=MagicMock(side_effect=KeyboardInterrupt),
        output_func=lambda msg: outputs.append(msg),
    )
    interrupt_repl.run()
    assert any("Session ended." in o for o in outputs)


def test_cli_dispatch_subcommands(tmp_path):
    """Verify CLI main dispatch for doctor, init, and repl."""
    # 1. Doctor command JSON format
    with patch("common_framework.cli.doctor.run_doctor") as mock_doc:
        mock_doc.return_value.format_json.return_value = '{"status": "ok"}'
        mock_doc.return_value.healthy = True
        assert cli_main(["doctor", "--format", "json"]) == 0

    # 2. Init scaffolding
    dest = tmp_path / "my_microservice"
    assert cli_main(["init", str(dest)]) == 0
    assert (dest / "main.py").exists()

    # 3. REPL command invocation
    with patch("common_framework.cli.repl.CLIPlaygroundREPL.run") as mock_run:
        assert cli_main(["repl"]) == 0
        mock_run.assert_called_once()

