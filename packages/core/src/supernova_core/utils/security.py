"""URL safety utilities: DNS pinning, SSRF / loopback detection, reachability."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from supernova_core.models.errors import ErrorCode, PentestError


def resolve_host(
    url: str, host_mappings: dict[str, str] | None = None
) -> str | None:
    """Resolve the hostname in *url* and return the pinned IP (string).

    If *host_mappings* is provided and the URL's hostname is present in it,
    the mapped IP is returned directly without touching DNS (this lets an
    internal domain that won't resolve via public DNS still be pinned to a
    configured IP). Otherwise the hostname is resolved via ``getaddrinfo``.

    Returns ``None`` on resolution failure (gaierror / OSError).
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return None
    # 命中映射 → 直接返回映射 IP，跳过 DNS（内网域名 DNS 解析失败兜底）
    if host_mappings and hostname in host_mappings:
        return host_mappings[hostname]
    try:
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


def resolve_and_pin_host(
    url: str, host_mappings: dict[str, str] | None = None
) -> tuple[str, str]:
    """Resolve DNS (or use *host_mappings*), run safety checks, and return
    ``(pinned_ip, original_host)``.

    Raises ``PentestError(TARGET_UNREACHABLE)`` if the resolved IP is unsafe
    or DNS resolution fails. The mapped IP still passes through
    ``check_ssrf`` / ``check_loopback``, so mappings of 127.x / 169.254.x.x
    are still blocked.
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

    pinned_ip = resolve_host(url, host_mappings=host_mappings)
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
                port_suffix = f":{parsed.port}" if parsed.port else ""
                ip_url = url.replace(
                    f"{parsed.scheme}://{parsed.netloc}",
                    f"{parsed.scheme}://{pinned_ip}{port_suffix}",
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


def validate_target_url(
    url: str, host_mappings: dict[str, str] | None = None
) -> str:
    """Synchronous preflight gate: resolve -> SSRF check -> loopback check.

    Returns the pinned IP string for downstream DNS-rebinding protection.
    *host_mappings* (optional) bypasses DNS for internal hostnames; the
    mapped IP still goes through SSRF / loopback interception.

    Raises ``PentestError(TARGET_UNREACHABLE)`` on failure.
    """
    pinned_ip, _host = resolve_and_pin_host(url, host_mappings=host_mappings)
    return pinned_ip
