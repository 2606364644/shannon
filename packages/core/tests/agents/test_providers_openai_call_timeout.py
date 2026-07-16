"""call() wall-clock 超时：openai 引擎 stream_events 永久 stall 时不得 hang。

回归 2026-07-16 trip_1784167551：deepseek-openai profile 跑 pre-recon，Runner.run_streamed
+ stream_events 无 wall-clock 超时，deepseek 流式响应 stall（服务端/网络断流，TCP 未断）
致 agent 静默永久 await、worker 线程全 sleeping、events 停止产出、live 页无日志更新；
run_agent activity 的 2h start_to_close_timeout 未到，temporal 不介入，用户 50min 只见
"没日志"。根因：openai 引擎是 in-process SDK，缺 claude CLI 子进程那层 HTTP 超时兜底
（见 CLAUDE.md §2「CLI 运行时 vs 纯框架」）。修：call() 给 stream 消费包 asyncio.wait_for，
超时 → _classify_error 判 retryable → activity 重试，不再静默永久 hang。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from shannon_core.agents.providers_openai import OpenAIProvider
from shannon_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


@pytest.mark.asyncio
async def test_call_streaming_stall_times_out_not_hang(monkeypatch):
    """stream_events 产出 1 event 后永久 stall → call() 必须 wall-clock 超时返回
    retryable 失败，而非永久 hang。"""
    monkeypatch.setenv("SHANNON_OPENAI_CALL_TIMEOUT", "0.5")
    p = _provider()

    async def _stalling_stream():
        # 产 1 个无害 event（agent_updated 被 collector 忽略），再永久 await
        yield MagicMock(type="agent_updated_stream_event")
        await asyncio.Event().wait()  # 永不 set → 模拟 deepseek 流式断流

    result = MagicMock()
    result.stream_events = _stalling_stream
    monkeypatch.setattr(
        "shannon_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=result))

    # wait_for 兜底：若超时缺失（call 永久 hang），10s 后测试失败而非挂死整个 suite
    ret = await asyncio.wait_for(
        p.call(prompt="P", cwd="/tmp", model_tier="medium"),
        timeout=10)

    assert ret.success is False
    assert ret.retryable is True
