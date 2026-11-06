"""Unit tests for SSRF protection and provider base_url validation."""

import pytest
from common_framework.core.security import validate_provider_base_url
from common_framework.core.llm import OpenAICompatibleClient, AnthropicClient
from common_framework.rag.embeddings import OpenAICompatibleEmbeddingProvider


def test_valid_provider_base_urls():
    """Verify standard legitimate provider URLs are accepted and trailing slashes stripped."""
    assert validate_provider_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert validate_provider_base_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1"
    assert validate_provider_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
    assert validate_provider_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"


def test_blocked_cloud_metadata_endpoints():
    """Verify cloud metadata IPs and internal hostnames are blocked with ValueError."""
    with pytest.raises(ValueError, match="SSRF protection"):
        validate_provider_base_url("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(ValueError, match="SSRF protection"):
        validate_provider_base_url("http://metadata.google.internal/computeMetadata/v1")

    with pytest.raises(ValueError, match="SSRF protection"):
        validate_provider_base_url("http://169.254.10.20:80")

    with pytest.raises(ValueError, match="SSRF protection"):
        validate_provider_base_url("http://instance-data/latest")


def test_invalid_url_schemes():
    """Verify non-http/https schemes are rejected."""
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        validate_provider_base_url("file:///etc/passwd")

    with pytest.raises(ValueError, match="Invalid URL scheme"):
        validate_provider_base_url("ftp://ftp.example.com")

    with pytest.raises(ValueError, match="Invalid URL scheme"):
        validate_provider_base_url("gopher://gopher.example.com")


def test_empty_or_malformed_urls():
    """Verify empty or missing hostname URLs raise ValueError."""
    with pytest.raises(ValueError, match="non-empty string"):
        validate_provider_base_url("")

    with pytest.raises(ValueError, match="hostname is missing"):
        validate_provider_base_url("http://")


def test_llm_clients_enforce_ssrf_protection():
    """Verify LLM client constructors block SSRF metadata base_urls."""
    with pytest.raises(ValueError, match="SSRF protection"):
        OpenAICompatibleClient(base_url="http://169.254.169.254/v1", api_key="test")

    with pytest.raises(ValueError, match="SSRF protection"):
        AnthropicClient(base_url="http://metadata.google.internal/v1", api_key="test")


def test_embedding_provider_enforces_ssrf_protection():
    """Verify Embedding provider constructor blocks SSRF metadata base_urls."""
    with pytest.raises(ValueError, match="SSRF protection"):
        OpenAICompatibleEmbeddingProvider(base_url="http://169.254.169.254/v1", api_key="test")
