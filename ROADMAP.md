# Roadmap & Release Train Model — AI RAG Common Framework

This framework adopts a staggered **Release Train** methodology to guarantee architectural stability, rigorous testing, and strict decoupling between active development and production releases.

---

## 🚆 Release Train Channels & Pipeline

The maturity lifecycle of every minor version progresses across 4 distinct channels with a controlled version offset:

```
[ Canary ] ────────▶ [ Alpha ] ────────▶ [ Beta ] ────────▶ [ Release ]
(Pre-Alpha:         (Active Dev:          (Stabilization:       (Production /
 Exploratory)       -4 from Release)      -2 from Release)      General Availability)
```

### Channel Definitions & Roles:
1. **`canary` (Pre-Alpha):**
   * Bleeding-edge / exploratory space.
   * Technical spikes, prototypes, and emerging protocol research.
   * Tag format: `vX.Y.0-canary`
2. **`alpha` (-4 versions from Release):**
   * **Where developers actively implement core features.**
   * Architecture design, contracts, base implementations, and unit test suites.
   * Tag format: `vX.Y.0-alpha`
3. **`beta` (-2 versions from Release):**
   * Feature freeze stage.
   * Integration testing, performance tuning, regression tests, and edge-case hardening.
   * Tag format: `vX.Y.0-beta`
4. **`release` (Final / Production):**
   * Fully consolidated, benchmarked, and stabilized version ready for production consumption.
   * Tag format: `vX.Y.0`

---

## 📊 Release Train Progression Matrix

As the framework evolves, the pipeline relationship across channels progresses as follows:

| Global Milestone | Canary (Pre-Alpha) | Alpha (Devs: -4) | Beta (-2) | Release (Production) |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | v0.5.0-canary | v0.1.0-alpha | — | — |
| **Phase 2** | v0.6.0-canary | v0.2.0-alpha | — | — |
| **Phase 3** | v0.7.0-canary | v0.3.0-alpha | v0.1.0-beta | — |
| **Phase 4** | v0.8.0-canary | v0.4.0-alpha | v0.2.0-beta | — |
| **Phase 5** | v0.9.0-canary | v0.5.0-alpha | v0.3.0-beta | **v0.1.0 (GA)** |
| **Phase 6** | v1.0.0-canary | v0.6.0-alpha | v0.4.0-beta | **v0.2.0 (GA)** |
| **Phase 7** | — | v0.5.0-alpha | — | **v0.3.0 (GA)** |
| **Phase 8** | — | — | — | **v0.4.0 (GA)** |
| **Phase 9** | — | **v0.5.0-alpha (Completed)** | v0.5.0-beta | **v0.5.0 (Upcoming)** |

---

## 📌 Minor Versions & Feature Breakdown

### 🔹 v0.1.0 — "Foundation & Core Architecture" *(Status: Released)*
* **Objective:** Clean, lightweight core foundation, typed configuration, and an agnostic LLM client.
* **Features:**
  * Package setup via `pyproject.toml` (PEP 517/518/621).
  * Centralized typed settings via `pydantic-settings` (`core/config.py`).
  * Unified async LLM client for Gemini, OpenAI, Ollama, DeepSeek, and Groq.
  * Native Structured Outputs with Pydantic validation.
  * Standard exception hierarchy and exponential backoff on transient errors (`RateLimitError`).

---

### 🔹 v0.2.0 — "Tooling & MCP Engine" *(Status: Released)*
* **Objective:** Universal tool ecosystem and native Model Context Protocol (MCP 2.x) support.
* **Features:**
  * `@tool` decorator with automatic JSON Schema generation via Pydantic introspection.
  * Centralized `ToolRegistry` for tool discovery and dynamic execution.
  * Official MCP 2.x server adapter (`FrameworkMCPServer`) covering **Tools, Resources, and Prompts**.
  * **Dual-Use Tools:** A single Python function serves both internal LLM agent workflows and external MCP clients.
  * Transport support for **stdio** (Cursor, Claude Desktop, Antigravity) and **SSE** (Web / HTTP / ASGI).

---

### 🔹 v0.3.0 — "Modular RAG Core" *(Status: Released)*
* **Objective:** Modular, decoupled Retrieval-Augmented Generation (RAG) engine.
* **Features:**
  * Abstract interfaces for Embeddings and Vector Stores.
  * `InMemoryVectorStore` with cosine similarity, zero external database requirements, metadata filtering (`filter_metadata`), and JSON persistence.
  * Text chunking with `CharacterTextSplitter` and `RecursiveTextSplitter` with lineage tracking (`parent_id`, `chunk_index`).
  * `RAGEngine` orchestrator with direct MCP export via `engine.as_tool()`.

---

### 🔹 v0.4.0 — "FastAPI Service & Hybrid Endpoints" *(Status: Released)*
* **Objective:** Production-grade REST API, Okapi BM25 hybrid search, and seamless MCP-over-SSE gateway integration.
* **Features:**
  * Reusable FastAPI application factory `create_app` with standard CORS, liveness (`/healthz`), and readiness (`/readyz`) probes.
  * Automated exception handlers translating framework exceptions to standard HTTP status codes.
  * Pre-built REST router `create_rag_router` (`/rag/query`, `/rag/retrieve`, `/rag/ingest`, `/rag/collections`).
  * Pure-Python Okapi BM25 indexing and Reciprocal Rank Fusion (RRF) for hybrid retrieval (`mode="hybrid"`).
  * Multi-tenant collection isolation with independent BM25 statistics and multi-collection persistence.
  * Native mounting utility `mount_mcp_to_fastapi` with built-in `MCPAuthMiddleware` supporting Bearer, `X-API-Key`, and query parameter auth.
* **Security Hardening & Bug Fixes (Consolidated):**
  * Fixed CORS specification vulnerability (wildcard origin disabled credentials by default to prevent cross-origin credential theft).
  * Hardened MCP auth middleware (query-param auth default changed to `False` to prevent key exposure in access logs).
  * Corrected `cosine_similarity` rank inversion (uniform `[-1.0, 1.0]` mapping preventing negative scores from outranking orthogonal vectors).
  * Prevented vector store data loss on corrupt JSON in `load_from_json` via pre-validation.
  * Fixed BM25 duplicate document frequency accumulation during document re-indexing (upsert semantics).
  * Enforced validation on retrieval modes in `RAGEngine.retrieve` (raising `ValueError` on unknown modes).
  * Handled empty choices and `message=None` in LLM client without crashing.
  * Added RFC 7235 `WWW-Authenticate: Bearer` challenge header to HTTP 401 responses.
  * Suppressed internal exception details in production responses.
  * Expanded regression test suite to 103 automated tests (44 new edge-case and security tests).

---

### 🔹 v0.5.0 / v0.5.1 — "Enterprise Resilience, Hardening & Concurrency" *(Status: Released)*
* **Objective:** Multi-provider resilience, streaming generation, enterprise security hardening, and 100% SLA compliance.
* **Tracked Tasks & Features:**
  * **Bloque C Core:**
    * Multi-provider model fallbacks with automatic failover (`FallbackLLMClient`, `AnthropicClient`, `AllProvidersFailedError`).
    * Native Server-Sent Events (SSE) streaming (`complete_stream()`, `RAGEngine.query_stream()`, `/rag/query/stream`).
    * In-memory semantic caching with cosine similarity, LRU eviction, and TTL (`SemanticCache`, `CachedLLMClient`).
  * **Security & Isolation:**
    * Path traversal validation in VectorStore persistence (`save_to_json` / `load_from_json`).
    * Structured XML tagging for context injection in RAG synthesis to mitigate indirect prompt injection.
    * Secret masking using `pydantic.SecretStr` and frozen settings singleton.
    * Configurable DNS rebinding and SSRF protection across HTTP servers and clients.
  * **Concurrency & Resource Management:**
    * Thread/coroutine safety locks for `InMemoryVectorStore`.
    * Non-blocking async execution for synchronous tools via `asyncio.to_thread`.
    * Shared persistent `httpx.AsyncClient` connection pools.
  * **Performance & 100% SLA Benchmark Compliance (v0.5.1):**
    * Bounded memoization for recursive text splitting (`0.005 ms` average, 127k ops/s).
    * Vectorized metadata filter index caching with BLAS GEMV and `np.argpartition` (`0.130 ms`).
    * LRU cached deterministic vector generation in `MockEmbeddingProvider` (`0.150 ms`).

---

### 🔹 v0.6.0 — "Autonomous ReAct Agent Framework" *(Status: In Backlog — EPIC-04)*
* **Objective:** Lightweight autonomous agent reasoning loop, conversation memory, and human approval safeguards.
* **Features & Tasks:**
  * **FEAT-04 / TASK-14:** `ReActAgent` execution loop with Thought-Action-Observation cycle and iteration limits.
  * **FEAT-05 / TASK-15:** Short-term conversation message buffer and vector-augmented long-term memory.
  * **FEAT-06 / TASK-16:** Human-in-the-loop (HITL) pre-execution confirmation hooks for sensitive tools.

---

### 🔹 v0.7.0 — "Observability, Telemetry & Cost Accounting" *(Status: In Backlog — EPIC-05)*
* **Objective:** Production distributed tracing, Prometheus metrics export, and dynamic token cost tracking.
* **Features & Tasks:**
  * **FEAT-07:** Token usage & cost attribution engine with configurable provider pricing tables.
  * **FEAT-08:** Prometheus metrics exporter & OpenTelemetry tracing middleware for HTTP, LLM, and vector search.
  * **TASK-17:** Distributed trace context propagation (`traceparent`, `tracestate`) across async pipelines.

---

### 🔹 v0.8.0 — "RAG Quality Evaluation & Guardrails" *(Status: In Backlog — EPIC-06)*
* **Objective:** Zero-external-dependency RAG Triad quality metrics, content moderation, and PII redaction.
* **Features & Tasks:**
  * **FEAT-09:** Lightweight RAG Triad evaluation suite (Context Relevance, Groundedness, Answer Relevance).
  * **FEAT-10:** Input/Output guardrails and regex/pattern PII sanitization filters.
  * **TASK-18:** Statistical and embedding-based hallucination detection heuristics and grounding confidence scores.

---

### 🔹 v0.9.0 — "Framework CLI & Developer Experience" *(Status: In Backlog — EPIC-07)*
* **Objective:** Unified CLI utility for project scaffolding, standalone MCP server runners, and diagnostic doctor.
* **Features & Tasks:**
  * **FEAT-11:** Unified framework CLI (`ai-rag` / `rag-mcp`) for microservice bootstrapping and runtime serving.
  * **FEAT-12:** Interactive terminal REPL & playground for testing prompts, tools, and vector stores live.
  * **TASK-19:** Configuration doctor (`ai-rag doctor`) verifying environment variables, Ollama status, and ports.

---

### 🔹 v1.0.0 — "Production GA & Enterprise MVP" *(Status: In Backlog — EPIC-08)*
* **Objective:** Frozen public API, enterprise containerization, high-concurrency stress testing, and documentation portal.
* **Features & Tasks:**
  * **FEAT-13:** Public API contract freeze and runtime deprecation warnings architecture.
  * **FEAT-14:** Multi-stage production Dockerfiles, Docker Compose production profiles, and Kubernetes manifests.
  * **TASK-20:** Enterprise SLA stress testing (10,000+ requests, 100+ concurrent users) with zero memory leaks.
  * **TASK-21:** Comprehensive developer documentation portal, cookbooks, and end-to-end tutorials.
