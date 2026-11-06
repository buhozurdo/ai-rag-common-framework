"""Security utilities: URL validation, SSRF mitigation, and secret handling."""

from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Set


# Blocked cloud metadata hosts and IP targets (IMDSv1, IMDSv2, GCP, Azure, OpenStack)
BLOCKED_METADATA_HOSTS: Set[str] = {
    "169.254.169.254",  # AWS IMDSv1/v2, Azure, OpenStack, GCP
    "metadata.google.internal",  # GCP internal metadata hostname
    "instance-data",  # Legacy AWS metadata DNS
    "fd00:ec2::254",  # AWS IMDS IPv6
}


def validate_provider_base_url(url: str) -> str:
    """Validate provider base URL for HTTP/HTTPS scheme and block SSRF against cloud metadata.

    Args:
        url: The candidate endpoint URL.

    Returns:
        The normalized (trailing slash stripped) validated URL string.

    Raises:
        ValueError: If scheme is not http/https or URL targets a link-local/cloud metadata endpoint.
    """
    if not url or not isinstance(url, str):
        raise ValueError("Provider base_url must be a non-empty string")

    stripped = url.strip()
    parsed = urllib.parse.urlparse(stripped)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL scheme '{scheme}' in base_url '{url}'. Provider base_url must use 'http' or 'https'."
        )

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise ValueError(f"Invalid base_url '{url}': hostname is missing")

    # Direct match against known cloud metadata hostnames
    if hostname in BLOCKED_METADATA_HOSTS:
        raise ValueError(
            f"SSRF protection: base_url '{url}' targets restricted cloud metadata endpoint '{hostname}'"
        )

    # Check IP address targets (link-local, cloud metadata)
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_link_local:
            raise ValueError(
                f"SSRF protection: base_url '{url}' targets restricted link-local address '{hostname}'"
            )
        if str(ip) in BLOCKED_METADATA_HOSTS:
            raise ValueError(
                f"SSRF protection: base_url '{url}' targets restricted cloud metadata IP '{hostname}'"
            )
    except ValueError as exc:
        if "SSRF protection" in str(exc):
            raise

    return stripped.rstrip("/")
