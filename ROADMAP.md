# Roadmap y Modelo de Release Trains — AI RAG Common Framework

Este framework adopta un modelo de **Release Train (tren de versiones)** con desfase controlado para garantizar estabilidad, pruebas rigurosas y desacoplamiento entre el desarrollo activo y el despliegue en producción.

---

## 🚆 Modelo de Release Trains y Canales

El ciclo de maduración de una versión fluye por 4 canales con un pipeline escalonado:

```
[ Canary ] ──────▶ [ Alpha ] ──────▶ [ Beta ] ──────▶ [ Release ]
(Pre-Alpha)       (4 versiones      (2 versiones       (Versión Final /
                 atrás de Release)  atrás de Release)   Producción)
```

### Canales y Roles:
1. **`canary` (Pre-Alpha):**
   * Canal experimental / bleeding-edge.
   * Prototipos, spikes técnicos, investigación de compatibilidad con nuevos modelos y estándares.
   * Tag: `vX.Y.0-canary`
2. **`alpha` (4 versiones atrás de Release):**
   * **Donde trabajan los desarrolladores activamente.**
   * Implementación de features base, contratos de API y arquitectura central.
   * Tag: `vX.Y.0-alpha`
3. **`beta` (2 versiones atrás de Release):**
   * Congelación de APIs (*feature freeze*).
   * Pruebas de integración, performance, tests de regresión y ajustes de compatibilidad.
   * Tag: `vX.Y.0-beta`
4. **`release` (Final / Producción):**
   * Versión completamente estabilizada, probada y lista para consumo en proyectos productivos.
   * Tag: `vX.Y.0`

---

## 📊 Matriz de Progresión del Pipeline

A medida que el proyecto avance en su tren de releases, la relación entre canales se escalona de la siguiente manera:

| Hito Global | Canary (Pre-Alpha) | Alpha (Devs: -4) | Beta (-2) | Release (Producción) |
| :--- | :--- | :--- | :--- | :--- |
| **Fase 1** | v0.5.0-canary | **v0.1.0-alpha** *(Iniciando aquí)* | — | — |
| **Fase 2** | v0.6.0-canary | **v0.2.0-alpha** | — | — |
| **Fase 3** | v0.7.0-canary | **v0.3.0-alpha** | **v0.1.0-beta** | — |
| **Fase 4** | v0.8.0-canary | **v0.4.0-alpha** | **v0.2.0-beta** | — |
| **Fase 5** | v0.9.0-canary | **v0.5.0-alpha** | **v0.3.0-beta** | **v0.1.0 (Final)** |
| **Fase 6** | v1.0.0-canary | **v0.6.0-alpha** | **v0.4.0-beta** | **v0.2.0 (Final)** |

---

## 📌 Detalle de Versiones Menores y Features

### 🔹 v0.1.0 — "Foundation & Core Architecture"
* **Canal actual:** `v0.1.0-alpha` *(Fase de desarrollo activo)*
* **Objetivo:** Núcleo ligero, configuración unificada y cliente agnóstico de LLMs.
* **Features:**
  * Estructura de paquete estándar (`pyproject.toml`).
  * Configuración tipada mediante `pydantic-settings` (`core/config.py`).
  * Abstracción agnóstica de LLMs (Gemini, OpenAI, Anthropic, Ollama) con soporte para *Structured Outputs*.
  * Jerarquía de excepciones y logging estructurado.

---

### 🔹 v0.2.0 — "Tooling & MCP Engine"
* **Canal:** `v0.2.0-alpha`
* **Objetivo:** Registro universal de herramientas y servidor Model Context Protocol.
* **Features:**
  * Decorador `@tool` con auto-generación de JSON Schema compatible con OpenAI/Anthropic/Gemini.
  * Servidor MCP nativo con soporte de transportes **stdio** (local) y **SSE** (red/HTTP).
  * *Dual-use tools*: una sola definición utilizable tanto por agentes internos como por clientes MCP externos.

---

### 🔹 v0.3.0 — "Modular RAG Core"
* **Canal:** `v0.3.0-alpha`
* **Objetivo:** Motor de Retrieval-Augmented Generation flexible y desacoplado.
* **Features:**
  * Interfaces universales de Embeddings y Vector Stores.
  * Adaptadores vectoriales: `InMemoryVectorStore` (cero dependencias externas para dev/test) y conectores a bases de datos vectoriales.
  * Splitters y chunkers semánticos configurables.
  * Pipeline de recuperación densa y síntesis de respuestas con fuentes citadas.

---

### 🔹 v0.4.0 — "FastAPI Service & Hybrid Endpoints"
* **Canal:** `v0.4.0-alpha`
* **Objetivo:** Exposición de RAG y MCP mediante API REST moderna.
* **Features:**
  * Factoría de aplicación FastAPI con middlewares, CORS y Swagger automático.
  * Rutas REST para `/rag/query`, `/rag/ingest` y `/rag/documents`.
  * Gateway MCP-over-SSE integrado directamente en el servidor HTTP.
  * Endpoints de salud (`/healthz`) y diagnóstico.

---

### 🔹 v0.5.0 — "Autonomous Agents & Workflows"
* **Canal:** `v0.5.0-alpha`
* **Objetivo:** Agentes autónomos ligeros que orquesten herramientas y RAG.
* **Features:**
  * Runner de agentes tipo ReAct (Reasoning + Action) sin sobrecarga de frameworks pesados.
  * Memoria conversacional de corto y largo plazo.
  * Soporte de streaming nativo (SSE).
  * Ganchos de confirmación (*Human-in-the-loop*).

---

### 🔹 v0.6.0 — "Observability, Evaluation & Gateway"
* **Canal:** `v0.6.0-alpha`
* **Objetivo:** Métricas, telemetría y evaluación de calidad.
* **Features:**
  * Monitor de consumo de tokens y estimación de costos.
  * Caché semántico para optimización de llamadas repetitivas.
  * Métricas RAG Triad (relevancia de contexto, fidelidad y respuesta).
  * Soporte para exportación OpenTelemetry / Langfuse.

---

### 🔹 v1.0.0 — "Production Hardening & Stable Release"
* **Canal:** `v1.0.0-alpha`
* **Objetivo:** Consolidación de API pública y distribución del paquete.
* **Features:**
  * Contratos estables y congelados en APIs públicas.
  * CLI del framework (`rag-mcp` CLI) para inicialización y ejecución.
  * Cobertura de pruebas completa y plantillas de despliegue.
