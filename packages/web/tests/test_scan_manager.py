import asyncio
import json
import os
import sys
import textwrap
import time

import pytest

from shannon_web.models import PathSource, RepoSource, ScanRequest
from shannon_web.components.scan_manager import ScanManager, TemporalUnavailable, TooManyScans


@pytest.fixture
def fake_ok(tmp_path):
    s = tmp_path / "ok.py"
    s.write_text(textwrap.dedent('''
        import os, sys
        ef = os.environ.get("SHANNON_WEB_EVENT_FILE")
        if ef:
            with open(ef, "a") as f:
                f.write(\'{"type":"InfoEvent","ts":"t","category":"INFO","message":"x"}\\n\')
                f.write(\'{"type":"scan_end","ts":"t","category":"CONTROL","status":"completed"}\\n\')
    '''))
    return s


@pytest.fixture
def fake_crash(tmp_path):
    s = tmp_path / "crash.py"
    s.write_text('import sys; sys.stderr.write("boom\\n"); sys.exit(1)\n')
    return s


@pytest.fixture
def fake_long(tmp_path):
    s = tmp_path / "long.py"
    s.write_text('import time; time.sleep(30)\n')
    return s


async def _ok():
    return None


def _patch_ok(monkeypatch, mgr):
    monkeypatch.setattr(mgr, "_check_temporal", _ok)


def test_build_argv_passes_temporal_address_from_env(tmp_path, monkeypatch):
    """容器内 temporal 在 compose 服务名上（非 localhost），必须显式传 --temporal-address，
    否则 CLI 默认 localhost:7233 探活失败 -> ensure_infra 退化到 docker 自建 -> 容器内无 docker -> 崩。"""
    monkeypatch.setenv("SHANNON_TEMPORAL_HOST", "temporal")
    monkeypatch.setenv("SHANNON_TEMPORAL_PORT", "7233")
    mgr = ScanManager(tmp_path, tmp_path / "repos", None)
    expected = ["--temporal-address", "temporal:7233"]

    wb = mgr._build_argv(ScanRequest(type="whitebox"), "/r", "ws")
    assert expected == wb[-2:]

    bb = mgr._build_argv(ScanRequest(type="blackbox", url="u"), "/r", "ws")
    assert expected == bb[-2:]

    from pathlib import Path
    cb = mgr._build_argv(ScanRequest(type="correlation", config_name="c"), None, "ws", Path("c.yaml"))
    assert expected == cb[-2:]


def test_build_argv_temporal_address_defaults_localhost(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANNON_TEMPORAL_HOST", raising=False)
    monkeypatch.delenv("SHANNON_TEMPORAL_PORT", raising=False)
    mgr = ScanManager(tmp_path, tmp_path / "repos", None)
    wb = mgr._build_argv(ScanRequest(type="whitebox"), "/r", "ws")
    assert ["--temporal-address", "localhost:7233"] == wb[-2:]


@pytest.mark.asyncio
async def test_start_writes_event_file_and_scan_end(tmp_workspaces, fake_ok, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "repos", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_ok)])
    ws = await mgr.start(ScanRequest(type="whitebox",
                                     source=PathSource(kind="path", value="/code/x"),
                                     url="http://e", workspace="WS1"))
    assert ws == "WS1"
    await asyncio.sleep(0.6)
    ef = tmp_workspaces / "WS1" / "events.ndjson"
    lines = [l for l in ef.read_text().splitlines() if l.strip()]
    assert any('"scan_end"' in l and '"completed"' in l for l in lines)
    assert "WS1" not in mgr.active_pids()  # 退出后清出


@pytest.mark.asyncio
async def test_crash_writes_scan_end_crashed_with_stderr(tmp_workspaces, fake_crash, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "repos", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_crash)])
    await mgr.start(ScanRequest(type="whitebox",
                                source=PathSource(kind="path", value="/x"),
                                url="u", workspace="WC"))
    await asyncio.sleep(0.6)
    text = (tmp_workspaces / "WC" / "events.ndjson").read_text()
    assert '"scan_end"' in text and '"crashed"' in text
    assert "boom" in text  # stderr_tail 透传


@pytest.mark.asyncio
async def test_concurrency_limit_raises(tmp_workspaces, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None, max_concurrent=1)
    _patch_ok(monkeypatch, mgr)
    mgr._procs["existing"] = object()  # 占位 1 个在跑
    with pytest.raises(TooManyScans):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="W2"))


@pytest.mark.asyncio
async def test_temporal_unavailable_raises(tmp_workspaces, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None)

    async def _fail():
        raise TemporalUnavailable()

    monkeypatch.setattr(mgr, "_check_temporal", _fail)
    with pytest.raises(TemporalUnavailable):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="W"))


@pytest.mark.asyncio
async def test_cancel_sends_sigint_then_killed_scan_end(tmp_workspaces, fake_long, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_long)])
    ws = await mgr.start(ScanRequest(type="whitebox",
                                     source=PathSource(kind="path", value="/x"),
                                     url="u", workspace="WL"))
    result = await mgr.cancel(ws)
    assert result == {"cancelled": "WL"}
    await asyncio.sleep(0.6)
    text = (tmp_workspaces / "WL" / "events.ndjson").read_text()
    assert '"killed"' in text


@pytest.mark.asyncio
async def test_correlation_resolves_yaml_and_runs(tmp_path, tmp_workspaces, fake_ok, monkeypatch):
    # 用真实 MultiRepoConfigStore 落盘一份合法 yaml，让 start() 能 parse 出 out_workspace。
    from shannon_web.components.multi_repo_config_store import MultiRepoConfigStore
    store = MultiRepoConfigStore(tmp_path / "configs")
    yaml_text = (
        "description: demo\n"
        "repos:\n"
        "  svc-a:\n"
        "    path: /code/a\n"
        "    role: entrypoint\n"
        "relations: []\n"
        "correlation:\n"
        "  out_workspace: my-cor-ws\n"
    )
    store.write("demo", yaml_text)
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", store, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    captured = {}
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: (captured.__setitem__("yaml", yaml),
                                                       captured.__setitem__("ws", ws),
                                                       [sys.executable, str(fake_ok)])[2])
    ws = await mgr.start(ScanRequest(type="correlation", config_name="demo"))
    await asyncio.sleep(0.4)
    assert str(captured["yaml"]).endswith("web-multi-demo.yaml")
    # out_workspace 来自 yaml，而非传入的 ws
    assert ws == "my-cor-ws"
    assert captured["ws"] == "my-cor-ws"


@pytest.mark.asyncio
async def test_scan_timeout_sends_sigint_and_writes_killed(tmp_workspaces, fake_long, monkeypatch):
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None, max_concurrent=2,
                      scan_timeout=0.5)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_long)])
    await mgr.start(ScanRequest(type="whitebox",
                                source=PathSource(kind="path", value="/x"),
                                url="u", workspace="WT"))
    # 超时 0.5s + 余量，等 _watch 收 SIGINT 写 scan_end
    await asyncio.sleep(1.5)
    text = (tmp_workspaces / "WT" / "events.ndjson").read_text()
    assert '"scan_end"' in text and '"killed"' in text  # SIGINT → rc<0 → killed


@pytest.mark.asyncio
async def test_correlation_uses_out_workspace_as_event_file(tmp_path, tmp_workspaces, fake_ok, monkeypatch):
    """correlation 扫描的 event_file 必须落在 out_workspace 目录下（与 orchestrator
    写 correlation_progress 的路径一致），否则 SSE 收不到联动进度（final-review Finding 1）。
    """
    from shannon_web.components.multi_repo_config_store import MultiRepoConfigStore
    store = MultiRepoConfigStore(tmp_path / "configs")
    yaml_text = (
        "repos:\n"
        "  svc-a:\n"
        "    path: /code/a\n"
        "    role: entrypoint\n"
        "relations: []\n"
        "correlation:\n"
        "  out_workspace: my-cor-ws\n"
    )
    store.write("demo", yaml_text)
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", store, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    captured = {}

    def _argv(req, t, ws, yaml=None):
        captured["ws"] = ws
        captured["yaml"] = yaml
        return [sys.executable, str(fake_ok)]

    monkeypatch.setattr(mgr, "_build_argv", _argv)
    ws = await mgr.start(ScanRequest(type="correlation", config_name="demo"))
    assert ws == "my-cor-ws"
    # event_file 路径与 orchestrator 写的 ndjson 一致（同 ws 目录下 events.ndjson）
    event_file = tmp_workspaces / "my-cor-ws" / "events.ndjson"
    # env 里 SHANNON_WEB_EVENT_FILE=str(event_file)，子进程（fake_ok）会往它写 scan_end。
    # 等子进程退出 + _watch flush。
    await asyncio.sleep(0.5)
    assert event_file.exists()
    assert "my-cor-ws" in str(event_file)
    assert str(event_file).endswith("my-cor-ws/events.ndjson")


@pytest.mark.asyncio
async def test_correlation_config_name_traversal_rejected(tmp_path, tmp_workspaces, monkeypatch):
    """config_name="../evil" 必须被 store 的遍历校验拦截，不产生越界路径
    （final-review Finding 3）。
    """
    from shannon_web.components.multi_repo_config_store import MultiRepoConfigStore
    store = MultiRepoConfigStore(tmp_path / "configs")
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", store, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)

    with pytest.raises(ValueError):
        await mgr.start(ScanRequest(type="correlation", config_name="../evil"))


def test_active_repo_sources_tracks_running_then_clears(tmp_workspaces):
    """active_repo_sources() 直接单测：在途 scan 引用的 repo 出现于集合，
    scan 结束（_active_reqs 清出）后消失。无需真实子进程——直接操纵 _active_reqs。
    """
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None)
    assert mgr.active_repo_sources() == set()  # 无在途
    mgr._active_reqs["ws1"] = ScanRequest(
        type="whitebox",
        source=RepoSource(kind="repo", value="foo"),
        url="http://e",
    )
    assert "foo" in mgr.active_repo_sources()  # 在途引用可见
    mgr._active_reqs.pop("ws1", None)  # 模拟 _watch finally 清理
    assert mgr.active_repo_sources() == set()  # scan 结束后消失


@pytest.mark.asyncio
async def test_cancel_host_running_writes_signal_and_marks_cancelled(tmp_workspaces):
    """② owner=host(heartbeat fresh,web 看不到 pid)→ 写 cancel.requested + 标 cancelled + via:signal。"""
    ws = "HOST1"
    ws_dir = tmp_workspaces / ws
    ws_dir.mkdir()
    (ws_dir / "heartbeat").write_text(f"{time.time()}\n")  # fresh → host 在跑
    (ws_dir / "session.json").write_text(json.dumps({"status": "running"}))
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None)
    result = await mgr.cancel(ws)
    assert result == {"cancelled": ws, "via": "signal"}
    assert (ws_dir / "cancel.requested").exists()  # 宿主 HeartbeatManager 据此自退
    sess = json.loads((ws_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"
    assert sess["completed_at"] is not None


@pytest.mark.asyncio
async def test_cancel_dead_marks_cancelled_was_dead(tmp_workspaces):
    """③ heartbeat stale(已死)→ 标 cancelled + was_dead:true(不写 cancel.requested)。"""
    ws = "DEAD1"
    ws_dir = tmp_workspaces / ws
    ws_dir.mkdir()
    (ws_dir / "heartbeat").write_text("x\n")
    old = time.time() - 3600
    os.utime(ws_dir / "heartbeat", (old, old))  # stale → 已死
    (ws_dir / "session.json").write_text(json.dumps({"status": "running"}))
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None)
    result = await mgr.cancel(ws)
    assert result == {"cancelled": ws, "was_dead": True}
    assert not (ws_dir / "cancel.requested").exists()  # 已死无需协作式信号
    sess = json.loads((ws_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_unknown_workspace_returns_none(tmp_workspaces):
    """workspace 不存在 → None(唯一 404 情况;spec §4.6)。"""
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None)
    assert await mgr.cancel("nope") is None


@pytest.mark.asyncio
async def test_start_marks_owner_web(tmp_workspaces, fake_ok, monkeypatch):
    """web 自起 scan → scan_manager.start 标 session.json owner=web(spec §4.2)。"""
    mgr = ScanManager(tmp_workspaces, tmp_workspaces / "r", None, max_concurrent=2)
    _patch_ok(monkeypatch, mgr)
    monkeypatch.setattr(mgr, "_build_argv",
                        lambda req, t, ws, yaml=None: [sys.executable, str(fake_ok)])
    ws = await mgr.start(ScanRequest(type="whitebox",
                                     source=PathSource(kind="path", value="/x"),
                                     url="u", workspace="WOWN"))
    await asyncio.sleep(0.3)
    sess = json.loads((tmp_workspaces / "WOWN" / "session.json").read_text())
    assert sess.get("owner") == "web"
