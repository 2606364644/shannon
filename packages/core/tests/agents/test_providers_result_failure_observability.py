"""可观测性缺口修复（2026-08-28 NodeGoat-20260828-054537 injection-exploit 失败调查）。

现场：CLI 安静终止（发 ResultMessage 后退出，无 stderr、无崩溃打印），
provider 失败分支只把分类结果（error_code/retryable）透传 executor，
原始信号（subtype / is_error / api_error_status / errors）一个都没落日志，
且 result.error 在传输链中丢失（PentestError 落 fallback 消息）——
根因（API 401/403 vs turn 限额 vs 执行错误）无法从任何日志确证。

本测试锁定：call() 失败分支必须以 warning 级别落盘全部原始失败信号。
"""
import asyncio
import logging
from types import SimpleNamespace

from supernova_core.agents.providers_anthropic import AnthropicProvider
from supernova_core.agents.runner import ProviderConfig

LOGGER_NAME = "supernova_core.agents.providers_anthropic"


def _make_failed_result_message(subtype, is_error=True, api_error_status=None, errors=None):
    """构造带 L1 元数据的假 ResultMessage（call() 全程 getattr 读取，SimpleNamespace 足够）。"""
    rm = SimpleNamespace(
        collected_text="",
        result="",
        usage={"input_tokens": 100, "output_tokens": 10,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        structured_output=None,
        result_is_error=is_error,
        result_subtype=subtype,
        stop_reason=None,
        api_error_status=api_error_status,
        result_errors=errors,
        permission_denials=None,
        turn_count=153,
    )
    return rm


def _run_call(provider, result_message, tmp_path):
    async def fake_execute(prompt, options, audit_logger=None):
        return result_message

    provider._execute_query = fake_execute
    return asyncio.run(provider.call(prompt="probe", cwd=str(tmp_path)))


class TestResultFailureObservability:

    def test_failure_branch_logs_raw_signals(self, tmp_path, caplog):
        """error_max_turns 形态：subtype/is_error 必须出现在 warning 日志。"""
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="probe-model"))
        rm = _make_failed_result_message("error_max_turns")
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _run_call(provider, rm, tmp_path)
        assert result.success is False
        assert result.error_code == "ExecutionLimitError"
        assert result.retryable is False
        assert "error_max_turns" in caplog.text
        assert "is_error=True" in caplog.text

    def test_failure_branch_logs_api_error_status(self, tmp_path, caplog):
        """API 错误形态：api_error_status（如 401/403）必须出现在 warning 日志。"""
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="probe-model"))
        rm = _make_failed_result_message(
            None, is_error=True, api_error_status=401, errors=["token expired"])
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _run_call(provider, rm, tmp_path)
        assert result.success is False
        assert "api_error_status=401" in caplog.text
        assert "token expired" in caplog.text

    def test_success_path_does_not_log_failure(self, tmp_path, caplog):
        """成功路径不得打失败信号日志（防噪音）。"""
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", model="probe-model"))
        rm = _make_failed_result_message(None, is_error=False)
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = _run_call(provider, rm, tmp_path)
        assert result.success is True
        assert "SDK result failure signals" not in caplog.text
