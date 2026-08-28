"""T4-1：claude 引擎 _execute_query 的 query 生成器必须被显式关闭（aclosing）。

spec 2026-08-28-temporal-native-cancel-design 修 C：cancel 打断 async for 或
``action == "complete"`` break 提前退出时，旧代码靠 GC 关闭 SDK query 生成器——
CLI 子进程清理不确定。包 contextlib.aclosing 后 async with 退出即显式 aclose
（GeneratorExit 进生成器 → SDK 清理 CLI 子进程）。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

import supernova_core.agents.providers_anthropic as pa


@pytest.fixture
def fake_query(monkeypatch):
    closed: list = []

    class _FakeResultMessage:
        def __init__(self, **kwargs):
            pass

    async def _fake_query(*, prompt, options):
        try:
            yield MagicMock()  # 非 ResultMessage 的普通 event
            await asyncio.Event().wait()  # 模拟 CLI 长跑（永不主动结束）
        finally:
            closed.append(1)

    monkeypatch.setattr(pa, "query", _fake_query)
    monkeypatch.setattr(pa, "ResultMessage", _FakeResultMessage)
    return closed


async def test_complete_break_closes_generator(fake_query):
    """dispatch 返回 complete → break → 生成器被显式关闭（不等 GC）。"""
    dispatcher = MagicMock()
    async def _dispatch(event):
        return "complete"
    dispatcher.dispatch = _dispatch
    dispatcher.collected_text = ""
    dispatcher.turn_count = 0
    dispatcher.spending_cap_detected = False
    dispatcher.result_is_error = None
    dispatcher.result_subtype = None
    dispatcher.stop_reason = None
    dispatcher.permission_denials = []
    dispatcher.api_error_status = None
    dispatcher.result_errors = []

    provider = pa.AnthropicProvider.__new__(pa.AnthropicProvider)
    await asyncio.wait_for(
        provider._execute_query("p", options=MagicMock(), dispatcher=dispatcher),
        timeout=5)
    assert fake_query == [1], "break 退出后 query 生成器应被 aclosing 显式关闭"


async def test_cancelled_stream_closes_generator(fake_query):
    """cancel 打断 async for → 生成器关闭（CancelledError 照穿不吞）。"""
    async def _dispatch(event):
        await asyncio.Event().wait()  # dispatch 也挂起，让 cancel 落在循环内

    dispatcher = MagicMock()
    dispatcher.dispatch = _dispatch

    provider = pa.AnthropicProvider.__new__(pa.AnthropicProvider)
    task = asyncio.create_task(
        provider._execute_query("p", options=MagicMock(), dispatcher=dispatcher))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # 取消路径生成器同样被关闭（aclosing 的 finally / GeneratorExit）
    for _ in range(50):
        if fake_query:
            break
        await asyncio.sleep(0.1)
    assert fake_query == [1], "取消打断后 query 生成器应被关闭（CLI 子进程清理）"
