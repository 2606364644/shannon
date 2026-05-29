# shannon-py/packages/core/tests/test_security.py
import ipaddress
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.security import (
    resolve_host,
    check_ssrf,
    check_loopback,
    check_url_reachable,
    validate_target_url,
)


class TestResolveHost:
    def test_resolves_localhost(self):
        ip = resolve_host("http://localhost:3000")
        assert ip is not None

    def test_resolves_domain(self):
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 443))
            ]
            ip = resolve_host("https://example.com")
            assert ip == "93.184.216.34"

    def test_returns_none_on_failure(self):
        with patch("socket.getaddrinfo", side_effect=OSError("DNS fail")):
            ip = resolve_host("https://nonexistent.invalid")
            assert ip is None


class TestCheckSsrf:
    def test_allows_public_ip(self):
        assert check_ssrf("93.184.216.34") is False

    def test_blocks_link_local(self):
        assert check_ssrf("169.254.169.254") is True

    def test_blocks_link_local_range(self):
        assert check_ssrf("169.254.0.1") is True

    def test_allows_private_not_link_local(self):
        # 10.x.x.x is private but NOT SSRF link-local
        assert check_ssrf("10.0.0.1") is False


class TestCheckLoopback:
    def test_blocks_127(self):
        assert check_loopback("127.0.0.1") is True

    def test_blocks_ipv6_loopback(self):
        assert check_loopback("::1") is True

    def test_blocks_zero(self):
        assert check_loopback("0.0.0.0") is True

    def test_allows_public(self):
        assert check_loopback("93.184.216.34") is False


class TestCheckUrlReachable:
    @pytest.mark.asyncio
    async def test_reachable_url(self):
        with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
            mock_head.return_value = AsyncMock(status_code=200)
            result = await check_url_reachable("https://example.com")
            assert result is True

    @pytest.mark.asyncio
    async def test_unreachable_url(self):
        with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
            mock_head.side_effect = httpx.ConnectError("refused")
            result = await check_url_reachable("https://unreachable.local")
            assert result is False


class TestValidateTargetUrl:
    def test_rejects_ssrf(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="169.254.169.254"):
            with pytest.raises(PentestError) as exc_info:
                validate_target_url("http://metadata.google.internal")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE

    def test_rejects_loopback(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="127.0.0.1"):
            with pytest.raises(PentestError) as exc_info:
                validate_target_url("http://localhost")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE

    def test_accepts_valid_url(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="93.184.216.34"):
            # Should not raise
            validate_target_url("https://example.com")
