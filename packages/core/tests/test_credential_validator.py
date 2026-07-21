# supernova/packages/core/tests/test_credential_validator.py
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.utils.credential_validator import validate_credentials


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
        mock_client.post.return_value = mock_response
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
        mock_client.post.return_value = mock_response
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
        mock_client.post.return_value = mock_response
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
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials(
                "openai_compatible",
                api_key="valid-key",
                base_url="https://api.example.com/"
            )
            # Should POST to chat/completions with trailing slash removed
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://api.example.com/chat/completions"

    @pytest.mark.asyncio
    async def test_connection_error_retryable(self):
        import httpx
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection failed")
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

    @pytest.mark.asyncio
    async def test_uses_chat_completions_not_models_list(self):
        """智谱 GLM 等专用通道(base_url 已含 /paas/v4 版本路径)没有 models 列表端点,
        只有 chat/completions。校验必须 POST {base}/chat/completions(所有 OpenAI 兼容
        服务必然支持),而不是 GET {base}/v1/models —— 后者在 base 含版本号时会拼出
        不存在的 /paas/v4/v1/models,越过鉴权层后被路由层拒绝成 HTTP 404。"""
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=200)
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials(
                "openai_compatible",
                api_key="valid-key",
                base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                model="glm-5.2",
            )
        # POST 到 chat/completions,绝不访问 /v1/models,也不发起 GET
        mock_client.post.assert_called_once()
        url = mock_client.post.call_args[0][0]
        assert url == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
        assert "/v1/models" not in url
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_auth_error_status_passes(self):
        """非鉴权类状态(如 400 未知 model)说明请求已越过鉴权层、endpoint 可达且 key
        有效 —— 必须放行。旧的 models 探测把任何非 200 都当 "unexpected status" 报错,
        正是智谱 coding 通道返回 404 时误杀 scan 的根因,此处固化新语义防回归。"""
        mock_client = AsyncMock()
        mock_response = MagicMock(status_code=400)
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await validate_credentials(  # must NOT raise
                "openai_compatible",
                api_key="valid-key",
                base_url="https://api.example.com",
                model="some-model",
            )
