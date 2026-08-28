"""异常路径 error 丢失 + 可观测性缺口（2026-08-28 NodeGoat-20260828-054537 后续）。

现场推演：injection-exploit turn 153 bash tool_start 后 1.5s CLI 层静默失败，
executor 收到 success=False + error 空 → PentestError 落 fallback
（"Agent injection-exploit execution failed"）。静态穷举 provider/runner/executor
全部失败路径均填非空 error——唯一能产出「success=False + error 空」的是 call()
的异常分支 _handle_error：error_msg = str(error)，str 为空的异常（如无参
TimeoutError()）产 error=""（falsy）→ executor 落 fallback。且该分支此前不落盘
type(e)/repr(e)——375ba4c2 的可观测性只覆盖 ResultMessage 路径，异常路径仍是黑洞。

本文件锁定：(1) 空消息异常的 result.error 必须保类型名（repr 兜底）；
(2) 异常路径必须落盘 type/repr warning；（3) 有消息异常的 error 保持原文不破坏。
"""
import asyncio
import logging

from supernova_core.agents.providers_anthropic import AnthropicProvider
from supernova_core.agents.runner import ProviderConfig

LOGGER_NAME = "supernova_core.agents.providers_anthropic"


def _run_call_raising(provider, exc, tmp_path):
    async def fake_execute(prompt, options, audit_logger=None):
        raise exc

    provider._execute_query = fake_execute
    return asyncio.run(provider.call(prompt="probe", cwd=str(tmp_path)))


class TestExceptionPathErrorIntegrity:

    def test_empty_message_exception_error_keeps_type_name(self, tmp_path):
        """str 为空的异常：result.error 必须非空且含异常类型名（不落 executor fallback）。"""
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="probe-model"))
        assert str(TimeoutError()) == ""  # 前置：本测试针对的形态
        result = _run_call_raising(provider, TimeoutError(), tmp_path)
        assert result.success is False
        assert result.error  # 非空（现场为 ""，executor 侧 falsy 落 fallback）
        assert "TimeoutError" in result.error

    def test_exception_path_logs_raw_exception(self, tmp_path, caplog):
        """异常路径必须落盘异常类型（type(e)），与 ResultMessage 路径的可观测性对齐。"""
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="probe-model"))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _run_call_raising(provider, TimeoutError(), tmp_path)
        assert result.success is False
        assert "TimeoutError" in caplog.text

    def test_nonempty_exception_error_keeps_message_and_logs_type(self, tmp_path, caplog):
        """有消息异常：error 保持 str(e) 原文（现有语义不动），type 同样落盘。"""
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="probe-model"))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _run_call_raising(provider, ValueError("gateway EOF"), tmp_path)
        assert result.success is False
        assert result.error == "gateway EOF"
        assert "ValueError" in caplog.text
