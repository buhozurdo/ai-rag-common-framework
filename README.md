# AI RAG Common Framework

> A lightweight, modular, zero-bloat foundation framework for building RAG systems, Model Context Protocol (MCP) servers, and modern AI microservices.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-v0.4.0%20(Stable)-brightgreen.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🚀 Project Status: `v0.4.0` (General Availability)

Following our **Release Train** methodology documented in [ROADMAP.md](ROADMAP.md), the stable release **`v0.4.0`** (*FastAPI Gateway, Hybrid Search & MCP Sub-App Integration*) is now consolidated and available for production.

### Key Highlights & Features:
* ⚡ **FastAPI Application Factory (`common_framework.api`):**
  * `create_app`: Production-ready FastAPI factory with preconfigured CORS, liveness (`/healthz`), and readiness (`/readyz`) probes.
  * Automated exception translation mapping framework errors (`RateLimitError` -> 429 + `Retry-After`, `AuthenticationError` -> 401, `ToolNotFoundError` -> 404, `ToolExecutionError` -> 400, `StructuredOutputError` -> 502, `FrameworkError` -> 500).
  * `create_rag_router`: Standard REST endpoints for query synthesis (`/rag/query`), chunk retrieval (`/rag/retrieve`), batch ingestion (`/rag/ingest`), and multi-tenant collection lifecycle (`/rag/collections`).
* 🔌 **Hybrid MCP Sub-App Gateway (`mount_mcp_to_fastapi`):**
  * Mount official Model Context Protocol 2.x SSE servers into FastAPI without path stripping conflicts.
  * Built-in `MCPAuthMiddleware` supporting Bearer tokens, `X-API-Key` headers, and `?api_key=` query parameter authentication.
* 🔍 **Pure-Python BM25 + Dense Hybrid Search (`common_framework.rag`):**
  * Okapi BM25 indexer with zero heavy C/NLP dependencies combined with dense vector retrieval via Reciprocal Rank Fusion ($k=60$).
  * Multi-tenant collection isolation with isolated BM25 statistical spaces and JSON persistence.
* 🧠 **Modular RAG Pipeline:**
  * `RAGEngine`: End-to-end orchestration of ingestion, splitting, vector generation, and LLM synthesis.
  * `RecursiveTextSplitter`: Hierarchical semantic chunking preserving traceability metadata (`parent_id`, `chunk_index`).
  * `InMemoryVectorStore`: Pure-Python vector store with metadata filtering (`filter_metadata`) and JSON persistence.
  * `EmbeddingFactory`: Adapters for OpenAI, Gemini, Ollama, and deterministic Mock embeddings.
  * **MCP Export (`engine.as_tool`):** Convert any RAG pipeline into an MCP tool with a single call.
* 🛠️ **Dual-Use Tooling Engine (`common_framework.tools`):** Universal `@tool` decorator with automatic JSON Schema generation via Pydantic.
* 🌐 **Native MCP Server (`common_framework.mcp`):** Built on the official `mcp 2.x` standard (`MCPServer`) with complete support for Tools, Resources, and Prompts over stdio and SSE transports.
* 🤖 **Agnostic & Unified LLM Client (`common_framework.core.llm`):** Unified async interface for Gemini, OpenAI, Ollama, DeepSeek, Groq, and vLLM with retries, exponential backoff, and Pydantic structured outputs.

---

## 📦 Installation

Install the package in editable mode locally:

```bash
pip install -e .
```

Or install with optional development and testing dependencies:

```bash
pip install -e ".[test,mcp]"
```

---

## 🛠️ Quick Configuration

Create a `.env` file in your project root (refer to `.env.example`):

```env
FRAMEWORK_ENV=development
LOG_LEVEL=INFO

# Default LLM Provider (gemini, openai, ollama)
DEFAULT_LLM_PROVIDER=gemini
DEFAULT_LLM_MODEL=gemini-2.5-flash

# API Keys
GEMINI_API_KEY=your_gemini_api_key
OPENAI_API_KEY=
```

---

## 💻 Usage Examples

### 1. Simple Chat Completion

```python
import asyncio
from common_framework import LLMFactory, ChatMessage, Role

async def main():
    client = LLMFactory.create()

    messages = [
        ChatMessage(role=Role.SYSTEM, content="You are a concise assistant."),
        ChatMessage(role=Role.USER, content="What is the Model Context Protocol in one sentence?")
    ]

    response = await client.complete(messages)
    print(f"Response ({response.model}):\n{response.content}")
    print(f"Total tokens: {response.usage.total_tokens}")

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Structured Outputs with Pydantic (Zero Manual Parsing)

```python
import asyncio
from pydantic import BaseModel, Field
from common_framework import LLMFactory, ChatMessage, Role

class SentimentAnalysis(BaseModel):
    sentiment: str = Field(description="positive, negative, or neutral")
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

async def main():
    client = LLMFactory.create("gemini")

    messages = [
        ChatMessage(
            role=Role.USER,
            content="Analyze this review: 'The framework setup is blazingly fast and elegant.'"
        )
    ]

    result: SentimentAnalysis = await client.structured_complete(
        messages=messages,
        response_model=SentimentAnalysis
    )

    print("Sentiment:", result.sentiment)
    print("Confidence:", result.confidence)
    print("Summary:", result.summary)

if __name__ == "__main__":
    asyncio.run(main())
```

### 3. MCP Server with Dual-Use Tools

```python
from common_framework import create_mcp_server, tool

# Define a universal tool
@tool(name="calculate_discount", description="Calculates final price after discount percentage.")
def calculate_discount(price: float, discount_pct: float) -> dict[str, float]:
    savings = round(price * (discount_pct / 100), 2)
    return {"final_price": price - savings, "savings": savings}

# Create MCP server and register tool
server = create_mcp_server(name="pricing-service", version="0.3.0")
server.add_tool(calculate_discount)

# Execution:
# server.run_stdio()        # For Cursor / Claude Desktop / Antigravity
# server.run_sse(port=8000) # For HTTP / Remote Web clients
```

### 4. Modular RAG Engine with Direct MCP Export

```python
import asyncio
from common_framework import InMemoryVectorStore, RAGEngine, create_mcp_server

async def main():
    # 1. Initialize RAG Engine without requiring external databases
    engine = RAGEngine(vector_store=InMemoryVectorStore())
    await engine.ingest_texts([
        "The primary production database is located at 10.0.0.1.",
        "Automated backups run daily at 02:00 AM UTC."
    ])

    # 2. Export RAG pipeline as a callable tool
    rag_tool = engine.as_tool(name="search_infra", description="Search internal infrastructure docs")

    # 3. Mount directly into an MCP server
    server = create_mcp_server(name="infra-mcp-service")
    server.add_tool(rag_tool)
    # server.run_stdio()

if __name__ == "__main__":
    asyncio.run(main())
```

### 5. Production FastAPI Microservice with RAG & Authenticated MCP

```python
from common_framework import RAGEngine, create_app, create_mcp_server, tool

# 1. Initialize RAG Engine and MCP Server
rag = RAGEngine()
mcp = create_mcp_server(name="gateway-mcp")

@mcp.tool(name="status", description="Get cluster health status")
def get_status() -> dict[str, str]:
    return {"cluster": "us-east-1", "health": "green"}

# 2. Create production FastAPI service with one unified call
app = create_app(
    title="AI Knowledge Gateway",
    enable_rag_routes=True,     # Exposes POST /rag/query, POST /rag/retrieve, POST /rag/ingest, GET /rag/collections
    rag_engine=rag,
    mcp_server=mcp,             # Mounts MCP server over SSE
    mcp_prefix="/mcp",          # Accessible at /mcp/sse and /mcp/messages/
    mcp_api_key="secret-token", # Enforces Bearer / X-API-Key / ?api_key= auth
)

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 🧪 Running Tests

```bash
python -m pytest -v
```

---

## 🗺️ Upcoming Releases in the Release Train

See [ROADMAP.md](ROADMAP.md) for full details.
* **`v0.5.0-alpha`**: *Autonomous Agents & Workflows* (ReAct reasoning loops, long-term memory retrieval, token streaming).
* **`v0.6.0-alpha`**: *Observability, Evaluation & Gateway* (Cost/token tracking, automatic model fallbacks, semantic caching).

