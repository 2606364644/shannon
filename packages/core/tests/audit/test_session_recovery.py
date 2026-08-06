"""Worker 重启后可观测信号恢复(方案 A)单元测试。

spec/plan: docs/superpowers/plans/2026-08-06-worker-restart-observability-recovery-plan.md

worker OOM 重启后 temporal 恢复 workflow 时只重投在途 activity,不重跑已 completed 的
setup_display -> 新进程 _SESSIONS 空 -> get_audit_session() 返 NullAuditSession ->
events/heartbeat 静默丢 -> live 页失明。ensure_audit_session 在每个 activity 入口幂等
重建 AuditSession + heartbeat。本文件锁定其行为:
- 仅真实 temporal activity 上下文(workflow_id 非空 str)才重建;CLI/测试上下文跳过。
- 重建后 session 注册 + heartbeat 写出 + events.ndjson append 接旧流。
- 幂等 + 并发安全(per-wf_id 锁) + best-effort( build 失败不阻断扫描)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from supernova_core.audit import session_recovery as recov
from supernova_core.audit.session import AuditSession
from supernova_core.audit.session_registry import (
    NullAuditSession,
    _SESSIONS,
    _current_wf_id,
    get_audit_session_for,
    set_audit_session,
)
from supernova_core.logging.log_bus import _BUSES
from supernova_core.runtime.heartbeat import _HEARTBEATS, stop_heartbeat


def _fake_input(tmp_path, event_file=None):
    """最小 input(duck-typed):含 build_headless_audit_session 所需字段。"""
    ws = tmp_path / "ws"
    return SimpleNamespace(
        workspace_path=str(ws),
        workspace_name="ws-test",
        repo_path=str(tmp_path),
        web_url="https://example.com",
        event_file=str(event_file) if event_file else str(ws / "events.ndjson"),
    )


def _patch_real_activity_info(monkeypatch, wf_id="wf-restart"):
    """patch temporalio.activity.info 返真实 str workflow_id(模拟 worker activity 上下文)。

    ensure_audit_session 严格守卫:activity.info() 不抛 + workflow_id 非空 str 才重建。
    测试无真实 temporal worker,故 patch。attempt 给 run_agent 类 activity 用,此处通用。
    """
    monkeypatch.setattr(
        "temporalio.activity.info",
        lambda: MagicMock(workflow_id=wf_id, attempt=1),
    )


@pytest.fixture(autouse=True)
async def _restore_process_state():
    """进程级单例隔离:每个 test 后清 _SESSIONS / _current_wf_id / _BUSES / heartbeat /
    root logger handler(build_headless_audit_session 的 configure_logging + LogBus.attach +
    start_heartbeat 都有进程级副作用)。async fixture 参照 test_log_bus_attach 同款范式。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    _SESSIONS.clear()
    _current_wf_id.set(None)
    yield
    # 停所有 heartbeat daemon(防泄漏到后续 test)。
    for wf in list(_HEARTBEATS):
        await stop_heartbeat(wf)
    _SESSIONS.clear()
    _current_wf_id.set(None)
    for bus in list(_BUSES.values()):
        bus._attached = False
        bus._dispatcher = None
        if bus._drain_task is not None and not bus._drain_task.done():
            bus._drain_task.cancel()
        bus._drain_task = None
    _BUSES.clear()
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(saved_level)


async def _stop_all_heartbeats():
    for wf in list(_HEARTBEATS):
        await stop_heartbeat(wf)


# ── build_headless_audit_session ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_headless_audit_session_constructs_registers_and_heartbeats(tmp_path, monkeypatch):
    """build:构造 AuditSession + 注册进 _SESSIONS + 写首个 heartbeat + events.ndjson 可写。"""
    _patch_real_activity_info(monkeypatch)
    inp = _fake_input(tmp_path)
    session = await recov.build_headless_audit_session(inp)
    try:
        assert isinstance(session, AuditSession)
        # 注册到当前 workflow_id(wf-restart)。
        assert get_audit_session_for("wf-restart") is session
        # heartbeat 首个文件已写(start_heartbeat 同步写)。
        assert (tmp_path / "ws" / "heartbeat").exists()
    finally:
        await session.close()
        await recov.drain_and_detach(workflow_id="wf-restart")
        await _stop_all_heartbeats()


# ── ensure_audit_session:重建路径 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_rebuilds_when_session_absent(tmp_path, monkeypatch):
    """模拟 worker 重启:_SESSIONS 空 + 真实 str workflow_id -> ensure 重建 session + heartbeat。"""
    _patch_real_activity_info(monkeypatch)
    inp = _fake_input(tmp_path)
    try:
        await recov.ensure_audit_session(inp)
        session = get_audit_session_for("wf-restart")
        assert not isinstance(session, NullAuditSession), "重建后 session 不应是 Null"
        assert (tmp_path / "ws" / "heartbeat").exists(), "heartbeat 应恢复写出"
    finally:
        if not isinstance(get_audit_session_for("wf-restart"), NullAuditSession):
            await get_audit_session_for("wf-restart").close()
        await recov.drain_and_detach(workflow_id="wf-restart")
        await _stop_all_heartbeats()


@pytest.mark.asyncio
async def test_ensure_appends_to_existing_events_ndjson(tmp_path, monkeypatch):
    """append 接旧流:预写 events.ndjson 旧事件 -> 重建 -> 写新事件 -> 旧+新共存,无覆盖。"""
    _patch_real_activity_info(monkeypatch)
    ef = tmp_path / "ws" / "events.ndjson"
    ef.parent.mkdir(parents=True, exist_ok=True)
    # 旧事件(worker 重启前 setup_display 写的)。
    ef.write_text(json.dumps({"ts": "t-old", "category": "X", "type": "OldEvent"}) + "\n")

    inp = _fake_input(tmp_path, event_file=str(ef))
    await recov.ensure_audit_session(inp)
    session = get_audit_session_for("wf-restart")
    try:
        # 重建后写新事件(renderer append 模式,接旧流)。
        await session.start_agent("agent-after-restart", "p", attempt=2)
    finally:
        await session.close()
        await recov.drain_and_detach(workflow_id="wf-restart")
        await _stop_all_heartbeats()

    content = ef.read_text()
    assert "OldEvent" in content, "旧事件被覆盖--renderer 必须 append 不 truncate"
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) >= 2, f"应有旧+新至少 2 行,实际 {len(lines)}"


# ── ensure_audit_session:幂等 / 跳过 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_idempotent_when_session_exists(tmp_path, monkeypatch):
    """session 已存在(setup_display 已建)-> ensure 不重建(断言 build 不被调)。"""
    _patch_real_activity_info(monkeypatch)
    # 预置一个非 Null session(模拟 setup_display 已跑)。
    sentinel = MagicMock(spec=AuditSession)
    set_audit_session(sentinel, workflow_id="wf-restart")
    build_spy = MagicMock(wraps=recov.build_headless_audit_session)
    monkeypatch.setattr(recov, "build_headless_audit_session", build_spy)
    try:
        await recov.ensure_audit_session(_fake_input(tmp_path))
        assert build_spy.call_count == 0, "session 已存在不应重建"
        assert get_audit_session_for("wf-restart") is sentinel
    finally:
        _SESSIONS.pop("wf-restart", None)


@pytest.mark.asyncio
async def test_ensure_skips_without_temporal_context(tmp_path):
    """无 temporal activity 上下文(activity.info() 抛 RuntimeError,如 CLI/纯单测)-> 跳过。"""
    # 不 patch activity.info -> 真实 activity.info() 在无 temporal 上下文时抛 RuntimeError。
    await recov.ensure_audit_session(_fake_input(tmp_path))
    assert isinstance(get_audit_session_for("wf-restart"), NullAuditSession), \
        "无 temporal 上下文不应重建"


@pytest.mark.asyncio
async def test_ensure_skips_when_workflow_id_not_string(tmp_path, monkeypatch):
    """activity.info 被 patch 成 MagicMock(workflow_id 非 str,如既有单测)-> 跳过,不误重建。

    守「不破坏现有 patch activity.info=MagicMock(attempt=N) 的单测」:那些测试自行 set/patch
    session,ensure 不应触发重建引入 daemon/LogBus 副作用泄漏。
    """
    monkeypatch.setattr("temporalio.activity.info",
                        lambda: MagicMock(attempt=1))  # workflow_id 自动 MagicMock(非 str)
    await recov.ensure_audit_session(_fake_input(tmp_path))
    assert isinstance(get_audit_session_for("wf-restart"), NullAuditSession), \
        "workflow_id 非 str(测试 patch)不应重建"


# ── ensure_audit_session:并发 + best-effort ─────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_concurrent_only_one_build(tmp_path, monkeypatch):
    """worker 重启后 temporal 并发重投多个在途 activity:per-wf_id 锁串行化,只首个重建。"""
    _patch_real_activity_info(monkeypatch)
    inp = _fake_input(tmp_path)

    build_count = 0

    async def counting_build(i):
        nonlocal build_count
        build_count += 1
        # 模拟 build 注册 session(让后续 ensure 的 double-check 命中跳过)。
        set_audit_session(MagicMock(spec=AuditSession), workflow_id="wf-restart")
        # 给其它 task 让出锁的机会(放大竞态窗口,使锁的必要性可被观测)。
        await asyncio.sleep(0)

    monkeypatch.setattr(recov, "build_headless_audit_session", counting_build)
    try:
        await asyncio.gather(
            recov.ensure_audit_session(inp),
            recov.ensure_audit_session(inp),
            recov.ensure_audit_session(inp),
        )
        assert build_count == 1, f"并发应只建 1 次,实际 {build_count}(锁未串行化重建)"
    finally:
        _SESSIONS.pop("wf-restart", None)


@pytest.mark.asyncio
async def test_ensure_swallows_build_failure(tmp_path, monkeypatch):
    """build 失败(磁盘满/权限等)-> ensure best-effort 吞掉,不阻断 activity(扫描继续 blind)。"""
    _patch_real_activity_info(monkeypatch)

    async def failing_build(i):
        raise OSError("disk full")

    monkeypatch.setattr(recov, "build_headless_audit_session", failing_build)
    # 不应抛。
    await recov.ensure_audit_session(_fake_input(tmp_path))
    assert isinstance(get_audit_session_for("wf-restart"), NullAuditSession), \
        "build 失败后 session 应仍为 Null(恢复失败=现状 blind)"
