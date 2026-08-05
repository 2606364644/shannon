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
    assert "prompt_manager" in kwargs and "executor" in kwargs  # 必传,非 None
