"""Example: Basic usage of AI RAG Common Framework (v0.1.0-alpha)."""

import asyncio
from pydantic import BaseModel, Field
from common_framework.core.config import get_settings
from common_framework.core.llm import ChatMessage, LLMFactory, Role


class CodeReviewSummary(BaseModel):
    """Pydantic schema for structured output."""
    summary: str = Field(description="Brief summary of code assessment")
    score: int = Field(ge=1, le=10, description="Overall score from 1 to 10")
    recommendations: list[str] = Field(description="Actionable recommendations")


async def main() -> None:
    settings = get_settings()
    print(f"Loaded framework environment: {settings.framework_env}")
    print(f"Default provider: {settings.default_provider} | Default model: {settings.default_model}")

    # Create client dynamically according to settings
    client = LLMFactory.create()
    print(f"Client instantiated: {client.__class__.__name__}")

    # Note: Requires a valid API key (e.g., GEMINI_API_KEY or OPENAI_API_KEY) in .env to call live APIs.
    api_key = settings.get_api_key_for_provider(settings.default_provider)
    if not api_key:
        print("\n[!] No API key configured in .env for this provider.")
        print("To run live requests, configure GEMINI_API_KEY or OPENAI_API_KEY in your .env file.")
        return

    print("\nSending sample prompt...")
    messages = [
        ChatMessage(role=Role.USER, content="Hello! Give a 1-sentence tip on writing clean Python code.")
    ]
    response = await client.complete(messages)
    print(f"\nResponse:\n{response.content}")
    print(f"Tokens: {response.usage.total_tokens}")


if __name__ == "__main__":
    asyncio.run(main())
