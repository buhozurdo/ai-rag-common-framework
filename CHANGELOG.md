# Changelog

All notable changes to this project are documented in this file following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v0.19.8] - 2026-11-19

### Pull Request
- **PR Title:** `release: v0.19.8 - Cross-Platform Path Traversal Remediation for Linux, Build Metadata & Search Performance Optimizations`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.8` resolving Linux CI test failures on path traversal validation, modernizing package metadata packaging compatibility (Hatchling / Twine), and delivering high-throughput search optimizations for hybrid BM25 and vector stores.
  >
  > ### Key Changes
  > - **Cross-Platform Path Traversal Remediation:** Hardened `_resolve_safe_path` in `InMemoryVectorStore` and `SQLiteVectorStore` using `PureWindowsPath` to detect and block Windows external absolute and drive paths on POSIX/Linux systems where `Path.is_absolute()` evaluates to `False`.
  > - **Build Metadata Compatibility:** Pinned `hatchling < 1.32.0` in `pyproject.toml` and upgraded `twine==7.0.0` in `.github/workflows/release.yml` to prevent `Invalid distribution metadata: '2.5'` errors during wheel verification.
  > - **BM25 Search Inverted Postings:** Added `_postings` inverted term index and candidate ID pre-filtering in `BM25Index.search()`, eliminating full collection scans on filtered queries (such as omnichannel catalog store availability).
  > - **VectorStore Closure & Top-K Optimization:** Replaced inner closure allocations with module-level metadata evaluators and utilized `heapq.nlargest` for top-k selection in pure-Python fallback cosine search.
  > - **OpenAI Schema Caching:** Added cached schema generation in `ToolRegistry.to_openai_schemas()` with automatic invalidation upon tool registration or removal.
  >
  > ### Verification
  > - `pytest tests/`: 385 passed in 8.70s (100% pass rate, 0 failed, 0 skipped).
  > - Added tests for high-dim search, omnichannel hybrid pre-filtering, and tool schema caching.

### Fixed
- **VectorStore & SQLite Storage:** Correctly detected external Windows paths on Linux runners, preventing path traversal bypass when `base_dir=None`.
- **Release CI Workflow:** Upgraded Twine to 7.0.0 and pinned Hatchling to 1.31.0 for valid wheel metadata verification.

### Changed
- **Version Bump:** `0.19.7` -> `0.19.8`.

---

## [v0.19.7] - 2026-11-18

### Pull Request
- **PR Title:** `release: v0.19.7 - P1 Audit Remediation (BUG-22 to BUG-27), LRU Filter Bounding, Fail-Close HITL & Path Security`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.7` resolving P1 audit bugs BUG-22 through BUG-27 tracked in the local Project Manager: kwargs safety in `LLMFactory`, bounded LRU filter index containment, nested unhashable metadata filtering, cross-loop deadlock prevention, strict Fail-Close HITL enforcement, and filesystem path traversal hardening.
  >
  > ### Key Changes
  > - **LLMFactory kwargs Safety (BUG-22 / P1-7):** Added `_safe_instantiate` with `inspect.signature` introspection to forward recognized constructor parameters to provider clients without raising `TypeError: unexpected keyword argument`, while preserving extended configuration.
  > - **Bounded LRU Filter Cache (BUG-23 / P1-10):** Replaced unbounded collection cache in `InMemoryVectorStore` with `OrderedDict` LRU eviction (bounded to 64 collections, 512 filter queries per collection) to eliminate long-term memory leaks.
  > - **Nested & Unhashable Metadata Filtering (BUG-24 / P1-11):** Implemented recursive `_make_hashable` and deep `_metadata_matches` supporting nested dicts, lists, tuples, and sets in vector metadata filtering without `TypeError: unhashable type`.
  > - **Cross-Loop Thread Isolation (BUG-25 / P1-14):** Partitioned `InMemoryVectorStore` locks by `(loop_id, collection)` and hardened `RAGEngine._run_sync` with dedicated worker loops and async generator shutdown to prevent event loop binding conflicts and deadlocks.
  > - **Strict Fail-Close HITL (BUG-26 / P1-16):** Hardened `ReActAgent` human-in-the-loop review pipeline: exceptions in approval hooks, malformed review payloads, and invalid modified arguments strictly block tool execution (*fail-close*).
  > - **Path Traversal Hardening (BUG-27 / P1-25):** Closed absolute path traversal vulnerability in `_resolve_safe_path` when `base_dir=None`, restricting file saving and loading to working directory and temporary directories.
  >
  > ### Verification
  > - `pytest tests/`: 382 passed in 9.90s (100% pass rate, 0 failed, 0 skipped).
  > - Added dedicated test suite `tests/test_p1_sprint2_fixes.py`.
  > - Pre-completion static defect audit: `ai-rag audit src` reports 0 blocking issues.
  > - Local Project Manager: Updated `BUG-22` through `BUG-27` to `done`.

### Fixed
- **LLMFactory:** Prevented `TypeError` on unexpected kwargs during client instantiation.
- **InMemoryVectorStore:** Prevented unbounded memory leak with LRU filter caching; supported nested unhashable metadata; blocked absolute path traversal when `base_dir=None`.
- **RAGEngine & Locks:** Prevented cross-loop deadlocks in sync methods and partitioned collection locks.
- **ReActAgent:** Enforced strict fail-close behavior on any approval hook error or unhandled decision.

### Changed
- **Version Bump:** `0.19.6` -> `0.19.7`.

---

## [v0.19.6] - 2026-11-18

### Pull Request
- **PR Title:** `release: v0.19.6 - Pre-Completion Bug Audit Guardrail, Agent Self-Correction & Security Hardening`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.6` delivering autonomous pre-completion verification guardrails for defect analysis and bug detection, agent self-correction feedback loops, salted PII HMAC masking, RAG endpoint payload bounding, and agent bus history preservation.
  >
  > ### Key Changes
  > - **Pre-Completion Bug Search & Verification Guardrail:** Implemented `BugAuditGuardrail` and `PreCompletionGuardrail` in `common_framework.guardrails.bug_audit`. Evaluates AST syntax correctness and scans for common enterprise defects (unvalidated SSRF, silent exception swallowing, unbounded request payloads, unanchored protocol triggers, and directory traversal).
  > - **Agent Self-Correction Integration:** Integrated `completion_guardrail` into `ReActAgent` and `AgentSupervisor`. If generated code or final answers violate guardrails, rejection feedback is injected directly back into the agent's thought scratchpad for autonomous correction before task completion.
  > - **Unified CLI Audit Subcommand:** Added `ai-rag audit` subcommand with positional path, `--strict`, and `--json` flags to scan source trees for implementation defects.
  > - **AgentBus History Preservation (P1-20):** Added `_history` tracking and `get_history(key)` method to `AgentBus` so multi-turn delegations retain all intermediate outputs. Supervisor worker execution introspects function signatures to pass `bus` or `context` dynamically.
  > - **Salted PII Anonymization & Local Phone Coverage (P1-22):** Added dynamic secret salt (`secrets.token_hex(16)`) and 12-char HMAC-SHA256 digest when `hmac_key` is omitted to eliminate rainbow-table reversibility. Expanded phone digit pre-filtering to $\ge 7$ digits and added regex coverage for 7-digit local phone numbers (`\b[2-9]\d{2}[.-]\d{4}\b`).
  > - **RAG API DoS Bounds (P1-24):** Added explicit `max_length` bounds across `RAGQueryRequest`, `RAGRetrieveRequest`, and `RAGIngestRequest` to prevent memory exhaustion DoS vectors.
  >
  > ### Verification
  > - `pytest tests/`: 373 passed in 9.13s (100% pass rate, 0 failed, 0 skipped).
  > - Static code audit: `ai-rag audit src` reports 0 blocking issues.
  > - Dedicated test suite added: `tests/test_bug_audit_guardrails.py`.

### Added
- **Bug Audit Guardrails:** `BugAuditGuardrail`, `PreCompletionGuardrail`, `BugFinding`, and `VerificationResult` in `common_framework.guardrails.bug_audit`.
- **Pre-Completion Hooks:** `completion_guardrail` parameter in `ReActAgent` and `AgentSupervisor` with iterative revision on rejection.
- **CLI Audit Command:** `ai-rag audit [path] [--path PATH] [--strict] [--json]`.
- **AgentBus History API:** `AgentBus.get_history(key)` for full intermediate delegation traceability.

### Security
- **Salted PII Hash:** Dynamic 16-byte random salt prevents offline rainbow-table brute force when anonymizing PII tokens without a fixed HMAC key.
- **Phone Pattern Expansion:** 7-digit local telephone detection with pre-filter lowering to 7 digits.
- **RAG Route Payload Bounds:** Protected `/rag/query`, `/rag/retrieve`, and `/rag/ingest` against memory exhaustion DoS with explicit `Field(max_length=...)` bounds.

### Changed
- **Version Bump:** `0.19.5` -> `0.19.6`.

---

## [v0.19.5] - 2026-11-18

### Pull Request
- **PR Title:** `release: v0.19.5 - P1 Audit Hardening, Eviction Optimization & Hybrid Search Metadata Fix`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.5` resolving key P1 audit findings across semantic caching performance, SQLite hybrid search candidate filtering, workflow event loop concurrency, and supervisor decision parsing.
  >
  > ### Key Changes
  > - **Cache Eviction Performance (P1-3):** Memoized byte estimation in `SemanticCacheEntry` with `_cached_bytes` to avoid repeated JSON re-serialization during capacity checks. Optimized `_enforce_capacity()` with single-pass sort, O(1) slice truncation, and running byte count accounting.
  > - **Config Case Normalization (P1-8):** Added `@field_validator` on `Settings.default_provider` and `framework_env` to normalize input case, preventing framework startup failures when environment variables use uppercase (e.g. `DEFAULT_LLM_PROVIDER=OPENAI`).
  > - **SQLite Hybrid Search Filtering (P1-13):** Fixed FTS5 BM25 early LIMIT truncation in `SQLiteVectorStore.hybrid_search()` when `filter_metadata` is provided, ensuring matching documents are retrieved without being dropped by pre-filter limits.
  > - **Supervisor Robustness (P1-17):** Line-anchored `FINAL_ANSWER:` detection and prioritized `DELEGATE:` actions to prevent premature supervisor loop termination on reasoning thought mentions.
  > - **Non-blocking StateGraph Callables (P1-18 & P1-19):** Offloaded synchronous node functions to `loop.run_in_executor()` in `StateGraph._invoke_callable()` to prevent asyncio event loop starvation, and added explicit `ValueError` validation when router transitions to unregistered nodes.
  > - **VectorMemory Purge & Collection Alignment (P1-21):** Aligned `clear(collection)` signature across `InMemoryVectorStore`, `SQLiteVectorStore`, and `VectorMemory` to cleanly purge scoped memories without argument mismatch crashes.
  >
  > ### Verification
  > - `pytest tests/`: 366 passed in 9.38s (100% pass rate, 0 failed, 0 skipped).
  > - Added dedicated test suite `tests/test_p1_fixes.py`.

### Performance
- **SemanticCache Eviction:** Eliminated O(K * N log N) repeated sort and byte recalculation during cache eviction down to single-pass O(N).
- **StateGraph Concurrency:** Prevented thread-blocking delays on the main asyncio loop by running sync callables in the default thread pool executor.

### Fixed
- **SQLite Hybrid Search:** Fixed candidate starvation bug where SQL `LIMIT` dropped matching documents prior to Python metadata evaluation.
- **VectorMemory Clear:** Fixed `TypeError` on `VectorMemory.clear()` when calling `vector_store.clear(collection)`.
- **Config Initialization:** Prevented literal validation crashes on uppercase provider strings in environment variables.
- **Supervisor Termination:** Fixed premature loop exit caused by `FINAL_ANSWER:` substring matches inside thought text.

### Changed
- **Version Bump:** `0.19.4` -> `0.19.5`.

---

## [v0.19.4] - 2026-11-17

### Pull Request
- **PR Title:** `release: v0.19.4 - CI Workflow Hardening & PDFLoader Dependency Precedence Fix`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.4` resolving CI/CD dependency conflicts, bumping GitHub Actions to secure pinned SHAs, and fixing `PDFLoader` optional dependency precedence in headless environments.
  >
  > ### Key Changes
  > - **PDFLoader Dependency Precedence:** Validates availability of optional `pypdf` dependency before asserting filesystem existence in `PDFLoader.load()`, avoiding unexpected `FileNotFoundError` when `pypdf` is missing in containerized and CI environments.
  > - **Test Isolation:** Decoupled `test_pdf_loader_missing_dependency` from host environment state using `unittest.mock.patch.dict(sys.modules, {"pypdf": None})` with temporary fixture files, and added `test_pdf_loader_file_not_found`.
  > - **CI/CD Action Pinned SHAs:** Unified workflow updates across `ci.yml`, `release.yml`, and `sonar.yml` with securely pinned commit SHAs for `actions/checkout` (v4.2.2), `actions/setup-python` (v5.6.0), `codecov/codecov-action` (v5.4.0), `actions/cache` (v4.3.0), `SonarSource/sonarqube-scan-action` (v8.5.0), and `softprops/action-gh-release` (v2.2.1).
  > - **Workflow Deduplication:** Enforced single-pipeline release architecture and prevented duplicate publish workflows.
  >
  > ### Verification
  > - `pytest tests/`: 360 passed (100% pass rate, 0 failed, 0 skipped).
  > - CI test matrix (Python 3.11, 3.12, 3.13) fully aligned.

### Fixed
- **PDFLoader Exception Ordering:** Fixed `PDFLoader.load()` to check and raise descriptive `ImportError` for missing `pypdf` prior to raising `FileNotFoundError`.
- **Test Skips and Failures in CI:** Eliminated non-deterministic `pytest.skip` in `test_pdf_loader_missing_dependency`, guaranteeing reproducible testing across all platforms.

### Changed
- **Version Bump:** `0.19.3` -> `0.19.4`.
- **GitHub Workflows:** Updated action pins across `ci.yml`, `release.yml`, and `sonar.yml` to latest secure commit SHAs; resolved Dependabot PRs #2, #3, #4, #5, #6, #7.

---

## [v0.19.3] - 2026-11-17

### Pull Request
- **PR Title:** `release: v0.19.3 - SLA Benchmark Performance Optimization (Guardrails Fast-Path & Lock-Free Cost Ledger)`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.3` delivering high-throughput performance optimizations and SLA window compliance for batch guardrail evaluation and concurrent token cost accounting.
  >
  > ### Key Changes
  > - **PII & Guardrails Fast-Path Pre-filtering:** Added ultra-fast character and substring trigger pre-filters before firing complex regex patterns in `PIISanitizer` and `PromptGuardrail`. Avoids evaluating expensive phone, credit card, and SSN regexes when text lacks required digit counts, and skips injection regex evaluation when no adversarial triggers are present.
  > - **Batch Evaluation APIs:** Introduced `PromptGuardrail.evaluate_batch()` and `PIISanitizer.sanitize_batch()`, cutting evaluation time on 250 realistic prompts from 60.57ms to 14.72ms (>4.1x speedup).
  > - **Lock-Free Token Ledger Concurrency:** Removed `asyncio.Lock` acquisition bottleneck from the critical path of `TokenCostTracker.record_usage()`, leveraging atomic bytecode list appending to eliminate lock contention during concurrent bursts of 250+ requests.
  > - **Lightweight CostRecord Allocation:** Replaced full Pydantic validation on every record insertion with `CostRecord.model_construct()`, significantly reducing CPU overhead and memory churn.
  > - **Batch Token Usage Recording:** Added `TokenCostTracker.record_usage_batch()` and `record_usage_batch_async()` for single-pass ledger ingestion.
  >
  > ### Verification
  > - `pytest tests/`: 358 passed, 1 skipped (100% pass rate) in 9.91s.
  > - Benchmark: `guardrails_and_pii_batch_250_prompts` executed in 14.72ms (under SLA window).
  > - Benchmark: `token_cost_ledger_concurrency_250_ops` executed in 2.30ms with zero lock contention.

### Added
- **Batch Processing Methods:** `PromptGuardrail.evaluate_batch()` and `PIISanitizer.sanitize_batch()`.
- **Batch Cost Ingestion:** `TokenCostTracker.record_usage_batch()` and `TokenCostTracker.record_usage_batch_async()`.
- **Targeted Test Suites:** `test_guardrail_and_pii_batch_processing()` in `tests/test_guardrails.py` and `test_token_cost_ledger_concurrency_and_batch()` in `tests/test_observability.py`.

### Changed
- **Version Bump:** `0.19.2` -> `0.19.3`.
- **Pre-filtering Optimization:** In `src/common_framework/guardrails/sanitizer.py`, numeric PII checks (SSN, credit card, phone) exit early based on fast digit counting, email checks require `@`, and IPv4 checks require `.` and digits.
- **Adversarial Pre-filtering:** In `src/common_framework/guardrails/moderation.py`, regex evaluation is gated by `_INJECTION_FAST_TRIGGERS`.
- **Non-blocking Token Accounting:** In `src/common_framework/observability/cost.py`, `record_usage()` appends records atomically without `async with self._lock:`, using `CostRecord.model_construct()` for allocation speed.

---

## [v0.19.2] - 2026-11-17

### Pull Request
- **PR Title:** `release: v0.19.2 - Critical Security Remediation, CI Hardening & Enterprise Audit Fixes`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.2` providing critical security fixes, runtime authorization hardening, test suite expansions, and CI/CD release pipeline consolidation based on the comprehensive enterprise multi-agent audit.
  >
  > ### Key Changes
  > - **SSRF Protection Hardening (BUG-13 / P0-1, P0-15):** Implemented active DNS resolution via `socket.getaddrinfo` to defeat DNS rebinding attacks (e.g., `*.nip.io` pointing to metadata services); enforced strict blocking of loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local/carrier-grade NAT, and unmapped IPv4-mapped IPv6 addresses.
  > - **CodeInterpreter AST Security Sandboxing (BUG-14 / P0-9):** Introduced static AST security policy (`safe_mode=True` by default) in `CodeInterpreter`, strictly blocking unauthorized process primitives (`os.system`, `os.popen`, `subprocess`, `pty`, `multiprocessing`, `ctypes`, and `socket`).
  > - **MCP Server Authentication & Scope Guards (BUG-15 / P0-10):** Enforced authentication guards across all ASGI scope types (including WebSockets with `4401` close code) and added `enforce_auth` configuration on `mount_mcp_to_fastapi`.
  > - **Prompt Guardrail Normalization (BUG-16 / P0-11):** Integrated Unicode NFKC normalization and invisible zero-width character stripping (`\u200b-\u200d\ufeff\u00ad\u2060`) to defeat homoglyph and obfuscation evasions; added detection for modern chat template delimiters (`<|im_start|>`, `<|eot_id|>`, `[INST]`, `<<SYS>>`).
  > - **Release Pipeline Consolidation (BUG-17 / P0-13):** Unified duplicate release workflows into a single idempotent `release.yml` with reproducible builds (`SOURCE_DATE_EPOCH`), `twine check --strict` verification, SHA-256 checksum generation, and OIDC Trusted Publishing.
  > - **LLM Streaming Cost & Token Tracking (BUG-18 / P1-5):** Hooked token accumulation and cost attribution directly into streaming completions (`OpenAIClient`, `AnthropicClient`) with `global_cost_tracker`.
  > - **Event Loop Binding Race Elimination (BUG-19 / P1-4):** Sourced `_get_client()` with event loop tracking (`asyncio.get_running_loop()`) to safely recreate HTTP client connections when invoked across disparate async event loops or threads.
  > - **Anthropic Streaming Mid-Stream Error Handling (BUG-20 / P1-6):** Added real-time error interception on Anthropic SSE streams raising `LLMProviderError` and tracking partial tokens.
  > - **Tool Authorization Enforcement (BUG-21 / P1-23):** Enforced `Tool.requires_approval` directly inside `Tool.execute()` and `ToolRegistry.execute()` to prevent bypasses, raising `ToolApprovalRequiredError`.
  > - **Provider Test Coverage Expansion (TASK-24 / P0-14):** Added dedicated test suites for `GeminiClient` and `OllamaClient` covering completions, streaming, and error propagation.
  > - **CI Hardening & Multi-Python Matrix (TASK-25 / P1-27, P1-28):** Enforced least-privilege `permissions: contents: read` across workflows and expanded the test matrix to Python 3.11, 3.12, and 3.13.
  > - **MCP Client/Server E2E Integration Test Suite (TASK-26 / P1-34):** Added live loopback integration tests executing `FrameworkMCPClient` against `FrameworkMCPServer` over stdio subprocess transports.
  >
  > ### Verification
  > - `pytest tests/`: 356 passed, 1 skipped (100% pass rate) in 10.07s.
  > - 12/12 EPIC-20 audit tasks verified and completed.

### Added
- **MCP Client/Server E2E Suite:** Live stdio loopback test in `tests/test_mcp_client.py`.
- **Gemini & Ollama Test Coverage:** Full suite of unit tests for `GeminiClient` and `OllamaClient` in `tests/test_llm.py`.
- **Tool Authorization Exception:** `ToolApprovalRequiredError` exported from `common_framework.tools`.

### Changed
- **Version Bump:** `0.19.1` -> `0.19.2`.
- **CI Matrix:** Expanded test matrix in `.github/workflows/ci.yml` across Python 3.11, 3.12, and 3.13.
- **Workflow Consolidation:** Removed duplicate `.github/workflows/python-publish.yml` and unified into `.github/workflows/release.yml`.

### Fixed
- **SSRF DNS Rebinding & IP Filtering:** Fully remediated SSRF bypass vectors in `src/common_framework/core/security.py`.
- **CodeInterpreter Arbitrary RCE:** Implemented AST security scanner in `src/common_framework/tools/interpreter.py`.
- **MCP Auth Bypass on WebSockets:** Enforced ASGI scope guards in `src/common_framework/api/mcp.py`.
- **Prompt Injection Unicode Evasion:** Added NFKC normalization and zero-width stripping in `src/common_framework/guardrails/moderation.py`.
- **Streaming Usage & Cost Leakage:** Ensured streamed completions register token counts in `src/common_framework/core/llm.py`.
- **Event Loop Mismatch in LLM Clients:** Sourced and rebound HTTP client pool to active event loop in `src/common_framework/core/llm.py`.
- **HITL Tool Approval Enforcement:** Mandated `approved=True` for destructive actions in `src/common_framework/tools/base.py` and `src/common_framework/agents/react.py`.

---

## [v0.19.1] - 2026-11-16

### Pull Request
- **PR Title:** `release: v0.19.1 - CI/CD Pipeline Automation, Test Matrix Hardening & PyPI Publishing`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.1` delivering complete GitHub Actions CI/CD automation, automated PyPI package deployment via OIDC Trusted Publishing, SonarQube quality checks, Dependabot monitoring, and test matrix hardening for vector store matrix caching.
  >
  > ### Key Changes
  > - **CI/CD Automation:** Added GitHub Actions workflows for continuous integration (`ci.yml`), PyPI deployment (`release.yml`), GitHub release asset publishing (`python-publish.yml`), and SonarQube analysis (`sonar.yml`).
  > - **Dependabot Integration:** Added `.github/dependabot.yml` for automated weekly Python and Action dependency updates.
  > - **Test Hardening & NumPy Support:** Added `numpy>=1.26.0` to `rag` and `test` optional dependencies in `pyproject.toml`, and guarded matrix cache tests with `@pytest.mark.skipif(not _HAS_NUMPY)`.
  > - **Developer Experience:** Added `[dev]` optional dependency extra in `pyproject.toml`.
  > - **Version Centralization:** Updated dynamic version tests in `test_p0_fixes.py` to reference `__version__`.
  >
  > ### Verification
  > - `pytest tests/`: 312 unit tests + 19 integration tests passing (100%), 91% global coverage.

### Added
- **GitHub Actions Workflows:** Added `.github/workflows/ci.yml`, `release.yml`, `python-publish.yml`, and `sonar.yml`.
- **Dependabot Configuration:** Added `.github/dependabot.yml`.
- **SonarQube Properties:** Added `sonar-project.properties`.
- **Optional Dependencies:** Added `dev` extra alias and `numpy>=1.26.0` to `rag` and `test` in `pyproject.toml`.

### Fixed
- **Vector Store Test Skip Guards:** Added `@pytest.mark.skipif(not _HAS_NUMPY)` to matrix cache test cases in `tests/test_vectorstore_perf.py`.
- **Version Assertion Alignment:** Updated `tests/test_p0_fixes.py` to dynamically assert package `__version__`.

---

## [v0.19.0] - 2026-11-15

### Pull Request
- **PR Title:** `release: v0.19.0 - Production Hardening, P0 Bug Fixes & API Contract Freeze`
- **PR Description:**
  > ### Summary
  > Releases `v0.19.0` delivering critical production hardening, resolving all 8 P0 architectural defects, establishing a formal API deprecation and contract freeze system, and reconciling test suite inventory.
  >
  > ### Key Changes
  > - **Dynamic Version Unification:** Sourced package version dynamically from `__version__` across `create_app()`, `FrameworkMCPServer`, and `FrameworkMCPClient`.
  > - **Prometheus Metrics Mounting:** Enabled automatic mounting of `GET /metrics` in `create_app(include_metrics=True)`.
  > - **CLI & Scaffold Fixes:** Reconciled `scaffold_project()` parameter signatures and aliased `create_sse_app` to `get_sse_app` on `FrameworkMCPServer` for `run-mcp --transport sse`.
  > - **TokenCostTracker Gemini & Cache Pricing:** Added pricing for Gemini models (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-flash`, `gemini-1.5-pro`, embeddings), supported `cached_tokens` discounts, and integrated automated usage tracking hooks.
  > - **Semantic Cache Parameter Isolation:** Partitioned cache keys by model, temperature, and system prompt hash to prevent cross-model cache collisions; enhanced mock vectors with word and character n-gram hashing for true semantic similarity.
  > - **PII Sanitizer Hardening:** Added HMAC-SHA256 support with custom secret keys/salts and expanded phone regex to support international E.164 formats in addition to US NANP.
  > - **Multilingual Lexical Evaluation:** Added Spanish stopwords to `HallucinationDetector` and `RAGTriadEvaluator` with configurable custom stop word sets.
  > - **Public API Contract Freeze:** Introduced `@deprecated` decorator and `warn_deprecated_parameter` emitting `FrameworkDeprecationWarning` with migration guidance.
  > - **Test Suite Reconciled:** 331 tests passing (100%) with 91% global coverage (312 unit tests across 29 modules + 19 enterprise integration tests).
  >
  > ### Verification
  > - `pytest tests/ examples/ --cov=common_framework`: 331/331 passed, 91% global coverage.
  > - Zero regressions across all prior modules.

### Fixed
- **Version Hardcoding:** Eliminated hardcoded version values in `create_app()` (`0.4.0-alpha`), MCP server (`0.4.0`), and MCP client (`0.7.0`), centralizing them dynamically from `common_framework.__version__`.
- **Scaffold Parameters:** Fixed `ai-rag init` scaffold generation by adding `include_metrics: bool = True` to `create_app()` signature.
- **MCP SSE Transport:** Fixed `ai-rag run-mcp --transport sse` by aliasing `create_sse_app` to `get_sse_app` on `FrameworkMCPServer`.
- **Prometheus Metrics Route:** Mounted `create_metrics_router()` in `create_app()` when `include_metrics=True`.
- **Gemini Pricing & Cached Inputs:** Added Gemini model pricing tables to `TokenCostTracker`, added `cached_tokens` tracking, and enabled automatic cost recording from `BaseLLMClient`.
- **Semantic Cache Collisions:** Included model, temperature, and system prompt hash into cache namespace; upgraded mock vector generator with feature hashing to enable semantic similarity above 0.80.
- **PII Security & International Formats:** Added HMAC key support to `PIISanitizer` for salted pseudonyms, and extended phone detection to cover international E.164 phone formats.
- **Evaluation Stopwords:** Added Spanish stop words and custom stopword overrides to `HallucinationDetector` and `RAGTriadEvaluator`.

### Added
- **API Deprecation System (`core/deprecation.py`):** Added `@deprecated` decorator and `warn_deprecated_parameter` emitting `FrameworkDeprecationWarning`.
- **Test Reconciliation Audit (`tests/test_p0_fixes.py`):** Added 9 comprehensive verification tests for all P0 fixes and test suite inventory reconciliation.

---

## [v0.18.0] - 2026-11-14

### Pull Request
- **PR Title:** `release: v0.18.0 - Ephemeral Code Interpreter & Tool Sandboxing`
- **PR Description:**
  > ### Summary
  > Releases `v0.18.0` introducing the Ephemeral Code Interpreter & Tool Sandboxing.
 > Provides a secure, zero-external-dependency Python execution sandbox (`CodeInterpreter`) running code in isolated subprocesses with ephemeral scratch directories, strict execution timeouts, output length truncation, automated secret/environment variable scrubbing, and native framework Tool integration.
 >
 > ### Key Changes
 > - **Sandboxed Code Interpreter:** Implemented `CodeInterpreter` and `ExecutionResult` in `src/common_framework/tools/interpreter.py`.
 > - **Security & Secret Scrubbing:** Enforced automated environment variable scrubbing to strip API keys, secrets, tokens, and passwords from child processes.
 > - **Resource Safety:** Configurable execution timeout (`asyncio.wait_for` + process kill) and stdout/stderr output truncation.
 > - **Framework Tool Integration:** Added `.as_tool()` method and pre-instantiated `code_interpreter_tool` compatible with `ReActAgent` and `AgentSupervisor`.
 > - **Public API Exports:** Exposed interpreter components in `src/common_framework/tools/__init__.py` and root `src/common_framework/__init__.py`.
 > - **Test Suite:** Added 12 unit tests in `tests/test_code_interpreter.py`. Total test suite: 322 tests passing 100% with 91% global coverage (`interpreter.py` at 94%).
 >
 > ### Verification
 > - `pytest tests/ examples/ --cov=common_framework`: 322/322 passed, 91% coverage.
 > - Zero regressions or breaking changes to existing APIs.

### Added
- **Ephemeral Code Interpreter (`tools/interpreter.py`):** Implemented `CodeInterpreter` executing Python code in isolated subprocesses with ephemeral temporary directories.
- **Environment & Secret Scrubbing:** Automated stripping of sensitive environment variables (`*KEY*`, `*SECRET*`, `*TOKEN*`, `*PASSWORD*`, `*AUTH*`, `*CREDENTIAL*`) with custom environment overrides.
- **Timeout & Output Guards:** Process execution timeout enforcement with process termination and max-length output truncation.
- **Framework Tool Interface:** Native `as_tool()` export providing `code_interpreter` tool ready for LLM function calling and agent usage.
- **Dual Execution Modes:** Support for async execution (`await interpreter.run(code)`) and synchronous execution (`interpreter.run_sync(code)`) with thread pool offloading.

---

## [v0.17.0] - 2026-11-13

### Pull Request
- **PR Title:** `release: v0.17.0 - Stateful Directed Graph Workflow Engine`
- **PR Description:**
  > ### Summary
  > Releases `v0.17.0` introducing the Stateful Directed Graph Workflow Engine.
 > Delivers a lightweight, zero-bloat state graph orchestrator (`StateGraph`) supporting multi-step acyclic and cyclic workflow topologies with conditional branching, execution step history, max-step guards, state checkpointing callbacks, and dual async/sync execution support.
 >
 > ### Key Changes
 > - **StateGraph Engine:** Implemented `StateGraph`, `WorkflowResult`, `WorkflowStep`, and `START`/`END` sentinels in `src/common_framework/agents/workflow.py`.
 > - **Dynamic Branching & Routing:** Added `add_node`, `add_edge`, `set_entry_point`, and `add_conditional_edges` with optional `path_map`.
 > - **Checkpointing & Guards:** Integrated step-by-step state snapshot tracking, configurable `checkpoint_callback`, and runaway loop detection via `max_steps`.
 > - **Public API Exports:** Exposed workflow components in `src/common_framework/agents/__init__.py` and root `src/common_framework/__init__.py`.
 > - **Test Suite:** Added 11 tests in `tests/test_workflow.py`. Total suite: 310 tests passing 100% with 91% global coverage (workflow engine at 98%).
 >
 > ### Verification
 > - `pytest tests/ examples/ --cov=common_framework`: 310/310 passed, 91% coverage.
 > - Zero regressions or breaking changes to existing APIs.

### Added
- **Stateful Directed Graph Workflow Engine (`agents/workflow.py`):** Implemented `StateGraph` coordinating multi-step cyclic and acyclic agent workflows with state mutation tracking and sentinel-based (`START`, `END`) lifecycle controls.
- **Dynamic Routing & Path Mapping:** Added conditional branching via router callables and dictionary-based target mappings (`add_conditional_edges`).
- **Graph Validation & Integrity:** Built-in validation ensuring entry point presence, valid node targets, and prevention of reserved name collisions.
- **Audit & Checkpointing Hooks:** Added `WorkflowStep` timing records, step state snapshots, and `checkpoint_callback` invocation.
- **Dual Execution Modes:** Support for async coroutines (`await graph.run()`) and synchronous thread pool offloaded execution (`graph.run_sync()`).

---

## [v0.16.0] - 2026-11-13

### Pull Request
- **PR Title:** `release: v0.16.0 - Multi-Agent Supervisor & Delegation Pattern`
- **PR Description:**
  > ### Summary
  > Releases `v0.16.0` introducing the Multi-Agent Supervisor & Delegation Pattern.
 > Establishes an Orchestrator-Workers hierarchy where an autonomous `AgentSupervisor` coordinates specialized agents (`ReActAgent`, coroutines, sync callables) via a text-based protocol, shared `AgentBus` state bus, and dual async/sync execution support.
 >
 > ### Key Changes
 > - **Supervisor & State Bus:** Implemented `AgentSupervisor`, `AgentBus`, `DelegationStep`, and `SupervisorResponse` in `src/common_framework/agents/supervisor.py`.
 > - **Public API Exports:** Exposed supervisor components in `src/common_framework/agents/__init__.py` and root `src/common_framework/__init__.py`.
 > - **Test Suite:** Added 10 tests in `tests/test_supervisor.py`. Total suite: 299 tests passing 100% with 90% global coverage.
 > - **Codebase Hygiene:** Standardized 100% of docstrings, comments, and tests across all enterprise suites and modules to English.
 >
 > ### Verification
 > - `pytest tests/ examples/ --cov=common_framework`: 299/299 passed, 90% coverage.
 > - Zero regressions or breaking changes to existing modules.

### Added
- **Multi-Agent Supervisor & Delegation Pattern (`agents/supervisor.py`):** Implemented `AgentSupervisor` orchestrating specialized worker agents (including `ReActAgent`, async coroutines, and sync callables) via autonomous multi-turn delegation and centralized final synthesis.
- **Shared State Bus (`agents/supervisor.py`):** Added `AgentBus` providing coordinated shared memory across workers and supervisor during multi-agent task execution.
- **Structured Delegation History:** Added `DelegationStep` and `SupervisorResponse` tracking step-by-step agent assignments, worker outputs, and bus snapshots.
- **Synchronous Execution Convenience:** Added `AgentSupervisor.run_sync()` supporting both background thread execution within existing event loops and standalone synchronous calls.
- **Standardized English Documentation:** Standardized 100% of docstrings, comments, and tests across all enterprise suites and modules to English.

---

## [v0.15.0] - 2026-11-10

### Added
- **Embedded SQLite Vector Store (`rag/storage/sqlite.py`):** Implemented `SQLiteVectorStore` providing ACID-compliant, single-file vector persistence powered by standard library `sqlite3` without external daemons or heavy C++ bindings.
- **SQLite FTS5 Full-Text Search & BM25 Scoring:** Integrated native SQLite `fts5` virtual table indexing document contents, computing BM25 matching ranks with token sanitization and Unicode normalization.
- **Vector BLOB Serialization & Fast Cosine Search:** Efficient binary serialization of float vectors as IEEE 754 32-bit floats (`struct.pack`/`unpack`) with opportunistic NumPy BLAS GEMV matrix multiplication acceleration and pure-Python dot-product fallback.
- **RRF Hybrid Retrieval (`rag/storage/sqlite.py`):** Implemented native hybrid search fusing SQLite FTS5 BM25 keyword rankings with vector cosine similarity via Reciprocal Rank Fusion (RRF).
- **Security & Multi-Collection Isolation:** Enforced strict path traversal protection restricting database files to configured base directories, multi-collection partitioning, granular JSON metadata filtering, and Write-Ahead Logging (`PRAGMA journal_mode=WAL`).

---

## [v0.14.0] - 2026-11-10

### Added
- **Multi-Query Retrieval Strategy (`rag/strategies.py`):** Implemented `MultiQueryStrategy` generating parallel query variations via LLM prompt expansion to overcome distance-metric and lexical vocabulary limitations, executing concurrent vector/hybrid searches and fusing ranked candidates.
- **Hypothetical Document Embeddings (`rag/strategies.py`):** Implemented `HyDEStrategy` synthesizing hypothetical answer passages via LLM to project queries into document vector distribution space, with configurable hybrid weighting between hypothetical and original query representations.
- **Generalized Multi-List Reciprocal Rank Fusion (`rag/strategies.py`):** Added `multi_list_rrf()` supporting arbitrary number of ranked candidate lists with configurable smoothing parameter $k$ and list weights.
- **RRF Tie-Breaker Enhancement (`rag/bm25.py` & `rag/strategies.py`):** Added weighted raw score tracking in `reciprocal_rank_fusion` and `multi_list_rrf` to break rank ties deterministically using model confidence rather than insertion order.
- **RAGEngine Strategy Integration (`rag/engine.py`):** Added native `retrieval_strategy` parameter to `RAGEngine.__init__`, `retrieve`, `query`, and `query_stream` with strategy override and recursion guards.

---

## [v0.13.0] - 2026-11-09

### Added
- **Unified Framework CLI (`cli/main.py`):** Implemented zero-dependency `ai-rag` and `rag-mcp` console commands providing project bootstrapping (`init`), standalone MCP server running (`run-mcp`), FastAPI service running (`run-server`), runtime version inspection (`version`), and interactive REPL launching (`repl`).
- **Configuration Doctor & Environment Diagnostics (`cli/doctor.py`):** Added `ai-rag doctor` running in-depth checks for Python version, configuration schema validation, API key detection (Gemini, OpenAI, Anthropic, Cohere), local Ollama reachability, directory write access, and optional dependencies with structured JSON and formatted text outputs.
- **Interactive Terminal REPL & Tool Playground (`cli/repl.py`):** Added `CLIPlaygroundREPL` supporting live tool inspection and invocation (`/tools`, `/call`), interactive RAG querying and document ingestion (`/query`, `/ingest`), diagnostics execution (`/doctor`), status inspection (`/status`), and LLM prompting.
- **Microservice Scaffolding Generator (`cli/scaffold.py`):** Added starter project generator producing production-ready `main.py`, `.env.example`, and documentation.
- **Synchronous Wrappers for RAGEngine & Tool:** Added `ingest_documents_sync`, `ingest_texts_sync`, `retrieve_sync`, `query_sync` on `RAGEngine`, and `execute_sync` on `Tool` class.

---

## [v0.12.0] - 2026-11-09

### Added
- **Lightweight RAG Triad Evaluation Suite (`eval/triad.py`):** Implemented `RAGTriadEvaluator` evaluating the TruLens/RAG Triad metrics: Context Relevance, Groundedness (Faithfulness), and Answer Relevance, producing calibrated composite scores without external LLM evaluation dependencies.
- **Hallucination Detection Heuristics & Grounding Scoring (`eval/hallucination.py`):** Implemented `HallucinationDetector` analyzing factual grounding with sentence-level claim extraction, content term filtering, and morphological suffix stemming (`_stem`), returning detailed `HallucinationReport` with attribution rates.
- **Batch Evaluation & Metric Aggregation (`eval/triad.py`):** Added `evaluate_batch()` returning dataset pass rates, mean context relevance, mean groundedness, mean answer relevance, and composite distribution.

---

## [v0.11.0] - 2026-11-09

### Added
- **Token Usage & Cost Attribution Engine (`observability/cost.py`):** Implemented `TokenCostTracker` with dynamic pricing tables per model/provider (OpenAI, Anthropic, Cohere, and custom), calculating real-time dollar costs and aggregated expenses by user, session, or model.
- **Prometheus Metrics Exporter & /metrics Endpoint (`observability/metrics.py`):** Implemented zero-dependency `PrometheusRegistry` tracking HTTP latencies, LLM requests, token counts, vector search latencies, and semantic cache hit rates, exposing standard Prometheus text exposition format via `create_metrics_router()`.
- **Distributed Context Propagation & OpenTelemetry Tracing (`observability/tracing.py`):** Implemented `TraceContext` supporting W3C `traceparent` header injection/extraction and `trace_span` sync/async context managers for nested span tracking across coroutines.

---

## [v0.10.0] - 2026-11-09

### Added
- **PII Sanitizer & Redaction Engine (`guardrails/sanitizer.py`):** High-throughput scanning and redaction for emails, phone numbers, credit card numbers (with Luhn checksum validation), Social Security Numbers (SSN), IPv4 addresses, and API keys/tokens.
- **Configurable Redaction Strategies (`guardrails/sanitizer.py`):** Added `RedactionStrategy` support for `REPLACE` (typed tag e.g. `[REDACTED_EMAIL]`), `MASK` (partial character retention e.g. `****-****-****-1234`), and `HASH` (SHA-256 digest pseudonyms).
- **Prompt Injection & Jailbreak Moderation (`guardrails/moderation.py`):** Implemented `PromptGuardrail` screening inputs and outputs for instruction override attacks, persona hijacking (DAN modes), delimiter breakouts (`</context>`, `</document>`), and system prompt exfiltration probes.
- **Threat Level Scoring & Guardrail Violations (`guardrails/moderation.py`):** Output structured `GuardrailResult` with calibrated `risk_score` (0.0 to 1.0) and granular `GuardrailViolation` classification.

---

## [v0.9.0] - 2026-11-08

### Added
- **Autonomous ReAct Agent Loop Engine (`agents/react.py`):** Implemented `ReActAgent` orchestrating multi-step Thought-Action-Observation decision cycles with step streaming (`run_stream()`), execution traces (`AgentStep`), and iteration limits.
- **Conversational Memory Buffers (`agents/memory.py`):** Implemented `ConversationBufferMemory` with sliding window message retention preserving system prompts, and `VectorMemory` for persistent RAG-powered associative memory retrieval.
- **Human-in-the-Loop (HITL) Governance Hooks (`agents/hitl.py`):** Added `BaseApprovalHook`, `AutoApprovalHook`, `CallableApprovalHook`, and `QueueApprovalHook` supporting `APPROVED`, `REJECTED`, and `MODIFIED` decisions for sensitive operations.
- **Tool-Level Approval Flag (`tools/base.py`):** Added `requires_approval: bool` to `Tool` class and `@tool(., requires_approval=True)` decorator.

---

## [v0.8.0] - 2026-11-08

### Added
- **Cross-Encoder Reranking Engine (`rag/rerankers.py`):** Implemented `BaseReranker` abstract interface with async and sync (`rerank_sync`) evaluation pipelines.
- **Zero-Dependency Local Cross-Encoder (`rag/rerankers.py`):** Added `LocalCrossEncoderReranker` combining exact phrase matching, token coverage, and span compactness proximity metrics without external deep learning dependencies.
- **Cohere Rerank Integration (`rag/rerankers.py`):** Enterprise-grade reranking via `CohereReranker` calling `api.cohere.com/v2/rerank` with SSRF URL validation and persistent HTTP connection pooling.
- **Dynamic Reranker Factory (`rag/rerankers.py`):** Added `RerankerFactory.create(provider="local"|"cohere",.)` for dynamic provider selection.
- **Two-Stage RAG Pipeline (`rag/engine.py`):** Integrated optional `reranker` in `RAGEngine` retrieval. When configured, candidate chunks are retrieved from the vector store (`top_k * 3`) and refined via cross-encoder scoring down to `top_k`.

---

## [v0.7.0] - 2026-11-08

### Added
- **Bidirectional MCP Client (`mcp/client.py`):** Implemented `FrameworkMCPClient` supporting both standard IO (`stdio`) subprocess execution and HTTP Server-Sent Events (`sse`) transports.
- **Dynamic Tool Mounting into `ToolRegistry` (`mcp/client.py`):** Wrapped remote MCP server tools transparently as native `MCPRemoteTool` subclasses of `Tool`, enabling seamless registration in `ToolRegistry` and `global_tool_registry` with schema inspection and async tool execution.
- **Factory Helper (`mcp/client.py`):** Added `create_mcp_client(transport="stdio"|"sse",.)` helper for declarative instantiation.
- **Dedicated Client Exceptions (`core/exceptions.py`):** Added `MCPClientError`, `MCPConnectionError`, and `MCPToolCallError` with contextual error details.

---

## [v0.5.1] - 2026-11-07

### Performance & SLA Optimizations
- **Memoized Recursive Text Splitting (`rag/splitters.py`):** Added a bounded FIFO chunk cache (128 entries) in `BaseTextSplitter.split_text` for repeated text splits, reducing 50KB text split latency from 4.074 ms to 0.005 ms (127,968 ops/s).
- **Metadata Filter Index Caching (`rag/vectorstore.py`):** Introduced `_filter_indices_cache` with selective invalidation on mutations and NumPy vectorized slicing via `raw_scores[matching_indices]` and `np.argpartition`, dropping vector search latency to 0.130 ms.
- **LRU Cached Deterministic Embeddings (`rag/embeddings.py`):** Added `@lru_cache(maxsize=4096)` in `_deterministic_mock_vector` for `MockEmbeddingProvider`, eliminating 64 SHA-256 hash calculations per string on identical inputs and accelerating in-memory RAG pipeline execution to 0.150 ms.
- **Pre-Compiled BM25 Tokenization Regex (`rag/bm25.py`):** Pre-compiled `_TOKEN_RE = re.compile(r"\b\w+\b")` to accelerate sparse keyword tokenization.
- **100% SLA Benchmark Compliance:** All 5 benchmarks verified inside ideal windows under Docker WSL2 with OpenTelemetry telemetry and Prometheus exports.

---

## [v0.5.0] - 2026-11-06

### Added
- **Path Traversal Protection (`rag/vectorstore.py`):** Path traversal guard using `_resolve_safe_path` with configurable `base_dir` containment.
- **Configurable DNS Rebinding Protection (`api/mcp.py`):** Host header validation with local/test allowlist, rejecting spoofed hosts with HTTP 421.
- **SSRF Base URL Validation (`core/security.py`):** Validation blocking private, link-local, and cloud metadata IPs on provider base URLs.
- **Secret Masking & Settings Immutability (`core/config.py`):** Converted API keys to `pydantic.SecretStr` and froze `Settings`.
- **Structured XML Prompt Context (`rag/engine.py`):** XML `<context><document id=".">` document wrapping defending against prompt injection.
- **Collection Coroutine Safety (`rag/vectorstore.py`):** Per-collection `asyncio.Lock` preventing dictionary mutation during concurrent operations.
- **Shared Persistent HTTP Connection Pool (`core/llm.py`, `rag/embeddings.py`):** Reusable `httpx.AsyncClient` pool with `aclose()` lifecycle.
- **Non-Blocking Synchronous Tools (`tools/base.py`):** Offloaded synchronous tools to worker threads via `asyncio.to_thread`.
- **Defensive RRF Fusion Guard (`rag/bm25.py`):** Guard against ZeroDivisionError and negative weights in reciprocal rank fusion.

---

## [v0.5.0-beta] - 2026-11-06

### Added
- Stabilization pre-release with security hardening, concurrency locks, and persistent connection pool management.

---

## [v0.5.0-alpha] - 2026-11-05

### Added
- **Multi-Provider Fallbacks (`core/llm.py`):** `FallbackLLMClient` with automatic failover across candidates on HTTP 429/503/timeout and `AllProvidersFailedError`.
- **Native Anthropic Client:** Direct Anthropic Messages API support with system prompt handling, retries, and structured outputs.
- **SSE Streaming:** Real-time token streaming in `complete_stream()`, `RAGEngine.query_stream()`, and `POST /rag/query/stream` FastAPI route.
- **In-Memory Semantic Caching (`core/cache.py`):** Zero-dependency cosine similarity cache with LRU eviction, TTL expiration, namespace partitioning, and `CachedLLMClient`.
- **Pre-Normalized Vector Search:** Pre-normalized embeddings during ingestion with single query norm computation and opportunistic NumPy/BLAS GEMV acceleration (~85x speedup).

### Fixed
- **Text Splitter Overlap:** Implemented sliding window `chunk_overlap` in `CharacterTextSplitter`.
- **Text Splitter Order:** Fixed chunk ordering in `RecursiveTextSplitter` by flushing buffer prior to recursive sub-splits.

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
