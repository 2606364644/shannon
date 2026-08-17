"""run_auth_validation_probe:独立认证验证探针(失败不抛异常,降级返回)。"""
import pytest
from unittest.mock import AsyncMock, patch

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.activities import run_auth_validation_probe
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _input():
    return BlackboxActivityInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )


@pytest.mark.asyncio
async def test_probe_success_passes_through_result():
    expected = AuthValidationResult(success=True)
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=expected),
    ):
        result = await run_auth_validation_probe(_input())
    assert result is expected  # 透传:不自判 structured output


@pytest.mark.asyncio
async def test_probe_failure_result_passed_through():
    """validate_authentication 内置 success=False 映射(如 no-structured-output),探针透传。"""
    expected = AuthValidationResult(
        success=False, failure_point="out_of_band", failure_detail="no verdict"
    )
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=expected),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.success is False
    assert result.failure_point == "out_of_band"


@pytest.mark.asyncio
async def test_probe_provider_exception_does_not_raise():
    """provider 异常(如引擎抛错)被吞,降级 success=False out_of_band(仿 run_endpoint_verify)。"""
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(side_effect=RuntimeError("engine down")),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "engine down" in (result.failure_detail or "")


@pytest.mark.asyncio
async def test_probe_calls_validate_with_prompt_manager_and_executor():
    """探针体内现造 prompt_manager + executor 并传入(对齐 run_blackbox_auth_validation)。"""
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=AuthValidationResult(success=True)),
    ) as m:
        await run_auth_validation_probe(_input())
    _, kwargs = m.call_args
    assert kwargs["web_url"] == "http://target/"
    assert kwargs["config_path"] == "/c.yaml"
    assert kwargs["workspace_path"] == "/wp"
    assert kwargs["deliverables_path"]  # 必传(对齐 run_blackbox_auth_validation;f1abf69d 删 fallback 后漏传会 raise)
    assert "prompt_manager" in kwargs and "executor" in kwargs  # 必传,非 None


@pytest.mark.asyncio
async def test_auth_validation_probe_forwards_proxy_url(monkeypatch):
    """独立 auth probe 也必须保留 proxy_url 语义（兼容 future HOST callers）。"""
    from types import SimpleNamespace
    monkeypatch.setattr("supernova_blackbox.pipeline.activities.activity.info", lambda: SimpleNamespace(attempt=1, workflow_id=""))
    inp = _input()
    inp.proxy_url = "http://127.0.0.1:19090"
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=AuthValidationResult(success=True)),
    ) as validate:
        await run_auth_validation_probe(inp)
    assert validate.call_args.kwargs["proxy_url"] == "http://127.0.0.1:19090"


@pytest.mark.asyncio
async def test_probe_forwards_provider_config():
    """完整 provider 配置透传 validate_authentication（2026-08-17 key/端点错配根因修复）。"""
    inp = _input()
    inp.provider_config = {"type": "openai_compatible", "base_url": "https://llm-proxy.example/v1",
                           "api_key": "user-key-x", "medium_model": "m"}
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=AuthValidationResult(success=True)),
    ) as m:
        await run_auth_validation_probe(inp)
    assert m.call_args.kwargs["provider_config"] == inp.provider_config


@pytest.mark.asyncio
async def test_probe_llm_401_classified_as_engine():
    """LLM 引擎 401（如 key/端点错配）→ failure_point=engine，与登录失败区分。"""
    from supernova_core.models.errors import PentestError

    err = PentestError(
        "Error code: 401 - {'error': {'code': '401', 'message': '令牌已过期或验证不正确'}}",
        "validation", retryable=False,
        context={"provider_error_code": "AuthenticationError"})
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(side_effect=err),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.success is False
    assert result.failure_point == "engine"
    assert "401" in (result.failure_detail or "")


@pytest.mark.asyncio
async def test_probe_llm_401_without_context_classified_as_engine():
    """无 provider_error_code context 的旧路径异常 → 消息签名兜底命中 engine。"""
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(side_effect=RuntimeError(
            "Error code: 401 - {'error': {'code': '401', 'message': '令牌已过期或验证不正确'}}")),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.failure_point == "engine"


@pytest.mark.asyncio
async def test_probe_rate_limit_classified_as_engine():
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(side_effect=RuntimeError("Rate limit reached, retry later")),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.failure_point == "engine"


@pytest.mark.asyncio
async def test_probe_unrelated_exception_stays_out_of_band():
    """非引擎类异常（如浏览器崩溃）保持 out_of_band，不被误分类。"""
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(side_effect=RuntimeError("browser crashed")),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.failure_point == "out_of_band"
