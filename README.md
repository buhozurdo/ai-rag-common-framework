# AI RAG Common Framework

> A lightweight, modular, zero-bloat foundation framework for building RAG systems, Model Context Protocol (MCP) servers, and modern AI microservices.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-v0.3.0%20(Stable)-brightgreen.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🚀 Project Status: `v0.3.0` (General Availability)

Following our **Release Train** methodology documented in [ROADMAP.md](ROADMAP.md), the stable release **`v0.3.0`** (*Modular RAG Core*) is now consolidated, featuring a decoupled RAG engine with seamless export to MCP tools.

### Key Highlights & Features:
* 🧠 **Modular RAG Pipeline (`common_framework.rag`):**
  * `RAGEngine`: End-to-end orchestration of ingestion, splitting, vector generation, and LLM synthesis.
  * `RecursiveTextSplitter`: Hierarchical semantic chunking preserving traceability metadata (`parent_id`, `chunk_index`).
  * `InMemoryVectorStore`: Pure-Python vector store with zero heavy database dependencies, metadata filtering (`filter_metadata`), and JSON persistence (`save_to_json` / `load_from_json`).
  * `EmbeddingFactory`: Adapters for OpenAI, Gemini, Ollama, and a deterministic Mock provider for local testing.
  * **MCP Export (`engine.as_tool`):** Convert any RAG pipeline into an MCP tool with a single method call.
* 🛠️ **Dual-Use Tooling Engine (`common_framework.tools`):** Universal `@tool` decorator with automatic JSON Schema generation via Pydantic. Use the same function inside internal LLM agent loops and expose it via MCP.
* 🌐 **Native MCP Server (`common_framework.mcp`):** Built on the official `mcp 2.x` standard (`MCPServer`) with complete support for **Tools, Resources, and Prompts** over **stdio** (local) and **SSE** (remote/web) transports.
* 🤖 **Agnostic & Unified LLM Client (`core.llm`):** Unified async interface for Gemini, OpenAI, Ollama, DeepSeek, Groq, and vLLM.
* 🛡️ **Resilience & Automatic Retries:** Exponential backoff on transient errors (HTTP 429, 5xx, network drops) and typed `RateLimitError` handling.
* 🎯 **Native Structured Outputs:** Guaranteed schema validation against Pydantic models (`structured_complete`).
* 📦 **Modern Packaging:** Standardized `pyproject.toml` (PEP 517/518/621).

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

---

## 🧪 Running Tests

```bash
python -m pytest -v
```

---

## 🗺️ Upcoming Releases in the Release Train

See [ROADMAP.md](ROADMAP.md) for full details.
* **`v0.4.0-alpha`**: *FastAPI Service & Hybrid Endpoints* (REST API + Integrated MCP over SSE Gateway).
* **`v0.5.0-alpha`**: *Autonomous Agents & Workflows*.
* **`v0.6.0-alpha`**: *Observability, Evaluation & Gateway*.
