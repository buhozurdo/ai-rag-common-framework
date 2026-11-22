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


def test_ssrf_dns_rebinding_protection(monkeypatch):
    """Verify domains resolving via DNS to metadata IPs are blocked (DNS rebinding mitigation)."""
    import socket

    # Mock socket.getaddrinfo to simulate attacker domain resolving to 169.254.169.254
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "attacker-rebinding.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    with pytest.raises(ValueError, match="SSRF protection.*restricted (link-local|cloud metadata)"):
        validate_provider_base_url("http://attacker-rebinding.com/v1")


def test_ssrf_loopback_and_private_controls():
    """Verify allow_local and block_private options enforce network boundaries."""
    # Disallow loopback
    with pytest.raises(ValueError, match="restricted loopback"):
        validate_provider_base_url("http://127.0.0.1:8000", allow_local=False)

    with pytest.raises(ValueError, match="restricted loopback"):
        validate_provider_base_url("http://localhost:8000", allow_local=False)

    # Block private IP
    with pytest.raises(ValueError, match="restricted private network"):
        validate_provider_base_url("http://10.0.0.1:8000", block_private=True)

    with pytest.raises(ValueError, match="restricted private network"):
        validate_provider_base_url("http://192.168.1.1:8000", block_private=True)


def test_ssrf_ipv6_and_ipv4_mapped_vectors():
    """Verify SSRF protection handles IPv6 metadata, IPv4-mapped IPv6, and link-local IPv6."""
    # AWS IMDS IPv6 target
    with pytest.raises(ValueError, match="SSRF protection.*restricted cloud metadata"):
        validate_provider_base_url("http://[fd00:ec2::254]/latest")

    # IPv4-mapped IPv6 pointing to metadata 169.254.169.254
    with pytest.raises(ValueError, match="SSRF protection.*restricted (cloud metadata|link-local)"):
        validate_provider_base_url("http://[::ffff:169.254.169.254]/v1")

    # IPv6 link-local fe80::
    with pytest.raises(ValueError, match="SSRF protection.*restricted link-local"):
        validate_provider_base_url("http://[fe80::1]:8080/v1")

    # IPv6 loopback ::1 blocked when allow_local=False
    with pytest.raises(ValueError, match="restricted loopback"):
        validate_provider_base_url("http://[::1]:8000", allow_local=False)

    # IPv4-mapped IPv6 loopback blocked when allow_local=False
    with pytest.raises(ValueError, match="restricted loopback"):
        validate_provider_base_url("http://[::ffff:127.0.0.1]:8000", allow_local=False)
