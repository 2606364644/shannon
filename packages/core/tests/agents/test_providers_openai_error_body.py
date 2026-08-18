"""_handle_error 记录底层 APIError.body：回归 __legacy__ probe-d6168171。

真机现象：litellm 流中途错误被 openai SDK 包成 APIError(message="An error
occurred during streaming", body="litellm.MidStreamFallbackError: ...DFLASH
speculative decoding does not support grammar-constrained decoding yet.")。
_handle_error 只记 str(error)=泛化 message，真实根因 body 被丢 → 日志无细节、
排查需手挖 APIError.body。修：当异常有 body 属性且非空，附进 error 文案。
"""
from unittest.mock import MagicMock

import pytest
from openai import APIError

from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def _make_api_error(message: str, body) -> APIError:
    """构造一个带 body 的 APIError（模拟 openai SDK 流内错误包装）。"""
    err = APIError(message=message, request=MagicMock(), body=body)
    return err


def test_handle_error_includes_api_error_body():
    err = _make_api_error(
        "An error occurred during streaming",
        "litellm.MidStreamFallbackError: DFLASH speculative decoding "
        "does not support grammar-constrained decoding yet.")
    res = _provider()._handle_error(err, duration=11138, model="deepseek-v4-flash-coder")
    assert res.success is False
    assert "DFLASH speculative decoding" in res.error
    assert "An error occurred during streaming" in res.error


def test_handle_error_body_without_message_fallback():
    """body 为空/None 时退回原 str(error)，不崩、不附空。"""
    err_none_body = _make_api_error("some error", None)
    res = _provider()._handle_error(err_none_body, duration=100, model="m")
    assert res.error == "some error"


def test_handle_error_plain_exception_unchanged():
    """普通异常（无 body 属性）行为不变。"""
    res = _provider()._handle_error(ValueError("boom"), duration=100, model="m")
    assert res.error == "boom"
