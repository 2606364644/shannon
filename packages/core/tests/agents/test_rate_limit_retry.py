"""统一 429 重试单测（rate_limit_retry.call_with_rate_limit_retry）。

背景（2026-09-02 NodeGoat-20260902-045436 深挖）：poc-agent-xss 两次 429
（GLM ServerOverloaded 过载窗口）直接整类丢弃、XSS 类 0/14 卡 PoC——全系统
对 429 的处理散装三层（SDK 内部 1 次 / chunk 路径 TRANSIENT_RETRIES / Temporal
activity 重试），agent 路径被 except Exception 吃掉后一处都不生效。本模块把
429 重试收敛到 runner 层单点（run_claude_prompt 是全仓唯一 LLM 漏斗），本测试
锁定其行为契约：

- 只重 error_code=="RateLimitError"：timeout（幂等，重试只是再超时一遍——
  docs/scan-time-gates.md §四血泪史）与其他 error_code 不重试；
- 预算耗尽后失败 result 原样返回（上层 Temporal/调用方语义不变）；
- CancelledError 穿透（BaseException，不吞不重——cancel 语义优先于重试）；
- backoff 指数增长 + 上限 + jitter（防并发 agent 同步风暴）；
- env 旋钮畸形值回落默认 + warning，不 crash（对齐 concurrency.py 容错契约）。
"""
import asyncio

import pytest

from supernova_core.agents.rate_limit_retry import (
    call_with_rate_limit_retry,
    get_rate_limit_backoff_base,
    get_rate_limit_retries,
)
from supernova_core.agents.runner import ClaudeRunResult


def _ok() -> ClaudeRunResult:
    return ClaudeRunResult(text="done", success=True)


def _rate_limited(msg: str = "Error code: 429 - ServerOverloaded") -> ClaudeRunResult:
    return ClaudeRunResult(
        success=False, error=msg, error_code="RateLimitError", retryable=True)


def _failed(code: str, retryable: bool) -> ClaudeRunResult:
    return ClaudeRunResult(
        success=False, error=f"boom ({code})", error_code=code, retryable=retryable)


class _SleepRecorder:
    """捕获重试退避时长（不真睡）。"""

    def __init__(self, monkeypatch) -> None:
        self.delays: list[float] = []
        monkeypatch.setattr(
            "supernova_core.agents.rate_limit_retry.asyncio.sleep", self._sleep)

    async def _sleep(self, delay: float) -> None:
        self.delays.append(delay)


class _CallCounter:
    """按序返回预置 result 序列的 call_fn，记录调用次数。"""

    def __init__(self, *results: ClaudeRunResult) -> None:
        self.results = list(results)
        self.calls = 0

    async def __call__(self, *args, **kwargs) -> ClaudeRunResult:
        idx = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[idx]


# ---- 核心行为 --------------------------------------------------------------


def test_success_first_try_no_retry(monkeypatch):
    sleep = _SleepRecorder(monkeypatch)
    fn = _CallCounter(_ok())

    result = asyncio.run(call_with_rate_limit_retry(fn, retries=2, backoff_base=0.01))

    assert result.success is True
    assert fn.calls == 1
    assert sleep.delays == []


def test_rate_limit_then_success(monkeypatch):
    """429 一次 → 退避 → 重试成功：返回成功 result，调用 2 次。"""
    _SleepRecorder(monkeypatch)
    fn = _CallCounter(_rate_limited(), _ok())

    result = asyncio.run(call_with_rate_limit_retry(fn, retries=2, backoff_base=0.01))

    assert result.success is True
    assert fn.calls == 2


def test_rate_limit_exhausted_returns_failure_as_is(monkeypatch):
    """持续 429 → 重试耗尽 → 失败 result 原样返回（error_code 保留给上层）。"""
    _SleepRecorder(monkeypatch)
    fn = _CallCounter(_rate_limited())

    result = asyncio.run(call_with_rate_limit_retry(fn, retries=2, backoff_base=0.01))

    assert result.success is False
    assert result.error_code == "RateLimitError"
    assert result.retryable is True
    assert fn.calls == 3  # 首次 + 2 次重试


def test_timeout_not_retried(monkeypatch):
    """timeout 刻意不重（幂等超时重试只是再超时一遍，scan-time-gates §四）。"""
    sleep = _SleepRecorder(monkeypatch)
    fn = _CallCounter(_failed("TimeoutError", retryable=False))

    result = asyncio.run(call_with_rate_limit_retry(fn, retries=2, backoff_base=0.01))

    assert result.success is False
    assert fn.calls == 1
    assert sleep.delays == []


def test_other_error_code_not_retried(monkeypatch):
    """非 RateLimitError 失败（如 AgentExecutionError）不重——重试语义只属 429。"""
    sleep = _SleepRecorder(monkeypatch)
    fn = _CallCounter(_failed("AgentExecutionError", retryable=True))

    result = asyncio.run(call_with_rate_limit_retry(fn, retries=2, backoff_base=0.01))

    assert result.success is False
    assert fn.calls == 1
    assert sleep.delays == []


def test_retries_zero_disables(monkeypatch):
    sleep = _SleepRecorder(monkeypatch)
    fn = _CallCounter(_rate_limited())

    result = asyncio.run(call_with_rate_limit_retry(fn, retries=0, backoff_base=0.01))

    assert result.success is False
    assert fn.calls == 1
    assert sleep.delays == []


def test_cancelled_error_propagates(monkeypatch):
    """CancelledError 穿透：cancel 语义优先于重试（activity 取消/超时收尾不拖泥带水）。"""
    _SleepRecorder(monkeypatch)

    class _Cancelled:
        calls = 0

        async def __call__(self):
            self.calls += 1
            raise asyncio.CancelledError()

    fn = _Cancelled()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call_with_rate_limit_retry(fn, retries=2, backoff_base=0.01))
    assert fn.calls == 1


# ---- backoff 曲线 ----------------------------------------------------------


def test_backoff_exponential_with_cap_and_jitter(monkeypatch):
    """指数 20s → 40s →（cap 120s 封顶），jitter ∈ [0, +25%]。"""
    sleep = _SleepRecorder(monkeypatch)
    fn = _CallCounter(_rate_limited())

    asyncio.run(call_with_rate_limit_retry(fn, retries=4, backoff_base=20.0))

    assert len(sleep.delays) == 4
    # 指数：base * 2^attempt，jitter 上界 +25%
    assert 20.0 <= sleep.delays[0] <= 20.0 * 1.25
    assert 40.0 <= sleep.delays[1] <= 40.0 * 1.25
    assert 80.0 <= sleep.delays[2] <= 80.0 * 1.25
    assert 120.0 <= sleep.delays[3] <= 120.0 * 1.25  # 160 被 cap 压回 120


# ---- env 旋钮容错（对齐 concurrency.py 契约） ------------------------------


def test_env_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_RATE_LIMIT_RETRIES", raising=False)
    monkeypatch.delenv("SUPERNOVA_RATE_LIMIT_BACKOFF_SECONDS", raising=False)

    assert get_rate_limit_retries() == 2
    assert get_rate_limit_backoff_base() == 20.0


def test_env_values_passed_through(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_RETRIES", "3")
    monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_BACKOFF_SECONDS", "45")

    assert get_rate_limit_retries() == 3
    assert get_rate_limit_backoff_base() == 45.0


def test_env_malformed_falls_back(monkeypatch):
    """畸形值回落默认 + warning，绝不 crash 扫描。"""
    monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_RETRIES", "abc")
    monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_BACKOFF_SECONDS", "-5")

    assert get_rate_limit_retries() == 2
    assert get_rate_limit_backoff_base() == 20.0


def test_env_retries_negative_falls_back(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_RETRIES", "-1")
    assert get_rate_limit_retries() == 2


# ---- run_claude_prompt 集成（接线验证） -------------------------------------


class TestRunClaudePromptRateLimitRetry:
    """run_claude_prompt 层：429 失败经统一重试后透出成功（接线不破不漏）。"""

    @pytest.mark.asyncio
    async def test_429_then_success_via_run_claude_prompt(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from supernova_core.agents.runner import run_claude_prompt

        _SleepRecorder(monkeypatch)  # 退避不真睡
        monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_RETRIES", "2")

        mock_provider = MagicMock()
        mock_provider.call = _CallCounter(
            _rate_limited(), _rate_limited(), _ok())

        with patch("supernova_core.agents.providers.create_provider",
                   return_value=mock_provider):
            result = await run_claude_prompt(prompt="p", repo_path="/tmp")

        assert result.success is True
        assert mock_provider.call.calls == 3  # 两次 429 + 重试成功

    @pytest.mark.asyncio
    async def test_timeout_failure_not_retried_via_run_claude_prompt(self, monkeypatch):
        """timeout 失败不经重试直接返回（幂等超时血泪史语义保持）。"""
        from unittest.mock import MagicMock, patch

        from supernova_core.agents.runner import run_claude_prompt

        sleep = _SleepRecorder(monkeypatch)
        monkeypatch.setenv("SUPERNOVA_RATE_LIMIT_RETRIES", "2")

        mock_provider = MagicMock()
        mock_provider.call = _CallCounter(_failed("TimeoutError", retryable=False))

        with patch("supernova_core.agents.providers.create_provider",
                   return_value=mock_provider):
            result = await run_claude_prompt(prompt="p", repo_path="/tmp")

        assert result.success is False
        assert result.error_code == "TimeoutError"
        assert mock_provider.call.calls == 1
        assert sleep.delays == []
