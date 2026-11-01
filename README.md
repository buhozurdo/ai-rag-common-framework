# AI RAG Common Framework

> Un framework base modular, ultraligero y con cero código redundante para construir sistemas RAG, servidores MCP (*Model Context Protocol*) y microservicios de IA modernos.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/Version-v0.1.0%20(Stable)-brightgreen.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🚀 Estado del Proyecto: `v0.1.0` (General Availability)

Siguiendo nuestro modelo de **Release Train** documentado en [ROADMAP.md](ROADMAP.md), se ha consolidado el primer release estable **`v0.1.0`** (*Foundation & Core Architecture*), proveyendo una base sólida y probada para los próximos proyectos.

### Features consolidadas en esta versión:
* ⚙️ **Configuración Centralizada (`core.config`):** Tipada estrictamente con `pydantic-settings`, soporte de variables de entorno `.env` y resolución de credenciales multi-proveedor.
* 🤖 **Cliente LLM Agnóstico y Unificado (`core.llm`):** Abstracción común para Gemini, OpenAI, Ollama y proveedores compatibles con la especificación OpenAI (DeepSeek, Groq, vLLM).
* 🛡️ **Resiliencia y Reintentos Automáticos:** Manejo de reintentos exponenciales ante errores temporales (HTTP 429, 5xx, desconexiones) y excepción `RateLimitError`.
* 🎯 **Structured Outputs Nativos:** Extracción de datos y validación garantizada en esquemas Pydantic (`structured_complete`).
* 📦 **Empaquetado Moderno:** Configuración estándar en `pyproject.toml` (PEP 517/518/621).

---

## 📦 Instalación

Para instalar el framework en modo editable localmente:

```bash
pip install -e .
```

O con dependencias opcionales de desarrollo y testing:

```bash
pip install -e ".[test]"
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
    # Instancia automáticamente el cliente configurado en el entorno
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

---

## 🧪 Ejecución de Tests

```bash
python -m pytest -v
```

---

## 🗺️ Próximas Versiones en el Release Train

Consulta el detalle completo en [ROADMAP.md](ROADMAP.md).
* **`v0.2.0-alpha`**: *Tooling & MCP Engine* (decorador `@tool`, servidor Model Context Protocol sobre transportes stdio y SSE).
* **`v0.3.0-alpha`**: *Modular RAG Core* (Vector Stores, Embeddings, Splitters).
* **`v0.4.0-alpha`**: *FastAPI Service & Hybrid Endpoints*.
