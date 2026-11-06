# Changelog

All notable changes to this project are documented in this file following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v0.5.0-alpha] - 2026-11-05

### Added
- **Multi-Provider Fallbacks (`core/llm.py`):** `FallbackLLMClient` with automatic failover across candidates on HTTP 429/503/timeout and `AllProvidersFailedError`.
- **Native Anthropic Client:** Direct Anthropic Messages API support with system prompt handling, retries, and structured outputs.
- **SSE Streaming:** Real-time token streaming in `complete_stream()`, `RAGEngine.query_stream()`, and `POST /rag/query/stream` FastAPI route.
- **In-Memory Semantic Caching (`core/cache.py`):** Zero-dependency cosine similarity cache with LRU eviction, TTL expiration, namespace partitioning, and `CachedLLMClient`.
- **Pre-Normalized Vector Search (`PERF-01`):** Pre-normalized embeddings during ingestion with single query norm computation and opportunistic NumPy/BLAS GEMV acceleration (~85x speedup).

### Fixed
- **Text Splitter Overlap (`TASK-01`):** Implemented sliding window `chunk_overlap` in `CharacterTextSplitter`.
- **Text Splitter Order (`TASK-02`):** Fixed chunk ordering in `RecursiveTextSplitter` by flushing buffer prior to recursive sub-splits.

---

## [v0.4.0] - 2026-11-05

### Added
- **FastAPI Application Factory (`create_app`):** Preconfigured app with CORS, health probes (`/healthz`, `/readyz`), and automatic RFC 7807 exception mapping.
- **FastAPI RAG Endpoints (`create_rag_router`):** REST routes for query, retrieval, ingestion, and collection lifecycle.
- **MCP Sub-App Mounting (`mount_mcp_to_fastapi`):** Mount MCP 2.x SSE servers with `MCPAuthMiddleware` (Bearer and `X-API-Key`).
- **Hybrid Search (`rag/bm25.py`):** Pure-Python Okapi BM25 index combined with dense vector retrieval via Reciprocal Rank Fusion (RRF).
- **Multi-Tenant Collections:** Isolated document stores and BM25 statistics in `InMemoryVectorStore`.

### Fixed
- Restricted CORS credentials to explicit origins to prevent wildcard credentials vulnerabilities.
- Defaulted `allow_query_param_auth` to `False` in MCP middleware to prevent API key exposure in logs.
- Fixed cosine similarity ranking discontinuity on negative scores with uniform `[-1, 1] -> [0, 1]` mapping.
- Pre-validated JSON documents prior to clearing in-memory state in `load_from_json`.
- Corrected BM25 document frequency double-counting on upserts.

---

## [v0.4.0-beta] - 2026-11-05

### Added
- RESTful RAG router and MCP SSE sub-app mounting with auth middleware.
- Transport security hardening against DNS rebinding.

---

## [v0.4.0-alpha] - 2026-11-05

### Added
- Pure-Python Okapi BM25 and Reciprocal Rank Fusion hybrid search.
- Multi-collection isolation and FastAPI application factory.

---

## [v0.3.0] - 2026-11-04

### Added
- **Modular RAG Pipeline:** Ingestion, text splitters, embeddings, and `RAGEngine` orchestrator.
- **In-Memory Vector Store:** Cosine similarity retrieval with metadata filtering and JSON persistence.
- **MCP Integration:** Direct export of RAG engines as callable `@tool`s via `as_tool()`.

---

## [v0.3.0-beta] - 2026-11-04

### Added
- Metadata filtering (`filter_metadata`) in `similarity_search` and vector dimension validation.

---

## [v0.3.0-alpha] - 2026-11-04

### Added
- Core decoupled RAG models (`Document`, `SearchResult`, `RAGResponse`) and basic text splitters.

---

## [v0.2.0] - 2026-11-02

### Added
- **Tooling Engine:** Universal `@tool` decorator, automatic Pydantic JSON schema generation, and `ToolRegistry`.
- **MCP 2.x Server:** Official Model Context Protocol support spanning Tools, Resources, and Prompts over stdio and SSE.

---

## [v0.2.0-beta] - 2026-11-02

### Added
- MCP `@server.resource` and `@server.prompt` support alongside existing tool capabilities.

---

## [v0.2.0-alpha] - 2026-10-31

### Added
- Universal `@tool` decorator and `FrameworkMCPServer` base implementation.

---

## [v0.1.0] - 2026-10-31

### Added
- **Core Architecture:** Centralized typed settings via `pydantic-settings` and `.env` support.
- **Unified LLM Client:** Async client supporting Gemini, OpenAI, Ollama, DeepSeek, and Groq with exponential backoff.
- **Structured Outputs:** Native Pydantic schema validation without external dependencies.
