# AI RAG Common Framework

> A lightweight, modular, zero-bloat foundation framework for building RAG systems, Model Context Protocol (MCP) servers, autonomous multi-agent workflows, and production AI microservices.

[![PyPI](https://img.shields.io/pypi/v/ai-rag-common-framework.svg)](https://pypi.org/project/ai-rag-common-framework/)
[![Python](https://img.shields.io/pypi/pyversions/ai-rag-common-framework.svg)](https://pypi.org/project/ai-rag-common-framework/)
[![CI](https://github.com/buhozurdo/ai-rag-common-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/buhozurdo/ai-rag-common-framework/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/ai-rag-common-framework.svg)](LICENSE)

---

**AI RAG Common Framework** is engineered as a clean, idiomatic, and dependency-light standard library for modern AI engineering. It replaces sprawling, over-engineered stacks with composable, typed primitives that work seamlessly across asynchronous coroutines and synchronous pipelines.

## Features

| Pillar | Highlights |
|---|---|
| **Unified LLM & Resilience** | Async clients for Gemini, OpenAI, Anthropic, Ollama, DeepSeek, Groq, and vLLM; `FallbackLLMClient` with circuit breaking; SSE streaming (`complete_stream()`); `SemanticCache`; Pydantic structured outputs. |
| **RAG & Vector Storage** | `RAGEngine`; `PostgresVectorStore` (`pgvector` HNSW/IVFFlat, JSONB `@>`, FTS); embedded `SQLiteVectorStore` (SQLite + FTS5); `InMemoryVectorStore` (NumPy BLAS GEMV); Okapi BM25 + Reciprocal Rank Fusion; `MultiQueryStrategy` & `HyDEStrategy`; local & Cohere rerankers. |
| **Agents & Workflows** | `ReActAgent`, `AgentSupervisor` with shared `AgentBus`, `StateGraph` cyclic/acyclic workflows with `SQLiteCheckpointSaver`, `ConversationBufferMemory`, `VectorMemory`, and Human-in-the-Loop (HITL) approval hooks. |
| **Tooling & MCP 2.x** | Universal `@tool` decorator with Pydantic JSON schema generation, `FrameworkMCPServer` and `FrameworkMCPClient` (stdio & SSE), and single-line `as_tool()` export. |
| **Security & Guardrails** | Sandboxed `CodeInterpreter` subprocesses, `PromptGuardrail` injection detection, `PIISanitizer` redaction, SSRF URL validation, and path-traversal guards. |
| **Observability & Eval** | `TokenCostTracker`, zero-dependency `PrometheusRegistry` (`/metrics`), W3C `traceparent` spans, `RAGTriadEvaluator`, and `HallucinationDetector`. |
| **Developer CLI** | `ai-rag` / `rag-mcp` with `doctor`, interactive `repl`, `init` project scaffolding, and `run-mcp`. |

---

## Installation

Install from [PyPI](https://pypi.org/project/ai-rag-common-framework/):

```bash
pip install ai-rag-common-framework
```

Or install only the optional feature bundles you need:

| Extra | Command | Includes |
|---|---|---|
| **API Gateway** | `pip install "ai-rag-common-framework[api]"` | FastAPI & Uvicorn |
| **MCP 2.x** | `pip install "ai-rag-common-framework[mcp]"` | Model Context Protocol SDK |
| **Vector / Postgres** | `pip install "ai-rag-common-framework[rag]"` | `pgvector`, `asyncpg`, NumPy BLAS |
| **Everything** | `pip install "ai-rag-common-framework[all]"` | All optional integrations & test tools |

---

## Quick Configuration

Create a `.env` file in your project root (see `.env.example`):

```env
FRAMEWORK_ENV=development
LOG_LEVEL=INFO

# Default LLM Provider (gemini, openai, anthropic, ollama, deepseek, groq, vllm)
DEFAULT_LLM_PROVIDER=gemini
DEFAULT_LLM_MODEL=gemini-2.5-flash

# API Keys
GEMINI_API_KEY=your_gemini_api_key
OPENAI_API_KEY=your_openai_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
```

---

## Usage Examples

### 1. Unified LLM with Structured Outputs & Fallbacks

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

async def main() -> None:
    primary = LLMFactory.create("openai", model="gpt-4o-mini")
    backup = LLMFactory.create("gemini", model="gemini-2.5-flash")
    client = FallbackLLMClient(clients=[primary, backup])

    messages = [
        ChatMessage(
            role=Role.USER,
            content="Summarize: AI RAG Common Framework delivers modular RAG, MCP tools, and multi-agent workflows.",
        )
    ]

    result: ArticleSummary = await client.structured_complete(
        messages=messages,
        response_model=ArticleSummary,
    )
    print(result.title, result.key_points, result.sentiment)

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Embedded SQLite Vector Store with FTS5 Hybrid Search

```python
import asyncio
from common_framework import RAGEngine, SQLiteVectorStore

async def main() -> None:
    # Single-file ACID vector store with FTS5 BM25 keyword index
    vector_store = SQLiteVectorStore(db_path="knowledge.db")
    engine = RAGEngine(vector_store=vector_store)

    await engine.ingest_texts([
        "Authentication is handled via Bearer JWT tokens in the HTTP Authorization header.",
        "Rate limits are enforced using an in-memory token bucket algorithm with 60 req/min.",
        "Database backups execute nightly at 03:00 UTC to an isolated S3 bucket.",
    ])

    response = await engine.query(
        "How is user authentication implemented?",
        mode="hybrid",
        top_k=2,
    )
    print(response.answer)

if __name__ == "__main__":
    asyncio.run(main())
```

### 3. Stateful Workflow Orchestration (`StateGraph`)

```python
import asyncio
from common_framework import END, START, StateGraph

def fetch_data(state: dict) -> dict:
    return {"raw_data": f"Processed: {state['query']}"}

def analyze_risk(state: dict) -> dict:
    return {"risk_score": 85}

def router(state: dict) -> str:
    return "high_risk" if state["risk_score"] > 50 else "low_risk"

def flag_review(state: dict) -> dict:
    return {"status": "Escalated for manual compliance review"}

def auto_approve(state: dict) -> dict:
    return {"status": "Auto-approved"}

async def main() -> None:
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
    print(result.state)

if __name__ == "__main__":
    asyncio.run(main())
```

### 4. Sandboxed Python Code Interpreter

```python
import asyncio
from common_framework import CodeInterpreter

async def main() -> None:
    interpreter = CodeInterpreter(timeout=5.0)
    result = await interpreter.run(
        "import statistics\n"
        "data = [10.2, 14.5, 18.9, 22.1, 35.6]\n"
        "print(f'Mean: {statistics.mean(data):.2f}, Stdev: {statistics.stdev(data):.2f}')"
    )
    print(result.stdout)

if __name__ == "__main__":
    asyncio.run(main())
```

### 5. FastAPI Microservice with RAG & Authenticated MCP

```python
from common_framework import RAGEngine, create_app, create_mcp_server

rag = RAGEngine()
mcp = create_mcp_server(name="gateway-mcp")

@mcp.tool(name="status", description="Get cluster health status")
def get_status() -> dict[str, str]:
    return {"cluster": "us-east-1", "health": "green"}

app = create_app(
    title="AI Knowledge Gateway",
    enable_rag_routes=True,
    rag_engine=rag,
    mcp_server=mcp,
    mcp_prefix="/mcp",
    mcp_api_key="secret-token",
)

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
```

### 6. Command-Line Interface (`ai-rag`)

```bash
ai-rag doctor                          # Check environment, API keys, and services
ai-rag doctor --json                   # Machine-readable diagnostics for CI/CD
ai-rag repl                            # Launch interactive playground
ai-rag init --dir ./my_ai_service      # Scaffold a starter microservice
ai-rag run-mcp --transport stdio       # Run standalone MCP server (stdio or sse)
ai-rag version                         # Print installed package version
```

---

## Development & Testing

```bash
git clone https://github.com/buhozurdo/ai-rag-common-framework.git
cd ai-rag-common-framework
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run tests with coverage
python -m pytest tests/ --cov=common_framework
```

---

## Documentation

* **[Changelog](CHANGELOG.md)** — Release history and upgrade notes.
* **[Roadmap](ROADMAP.md)** — Release train model and upcoming milestones.
* **[GitHub Wiki](https://github.com/buhozurdo/ai-rag-common-framework/wiki)** — Architecture guides, API cheatsheet, and production runbooks.

---

## License

This project is licensed under the [MIT License](LICENSE).
