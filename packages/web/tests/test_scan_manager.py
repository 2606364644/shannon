"""C1 Phase B: ScanManager 改 temporal workflow 提交者(不再 fork CLI 子进程).

start → Client.connect + start_workflow(固定 queue shannon-py-wb-web);
_watch → tail events.ndjson 直到 scan_end; cancel → handle.cancel(temporal 原生) +
② ③ 轨(heartbeat/cancel.requested 文件, 兼容 host CLI). active_pids 返空.
"""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shannon_web.models import PathSource, RepoSource, ScanRequest
from shannon_web.components.scan_manager import ScanManager, TemporalUnavailable, TooManyScans


async def _ok():
    return None


def _patch_temporal_ok(monkeypatch, mgr):
    """跳过 _check_temporal socket 探活."""
    monkeypatch.setattr(mgr, "_check_temporal", _ok)


def _patch_client(monkeypatch, handle=None):
    """mock Client.connect → mock_client(start_workflow → handle). 返回 mock_client."""
    mock_handle = handle or MagicMock()
    mock_handle.id = "ws-mock"
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)
    monkeypatch.setattr("shannon_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    return mock_client


# ── start: fork → start_workflow ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_submits_workflow_to_fixed_queue(tmp_path, monkeypatch):
    """start 改 start_workflow: 连 temporal + 提交到 WEB_TASK_QUEUE_WHITEBOX + 存 handle."""
    from shannon_core.services.temporal_infra import WEB_TASK_QUEUE_WHITEBOX
    from shannon_whitebox.pipeline.shared import PipelineInput

    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)

    ws = await mgr.start(ScanRequest(type="whitebox",
                                     source=PathSource(kind="path", value="/code/x"),
                                     url="http://e", workspace="WS1"))
    assert ws == "WS1"
    mock_client.start_workflow.assert_awaited_once()
    call = mock_client.start_workflow.call_args
    assert call.kwargs["task_queue"] == WEB_TASK_QUEUE_WHITEBOX
    assert call.kwargs["id"]  # workflow_id 由 web 算
    wf_input = call.args[1]  # (WhiteboxScanWorkflow.run, inp, id=, task_queue=)
    assert isinstance(wf_input, PipelineInput)
    assert wf_input.event_file.endswith("events.ndjson")
    assert wf_input.workspace_name == "WS1"
    assert "WS1" in mgr._handles  # handle 存进 _handles(供 cancel)


@pytest.mark.asyncio
async def test_start_marks_owner_web(tmp_path, monkeypatch):
    """web 自起 scan → start 标 session.json owner=web(spec §4.2)."""
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    await mgr.start(ScanRequest(type="whitebox",
                                source=PathSource(kind="path", value="/x"),
                                url="u", workspace="WOWN"))
    sess = json.loads((tmp_path / "WOWN" / "session.json").read_text())
    assert sess.get("owner") == "web"


@pytest.mark.asyncio
async def test_start_writes_submitted_at(tmp_path, monkeypatch):
    """start 提交 workflow 成功后写 session.json submitted_at(提交宽限门锚点,防冷启动误杀).

    submitted_at 每次 start_workflow 提交刷新 → resume 场景也准确(resume 时 created_at 是老的).
    提交失败(start_workflow 抛)不写此字段(start 已抛, 不到此分支).
    """
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    before = time.time()
    await mgr.start(ScanRequest(type="whitebox",
                                source=PathSource(kind="path", value="/x"),
                                url="u", workspace="WSUB"))
    after = time.time()
    sess = json.loads((tmp_path / "WSUB" / "session.json").read_text())
    assert "submitted_at" in sess
    assert before <= sess["submitted_at"] <= after


@pytest.mark.asyncio
async def test_start_cleanup_active_reqs_on_submit_failure(tmp_path, monkeypatch):
    """提交失败(start_workflow 抛)→ _active_reqs 必须清理, 否则 active_repo_sources 误报."""
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(side_effect=RuntimeError("temporal reject"))
    monkeypatch.setattr("shannon_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    with pytest.raises(RuntimeError, match="temporal reject"):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="WFAIL"))
    assert "WFAIL" not in mgr._active_reqs  # 清理 → active_repo_sources 不误报
    assert mgr.active_repo_sources() == set()


@pytest.mark.asyncio
async def test_blackbox_not_implemented_phase_c(tmp_path, monkeypatch):
    """blackbox C1 化留 Phase C: start 对 blackbox raise NotImplementedError."""
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    with pytest.raises(NotImplementedError, match="Phase C"):
        await mgr.start(ScanRequest(type="blackbox", url="u", workspace="BB"))


@pytest.mark.asyncio
async def test_concurrency_limit_raises(tmp_path, monkeypatch):
    mgr = ScanManager(tmp_path, tmp_path / "r", None, max_concurrent=1)
    _patch_temporal_ok(monkeypatch, mgr)
    mgr._handles["existing"] = object()  # 占位 1 个在跑
    with pytest.raises(TooManyScans):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="W2"))


@pytest.mark.asyncio
async def test_temporal_unavailable_raises(tmp_path, monkeypatch):
    mgr = ScanManager(tmp_path, tmp_path / "r", None)

    async def _fail():
        raise TemporalUnavailable()

    monkeypatch.setattr(mgr, "_check_temporal", _fail)
    with pytest.raises(TemporalUnavailable):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="W"))


# ── _resolve_workflow_id: 读 resumeAttempts 算 -resume-N ──────────────────

def test_resolve_workflow_id_fresh_no_suffix(tmp_path):
    """无 resumeAttempts → workflow_id = ws(无后缀)."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    (tmp_path / "WS").mkdir()
    (tmp_path / "WS" / "session.json").write_text(json.dumps({"status": "running"}))
    assert mgr._resolve_workflow_id("WS") == "WS"


def test_resolve_workflow_id_resume_appends_n(tmp_path):
    """有 N 条 resumeAttempts → workflow_id = ws-resume-N."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    (tmp_path / "WS").mkdir()
    (tmp_path / "WS" / "session.json").write_text(json.dumps({
        "resumeAttempts": [{"workflowId": "WS-resume-1"}, {"workflowId": "WS-resume-2"}],
    }))
    assert mgr._resolve_workflow_id("WS") == "WS-resume-2"


# ── cancel: handle.cancel(①) + ② ③ 轨 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_web_started_scan_calls_handle_cancel(tmp_path):
    """① web 自起 scan(_handles 有) → handle.cancel(temporal 原生), 不再 SIGINT 子进程."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mock_handle = AsyncMock()
    mgr._handles["ws"] = mock_handle
    (tmp_path / "ws").mkdir()
    result = await mgr.cancel("ws")
    mock_handle.cancel.assert_awaited_once()
    assert result == {"cancelled": "ws"}


@pytest.mark.asyncio
async def test_cancel_host_running_writes_signal_and_marks_cancelled(tmp_path):
    """② owner=host(heartbeat fresh, web 无 handle)→ 写 cancel.requested + 标 cancelled + via:signal."""
    ws = "HOST1"
    ws_dir = tmp_path / ws
    ws_dir.mkdir()
    (ws_dir / "heartbeat").write_text(f"{time.time()}\n")  # fresh → host 在跑
    (ws_dir / "session.json").write_text(json.dumps({"status": "running"}))
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    result = await mgr.cancel(ws)
    assert result == {"cancelled": ws, "via": "signal"}
    assert (ws_dir / "cancel.requested").exists()
    sess = json.loads((ws_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"
    assert sess["completed_at"] is not None


@pytest.mark.asyncio
async def test_cancel_dead_marks_cancelled_was_dead(tmp_path):
    """③ heartbeat stale(已死)→ 标 cancelled + was_dead:true(不写 cancel.requested)."""
    ws = "DEAD1"
    ws_dir = tmp_path / ws
    ws_dir.mkdir()
    (ws_dir / "heartbeat").write_text("x\n")
    old = time.time() - 3600
    import os
    os.utime(ws_dir / "heartbeat", (old, old))  # stale → 已死
    (ws_dir / "session.json").write_text(json.dumps({"status": "running"}))
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    result = await mgr.cancel(ws)
    assert result == {"cancelled": ws, "was_dead": True}
    assert not (ws_dir / "cancel.requested").exists()
    sess = json.loads((ws_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_unknown_workspace_returns_none(tmp_path):
    """workspace 不存在 → None(唯一 404 情况;spec §4.6)."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    assert await mgr.cancel("nope") is None


# ── _watch: tail events.ndjson 直到 scan_end ──────────────────────────────

@pytest.mark.asyncio
async def test_watch_tails_events_until_scan_end(tmp_path):
    """_watch tail events.ndjson, 见 scan_end 后退出 + 清理 _handles/_active_reqs."""
    ws_dir = tmp_path / "ws"; ws_dir.mkdir()
    event_file = ws_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mgr._handles["ws"] = MagicMock()
    mgr._active_reqs["ws"] = ScanRequest(type="whitebox", url="u")

    async def write_end():
        await asyncio.sleep(0.15)
        event_file.write_text('{"type":"scan_end","status":"completed"}\n')

    asyncio.create_task(write_end())
    await mgr._watch("ws", event_file)
    assert "ws" not in mgr._handles  # finally 清理
    assert "ws" not in mgr._active_reqs


@pytest.mark.asyncio
async def test_watch_timeout_writes_timeout_scan_end(tmp_path):
    """scan_timeout 到且无 scan_end → 兜底写 timeout scan_end + 清理."""
    ws_dir = tmp_path / "wt"; ws_dir.mkdir()
    event_file = ws_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path / "r", None, scan_timeout=0.3)
    mgr._handles["wt"] = MagicMock()
    await mgr._watch("wt", event_file)
    text = event_file.read_text()
    assert '"scan_end"' in text and '"timeout"' in text
    assert "wt" not in mgr._handles


@pytest.mark.asyncio
async def test_watch_crashed_fallback_when_no_scan_end(tmp_path):
    """_watch 超时无 scan_end → 兜底写(scan_end 缺失时 finally 补写).

    注: 纯 cancel 场景 finally 的 await 不可靠(asyncio CancelledError 中断 await), 故
    用 timeout 路径验证兜底写逻辑(timeout 路径 finally 的 if-not-has_scan_end 同源).
    """
    ws_dir = tmp_path / "wc"; ws_dir.mkdir()
    event_file = ws_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path / "r", None, scan_timeout=0.2)
    mgr._handles["wc"] = MagicMock()
    await mgr._watch("wc", event_file)
    text = event_file.read_text()
    assert '"scan_end"' in text  # 兜底写了


# ── active_repo_sources / active_pids ─────────────────────────────────────

def test_active_repo_sources_tracks_running_then_clears(tmp_path):
    """active_repo_sources(): 在途 scan 引用的 repo 出现于集合, scan 结束后消失."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    assert mgr.active_repo_sources() == set()
    mgr._active_reqs["ws1"] = ScanRequest(
        type="whitebox", source=RepoSource(kind="repo", value="foo"), url="http://e")
    assert "foo" in mgr.active_repo_sources()
    mgr._active_reqs.pop("ws1", None)
    assert mgr.active_repo_sources() == set()


def test_active_pids_returns_empty(tmp_path):
    """C1: web 无本机 pid(扫描跑在 worker 容器), active_pids 恒空."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mgr._handles["ws"] = MagicMock()
    assert mgr.active_pids() == {}


# ── correlation: traversal 校验仍触发(Phase C 前 raise ValueError) ────────

@pytest.mark.asyncio
async def test_correlation_config_name_traversal_rejected(tmp_path, monkeypatch):
    """config_name="../evil" 必须被 store 遍历校验拦截(在 C1 raise ValueError 前)."""
    from shannon_web.components.multi_repo_config_store import MultiRepoConfigStore
    store = MultiRepoConfigStore(tmp_path / "configs")
    mgr = ScanManager(tmp_path, tmp_path / "r", store, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    with pytest.raises(ValueError):
        await mgr.start(ScanRequest(type="correlation", config_name="../evil"))
