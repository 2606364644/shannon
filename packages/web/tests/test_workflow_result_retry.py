# packages/web/tests/test_workflow_result_retry.py
"""_await_workflow_result 瞬态 RPC 容错（NodeGoat-20260817-132940 事故根因修复）。

temporalio result() 的 UserLongPoll 长轮询单次 >70s 即抛 RPCError(DEADLINE_EXCEEDED,
"context deadline exceeded")且 core 不重试——曾把仍在跑的黑盒 run 误标 failed。
重试仅覆盖 DEADLINE_EXCEEDED / UNAVAILABLE；其余（含 WorkflowFailureError 非 RPCError）
原样上抛，重试耗尽抛最后一次。快乐路径用原 handle（零额外连接），重试才重取。
"""
from unittest.mock import MagicMock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from supernova_web.components import scan_manager as sm


def _rpc_error(status: RPCStatusCode) -> RPCError:
    return RPCError("context deadline exceeded", status, b"")


def _manager(sequence: list):
    """构造 ScanManager（绕过 __init__），make_handle() 依序消费 sequence；
    重试路径的 Client.connect 也接到同一序列。"""
    mgr = object.__new__(sm.ScanManager)
    mgr._temporal_address = lambda: "localhost:7233"  # type: ignore[method-assign]
    calls = {"n": 0}

    def make_handle():
        h = MagicMock()
        idx = calls["n"]
        calls["n"] += 1
        item = sequence[idx]

        async def _result():
            if isinstance(item, Exception):
                raise item
            return item
        h.result = _result
        h.id = "wf-1"
        return h

    class _FakeClient:
        def get_workflow_handle(self, workflow_id):
            return make_handle()

    class _Client:
        @staticmethod
        async def connect(addr):
            return _FakeClient()

    return mgr, make_handle, _Client, calls


@pytest.mark.asyncio
async def test_transient_deadline_retried_then_success(monkeypatch):
    """DEADLINE_EXCEEDED 两次 → 第三次成功：重试吞掉瞬态错误，run 不再被误判。"""
    mgr, make_handle, _Client, calls = _manager([
        _rpc_error(RPCStatusCode.DEADLINE_EXCEEDED),
        _rpc_error(RPCStatusCode.DEADLINE_EXCEEDED),
        {"status": "completed"},
    ])
    monkeypatch.setattr(sm, "Client", _Client)
    out = await mgr._await_workflow_result(make_handle(), backoff_base=0.001)
    assert out == {"status": "completed"}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_unavailable_retried(monkeypatch):
    mgr, make_handle, _Client, _ = _manager([
        _rpc_error(RPCStatusCode.UNAVAILABLE),
        {"ok": 1},
    ])
    monkeypatch.setattr(sm, "Client", _Client)
    assert await mgr._await_workflow_result(
        make_handle(), backoff_base=0.001) == {"ok": 1}


@pytest.mark.asyncio
async def test_non_transient_raised_immediately(monkeypatch):
    """NOT_FOUND 等非瞬态：不重试直接抛（重试会掩盖真实错误）。"""
    mgr, make_handle, _Client, calls = _manager([
        _rpc_error(RPCStatusCode.NOT_FOUND),
    ])
    monkeypatch.setattr(sm, "Client", _Client)
    with pytest.raises(RPCError):
        await mgr._await_workflow_result(make_handle(), backoff_base=0.001)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_exhausted_raises_last(monkeypatch):
    """连续瞬态直到耗尽：抛最后一次 RPCError（5 次尝试）。"""
    mgr, make_handle, _Client, calls = _manager(
        [_rpc_error(RPCStatusCode.DEADLINE_EXCEEDED)] * 5)
    monkeypatch.setattr(sm, "Client", _Client)
    with pytest.raises(RPCError):
        await mgr._await_workflow_result(make_handle(), backoff_base=0.001)
    assert calls["n"] == 5


@pytest.mark.asyncio
async def test_workflow_failure_passes_through(monkeypatch):
    """WorkflowFailureError（workflow 真失败，非 RPCError）不被吞，原样上抛。"""
    from temporalio.client import WorkflowFailureError
    mgr, make_handle, _Client, calls = _manager([
        WorkflowFailureError(cause=RuntimeError("workflow failed")),
    ])
    monkeypatch.setattr(sm, "Client", _Client)
    with pytest.raises(WorkflowFailureError):
        await mgr._await_workflow_result(make_handle(), backoff_base=0.001)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_happy_path_no_extra_connect(monkeypatch):
    """快乐路径直接用传入 handle：不连 Client（零额外连接）。"""
    mgr, make_handle, _Client, calls = _manager([{"status": "completed"}])

    async def _boom(addr):
        raise AssertionError("happy path 不应 Client.connect")
    _Client.connect = staticmethod(_boom)
    monkeypatch.setattr(sm, "Client", _Client)
    assert await mgr._await_workflow_result(make_handle()) == {"status": "completed"}
    assert calls["n"] == 1
