"""HeartbeatManager TDD: scan worker 进程级心跳(周期写 heartbeat)+ 协作式取消监听。

设计见 docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md §4.1/§4.4。
心跳 task 进程级、独立于 Temporal workflow/activity 调度——worker 活就跳、worker 死就停。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from supernova_core.runtime.heartbeat import HeartbeatManager

pytestmark = pytest.mark.asyncio


async def test_enter_writes_initial_heartbeat(tmp_path):
    """进入 context 立即写初始 heartbeat(不等首周期),消除「workspace 刚建、未到首周期」空窗。"""
    async with HeartbeatManager(tmp_path, interval=30):
        hb = tmp_path / "heartbeat"
        assert hb.exists()
        float(hb.read_text().strip())  # 内容是单行 unix 时间戳


async def test_periodic_writes_update_mtime(tmp_path):
    """周期写刷新 heartbeat mtime(worker 活就持续跳)。用 ns 精度避开秒级 mtime 边界。"""
    async with HeartbeatManager(tmp_path, interval=0.05):
        hb = tmp_path / "heartbeat"
        mtime1 = hb.stat().st_mtime_ns
        await asyncio.sleep(0.12)  # >2 个周期
        assert hb.stat().st_mtime_ns > mtime1


async def test_atomic_write_no_partial(tmp_path):
    """原子写(temp + os.replace):并发读不读到半截,内容始终是完整单行 float。"""
    async with HeartbeatManager(tmp_path, interval=0.01):
        hb = tmp_path / "heartbeat"
        samples: list[str] = []
        for _ in range(60):
            try:
                samples.append(hb.read_text())
            except FileNotFoundError:
                pass
            await asyncio.sleep(0)  # 让出给写 task 制造并发
        assert samples  # 至少读到一些
        for s in samples:
            stripped = s.strip()
            assert stripped != ""
            assert stripped.count("\n") == 0  # 单行,不半截
            float(stripped)  # 完整可 parse


async def test_exit_removes_heartbeat(tmp_path):
    """正常退出 best-effort 删 heartbeat(判活不依赖删除,但正常退出应清理)。"""
    async with HeartbeatManager(tmp_path, interval=30):
        assert (tmp_path / "heartbeat").exists()
    assert not (tmp_path / "heartbeat").exists()


async def test_cancel_requested_triggers_on_cancel(tmp_path):
    """检测 cancel.requested 文件存在 → 触发 on_cancel(协作式取消桥)。"""
    called = asyncio.Event()

    def on_cancel() -> None:
        called.set()

    async with HeartbeatManager(tmp_path, interval=0.05, on_cancel=on_cancel):
        (tmp_path / "cancel.requested").write_text("")
        await asyncio.wait_for(called.wait(), timeout=1.0)
    assert called.is_set()


async def test_cancel_not_triggered_twice(tmp_path):
    """on_cancel 只触发一次:检测后删 cancel.requested,避免重复触发。"""
    count = 0

    def on_cancel() -> None:
        nonlocal count
        count += 1

    async with HeartbeatManager(tmp_path, interval=0.05, on_cancel=on_cancel):
        (tmp_path / "cancel.requested").write_text("")
        await asyncio.sleep(0.25)  # 多个监听周期
    assert count == 1


async def test_no_on_cancel_callback_safe(tmp_path):
    """on_cancel=None 不崩,只是不起取消监听。"""
    async with HeartbeatManager(tmp_path, interval=0.05, on_cancel=None):
        (tmp_path / "cancel.requested").write_text("")
        await asyncio.sleep(0.1)
    # 不崩即可(三 pipeline 不传 on_cancel 时的安全退路)


async def test_default_interval_from_env(monkeypatch):
    """interval 默认从 SUPERNOVA_HEARTBEAT_INTERVAL_SECONDS 读(默认 30;spec §5)。"""
    from supernova_core.runtime.heartbeat import _default_interval
    monkeypatch.delenv("SUPERNOVA_HEARTBEAT_INTERVAL_SECONDS", raising=False)
    assert _default_interval() == 30.0
    monkeypatch.setenv("SUPERNOVA_HEARTBEAT_INTERVAL_SECONDS", "5")
    assert _default_interval() == 5.0


async def test_mark_owner_if_unset(tmp_path):
    """worker 标 owner=host,仅当 session.json 未设 owner(不覆盖 scan_manager 写的 owner=web)。"""
    from supernova_core.runtime.heartbeat import mark_owner_if_unset
    sf = tmp_path / "session.json"
    # 未设 → 写 host(CLI 起的 scan)
    sf.write_text(json.dumps({"status": "running"}))
    mark_owner_if_unset(tmp_path, "host")
    assert json.loads(sf.read_text())["owner"] == "host"
    # 已设 web → 不覆盖(web 起的 scan,scan_manager 已写 web)
    sf.write_text(json.dumps({"owner": "web", "status": "running"}))
    mark_owner_if_unset(tmp_path, "host")
    assert json.loads(sf.read_text())["owner"] == "web"


async def test_creates_ws_dir_if_missing(tmp_path):
    """ws_dir 不存在时建目录后写 heartbeat(健壮性:blackbox resume 传 workspace_name 时
    ws_dir 可能尚未由子进程创建;production ws_dir 总存在,mkdir exist_ok 无害)。"""
    ws_dir = tmp_path / "missing-ws"
    assert not ws_dir.exists()
    async with HeartbeatManager(ws_dir, interval=30):
        assert ws_dir.exists()
        assert (ws_dir / "heartbeat").exists()


async def test_heartbeat_writes_while_event_loop_blocked(tmp_path):
    """核心不变量:event loop 被同步 CPU 密集段阻塞期间,heartbeat 仍被周期写入。

    根因(2026-07-15 trip_1784116216):心跳曾用 asyncio.sleep(依赖 event loop 调度),
    run_code_index 内 GitNexus taint/sink/source 分析的同步 CPU 密集段阻塞 worker
    event loop ~161s,期间心跳 task 得不到调度 → heartbeat mtime 超 90s freshness
    阈值 → web 端 reconcile(is_scan_alive=False)误判 interrupted(终态不可逆),
    而 worker 实际从未崩溃。线程化后:daemon 线程 time.sleep,脱离 event loop,
    阻塞期间照写,真正兑现「worker 活着就跳」的进程级独立承诺。

    用 time.sleep(同步阻塞 event loop)复现 code-index 阻塞段,断言 heartbeat
    在阻塞期间仍被更新(旧 asyncio 实现此测试失败)。
    """
    hb = tmp_path / "heartbeat"
    async with HeartbeatManager(tmp_path, interval=0.1):
        await asyncio.sleep(0.05)  # 让初始 heartbeat 落盘
        mtime_before = hb.stat().st_mtime_ns
        # 同步阻塞 event loop 1.0s(>> interval 0.1):event loop 完全停转,
        # asyncio.sleep 驱动的心跳 task 不可能在此期间被调度。
        time.sleep(1.0)
        mtime_after = hb.stat().st_mtime_ns
        # 线程化实现:daemon 线程在阻塞期间多次写 heartbeat → mtime 变新。
        # 旧 asyncio 实现:阻塞期间 task 不调度,heartbeat 不写 → mtime 不变。
        assert mtime_after > mtime_before, (
            "event loop 阻塞 1.0s 期间 heartbeat 未被更新 → 心跳仍依赖 event loop 调度,"
            "会被 code-index 等 CPU 密集 activity 阻塞致误判 interrupted"
        )
        # 且最后一次写距现在不远(线程在阻塞末尾仍活跃,非阻塞前的陈旧值)
        age = time.time() - hb.stat().st_mtime
        assert age < 0.5, f"heartbeat age={age:.2f}s 过大,疑似阻塞期间未持续写"


async def test_heartbeat_self_stops_when_session_reaches_terminal_status(tmp_path):
    """终态自停:session.json 标 cancelled 后心跳线程在 ≤1 周期内自停(不依赖 cancel 传播链)。

    根因(2026-07-23 sentinel_dashboard_1784787580):WEB cancel → temporal workflow cancel
    的取消信号未可靠传到 run_heartbeat activity,HeartbeatManager.__aexit__ 不执行,daemon
    心跳线程因 worker 常驻进程不退出而永久残留(实测 scan 取消 9+ 分钟后仍每 30s 写)。
    终态自停让心跳线程下个周期读 session.json 见终态即自行退出,绕开脆弱的 cancel 传播链。
    """
    async with HeartbeatManager(tmp_path, interval=0.05) as mgr:
        thread = mgr._heartbeat_thread
        assert thread is not None and thread.is_alive()
        # 写终态(web cancel 时 scan_manager 标 status=cancelled,bind mount 共享)
        (tmp_path / "session.json").write_text(json.dumps({"status": "cancelled"}))
        # 等 ≤ 数个周期让心跳线程检测到终态自停
        deadline = time.time() + 2.0
        while time.time() < deadline and thread.is_alive():
            await asyncio.sleep(0.05)
        assert not thread.is_alive(), "终态后心跳线程未自停(cancel 传播链断裂时残留)"


async def test_heartbeat_keeps_writing_when_session_running(tmp_path):
    """非终态(running)不停:自停只在终态触发,正常 scan 期间心跳照跳。"""
    (tmp_path / "session.json").write_text(json.dumps({"status": "running"}))
    async with HeartbeatManager(tmp_path, interval=0.05) as mgr:
        await asyncio.sleep(0.15)  # >2 周期
        assert mgr._heartbeat_thread.is_alive(), "running 态心跳被误停"
        assert (tmp_path / "heartbeat").exists()


async def test_heartbeat_self_stops_for_each_terminal_status(tmp_path):
    """六个终态 status 都触发自停;且兼容 nested session 格式({"session":{"status":...}})。"""
    cases = [
        ("completed", False), ("failed", False), ("interrupted", False),
        ("cancelled", False), ("killed", False), ("crashed", False),
        ("cancelled", True),  # nested 格式(与 session.SessionManager.get_status 同口径)
    ]
    for status, nested in cases:
        ws = tmp_path / f"ws-{status}-{nested}"
        ws.mkdir()
        payload = ({"session": {"status": status}} if nested else {"status": status})
        (ws / "session.json").write_text(json.dumps(payload))
        mgr = HeartbeatManager(ws, interval=0.03)
        async with mgr:
            deadline = time.time() + 1.5
            while time.time() < deadline and mgr._heartbeat_thread.is_alive():
                await asyncio.sleep(0.03)
            assert not mgr._heartbeat_thread.is_alive(), (
                f"status={status} nested={nested} 未触发自停")


async def test_heartbeat_keeps_writing_when_session_missing_or_invalid(tmp_path):
    """session.json 缺失/损坏/非 dict → 不停(best-effort,继续跳,绝不误杀正常 scan)。"""
    async with HeartbeatManager(tmp_path, interval=0.05) as mgr:
        # 1) 无 session.json
        await asyncio.sleep(0.15)
        assert mgr._heartbeat_thread.is_alive(), "无 session.json 时心跳被误停"
        # 2) 损坏 json
        (tmp_path / "session.json").write_text("not-json{")
        await asyncio.sleep(0.15)
        assert mgr._heartbeat_thread.is_alive(), "session.json 损坏时心跳被误停"
        # 3) 非 dict 根
        (tmp_path / "session.json").write_text(json.dumps(["a", "b"]))
        await asyncio.sleep(0.15)
        assert mgr._heartbeat_thread.is_alive(), "session.json 非 dict 时心跳被误停"
