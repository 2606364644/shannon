"""task 子代理（_make_subagent_runner）流式运行 + wall-clock 兜底 + 可观测性。

回归 NodeGoat-20260818-133852 xss-vuln（2026-08-18）：子代理用非流式
``Runner.run``——生成完成前零字节返回，HTTP 读超时（默认 300s）等于
"整个生成必须 300s 内完成"。marked 源码分析类长生成必超时；SDK 原样重发
同样的请求 → 每次精确死在 300s（diagnostic.log 14:37:57/14:42:58/14:47:59
三条 Retrying 间隔正好 300s），4 次耗尽后 ``[task error] subagent failed:
Request timed out.``，白烧 28 分钟并把主 agent 的 40min call_timeout 拖满，
45 轮成果全弃（xss_exploitation_queue.json 缺失）。

主 agent 早已是 run_streamed（流式 chunk 持续重置读超时，长生成扛得住）；
子代理必须对齐。同时补 wall-clock 兜底（子代理此前零超时，stall 时只能靠
主 agent 2400s 拖死）与进度日志（28 分钟黑盒只能靠主 agent 侧尸检推断）。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def _fake_streamed_result(events, final_output="done"):
    """构造 run_streamed 风格的 result：stream_events() 产 events 后结束。"""

    async def _stream():
        for ev in events:
            yield ev

    result = MagicMock()
    result.stream_events = _stream
    result.final_output = final_output
    return result


@pytest.mark.asyncio
async def test_subagent_runner_uses_run_streamed(monkeypatch):
    """子代理必须经 Runner.run_streamed + 消费 stream_events（非流式 Runner.run
    的长生成必被 HTTP 读超时杀死——本次回归根因）。"""
    p = _provider()
    calls = {"run_streamed": 0, "run": 0}

    def _fake_run_streamed(*args, **kwargs):
        calls["run_streamed"] += 1
        return _fake_streamed_result([], final_output="subagent output")

    async def _fail_run(*args, **kwargs):
        calls["run"] += 1
        raise AssertionError("subagent must not use non-streaming Runner.run")

    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(side_effect=_fake_run_streamed))
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run",
        MagicMock(side_effect=_fail_run))

    run = p._make_subagent_runner("model-x", "/tmp")
    out = await run("analyze app.py")
    assert out == "subagent output"
    assert calls["run_streamed"] == 1
    assert calls["run"] == 0


@pytest.mark.asyncio
async def test_subagent_runner_wall_clock_timeout(monkeypatch):
    """子代理 stream 永久 stall → 必须 wall-clock 超时抛错（不得永久 hang），
    由 task 工具层转 [task error] 文案让父 agent 自行兜底。"""
    monkeypatch.setenv("SUPERNOVA_OPENAI_SUBAGENT_CALL_TIMEOUT", "0.5")
    p = _provider()

    async def _stalling_stream():
        yield MagicMock(type="agent_updated_stream_event")
        await asyncio.Event().wait()  # 永不 set → 模拟子代理流断流

    result = MagicMock()
    result.stream_events = _stalling_stream
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=result))

    run = p._make_subagent_runner("model-x", "/tmp")
    # wait_for 兜底：若子代理超时缺失（永久 hang），10s 后测试失败而非挂死
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run("prompt"), timeout=10)


def test_subagent_call_timeout_env_default():
    """wall-clock 兜底读 SUPERNOVA_OPENAI_SUBAGENT_CALL_TIMEOUT，有独立默认
    （子代理体量小于主 agent，默认短于主 2400s）。env 未设时返回正值默认。"""
    import os
    os.environ.pop("SUPERNOVA_OPENAI_SUBAGENT_CALL_TIMEOUT", None)
    p = _provider()
    timeout = p._subagent_call_timeout()
    assert timeout > 0
    assert timeout < float(os.getenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", "2400"))


@pytest.mark.asyncio
async def test_subagent_runner_logs_lifecycle(caplog, monkeypatch):
    """可观测性：子代理 start/结束（含 duration/turns）必须落日志——本次回归
    28 分钟只能靠主 agent 侧 retry 时间戳尸检推断，子代理是黑盒。"""
    p = _provider()
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_fake_streamed_result(
            [MagicMock(type="agent_updated_stream_event")] * 3,
            final_output="ok")))

    run = p._make_subagent_runner("model-x", "/tmp")
    with caplog.at_level("INFO", logger="supernova_core.agents.providers_openai"):
        await run("some prompt")

    text = caplog.text
    assert "task subagent" in text
    assert "start" in text
    # 结束行带 duration
    assert "finish" in text or "end" in text or "done" in text
