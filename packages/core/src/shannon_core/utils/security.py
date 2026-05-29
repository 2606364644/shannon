"""URL safety utilities: DNS pinning, SSRF / loopback detection, reachability."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from shannon_core.models.errors import ErrorCode, PentestError


def resolve_host(url: str) -> str | None:
    """DNS-resolve the hostname in *url* and return the pinned IP (string).

    Returns ``None`` on resolution failure.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return None
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canon, sockaddr in addrinfos:
            return sockaddr[0]
        return None
    except (socket.gaierror, OSError):
        return None


def check_ssrf(ip: str) -> bool:
    """Return ``True`` if *ip* falls into SSRF-sensitive ranges (link-local 169.254.0.0/16)."""
    addr = ipaddress.ip_address(ip)
    return addr in ipaddress.ip_network("169.254.0.0/16")


def check_loopback(ip: str) -> bool:
    """Return ``True`` if *ip* is a loopback or wildcard address."""
    addr = ipaddress.ip_address(ip)
    return addr.is_loopback or addr.is_unspecified


async def check_url_reachable(url: str, timeout: int = 10) -> bool:
    """Return ``True`` when an HTTP HEAD to *url* succeeds (any HTTP response)."""
    try:
        # verify=False is intentional: pentest targets often use self-signed certs
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            resp = await client.head(url, follow_redirects=True)
            return True
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def validate_target_url(url: str) -> None:
    """Synchronous preflight gate: resolve -> SSRF check -> loopback check.

    Raises ``PentestError(TARGET_UNREACHABLE)`` on failure.
    """
    ip = resolve_host(url)
    if ip is None:
        raise PentestError(
            f"Cannot resolve hostname for {url}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )
    if check_ssrf(ip):
        raise PentestError(
            f"Target {url} resolves to SSRF-sensitive IP {ip}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )
    if check_loopback(ip):
        raise PentestError(
            f"Target {url} resolves to loopback address {ip}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )
