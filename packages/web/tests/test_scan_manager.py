import asyncio
import sys
import textwrap

import pytest

from shannon_web.models import PathSource, ScanRequest
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
    ok = await mgr.cancel(ws)
    assert ok is True
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
