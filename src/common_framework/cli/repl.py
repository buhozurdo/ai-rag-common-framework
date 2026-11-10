"""Interactive Terminal REPL & Tool Playground (FEAT-12)."""

from __future__ import annotations

import json
import shlex
import sys
from typing import Any, Callable, Optional

from common_framework.cli.doctor import run_doctor
from common_framework.core.config import Settings, get_settings
from common_framework.core.llm import BaseLLMClient, ChatMessage, Role
from common_framework.rag.engine import RAGEngine
from common_framework.rag.models import Document
from common_framework.tools.registry import ToolRegistry, global_tool_registry


_HELP_BANNER = """
Interactive Terminal REPL & Tool Playground
===================================================
Available commands:
  /help                  Show this help menu
  /tools                 List all registered tools in the tool registry
  /call <name> <args>    Invoke a tool by name with JSON or key=value arguments
                         Example: /call calculate {"a": 10, "b": 20}
  /query <question>      Query the connected RAG engine
  /ingest <text>         Ingest text into the in-memory RAG collection
  /doctor                Run environment and configuration diagnostics
  /status                Show active model, settings, and registry status
  /exit, /quit           Exit the REPL session
Any other input is forwarded directly to the connected LLM.
===================================================
"""


class CLIPlaygroundREPL:
    """Interactive playground for LLM testing, tool invocation, and RAG querying."""

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        rag_engine: Optional[RAGEngine] = None,
        tool_registry: Optional[ToolRegistry] = None,
        settings: Optional[Settings] = None,
        input_func: Optional[Callable[[str], str]] = None,
        output_func: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.llm_client = llm_client
        self.rag_engine = rag_engine
        self.tool_registry = tool_registry or global_tool_registry
        self.settings = settings or get_settings()
        self.input_func = input_func or (lambda prompt: input(prompt))
        self.output_func = output_func or (lambda text: print(text))
        self.history: list[ChatMessage] = []

    def handle_line(self, line: str) -> bool:
        """Process a single input line. Returns False if REPL should terminate."""
        text = line.strip()
        if not text:
            return True

        if text.lower() in ("/exit", "/quit", "exit", "quit"):
            self.output_func("Goodbye!")
            return False

        if text.startswith("/"):
            self._handle_command(text)
            return True

        self._handle_llm_prompt(text)
        return True

    def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            self.output_func(_HELP_BANNER)

        elif cmd == "/tools":
            tools = self.tool_registry.list_tools()
            if not tools:
                self.output_func("No tools registered in the global tool registry.")
            else:
                self.output_func(f"Registered Tools ({len(tools)}):")
                for t in tools:
                    desc = t.description or "No description"
                    approval_tag = " [Requires Approval]" if t.requires_approval else ""
                    self.output_func(f"  - {t.name}{approval_tag}: {desc}")

        elif cmd == "/call":
            self._handle_tool_call(args_str)

        elif cmd == "/query":
            if not self.rag_engine:
                self.output_func("Error: No RAGEngine configured. Start REPL with a connected RAGEngine.")
                return
            if not args_str.strip():
                self.output_func("Usage: /query <your question>")
                return
            try:
                response = self.rag_engine.query_sync(args_str.strip())
                self.output_func(f"\n[RAG Response]:\n{response.answer}\n")
                if response.sources:
                    self.output_func(f"Sources ({len(response.sources)} chunks):")
                    for i, doc in enumerate(response.sources, 1):
                        self.output_func(f"  [{i}] {doc.content[:120]}...")
            except Exception as exc:
                self.output_func(f"RAG Query Error: {exc}")

        elif cmd == "/ingest":
            if not self.rag_engine:
                self.output_func("Error: No RAGEngine configured to ingest text.")
                return
            if not args_str.strip():
                self.output_func("Usage: /ingest <text content to store>")
                return
            try:
                self.rag_engine.ingest_documents_sync([Document(content=args_str.strip())])
                self.output_func(f"Successfully ingested document ({len(args_str)} chars).")
            except Exception as exc:
                self.output_func(f"Ingest Error: {exc}")

        elif cmd == "/doctor":
            report = run_doctor(self.settings, check_network=True)
            self.output_func(report.format_text())

        elif cmd == "/status":
            llm_name = type(self.llm_client).__name__ if self.llm_client else "None (Unconfigured)"
            rag_name = "Configured" if self.rag_engine else "None"
            tool_count = len(self.tool_registry.list_tools())
            self.output_func(f"Status:\n  LLM Client: {llm_name}\n  RAG Engine: {rag_name}\n  Tools: {tool_count} registered\n  Messages in history: {len(self.history)}")

        else:
            self.output_func(f"Unknown command '{cmd}'. Type /help for available commands.")

    def _handle_tool_call(self, args_str: str) -> None:
        if not args_str.strip():
            self.output_func("Usage: /call <tool_name> [json_arguments_or_kwargs]")
            return

        parts = args_str.strip().split(maxsplit=1)
        tool_name = parts[0]
        params_str = parts[1].strip() if len(parts) > 1 else "{}"

        kwargs: dict[str, Any] = {}
        if params_str:
            if params_str.startswith("{") and params_str.endswith("}"):
                try:
                    kwargs = json.loads(params_str)
                except Exception as exc:
                    self.output_func(f"Invalid JSON parameters: {exc}")
                    return
            else:
                # Key=value pairs e.g. a=10 b="hello"
                for pair in params_str.split():
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        try:
                            kwargs[k.strip()] = json.loads(v.strip())
                        except Exception:
                            kwargs[k.strip()] = v.strip()

        try:
            tool = self.tool_registry.get(tool_name)
            result = tool.execute_sync(**kwargs)
            self.output_func(f"[Result ({tool_name})]: {result}")
        except Exception as exc:
            self.output_func(f"Tool Execution Error: {exc}")

    def _handle_llm_prompt(self, prompt: str) -> None:
        if not self.llm_client:
            self.output_func("No LLM client configured. Configure API keys or pass an LLM client to use prompting.")
            return

        self.history.append(ChatMessage(role=Role.USER, content=prompt))
        try:
            response = self.llm_client.complete_sync(self.history)
            self.history.append(ChatMessage(role=Role.ASSISTANT, content=response.content))
            self.output_func(f"\n{response.content}\n")
        except Exception as exc:
            self.output_func(f"LLM Error: {exc}")

    def run(self) -> None:
        """Start interactive REPL loop until exit command or EOF."""
        self.output_func("Starting AI RAG Interactive REPL. Type /help for assistance, /exit to quit.")
        while True:
            try:
                line = self.input_func("rag> ")
            except (EOFError, KeyboardInterrupt):
                self.output_func("\nSession ended.")
                break

            should_continue = self.handle_line(line)
            if not should_continue:
                break
