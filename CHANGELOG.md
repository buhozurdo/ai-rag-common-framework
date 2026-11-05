# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) using a multi-channel Release Train model.

---

## [v0.3.0] - 2026-11-04

### Release Title:
### 📚 `v0.3.0: Modular RAG Core (General Availability)`

### Summary:
Third stable release (GA) of the framework. Consolidates a decoupled, modular Retrieval-Augmented Generation (RAG) engine featuring strict metadata filtering, vector dimension validation, local JSON persistence, and native export to MCP tools for agents and microservices.

---

### 🌟 Key Highlights & Consolidated Features:
* **End-to-End Modular RAG Pipeline:**
  * Ingestion, recursive semantic chunking (`RecursiveTextSplitter`), and provenance metadata preservation (`parent_id`, `chunk_index`).
  * Embedding generation and normalization supporting OpenAI, Gemini, Ollama, and a deterministic Mock provider for offline testing.
* **In-Memory Vector Store with Filtering & Persistence (`InMemoryVectorStore`):**
  * Cosine similarity search with boolean attribute filtering on document metadata (`filter_metadata`).
  * Dimension mismatch validation raising informative `ValueError`s.
  * Dependency-free local serialization (`save_to_json` / `load_from_json`).
* **Direct MCP Integration (`as_tool`):**
  * Convert any `RAGEngine` instance into a callable `@tool` in one line of code, ready to serve over stdio or SSE.
* **Quality & Verification:**
  * 33 automated unit tests passing at 100%.

---

## [v0.3.0-beta] - 2026-11-04

### Release Title:
### 🛡️ `v0.3.0-beta: Metadata Filtering, Persistence & Dimension Safety`

### Summary:
Beta stabilization stage for version `0.3.0`. End-to-end hardening of vector storage through metadata filtering, dimension safety checks, and local file persistence.

---

### 🔧 Improvements & Fixes:
* **Metadata Filtering:** Added `filter_metadata: Optional[dict[str, Any]]` to `similarity_search`, `retrieve`, `query`, and `as_tool`.
* **Dimension Safety:** Added strict dimension comparison in `cosine_similarity`.
* **Local Persistence:** Added `save_to_json` and `load_from_json` to `InMemoryVectorStore`.
* **Integration Tests:** New unit tests added (`test_dimension_mismatch_validation`, `test_vectorstore_metadata_filtering`, `test_vectorstore_json_save_and_load`).

---

## [v0.3.0-alpha] - 2026-11-04

### Release Title:
### 🧠 `v0.3.0-alpha: Modular RAG Core`

### Summary:
Active development Alpha release (-4 versions from production). Introduced the core decoupled RAG architecture, semantic text splitting, in-memory vector storage, and automatic tool conversion.

---

### 🚀 New Features:
* **RAG Data Models (`common_framework.rag.models`):** `Document`, `SearchResult`, and `RAGResponse`.
* **Splitting Strategies (`common_framework.rag.splitters`):** `CharacterTextSplitter` and `RecursiveTextSplitter`.
* **Embeddings Abstraction (`common_framework.rag.embeddings`):** `BaseEmbeddingProvider`, `MockEmbeddingProvider`, and `EmbeddingFactory`.
* **Vector Store (`common_framework.rag.vectorstore`):** `InMemoryVectorStore` with cosine similarity search.
* **Orchestration (`common_framework.rag.engine`):** `RAGEngine` with `as_tool()` export.

---

## [v0.2.0] - 2026-11-02

### Release Title:
### ⚡ `v0.2.0: Tooling & MCP Engine (General Availability)`

### Summary:
Second stable release (GA). Delivers the complete Tool ecosystem and official **Model Context Protocol (MCP 2.x)** standard, spanning **Tools, Resources, and Prompts** with local (**stdio**) and remote (**SSE/ASGI**) transports.

---

### 🌟 Key Highlights & Consolidated Features:
* **Full Model Context Protocol (MCP 2.x) Spectrum:**
  * **Tools:** Universal registration via `@server.tool` and dual-use tool binding.
  * **Resources:** Decorator `@server.resource` for exposing documents and structured data.
  * **Prompts:** Decorator `@server.prompt` for exposing parameterized prompt templates.
* **Dual-Use Tooling Engine (`common_framework.tools`):**
  * Auto-generates standard JSON Schema compatible with OpenAI and MCP via Pydantic introspection.
  * Synchronous and asynchronous function support with runtime input validation.
  * Decoupled `ToolRegistry` for internal agent orchestration.
* **Production Transports:**
  * Native **stdio** transport for local desktop clients (Cursor, Claude Desktop, Antigravity).
  * **SSE** (Server-Sent Events) with an ASGI application ready for mounting into FastAPI/Starlette.
* **Quality:**
  * 23 automated unit tests passing at 100%.

---

## [v0.2.0-beta] - 2026-11-02

### Release Title:
### 🛡️ `v0.2.0-beta: Full MCP Spectrum (Tools, Prompts & Resources)`

### Summary:
Beta stabilization phase for version `0.2.0`. Expanded MCP 2.x integration by adding native support for Resources and Prompts alongside the existing Tool engine.

---

### 🔧 Improvements & Fixes:
* **MCP Resources Support:** Added `@server.resource(uri=...)` decorator and async `list_resources()`.
* **MCP Prompts Support:** Added `@server.prompt(name=...)` decorator and async `list_prompts()`.
* **Integration Tests:** Added unit tests verifying resource and prompt registration (`test_mcp_server_resource_and_prompt`).

---

## [v0.2.0-alpha] - 2026-10-31

### Release Title:
### 🛠️ `v0.2.0-alpha: Tooling & MCP Engine`

### Summary:
Alpha release for version `0.2.0`. Introduced the universal `@tool` decorator, `ToolRegistry`, and native `FrameworkMCPServer` adapter.

---

## [v0.1.0] - 2026-10-31

### Release Title:
### 🚀 `v0.1.0: Foundation & Core Architecture (General Availability)`

### Summary:
First stable release (GA). Provides a typed, resilient, and modular foundation for building AI applications, agents, and RAG systems with zero unnecessary dependencies.

---

### 🌟 Key Highlights & Consolidated Features:
* **Production Resilience:** Automatic exponential backoff on transient errors (HTTP 429, 500, 502, 503, 504) and specialized `RateLimitError`.
* **Centralized Settings (`common_framework.core.config`):** Powered by `pydantic-settings` with `.env` file resolution.
* **Unified LLM Client (`common_framework.core.llm`):** Agnostic async client supporting Gemini, OpenAI, Ollama, DeepSeek, and Groq.
* **Native Structured Outputs (`structured_complete`):** Automatic Pydantic schema validation without heavy dependencies.
* **Quality:** 14 automated unit tests passing at 100%.

---

## [v0.1.0-beta] - 2026-11-31

### Release Title:
### 🛡️ `v0.1.0-beta: Hardening, Resiliency & Rate Limiting`

---

## [v0.1.0-alpha] - 2026-10-31

### Release Title:
### 🏗️ `v0.1.0-alpha: Foundation & Core Architecture`
