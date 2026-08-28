"""T4-2：openai 引擎 call() 的 stream_collector.close 必须在取消/超时路径收尾。

spec 2026-08-28-temporal-native-cancel-design 修 D：close→_flush_turn 非幂等，
旧代码只在正常/MaxTurns 路径显式 close，取消（wait_for 抛 CancelledError）与
超时路径无收尾。修：try 加 finally（suppress(Exception)——CancelledError 照穿），
删两处显式 close（防双调重复上报尾文本）。
"""
import asyncio
from unittest.mock import MagicMock

from supernova_core.agents import providers_openai as po
from supernova_core.agents.runner import ProviderConfig


def _provider():
    return po.OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


async def test_cancel_closes_stream_collector(monkeypatch):
    """stream 消费被外部 cancel → finally close 被调且只一次，CancelledError 照穿。"""
    p = _provider()
    closes: list = []

    async def _stall_stream():
        yield MagicMock(type="agent_updated_stream_event")
        await asyncio.Event().wait()

    result = MagicMock()
    result.stream_events = _stall_stream
    monkeypatch.setattr(
        po, "Runner", MagicMock(run_streamed=MagicMock(return_value=result)))
    # 直接观察 close 调用数：monkeypatch StreamCollector.close
    orig_close = po.StreamCollector.close

    async def _spy_close(self):
        closes.append(1)
        await orig_close(self)

    monkeypatch.setattr(po.StreamCollector, "close", _spy_close)

    task = asyncio.create_task(p.call(prompt="P", cwd="/tmp", model_tier="medium"))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
        raised = None
    except asyncio.CancelledError:
        raised = True
    except BaseException as e:  # noqa: BLE001 - call 可能把 cancel 归类为失败结果？
        raised = e
    # 无论取消以异常还是失败结果形态浮出，close 必须恰好一次
    assert len(closes) == 1, f"close 调用 {len(closes)} 次（期望恰 1：取消路径也收尾、不双调）"


async def test_timeout_path_closes_stream_collector(monkeypatch):
    """wait_for 超时（TimeoutError 路径）→ finally close 同样被调恰一次。"""
    monkeypatch.setenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", "0.3")
    p = _provider()
    closes: list = []

    async def _stall_stream():
        yield MagicMock(type="agent_updated_stream_event")
        await asyncio.Event().wait()

    result = MagicMock()
    result.stream_events = _stall_stream
    monkeypatch.setattr(
        po, "Runner", MagicMock(run_streamed=MagicMock(return_value=result)))
    orig_close = po.StreamCollector.close

    async def _spy_close(self):
        closes.append(1)
        await orig_close(self)

    monkeypatch.setattr(po.StreamCollector, "close", _spy_close)

    ret = await asyncio.wait_for(
        p.call(prompt="P", cwd="/tmp", model_tier="medium"), timeout=10)
    assert ret.success is False  # 超时失败语义（对齐 test_providers_openai_call_timeout）
    assert len(closes) == 1, f"close 调用 {len(closes)} 次（期望恰 1）"
