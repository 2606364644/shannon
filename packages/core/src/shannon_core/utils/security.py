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


def resolve_and_pin_host(url: str) -> tuple[str, str]:
    """Resolve DNS, run safety checks, and return ``(pinned_ip, original_host)``.

    Raises ``PentestError(TARGET_UNREACHABLE)`` if the resolved IP is unsafe
    or DNS resolution fails.
    """
    parsed = urlparse(url)
    original_host = parsed.hostname
    if not original_host:
        raise PentestError(
            f"Cannot parse hostname from URL: {url}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )

    pinned_ip = resolve_host(url)
    if pinned_ip is None:
        raise PentestError(
            f"Cannot resolve hostname for {url}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )

    if check_ssrf(pinned_ip):
        raise PentestError(
            f"Target {url} resolves to SSRF-sensitive IP {pinned_ip}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )

    if check_loopback(pinned_ip):
        raise PentestError(
            f"Target {url} resolves to loopback address {pinned_ip}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.TARGET_UNREACHABLE,
        )

    return (pinned_ip, original_host)


async def check_url_reachable(
    url: str,
    timeout: int = 10,
    pinned_ip: str | None = None,
    original_host: str | None = None,
) -> bool:
    """Return ``True`` when an HTTP HEAD to *url* succeeds (any HTTP response).

    When *pinned_ip* and *original_host* are provided, the request connects
    to the pinned IP directly with a ``Host`` header set to the original host.
    This prevents DNS rebinding attacks.
    """
    try:
        # verify=False is intentional: pentest targets often use self-signed certs
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            if pinned_ip and original_host:
                parsed = urlparse(url)
                ip_url = url.replace(
                    f"{parsed.scheme}://{parsed.netloc}",
                    f"{parsed.scheme}://{pinned_ip}",
                    1,
                )
                headers = {"Host": original_host}
                resp = await client.head(
                    ip_url,
                    headers=headers,
                    follow_redirects=False,
                )
            else:
                resp = await client.head(url, follow_redirects=True)
            return True
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def validate_target_url(url: str) -> str:
    """Synchronous preflight gate: resolve -> SSRF check -> loopback check.

    Returns the pinned IP string for downstream DNS-rebinding protection.

    Raises ``PentestError(TARGET_UNREACHABLE)`` on failure.
    """
    pinned_ip, _host = resolve_and_pin_host(url)
    return pinned_ip
