"""Embedding provider abstractions and factory."""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from typing import Any, Optional
import httpx

from common_framework.core.config import Settings, get_settings
from common_framework.core.exceptions import AuthenticationError, ConfigurationError, LLMProviderError
from common_framework.core.security import validate_provider_base_url


class BaseEmbeddingProvider(ABC):
    """Abstract interface for dense vector embedding models."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Generate vector embedding for a search query string."""
        pass

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate vector embeddings for a list of document strings."""
        pass


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic, lightweight embedding provider for testing and offline development."""

    def __init__(self, dimension: int = 64, settings: Optional[Settings] = None) -> None:
        super().__init__(settings)
        self.dimension = dimension

    def _generate_vector(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dimension

        # Generate deterministic vector based on MD5 and character codes
        vec: list[float] = []
        for i in range(self.dimension):
            h = hashlib.sha256(f"{text}_{i}".encode("utf-8")).digest()
            val = (int.from_bytes(h[:4], byteorder="big") / (2**32 - 1)) * 2.0 - 1.0
            vec.append(val)

        # L2 normalize vector
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    async def embed_query(self, text: str) -> list[float]:
        return self._generate_vector(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._generate_vector(t) for t in texts]


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """Embedding provider using OpenAI or any OpenAI-compatible API (Ollama, vLLM, DeepSeek)."""

    def __init__(
        self,
        api_key: Optional[Union[str, Any]] = None,
        base_url: Optional[str] = None,
        model: str = "text-embedding-3-small",
        provider_name: str = "openai",
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(settings)
        self.provider_name = provider_name
        raw_key = api_key or self.settings.get_api_key_for_provider(provider_name)
        self.api_key = (
            raw_key.get_secret_value() if hasattr(raw_key, "get_secret_value") else raw_key
        )
        self.base_url = validate_provider_base_url(base_url or self.settings.openai_base_url)
        self.model = model
        self._external_client = client

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _send_request(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []

        url = f"{self.base_url}/embeddings"
        payload = {"input": inputs, "model": self.model}
        headers = self._get_headers()

        async def _call(http: httpx.AsyncClient) -> list[list[float]]:
            try:
                res = await http.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.default_timeout_seconds,
                )
                if res.status_code in (401, 403):
                    raise AuthenticationError(
                        f"Authentication failed for {self.provider_name}: {res.text}",
                        status_code=res.status_code,
                        provider=self.provider_name,
                    )
                res.raise_for_status()
                data = res.json()
                sorted_items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in sorted_items]
            except httpx.HTTPStatusError as exc:
                raise LLMProviderError(
                    f"Error from embedding provider {self.provider_name}: {exc.response.text}",
                    status_code=exc.response.status_code,
                    provider=self.provider_name,
                ) from exc
            except httpx.RequestError as exc:
                raise LLMProviderError(
                    f"Network error connecting to embedding provider {self.provider_name}: {exc}",
                    provider=self.provider_name,
                ) from exc

        if self._external_client:
            return await _call(self._external_client)

        async with httpx.AsyncClient() as client:
            return await _call(client)

    async def embed_query(self, text: str) -> list[float]:
        results = await self._send_request([text])
        return results[0] if results else []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._send_request(texts)


class EmbeddingFactory:
    """Factory to create embedding providers."""

    @staticmethod
    def create(
        provider: Optional[str] = None,
        settings: Optional[Settings] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> BaseEmbeddingProvider:
        cfg = settings or get_settings()
        target = (provider or cfg.default_provider).lower().strip()

        if target == "mock":
            return MockEmbeddingProvider(settings=cfg, **kwargs)
        if target == "openai":
            target_model = model or "text-embedding-3-small"
            return OpenAICompatibleEmbeddingProvider(
                settings=cfg, model=target_model, provider_name="openai", **kwargs
            )
        if target == "ollama":
            target_model = model or "nomic-embed-text"
            ollama_url = f"{cfg.ollama_base_url.rstrip('/')}/v1"
            return OpenAICompatibleEmbeddingProvider(
                base_url=ollama_url,
                model=target_model,
                provider_name="ollama",
                settings=cfg,
                **kwargs,
            )
        if target == "gemini":
            # Gemini OpenAI-compatible endpoint supports text-embedding-004
            gemini_base = "https://generativelanguage.googleapis.com/v1beta/openai"
            target_model = model or "text-embedding-004"
            return OpenAICompatibleEmbeddingProvider(
                api_key=cfg.get_api_key_for_provider("gemini"),
                base_url=gemini_base,
                model=target_model,
                provider_name="gemini",
                settings=cfg,
                **kwargs,
            )

        raise ConfigurationError(
            f"Unsupported embedding provider: {target!r}. Supported: 'mock', 'openai', 'ollama', 'gemini'."
        )
