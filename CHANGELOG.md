# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) using a multi-channel Release Train model.

---

## [v0.2.0] - 2026-11-02

### Release Title:
### ⚡ `v0.2.0: Tooling & MCP Engine (General Availability)`

### Summary:
Segundo release estable (GA) del framework. Consolida el ecosistema completo de herramientas (*Tools*) y el estándar oficial **Model Context Protocol (MCP)**, abarcando **Tools, Resources y Prompts**. Permite la creación de servicios de IA extensibles con soporte de ejecución local (**stdio**) y remota (**SSE/ASGI**).

---

### 🌟 Hitos y Características Consolidadas:
* **Tríada Completa de Model Context Protocol (MCP 2.x):**
  * **Tools:** Registro universal con `@server.tool` y soporte de *dual-use tools*.
  * **Resources:** Decorador `@server.resource` para exponer documentación, schemas y datos estructurados.
  * **Prompts:** Decorador `@server.prompt` para exponer plantillas de instrucciones parametrizadas.
* **Dual-Use Tooling Engine (`common_framework.tools`):**
  * Una sola función Python con tipado estándar genera automáticamente el JSON Schema compatible con OpenAI y MCP sin librerías externas.
  * Invocación agnóstica para funciones síncronas y asíncronas con validación estricta de parámetros en tiempo de ejecución.
  * `ToolRegistry` global y desacoplado para orquestación en agentes internos.
* **Transportes de Producción:**
  * Soporte nativo para transporte **stdio** (Cursor, Claude Desktop, Antigravity).
  * Transporte **SSE** (Server-Sent Events) con aplicación ASGI lista para embeber en FastAPI/Starlette.
* **Calidad y Cobertura:**
  * 23 pruebas unitarias aprobadas al 100%.

---

## [v0.2.0-beta] - 2026-11-02

### Release Title:
### 🛡️ `v0.2.0-beta: Full MCP Spectrum (Tools, Prompts & Resources)`

### Summary:
Fase Beta de estabilización de la versión `0.2.0`. Amplía la integración con la especificación `mcp 2.x` añadiendo soporte nativo para Resources y Prompts, complementando el motor de Tools.

---

### 🔧 Mejoras y Ajustes (Improvements & Fixes):
* **Soporte de Resources en MCP:** Método decorador `@server.resource(uri=...)` y listado asíncrono `list_resources()`.
* **Soporte de Prompts en MCP:** Método decorador `@server.prompt(name=...)` y listado asíncrono `list_prompts()`.
* **Pruebas de Integración:** Verificación de registro y descubrimiento de recursos y plantillas de prompts (`test_mcp_server_resource_and_prompt`).

---

## [v0.2.0-alpha] - 2026-10-31

### Release Title:
### 🛠️ `v0.2.0-alpha: Tooling & MCP Engine`

### Summary:
Segunda versión en el canal de desarrollo activo **Alpha** (-4 versiones respecto al release final). Introduce el motor universal de herramientas (*Tools*) y soporte nativo del estándar **Model Context Protocol (MCP)** con capacidades de doble uso (*dual-use tools*).

---

### 🚀 Nuevas Características (Features):
* **Decorador Universal `@tool` y Abstracción `Tool` (`common_framework.tools`):**
  * Auto-generación de esquemas JSON Schema completos mediante introspección tipada con Pydantic.
  * Invocación unificada tanto para funciones síncronas como asíncronas (`execute` y `__call__`).
  * Validación y coerción automática de argumentos de entrada.
  * Exportación directa al formato de Function Calling de OpenAI (`to_openai_schema`).
* **Catálogo Centralizado de Herramientas (`ToolRegistry`):**
  * Descubrimiento, registro dinámico y ejecución de herramientas por identificador único.
  * Registro global singleton (`global_tool_registry`).
* **Adaptador de Servidor MCP Nativo (`FrameworkMCPServer`):**
  * Compatible con el estándar oficial `mcp 2.x` (`MCPServer`).
  * **Dual-Use Tools:** Una herramienta declarada con `@tool` puede usarse directamente dentro de flujos internos de LLMs y exponerse al mismo tiempo como endpoint MCP hacia clientes externos (Cursor, Claude Desktop, Antigravity, ChatGPT).
  * Soporte de transporte estándar **stdio** (ejecución local).
  * Soporte de transporte **SSE** (Server-Sent Events) para acceso HTTP/remoto y montaje ASGI (`get_sse_app`).
* **Suite de Pruebas y Ejemplos:**
  * 8 nuevas pruebas unitarias en `test_tools.py` y `test_mcp.py` (totalizando 22 tests aprobados).
  * Script de demostración interactivo en `examples/mcp_server_demo.py`.

---

## [v0.1.0] - 2026-10-31

### Release Title:
### 🚀 `v0.1.0: Foundation & Core Architecture (General Availability)`

### Summary:
Primer release estable (GA) de la versión menor `v0.1.0`. Provee una base sólida, tipada, resiliente y modular para el desarrollo de aplicaciones de IA, agentes y RAG con cero dependencias innecesarias. Promovido exitosamente tras completar las fases `v0.1.0-alpha` y `v0.1.0-beta`.

---

### 🌟 Hitos y Características Consolidadas:
* **Estabilidad y Resiliencia en Producción:**
  * Reintentos automáticos con *exponential backoff* ante errores transitorios de red o sobrecarga de proveedores (HTTP 429, 500, 502, 503, 504).
  * Excepción especializada `RateLimitError` con soporte del encabezado `Retry-After`.
* **Configuración Centralizada (`common_framework.core.config`):**
  * Basada en `pydantic-settings` con lectura automática de `.env` y validación estricta en tiempo de ejecución.
  * Soporte multi-entorno (`development`, `staging`, `production`, `test`) y resolución de credenciales.
* **Abstracción Unificada de Modelos (`common_framework.core.llm`):**
  * `OpenAICompatibleClient`: cliente HTTP asíncrono para OpenAI, Ollama, DeepSeek, Groq, vLLM.
  * `GeminiClient`: integración directa con la API de Google Gemini.
  * `LLMFactory`: inicialización dinámica de proveedores en una línea de código.
* **Structured Outputs Nativos (`structured_complete`):**
  * Generación y validación de esquemas Pydantic directamente sin intermediarios pesados.
* **Calidad y Verificación:**
  * 100% de tests unitarios pasando en suite de pruebas asíncronas (`pytest`).
  * Clasificador de paquete promovido a `Production/Stable`.

---

## [v0.1.0-beta] - 2026-10-31

### Release Title:
### 🛡️ `v0.1.0-beta: Hardening, Resiliency & Rate Limiting`

### Summary:
Segunda fase en el ciclo de maduración de la versión `0.1.0` (canal **Beta**, 2 versiones atrás de producción). Enfocada en endurecimiento del cliente HTTP, tolerancia a fallos y manejo de límites de tasa.

---

### 🔧 Mejoras y Ajustes (Improvements & Fixes):
* **Lógica de Reintentos Exponenciales:**
  * Incorporación de reintentos con retraso exponencial (`0.2s * 2^attempt`) para errores HTTP temporales (429, 5xx) y desconexiones de red (`httpx.RequestError`).
* **Soporte de Rate Limiting (`RateLimitError`):**
  * Nueva excepción tipada `RateLimitError` que captura el código de estado 429 y parsea el header estándar `Retry-After`.
* **Ampliación de Pruebas Unitarias:**
  * Cobertura de recuperación automática tras fallos 503 (`test_retry_success_after_transient_failure`).
  * Cobertura de agotamiento de reintentos en límites de cuota (`test_rate_limit_error_handling`).

---

## [v0.1.0-alpha] - 2026-10-31

### Release Title:
### 🏗️ `v0.1.0-alpha: Foundation & Core Architecture`

### Summary:
Primera versión pre-release en el canal de desarrollo activo **Alpha** (-4 versiones respecto al release de producción). Establece los cimientos arquitectónicos del framework común para proyectos de IA, RAG y servidores MCP con una política estricta de cero código redundante y mínimo boilerplate.

---

### 🚀 Nuevas Características (Features):
* **Configuración Centralizada Tipada (`common_framework.core.config`):**
  * Gestión centralizada de ajustes mediante `pydantic-settings`.
  * Detección y carga automática de variables de entorno desde `.env`.
* **Abstracción Unificada de LLMs (`common_framework.core.llm`):**
  * Contrato base `BaseLLMClient` con operaciones asíncronas nativas.
  * `OpenAICompatibleClient`, `GeminiClient`, `OllamaClient` y `LLMFactory`.
* **Structured Outputs Nativos (`structured_complete`):**
  * Extracción y validación automática de datos contra esquemas Pydantic (`BaseModel`).
* **Jerarquía Estándar de Excepciones (`common_framework.core.exceptions`):**
  * `FrameworkError`, `ConfigurationError`, `LLMProviderError`, `AuthenticationError`, `ModelNotFoundError`, y `StructuredOutputError`.
* **Empaquetado y Setup de Desarrollo:**
  * Configuración moderna en `pyproject.toml` con backend `hatchling`.
