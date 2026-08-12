"""Host-mapping (DNS fallback) tests for security.py — Task 3.

An internal domain that won't resolve via public DNS can still be pinned to a
configured IP via ``host_mappings``; SSRF/loopback interception still applies to
the mapped IP.
"""

import pytest

from supernova_core.models.errors import PentestError
from supernova_core.utils.security import resolve_host, validate_target_url


def test_resolve_host_uses_mapping_when_hit():
    """映射命中 hostname → 直接返回映射 IP，不走 DNS。"""
    ip = resolve_host("http://target.test", host_mappings={"target.test": "10.0.0.1"})
    assert ip == "10.0.0.1"


def test_resolve_host_falls_back_to_dns_when_unmapped(monkeypatch):
    """未命中映射 → 走原 DNS（mock 成一个公网 IP）。"""
    import supernova_core.utils.security as sec

    monkeypatch.setattr(
        sec.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, 0, ("93.184.216.34", 0))]
    )
    ip = resolve_host("http://example.com", host_mappings={"other.test": "10.0.0.1"})
    assert ip == "93.184.216.34"


def test_resolve_host_no_mapping_behaves_as_before(monkeypatch):
    """host_mappings=None → 完全等同旧行为（向后兼容）。"""
    import supernova_core.utils.security as sec

    monkeypatch.setattr(
        sec.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, 0, ("1.2.3.4", 0))]
    )
    assert resolve_host("http://example.com") == "1.2.3.4"


def test_validate_target_url_mapping_bypasses_dns(monkeypatch):
    """映射 IP 直接用，不经 DNS（避免内网域名 DNS 解析失败）。"""
    import supernova_core.utils.security as sec

    called = []
    monkeypatch.setattr(
        sec.socket,
        "getaddrinfo",
        lambda *a, **k: called.append(1) or [(0, 0, 0, 0, ("9.9.9.9", 0))],
    )
    ip = validate_target_url(
        "http://target.test", host_mappings={"target.test": "10.0.0.1"}
    )
    assert ip == "10.0.0.1"
    assert called == []  # 没调 DNS


def test_mapping_loopback_still_blocked():
    """映射里填 127.x → preflight 照拦（SSRF/loopback 不退化）。"""
    with pytest.raises(PentestError):
        validate_target_url(
            "http://target.test", host_mappings={"target.test": "127.0.0.1"}
        )
