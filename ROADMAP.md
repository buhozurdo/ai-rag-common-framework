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

## 📌 Release History Summary (v0.1.0 – v0.18.0)

| Version | Status | Milestone Focus |
|---|:---:|---|
| **v0.1.0** | Released | Foundation, Unified Agnostic LLM Client & Typed Settings |
| **v0.2.0** | Released | Tooling Engine & Native MCP 2.x Server (stdio/SSE) |
| **v0.3.0** | Released | Modular RAG Core, Text Splitters & InMemoryVectorStore |
| **v0.4.0** | Released | FastAPI Gateway, Okapi BM25 & Hybrid Retrieval |
| **v0.5.0** | Released | Enterprise Resilience, Fallbacks, Streaming & Semantic Cache |
| **v0.5.1** | Released | Performance SLA Benchmarks & Vector BLAS Optimization |
| **v0.7.0** | Released | Bidirectional MCP Client & Tool Mounting |
| **v0.8.0** | Released | Cross-Encoder Rerankers (Local & Cohere) & Two-Stage RAG |
| **v0.9.0** | Released | Autonomous ReAct Agent Framework & Human-in-the-Loop Hooks |
| **v0.10.0** | Released | Input/Output Guardrails, Prompt Moderation & PII Sanitizer |
| **v0.11.0** | Released | Observability, Token Cost Tracker & Prometheus Metrics |
| **v0.12.0** | Released | Lightweight RAG Triad Evaluator & Hallucination Detector |
| **v0.13.0** | Released | Unified Framework CLI (`ai-rag`), REPL & Doctor Diagnostics |
| **v0.14.0** | Released | Multi-Query & HyDE Retrieval Strategies with Multi-List RRF |
| **v0.15.0** | Released | Embedded SQLite Vector Store with FTS5 BM25 Hybrid Search |
| **v0.16.0** | Released | Multi-Agent Supervisor & Shared State Bus (`AgentBus`) |
| **v0.17.0** | Released | Stateful Directed Graph Workflow Engine (`StateGraph`) |
| **v0.18.0** | Released | Ephemeral Code Interpreter & Tool Sandboxing |

---

## 🔮 Future Development Milestones & Epics Backlog

To maintain a competitive edge against monolithic frameworks (LangChain, LlamaIndex) while preserving our **zero-bloat, stdlib-first, type-safe architecture**, the following epics are scheduled:

---

### 🔹 v0.19.0 — "Multimodal Vision & Media Intelligence" *(EPIC-11)*
* **Objective:** Enable multi-modal understanding for vision-capable models (Gemini 2.0, GPT-4o, Claude 3.5), allowing agents and RAG to process charts, diagrams, screenshots, and visual documents.
* **Features & Tasks:**
  * **FEAT-22:** Multimodal `ChatMessage` supporting structured content blocks (text, base64 images, remote URLs).
  * **FEAT-23:** Visual RAG support: image metadata extraction, figure descriptions, and multimodal embedding integration.
  * **FEAT-24:** Built-in `image_inspector` tool for agents to inspect visual assets dynamically during reasoning loops.

---

### 🔹 v0.20.0 — "Native Document Loaders & Context-Aware Parsers" *(EPIC-12)*
* **Objective:** Native, zero-heavy-dependency document ingestion for common enterprise formats (PDF, Markdown, CSV, JSON, HTML) with hierarchical context tracking.
* **Features & Tasks:**
  * **FEAT-25:** Zero-bloat file loaders: `DirectoryLoader`, `MarkdownLoader`, `CSVLoader`, `PDFLoader` with page/header metadata.
  * **FEAT-26:** Context-aware chunking: Parent Document Retriever and Sentence-Window chunking preserving adjacent context windows.
  * **FEAT-27:** Batch document ingestion CLI command (`ai-rag ingest --dir ./docs --store sqlite`).

---

### 🔹 v0.21.0 — "Durable Workflow State, Checkpointing & Time Travel" *(EPIC-13)*
* **Objective:** Transactional persistence for `StateGraph` workflows allowing executions to be paused, persisted across server restarts, resumed via API, or rewound (*time travel*).
* **Features & Tasks:**
  * **FEAT-28:** `SQLiteCheckpointSaver`: Persistent disk backend storing graph execution snapshots indexed by `thread_id` and `checkpoint_id`.
  * **FEAT-29:** Workflow resumption and Human-in-the-Loop pause gates resuming execution via HTTP REST triggers.
  * **FEAT-30:** Graph time travel and branching (`graph.rewind(thread_id, step)`), allowing state inspection and alternate branch execution.

---

### 🔹 v0.22.0 — "Self-Correcting Agent Validation & Dependency Injection" *(EPIC-14)*
* **Objective:** Eliminate brittle agent tool failures by feeding Pydantic validation errors directly back into LLM reasoning loops, and support clean dependency injection.
* **Features & Tasks:**
  * **FEAT-31:** Self-correcting tool execution loop: Automatic reflection capturing `ValidationError` and reprompting the model with error diagnostics.
  * **FEAT-32:** Contextual Dependency Injection (`ExecutionContext[T]`): Clean runtime injection of database pools, auth tokens, and tenant IDs into `@tool` functions.
  * **FEAT-33:** Dynamic system prompts: State-dependent system prompt generators that evolve as workflows progress.

---

### 🔹 v0.23.0 — "Real-Time Event Streaming for Multi-Agent & Workflows" *(EPIC-15)*
* **Objective:** Real-time visibility into multi-agent and state graph execution via granular, asynchronous event generators and Server-Sent Events (SSE).
* **Features & Tasks:**
  * **FEAT-34:** Asynchronous event streaming generator (`graph.stream()`, `supervisor.stream()`) yielding node transitions, token chunks, and tool execution events.
  * **FEAT-35:** FastAPI Workflow SSE router exposing `/workflows/{name}/stream` for live web dashboards.
  * **FEAT-36:** OpenTelemetry distributed step spans for each graph transition and agent delegation step.

---

### 🔹 v0.24.0 — "Enterprise Tenant Governance & Budget Enforcers" *(EPIC-16)*
* **Objective:** Production multi-tenant safety controls enforcing strict spending caps, rate limiters, and quota management.
* **Features & Tasks:**
  * **FEAT-37:** Hard spending limit enforcer: Proactively blocking LLM calls when tenant token or dollar budget thresholds are reached.
  * **FEAT-38:** Sliding-window rate limiter per tenant / API key integrated with FastAPI middleware.
  * **FEAT-39:** Tenant audit ledger and spending breakdown exporter.

---

### 🔹 v1.0.0 — "Production GA & Enterprise MVP" *(EPIC-08)*
* **Objective:** Final API freeze, enterprise containerization, concurrency certification, and developer documentation portal.
* **Features & Tasks:**
  * **FEAT-13:** Public API contract freeze and runtime deprecation warning system.
  * **FEAT-14:** Production multi-stage Dockerfiles, Docker Compose profiles, and Kubernetes manifests.
  * **TASK-20:** Concurrency certification under high load (10,000+ requests, 100+ concurrent workers) with zero memory leaks.
  * **TASK-21:** Comprehensive developer documentation portal, cookbooks, and end-to-end tutorials.
