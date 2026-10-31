"""Unit tests for unified LLM client."""

import json
import httpx
import pytest
from pydantic import BaseModel

from common_framework.core.config import Settings
from common_framework.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ModelNotFoundError,
    StructuredOutputError,
)
from common_framework.core.llm import (
    ChatMessage,
    GeminiClient,
    LLMFactory,
    OllamaClient,
    OpenAICompatibleClient,
    Role,
)


class SampleUserSchema(BaseModel):
    name: str
    age: int
    role: str


@pytest.mark.asyncio
async def test_chat_message_to_dict():
    """Verify serialization of ChatMessage."""
    msg = ChatMessage(role=Role.USER, content="Hello World", name="UserTest")
    data = msg.to_dict()
    assert data == {"role": "user", "content": "Hello World", "name": "UserTest"}


@pytest.mark.asyncio
async def test_openai_complete_success():
    """Verify successful chat completion response parsing."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers.get("Authorization") == "Bearer sk-test"
        response_body = {
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Framework configured successfully.",
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 8,
                "total_tokens": 23,
            },
        }
        return httpx.Response(200, json=response_body)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            client=http_client,
        )

        response = await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert response.content == "Framework configured successfully."
        assert response.model == "gpt-4o-mini"
        assert response.usage.prompt_tokens == 15
        assert response.usage.completion_tokens == 8
        assert response.usage.total_tokens == 23


@pytest.mark.asyncio
async def test_structured_complete_success():
    """Verify structured output parsing into Pydantic model."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"name": "Alice", "age": 30, "role": "Engineer"}),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        user = await client.structured_complete(
            messages=[ChatMessage(role=Role.USER, content="Extract user info")],
            response_model=SampleUserSchema,
        )
        assert isinstance(user, SampleUserSchema)
        assert user.name == "Alice"
        assert user.age == 30
        assert user.role == "Engineer"


@pytest.mark.asyncio
async def test_structured_complete_invalid_json():
    """Verify StructuredOutputError is raised when model returns invalid JSON."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Not a valid JSON"}}]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        with pytest.raises(StructuredOutputError):
            await client.structured_complete(
                messages=[ChatMessage(role=Role.USER, content="Test")],
                response_model=SampleUserSchema,
            )


@pytest.mark.asyncio
async def test_auth_error_handling():
    """Verify 401 raises AuthenticationError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized: Invalid API key")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="bad-key", client=http_client)
        with pytest.raises(AuthenticationError) as exc_info:
            await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_not_found_error_handling():
    """Verify 404 raises ModelNotFoundError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Model not found")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleClient(api_key="sk-test", client=http_client)
        with pytest.raises(ModelNotFoundError) as exc_info:
            await client.complete([ChatMessage(role=Role.USER, content="Test")])
        assert exc_info.value.status_code == 404


def test_factory_creation():
    """Verify LLMFactory instantiates correct client types."""
    settings = Settings(
        GEMINI_API_KEY="gemini-key",
        OPENAI_API_KEY="openai-key",
        OLLAMA_BASE_URL="http://localhost:11434",
    )

    gemini_client = LLMFactory.create("gemini", settings=settings)
    assert isinstance(gemini_client, GeminiClient)

    openai_client = LLMFactory.create("openai", settings=settings)
    assert isinstance(openai_client, OpenAICompatibleClient)

    ollama_client = LLMFactory.create("ollama", settings=settings)
    assert isinstance(ollama_client, OllamaClient)

    with pytest.raises(ConfigurationError):
        LLMFactory.create("unsupported_provider", settings=settings)
