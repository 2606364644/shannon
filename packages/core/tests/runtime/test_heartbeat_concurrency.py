"""P3c 阶段 3：heartbeat 按 workflow_id 隔离（B 启动不停 A）。

钉死旧「start_heartbeat ws_dir 变了先停旧」分支是并发杀心跳元凶——dict 化后按
workflow_id 隔离，不同 workflow 各自 daemon 互不影响。
"""
import asyncio

import pytest

from supernova_core.runtime.heartbeat import (
    start_heartbeat,
    stop_heartbeat,
    _HEARTBEATS,
)


@pytest.fixture(autouse=True)
async def _clean_heartbeats():
    """每个 test 前后清注册表 + 停残留 daemon 线程（避免泄漏 / 串台）。"""
    for wf_id in list(_HEARTBEATS):
        await stop_heartbeat(workflow_id=wf_id)
    _HEARTBEATS.clear()
    yield
    for wf_id in list(_HEARTBEATS):
        await stop_heartbeat(workflow_id=wf_id)
    _HEARTBEATS.clear()


@pytest.mark.asyncio
async def test_start_B_does_not_kill_A(tmp_path, monkeypatch):
    """wf-B 的 start_heartbeat 不能停掉 wf-A 的 daemon（删'先停旧'分支）。"""
    dirA, dirB = tmp_path / "A", tmp_path / "B"
    dirA.mkdir()
    dirB.mkdir()
    # 缩短 daemon 周期便于测试（_default_interval 是函数，patch 成 lambda）。
    monkeypatch.setattr(
        "supernova_core.runtime.heartbeat._default_interval", lambda: 0.05
    )
    await start_heartbeat(dirA, workflow_id="wf-A")
    mgrA = _HEARTBEATS["wf-A"]
    await start_heartbeat(dirB, workflow_id="wf-B")
    await asyncio.sleep(0.15)  # 让 daemon 跑几个周期
    # A 仍在注册表，且其 daemon 线程仍存活（未被 B 启动停掉）。
    assert "wf-A" in _HEARTBEATS
    assert "wf-B" in _HEARTBEATS
    assert _HEARTBEATS["wf-A"] is mgrA
    assert mgrA._heartbeat_thread is not None
    assert mgrA._heartbeat_thread.is_alive()


@pytest.mark.asyncio
async def test_stop_one_does_not_affect_other(tmp_path, monkeypatch):
    dirA, dirB = tmp_path / "A", tmp_path / "B"
    dirA.mkdir()
    dirB.mkdir()
    monkeypatch.setattr(
        "supernova_core.runtime.heartbeat._default_interval", lambda: 0.05
    )
    await start_heartbeat(dirA, workflow_id="wf-A")
    await start_heartbeat(dirB, workflow_id="wf-B")
    mgrB = _HEARTBEATS["wf-B"]
    await stop_heartbeat(workflow_id="wf-A")
    assert "wf-A" not in _HEARTBEATS
    assert "wf-B" in _HEARTBEATS
    assert _HEARTBEATS["wf-B"] is mgrB
    assert mgrB._heartbeat_thread is not None
    assert mgrB._heartbeat_thread.is_alive()


@pytest.mark.asyncio
async def test_idempotent_same_workflow_same_dir(tmp_path, monkeypatch):
    """同 workflow_id + 同 ws_dir → 幂等（不重启，同一 mgr 实例）。"""
    monkeypatch.setattr(
        "supernova_core.runtime.heartbeat._default_interval", lambda: 0.05
    )
    await start_heartbeat(tmp_path, workflow_id="wf-A")
    mgr1 = _HEARTBEATS["wf-A"]
    await start_heartbeat(tmp_path, workflow_id="wf-A")  # 幂等
    assert _HEARTBEATS["wf-A"] is mgr1
