# shannon-py/packages/core/tests/test_credential_validator.py
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.credential_validator import validate_credentials


class TestValidateAnthropic:
    @pytest.mark.asyncio
    async def test_valid_key(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=200)
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials("anthropic_api", api_key="sk-ant-valid")

    @pytest.mark.asyncio
    async def test_invalid_key(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=401)
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("anthropic_api", api_key="sk-ant-bad")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_forbidden_key(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=403)
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("anthropic_api", api_key="sk-ant-forbidden")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED


class TestValidateBedrock:
    @pytest.mark.asyncio
    async def test_valid(self):
        with patch("boto3.client") as mock_boto:
            mock_sts = MagicMock()
            mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
            mock_boto.return_value = mock_sts
            await validate_credentials("bedrock")

    @pytest.mark.asyncio
    async def test_invalid(self):
        with patch("boto3.client") as mock_boto:
            from botocore.exceptions import ClientError
            mock_boto.side_effect = ClientError(
                {"Error": {"Code": "InvalidClientTokenId"}}, "GetCallerIdentity"
            )
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("bedrock")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED


class TestValidateVertex:
    @pytest.mark.asyncio
    async def test_valid(self):
        mock_ai = MagicMock()
        mock_cloud = MagicMock()
        mock_cloud.aiplatform = mock_ai
        with patch.dict("sys.modules", {"google": MagicMock(), "google.cloud": mock_cloud, "google.cloud.aiplatform": mock_ai}):
            await validate_credentials("vertex")

    @pytest.mark.asyncio
    async def test_invalid(self):
        mock_ai = MagicMock()
        mock_ai.init.side_effect = Exception("no project")
        mock_cloud = MagicMock()
        mock_cloud.aiplatform = mock_ai
        with patch.dict("sys.modules", {"google": MagicMock(), "google.cloud": mock_cloud, "google.cloud.aiplatform": mock_ai}):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("vertex")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED


class TestValidateLiteLLM:
    @pytest.mark.asyncio
    async def test_valid(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=200)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials("litellm_router", base_url="http://router:4000", auth_token="tok")

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=401)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("litellm_router", base_url="http://router:4000", auth_token="bad")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_missing_credentials_raises(self):
        # LiteLLM without base_url or auth_token should raise
        with pytest.raises(PentestError) as exc_info:
            await validate_credentials("litellm_router")
        assert exc_info.value.error_code == ErrorCode.AUTH_FAILED


class TestValidateUnknownProvider:
    @pytest.mark.asyncio
    async def test_unknown_provider_skipped(self):
        # Should not raise for unknown provider — gracefully skip
        await validate_credentials("unknown_provider")


class TestValidateOpenAICompatible:
    @pytest.mark.asyncio
    async def test_valid_credentials(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=200)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials(
                "openai_compatible",
                api_key="valid-key",
                base_url="https://api.example.com"
            )

    @pytest.mark.asyncio
    async def test_invalid_key_401(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=401)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials(
                    "openai_compatible",
                    api_key="invalid-key",
                    base_url="https://api.example.com"
                )
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED
            assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_forbidden_key_403(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=403)
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials(
                    "openai_compatible",
                    api_key="forbidden-key",
                    base_url="https://api.example.com"
                )
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED
            assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        with pytest.raises(PentestError) as exc_info:
            await validate_credentials(
                "openai_compatible",
                base_url="https://api.example.com"
            )
        assert exc_info.value.error_code == ErrorCode.AUTH_FAILED
        assert "api_key" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_base_url_raises(self):
        with pytest.raises(PentestError) as exc_info:
            await validate_credentials(
                "openai_compatible",
                api_key="some-key"
            )
        assert exc_info.value.error_code == ErrorCode.AUTH_FAILED
        assert "base_url" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_base_url_with_trailing_slash(self):
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=200)
        # Verify that trailing slash is handled correctly
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials(
                "openai_compatible",
                api_key="valid-key",
                base_url="https://api.example.com/"
            )
            # Should call get with correct URL (trailing slash removed)
            mock_client.get.assert_called_once()
            call_args = mock_client.get.call_args
            assert "/v1/models" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_connection_error_retryable(self):
        import httpx
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials(
                    "openai_compatible",
                    api_key="valid-key",
                    base_url="https://unreachable.example.com"
                )
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED
            assert exc_info.value.retryable
