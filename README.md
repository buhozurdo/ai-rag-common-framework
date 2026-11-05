# AI RAG Common Framework

> Un framework base modular, ultraligero y con cero código redundante para construir sistemas RAG, servidores MCP (*Model Context Protocol*) y microservicios de IA modernos.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-v0.3.0%20(Stable)-brightgreen.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🚀 Estado del Proyecto: `v0.3.0` (General Availability)

Siguiendo nuestro modelo de **Release Train** documentado en [ROADMAP.md](ROADMAP.md), se ha consolidado el release estable **`v0.3.0`** (*Modular RAG Core*), integrando un motor RAG completo, desacoplado y con exportación directa hacia herramientas MCP.

### Features consolidadas en esta versión:
* 🧠 **Modular RAG Pipeline (`common_framework.rag`):**
  * `RAGEngine`: orquestación de ingesta, fragmentación, cálculo vectorial y síntesis con LLM.
  * `RecursiveTextSplitter`: particionado semántico inteligente preservando metadatos (`parent_id`, `chunk_index`).
  * `InMemoryVectorStore`: base vectorial pura sin librerías externas pesadas, con filtrado por metadatos (`filter_metadata`) y persistencia local (`save_to_json` / `load_from_json`).
  * `EmbeddingFactory`: adaptadores para OpenAI, Gemini, Ollama y Mock determinista para testing local.
  * **Exportación a MCP (`engine.as_tool`):** Convierte cualquier pipeline RAG en una herramienta MCP en una sola línea.
* 🛠️ **Dual-Use Tooling Engine (`common_framework.tools`):** Decorador `@tool` con auto-generación de JSON Schema compatible con OpenAI y MCP.
* 🌐 **Servidor MCP Nativo (`common_framework.mcp`):** Protocolo `mcp 2.x` con soporte completo de **Tools, Resources y Prompts** sobre transportes **stdio** y **SSE**.
* 🤖 **Cliente LLM Agnóstico y Unificado (`core.llm`):** Abstracción común para Gemini, OpenAI, Ollama, DeepSeek, Groq y vLLM.
* 🛡️ **Resiliencia y Reintentos Automáticos:** Exponential backoff para fallos transitorios (HTTP 429, 5xx) y excepción `RateLimitError`.
* 🎯 **Structured Outputs Nativos:** Validación garantizada contra esquemas Pydantic (`structured_complete`).
* 📦 **Empaquetado Moderno:** Configuración estándar en `pyproject.toml` (PEP 517/518/621).

---

## 📦 Instalación

Para instalar el framework en modo editable localmente:

```bash
pip install -e .
```

O con dependencias opcionales de desarrollo y testing:

```bash
pip install -e ".[test,mcp]"
```

---

## 🛠️ Configuración Rápida

Crea un archivo `.env` en la raíz de tu proyecto (puedes basarte en `.env.example`):

```env
FRAMEWORK_ENV=development
LOG_LEVEL=INFO

# Proveedor por defecto (gemini, openai, ollama)
DEFAULT_LLM_PROVIDER=gemini
DEFAULT_LLM_MODEL=gemini-2.5-flash

# Llaves API
GEMINI_API_KEY=tu_api_key_aqui
OPENAI_API_KEY=
```

---

## 💻 Ejemplos de Uso

### 1. Inicialización y Chat Completion Simple

```python
import asyncio
from common_framework import LLMFactory, ChatMessage, Role

async def main():
    client = LLMFactory.create()

    messages = [
        ChatMessage(role=Role.SYSTEM, content="Eres un asistente conciso."),
        ChatMessage(role=Role.USER, content="¿Qué es el Model Context Protocol en una frase?")
    ]

    response = await client.complete(messages)
    print(f"Respuesta ({response.model}):\n{response.content}")
    print(f"Tokens usados: {response.usage.total_tokens}")

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Structured Outputs con Pydantic (Cero Parseo Manual)

```python
import asyncio
from pydantic import BaseModel, Field
from common_framework import LLMFactory, ChatMessage, Role

class SentimentAnalysis(BaseModel):
    sentiment: str = Field(description="positive, negative, o neutral")
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

async def main():
    client = LLMFactory.create("gemini")

    messages = [
        ChatMessage(
            role=Role.USER,
            content="Analiza este comentario: 'El framework es sumamente rápido y limpio de configurar.'"
        )
    ]

    result: SentimentAnalysis = await client.structured_complete(
        messages=messages,
        response_model=SentimentAnalysis
    )

    print("Sentimiento:", result.sentiment)
    print("Confianza:", result.confidence)
    print("Resumen:", result.summary)

if __name__ == "__main__":
    asyncio.run(main())
```

### 3. Servidor MCP con Herramientas de Doble Uso (Dual-Use Tools)

```python
from common_framework import create_mcp_server, tool

@tool(name="calculate_discount", description="Calculates final price after discount percentage.")
def calculate_discount(price: float, discount_pct: float) -> dict[str, float]:
    savings = round(price * (discount_pct / 100), 2)
    return {"final_price": price - savings, "savings": savings}

server = create_mcp_server(name="pricing-service", version="0.3.0")
server.add_tool(calculate_discount)

# Ejecución:
# server.run_stdio()       # Para Cursor / Claude Desktop / Antigravity
# server.run_sse(port=8000) # Para acceso HTTP / Webhooks
```

### 4. Modular RAG Engine con Exportación Directa a MCP

```python
import asyncio
from common_framework import InMemoryVectorStore, RAGEngine, create_mcp_server

async def main():
    # 1. Iniciar RAG Engine sin necesidad de base de datos externa
    engine = RAGEngine(vector_store=InMemoryVectorStore())
    await engine.ingest_texts([
        "El servidor principal de producción está en 10.0.0.1.",
        "Las copias de seguridad se realizan a las 02:00 AM UTC diariamente."
    ])

    # 2. Convertir el pipeline RAG en una herramienta para agentes y MCP
    rag_tool = engine.as_tool(name="search_infra", description="Consulta datos de infraestructura")

    # 3. Montar en servidor MCP
    server = create_mcp_server(name="infra-mcp-service")
    server.add_tool(rag_tool)
    # server.run_stdio()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🧪 Ejecución de Tests

```bash
python -m pytest -v
```

---

## 🗺️ Próximas Versiones en el Release Train

Consulta el detalle completo en [ROADMAP.md](ROADMAP.md).
* **`v0.4.0-alpha`**: *FastAPI Service & Hybrid Endpoints* (REST API + MCP over SSE Gateway integrado).
* **`v0.5.0-alpha`**: *Autonomous Agents & Workflows*.
* **`v0.6.0-alpha`**: *Observability, Evaluation & Gateway*.
