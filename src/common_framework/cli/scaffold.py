"""Scaffolding generator for bootstrapping AI RAG microservices (FEAT-11)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union


_MAIN_PY_TEMPLATE = '''"""Starter AI RAG Microservice."""

from fastapi import FastAPI
from common_framework import (
    create_app,
    tool,
    InMemoryVectorStore,
    MockEmbeddingProvider,
    RAGEngine,
    Document,
)

# 1. Initialize RAG Engine
vector_store = InMemoryVectorStore()
embedding_provider = MockEmbeddingProvider()
rag_engine = RAGEngine(vector_store=vector_store, embedding_provider=embedding_provider)

# Ingest sample knowledge
rag_engine.ingest_sync([
    Document(content="AI RAG Common Framework is a lightweight, zero-bloat foundation for production AI applications."),
    Document(content="FastAPI microservices can be created easily using create_app() factory."),
])

# 2. Register tools
@tool(name="framework_version", description="Returns current framework version.")
def get_version() -> str:
    from common_framework import __version__
    return f"AI RAG Common Framework v{__version__}"

# 3. Create FastAPI Gateway with built-in RAG and metrics routes
app = create_app(
    title="My AI RAG Service",
    description="Production-grade AI RAG API powered by ai-rag-common-framework",
    rag_engine=rag_engine,
    include_metrics=True,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
'''

_ENV_EXAMPLE_TEMPLATE = '''# AI RAG Common Framework Environment Configuration
ENVIRONMENT=development
LOG_LEVEL=INFO
APP_NAME=my-ai-rag-service

# LLM Providers (Configure at least one)
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
COHERE_API_KEY=

# Local LLM / Ollama Endpoint (Optional)
OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434

# Security & CORS
ALLOWED_ORIGINS=["*"]
'''

_README_TEMPLATE = '''# {project_name}

An AI microservice built with **[AI RAG Common Framework](https://github.com/buhozurdo/ai-rag-common-framework)**.

## Quick Start

1. Install dependencies:
   ```bash
   pip install ai-rag-common-framework[api]
   ```

2. Copy environment file:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

3. Verify environment health:
   ```bash
   ai-rag doctor
   ```

4. Start the service:
   ```bash
   ai-rag run-server --port 8000 --reload
   ```

5. Explore the interactive REPL:
   ```bash
   ai-rag repl
   ```
'''


def scaffold_project(
    target_dir: Union[str, Path],
    project_name: str = "my-rag-service",
) -> list[str]:
    """Scaffold a starter AI RAG microservice project."""
    dest = Path(target_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    created_files: list[str] = []

    files_to_write = {
        "main.py": _MAIN_PY_TEMPLATE,
        ".env.example": _ENV_EXAMPLE_TEMPLATE,
        "README.md": _README_TEMPLATE.format(project_name=project_name),
    }

    for filename, content in files_to_write.items():
        file_path = dest / filename
        if not file_path.exists():
            file_path.write_text(content.strip() + "\n", encoding="utf-8")
            created_files.append(str(file_path))

    return created_files
