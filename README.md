# AI RAG Common Framework

> A lightweight, modular, zero-bloat foundation framework for building RAG systems, Model Context Protocol (MCP) servers, autonomous multi-agent workflows, and production AI microservices.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-v0.18.0%20(Stable)-brightgreen.svg)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/Tests-322%20passed%20(100%25)-success.svg)](tests/)
[![Coverage](https://img.shields.io/badge/Coverage-91%25-brightgreen.svg)](tests/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🚀 Overview & Current Status: `v0.18.0` (Stable GA)

**AI RAG Common Framework** is engineered as a clean, idiomatic, and dependency-light standard library for modern AI engineering. It replaces sprawling, over-engineered stacks with composable, typed primitives that work seamlessly across asynchronous coroutines and synchronous pipelines.

### 🌟 Core Architectural Pillars

1. **Agnostic LLM Engine & Enterprise Resilience:**
   * Unified asynchronous LLM client supporting Google Gemini, OpenAI, Anthropic, Ollama, DeepSeek, Groq, and vLLM.
   * `FallbackLLMClient`: Automatic circuit-breaking failover across prioritized provider chains.
   * Real-time Server-Sent Events (SSE) token streaming (`complete_stream()`).
   * `SemanticCache`: Sub-millisecond similarity caching with LRU eviction and TTL invalidation.
   * Native Pydantic Structured Outputs with schema validation.

2. **Advanced RAG & Embedded Vector Persistence:**
   * `RAGEngine`: End-to-end orchestration of chunking, vectorization, hybrid retrieval, and generation.
   * `SQLiteVectorStore`: ACID-compliant, single-file embedded vector store using Python's standard library `sqlite3` and FTS5 full-text indexing (no external database daemon required).
   * `InMemoryVectorStore`: High-performance pure-Python vector store with BLAS GEMV acceleration.
   * Hybrid Search: Native Okapi BM25 sparse matching fused with dense vector similarity via Reciprocal Rank Fusion (RRF).
   * Algorithmic Retrieval Strategies: Parallel `MultiQueryStrategy` expansion and Hypothetical Document Embeddings (`HyDEStrategy`).
   * Two-Stage Reranking: `LocalCrossEncoderReranker` and `CohereReranker` pipeline stages.

3. **Autonomous Agents & Stateful Workflow Orchestration:**
   * `ReActAgent`: Autonomous thought-action-observation reasoning loop with iteration limits.
   * `AgentSupervisor`: Orchestrator-Workers hierarchy routing subtasks to specialized agents with a shared `AgentBus` state bus.
   * `StateGraph`: Stateful directed graph execution engine supporting cyclic and acyclic workflow topologies, conditional branching, execution history, and state checkpointing.
   * `ConversationBufferMemory` and `VectorMemory` for contextual recall.
   * Human-in-the-Loop (HITL) approval hooks for sensitive tool execution.

4. **Universal Tooling & Model Context Protocol (MCP 2.x):**
   * Universal `@tool` decorator generating standard JSON schemas via Pydantic introspection.
   * `FrameworkMCPServer`: Full MCP 2.x server adapter supporting Tools, Resources, and Prompts over **stdio** and **SSE** transports.
   * `FrameworkMCPClient`: Bidirectional MCP client dynamically discovering and mounting remote tools into local agent registries.
   * Single-line export: Any RAG pipeline or interpreter can be converted into an MCP tool via `as_tool()`.

5. **Security, Guardrails & Ephemeral Sandboxing:**
   * `CodeInterpreter`: Secure Python code sandbox running in isolated subprocesses with ephemeral directories, secret scrubbing, and execution timeouts.
   * `PromptGuardrail`: Fast regex and heuristic detection of prompt injections, jailbreaks, and system prompt override attempts.
   * `PIISanitizer`: High-throughput redacting and masking of sensitive personal data (emails, credit cards, SSNs, API keys).
   * Strict SSRF validation and path traversal guards across all filesystem persistence.

6. **Observability, Telemetry & Quality Evaluation:**
   * `TokenCostTracker`: Granular dollar accounting tracking prompt and completion tokens across providers.
   * `PrometheusRegistry`: Zero-dependency metrics router exposing `/metrics` in standard Prometheus text format.
   * Distributed OpenTelemetry Tracing: W3C `traceparent` context propagation and `trace_span` context managers.
   * `RAGTriadEvaluator`: In-process evaluation of Context Relevance, Groundedness (Faithfulness), and Answer Relevance.
   * `HallucinationDetector`: Sentence-level grounding confidence scoring without third-party LLM evaluation dependencies.

7. **Developer CLI & Interactive Playground:**
   * Unified CLI console command: `ai-rag` and `rag-mcp`.
   * `ai-rag doctor`: Comprehensive diagnostic utility checking Python version, API keys, Ollama reachability, and directory permissions.
   * `ai-rag repl`: Interactive terminal playground for testing prompts, running tools, ingesting documents, and executing RAG queries live.
   * `ai-rag init`: Production microservice scaffolding generator.

---

## 📦 Installation

Install the package in editable mode locally:

```bash
pip install -e .
```

Or install with optional dependencies:

```bash
# With MCP protocol support and full test suite
pip install -e ".[test,mcp]"
```

---

## 🛠️ Quick Configuration

Create a `.env` file in your project root (refer to `.env.example`):

```env
FRAMEWORK_ENV=development
LOG_LEVEL=INFO

# Default LLM Provider (gemini, openai, anthropic, ollama)
DEFAULT_LLM_PROVIDER=gemini
DEFAULT_LLM_MODEL=gemini-2.5-flash

# API Keys
GEMINI_API_KEY=your_gemini_api_key
OPENAI_API_KEY=your_openai_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
```

---

## 💻 Usage Examples

### 1. Unified LLM with Structured Outputs & Model Fallbacks

```python
import asyncio
from pydantic import BaseModel, Field
from common_framework import (
    LLMFactory,
    FallbackLLMClient,
    ChatMessage,
    Role,
)

class ArticleSummary(BaseModel):
    title: str
    key_points: list[str] = Field(description="3-5 bullet points")
    sentiment: str = Field(description="positive, neutral, or negative")

async def main():
    # Primary client (OpenAI) with automatic fallback to Gemini
    primary = LLMFactory.create("openai", model="gpt-4o-mini")
    backup = LLMFactory.create("gemini", model="gemini-2.5-flash")
    client = FallbackLLMClient(candidates=[primary, backup])

    messages = [
        ChatMessage(
            role=Role.USER,
            content="Summarize: AI RAG Common Framework delivers modular RAG, MCP tools, and multi-agent workflows."
        )
    ]

    result: ArticleSummary = await client.structured_complete(
        messages=messages,
        response_model=ArticleSummary,
    )
    print(f"Title: {result.title}")
    print(f"Points: {result.key_points}")
    print(f"Sentiment: {result.sentiment}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 2. Embedded SQLite Vector Store with FTS5 Hybrid Search

```python
import asyncio
from common_framework import RAGEngine, SQLiteVectorStore

async def main():
    # Single-file ACID vector store with FTS5 BM25 keyword index
    vector_store = SQLiteVectorStore(database_path="knowledge.db")

    engine = RAGEngine(vector_store=vector_store)

    # Ingest knowledge documents
    await engine.ingest_texts([
        "Authentication is handled via Bearer JWT tokens in the HTTP Authorization header.",
        "Rate limits are enforced using an in-memory token bucket algorithm with 60 req/min.",
        "Database backups execute nightly at 03:00 UTC to an isolated S3 bucket."
    ])

    # Hybrid query combining vector similarity with BM25 full-text rank fusion
    response = await engine.query(
        "How is user authentication implemented?",
        mode="hybrid",
        top_k=2,
    )
    print("Answer:\n", response.answer)

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 3. Multi-Agent Supervisor & Delegation Pattern

```python
import asyncio
from common_framework import AgentSupervisor, LLMFactory

async def researcher(task: str) -> str:
    return f"Research findings: Found 3 relevant CVEs regarding SSL renegotiation."

def coder(task: str) -> str:
    return "Patch generated: SSL_OP_NO_RENEGOTIATION flag enabled in context."

async def main():
    supervisor = AgentSupervisor(
        agents={
            "researcher": ("Security researcher finding vulnerabilities", researcher),
            "coder": ("Senior systems engineer writing security patches", coder),
        },
        llm_client=LLMFactory.create(),
        max_steps=4,
    )

    response = await supervisor.run("Investigate and patch SSL renegotiation vulnerability.")
    print("Final Resolution:\n", response.final_answer)
    for step in response.delegations:
        print(f"Step {step.step}: [{step.agent_name}] -> {step.result[:60]}...")

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 4. Stateful Directed Graph Workflow (`StateGraph`)

```python
import asyncio
from common_framework import StateGraph, START, END

def fetch_data(state: dict) -> dict:
    return {"query": state.get("query"), "raw_text": "Sample financial report data."}

def analyze_risk(state: dict) -> dict:
    risk_score = 75 if "financial" in state["raw_text"] else 20
    return {"risk_score": risk_score}

def router(state: dict) -> str:
    return "high_risk" if state["risk_score"] > 50 else "low_risk"

def flag_review(state: dict) -> dict:
    return {"status": "Escalated for manual compliance review"}

def auto_approve(state: dict) -> dict:
    return {"status": "Auto-approved"}

async def main():
    graph = (
        StateGraph()
        .add_node("fetch", fetch_data)
        .add_node("analyze", analyze_risk)
        .add_node("flag_review", flag_review)
        .add_node("auto_approve", auto_approve)
        .add_edge(START, "fetch")
        .add_edge("fetch", "analyze")
        .add_conditional_edges("analyze", router, {
            "high_risk": "flag_review",
            "low_risk": "auto_approve",
        })
        .add_edge("flag_review", END)
        .add_edge("auto_approve", END)
    )

    result = await graph.run({"query": "Audit Q3 numbers"})
    print("Workflow Completed:", result.completed)
    print("Final State:", result.state)

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 5. Sandboxed Python Code Interpreter

```python
import asyncio
from common_framework import CodeInterpreter, code_interpreter_tool

async def main():
    # Ephemeral sandbox stripping parent secrets and enforcing 5s timeout
    interpreter = CodeInterpreter(timeout=5.0)

    code = """
import statistics
data = [10.2, 14.5, 18.9, 22.1, 35.6]
mean = statistics.mean(data)
stdev = statistics.stdev(data)
print(f"Mean: {mean:.2f}, Stdev: {stdev:.2f}")
"""
    result = await interpreter.run(code)
    print("Execution Success:", result.success)
    print("Output:\n", result.stdout)

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 6. Production FastAPI Microservice with RAG & Authenticated MCP

```python
from common_framework import RAGEngine, create_app, create_mcp_server, tool

# 1. Initialize RAG Engine and MCP Server
rag = RAGEngine()
mcp = create_mcp_server(name="gateway-mcp")

@mcp.tool(name="status", description="Get cluster health status")
def get_status() -> dict[str, str]:
    return {"cluster": "us-east-1", "health": "green"}

# 2. Create production FastAPI service with unified endpoints
app = create_app(
    title="AI Knowledge Gateway",
    enable_rag_routes=True,     # Exposes /rag/query, /rag/retrieve, /rag/ingest, /rag/collections
    rag_engine=rag,
    mcp_server=mcp,             # Mounts MCP server over SSE (/mcp/sse, /mcp/messages/)
    mcp_prefix="/mcp",
    mcp_api_key="secret-token", # Enforces Bearer / X-API-Key / ?api_key= auth
)

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
```

---

### 7. Unified Framework CLI

The package provides the `ai-rag` command-line interface out of the box:

```bash
# Run environment diagnostics and health checks
ai-rag doctor

# Output diagnostics in JSON format for CI/CD pipelines
ai-rag doctor --json

# Launch the interactive terminal REPL playground
ai-rag repl

# Scaffold a new starter microservice project
ai-rag init --dir ./my_ai_service

# Run a standalone MCP server over stdio or SSE
ai-rag run-mcp --transport stdio
ai-rag run-mcp --transport sse --port 8080

# Check installed framework version
ai-rag version
```

---

## 🧪 Running Tests & Quality Verification

The test suite contains **322 unit, integration, and security tests** running with **91% global coverage**:

```bash
# Run all unit tests
python -m pytest tests/ -v

# Run full test suite with enterprise integration suites and coverage
python -m pytest tests/ examples/ --cov=common_framework
```

---

## 📚 Documentation & Roadmap

* [CHANGELOG.md](CHANGELOG.md): Complete release history, Pull Request notes, and breaking changes.
* [RELEASE.md](RELEASE.md): Gitflow release process, tagging schemes, and tag-by-tag release notes.
* [ROADMAP.md](ROADMAP.md): Release train progression and future architectural milestones.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
