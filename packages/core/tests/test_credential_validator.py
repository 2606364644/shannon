# shannon-py/packages/core/tests/test_credential_validator.py
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.utils.credential_validator import validate_credentials


class TestValidateAnthropic:
    @pytest.mark.asyncio
    async def test_valid_key(self):
        mock_resp = MagicMock(status_code=200)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            await validate_credentials("anthropic_api", api_key="sk-ant-valid")

    @pytest.mark.asyncio
    async def test_invalid_key(self):
        mock_resp = MagicMock(status_code=401)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(PentestError) as exc_info:
                await validate_credentials("anthropic_api", api_key="sk-ant-bad")
            assert exc_info.value.error_code == ErrorCode.AUTH_FAILED

    @pytest.mark.asyncio
    async def test_forbidden_key(self):
        mock_resp = MagicMock(status_code=403)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
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


class TestValidateUnknownProvider:
    @pytest.mark.asyncio
    async def test_unknown_provider_skipped(self):
        # Should not raise for unknown provider — gracefully skip
        await validate_credentials("unknown_provider")
