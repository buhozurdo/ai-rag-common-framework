"""Security utilities: URL validation, SSRF mitigation, and secret handling."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import FrozenSet, Optional, Set


# Blocked cloud metadata hosts and IP targets (IMDSv1, IMDSv2, GCP, Azure, OpenStack, Alibaba, DigitalOcean, Tencent)
BLOCKED_METADATA_HOSTS: FrozenSet[str] = frozenset({
    "169.254.169.254",  # AWS IMDSv1/v2, Azure, OpenStack, GCP
    "169.254.0.1",      # DigitalOcean metadata
    "100.100.100.200",  # Alibaba Cloud metadata
    "metadata.google.internal",  # GCP internal metadata hostname
    "metadata.tencentyun.com",   # Tencent Cloud metadata hostname
    "instance-data",  # Legacy AWS metadata DNS
    "fd00:ec2::254",  # AWS IMDS IPv6
})


def _validate_ip_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    url: str,
    original_target: str,
    allow_local: bool = True,
    block_private: bool = False,
) -> None:
    """Validate a resolved or parsed IP against SSRF restriction rules."""
    ip_to_check = getattr(ip, "ipv4_mapped", None) or ip
    ip_str = str(ip_to_check)

    # Check loopback first (e.g. 127.0.0.1, ::1)
    if ip_to_check.is_loopback:
        if not allow_local:
            raise ValueError(
                f"SSRF protection: base_url '{url}' targets restricted loopback address '{original_target}' ({ip_str})"
            )
        return

    # Check cloud metadata targets explicitly
    if ip_str in BLOCKED_METADATA_HOSTS or str(ip) in BLOCKED_METADATA_HOSTS:
        raise ValueError(
            f"SSRF protection: base_url '{url}' targets restricted cloud metadata IP '{original_target}' ({ip_str})"
        )

    # Check link-local (e.g. 169.254.x.x, fe80::/10)
    if ip_to_check.is_link_local:
        raise ValueError(
            f"SSRF protection: base_url '{url}' targets restricted link-local address '{original_target}' ({ip_str})"
        )

    # Check multicast / unspecified / reserved
    if ip_to_check.is_multicast or ip_to_check.is_unspecified or ip_to_check.is_reserved:
        raise ValueError(
            f"SSRF protection: base_url '{url}' targets restricted IP address '{original_target}' ({ip_str})"
        )

    # Check private network (e.g. 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    if block_private and ip_to_check.is_private:
        raise ValueError(
            f"SSRF protection: base_url '{url}' targets restricted private network address '{original_target}' ({ip_str})"
        )


def validate_provider_base_url(
    url: str,
    resolve_dns: bool = True,
    allow_local: bool = True,
    block_private: bool = False,
) -> str:
    """Validate provider base URL for HTTP/HTTPS scheme and block SSRF against cloud metadata.

    Performs static hostname checks, direct IP inspection (with IPv4-mapped IPv6 unmapping),
    and active DNS resolution (via socket.getaddrinfo) to defeat DNS rebinding attacks.

    Args:
        url: The candidate endpoint URL.
        resolve_dns: Whether to perform DNS resolution to validate target IP addresses.
        allow_local: Whether to allow localhost/127.0.0.1 for local Ollama / vLLM deployments.
        block_private: Whether to strictly block private RFC 1918 subnets.

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

    # 1. Check if hostname is directly an IP literal (IPv4 or IPv6)
    try:
        ip = ipaddress.ip_address(hostname)
        _validate_ip_address(
            ip=ip,
            url=url,
            original_target=hostname,
            allow_local=allow_local,
            block_private=block_private,
        )
        return stripped.rstrip("/")
    except ValueError as exc:
        if "SSRF protection" in str(exc):
            raise
        # Not a valid direct IP address string, proceed to DNS resolution

    # 2. DNS Resolution check (mitigate DNS rebinding e.g. *.nip.io resolving to metadata)
    if resolve_dns:
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved_ips = set()
            for item in addr_info:
                sockaddr = item[4]
                if sockaddr and sockaddr[0]:
                    resolved_ips.add(sockaddr[0])

            for rip_str in resolved_ips:
                try:
                    rip = ipaddress.ip_address(rip_str)
                    _validate_ip_address(
                        ip=rip,
                        url=url,
                        original_target=hostname,
                        allow_local=allow_local,
                        block_private=block_private,
                    )
                except ValueError as exc:
                    if "SSRF protection" in str(exc):
                        raise
        except socket.gaierror:
            # Domain does not resolve (e.g. offline tests or internal mock domains)
            pass

    return stripped.rstrip("/")
