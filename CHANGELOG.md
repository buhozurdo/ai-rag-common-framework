# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) using a multi-channel Release Train model.

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
