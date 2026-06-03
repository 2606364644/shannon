"""Tests for DNS rebinding protection (resolve_and_pin_host)."""
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.security import (
    resolve_and_pin_host,
    check_url_reachable,
    validate_target_url,
)


class TestResolveAndPinHost:
    def test_returns_pinned_ip_and_host(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="93.184.216.34"):
            ip, host = resolve_and_pin_host("https://example.com/path")
            assert ip == "93.184.216.34"
            assert host == "example.com"

    def test_rejects_ssrf_ip(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="169.254.169.254"):
            with pytest.raises(PentestError) as exc_info:
                resolve_and_pin_host("http://metadata.google.internal")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE

    def test_rejects_loopback_ip(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="127.0.0.1"):
            with pytest.raises(PentestError) as exc_info:
                resolve_and_pin_host("http://localhost:3000")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE

    def test_returns_none_on_resolution_failure(self):
        with patch("shannon_core.utils.security.resolve_host", return_value=None):
            with pytest.raises(PentestError) as exc_info:
                resolve_and_pin_host("https://unresolvable.invalid")
            assert exc_info.value.error_code == ErrorCode.TARGET_UNREACHABLE


class TestCheckUrlReachablePinned:
    @pytest.mark.asyncio
    async def test_uses_pinned_ip_with_host_header(self):
        """When pinned_ip provided, connects to IP with Host header."""
        with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
            mock_head.return_value = AsyncMock(status_code=200)
            result = await check_url_reachable(
                "https://example.com/path",
                pinned_ip="93.184.216.34",
                original_host="example.com",
            )
            assert result is True
            # Verify the request used the pinned IP URL with Host header
            call_args = mock_head.call_args
            assert "93.184.216.34" in call_args[0][0] or "93.184.216.34" in str(call_args)
            headers = call_args[1].get("headers", {})
            assert headers.get("Host") == "example.com"

    @pytest.mark.asyncio
    async def test_no_pin_uses_original_url(self):
        """Without pinned_ip, behaves like before."""
        with patch("httpx.AsyncClient.head", new_callable=AsyncMock) as mock_head:
            mock_head.return_value = AsyncMock(status_code=200)
            result = await check_url_reachable("https://example.com/path")
            assert result is True
            call_url = mock_head.call_args[0][0]
            assert "example.com" in call_url


class TestValidateTargetUrlReturnsPinnedIp:
    def test_returns_pinned_ip_on_success(self):
        with patch("shannon_core.utils.security.resolve_host", return_value="93.184.216.34"):
            ip = validate_target_url("https://example.com")
            assert ip == "93.184.216.34"
