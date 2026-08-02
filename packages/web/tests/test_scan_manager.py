"""C1 Phase B + T3: ScanManager 改 temporal workflow 提交者 + 1 ws : N scans。

start -> Client.connect + start_workflow(固定 queue supernova-wb-web)；返回 (ws, scan_id)。
T3: ScanStore.create_scan 建 scans/<scan_id>/session.json；_handles/_tasks/_active_reqs key
= (ws, scan_id)；同 ws 多 scan 不互斥。cancel(ws, scan_id) 精确 / cancel(ws) shim latest。
_watch -> tail events.ndjson 直到 scan_end; cancel -> handle.cancel(temporal 原生) +
② ③ 轨(heartbeat/cancel.requested 文件, 兼容 host CLI). active_pids 返空.
"""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from supernova_web.models import PathSource, RepoSource, ScanRequest
from supernova_web.components.scan_manager import ScanManager, ScanRunning, TemporalUnavailable, TooManyScans


async def _ok():
    return None


def _patch_temporal_ok(monkeypatch, mgr):
    """跳过 _check_temporal socket 探活."""
    monkeypatch.setattr(mgr, "_check_temporal", _ok)


def _patch_client(monkeypatch, handle=None):
    """mock Client.connect -> mock_client(start_workflow -> handle). 返回 mock_client."""
    mock_handle = handle or MagicMock()
    mock_handle.id = "ws-mock"
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    return mock_client


def _make_scan_dir(workspaces_dir, ws, scan_id="20260727-120000", status="running"):
    """在 workspaces/<ws>/scans/<scan_id>/ 写 session.json（新模型 scan 目录）。"""
    scan_dir = Path(workspaces_dir) / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "status": status, "scan_type": "whitebox", "created_at": time.time(),
        "web_url": "", "repo_path": "",
    }))
    return scan_dir


# ── start: fork -> start_workflow ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_submits_workflow_to_fixed_queue(tmp_path, monkeypatch):
    """start 改 start_workflow: 连 temporal + 提交到 WEB_TASK_QUEUE_WHITEBOX + 存 handle."""
    from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_WHITEBOX
    from supernova_whitebox.pipeline.shared import PipelineInput

    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)

    ws, scan_id = await mgr.start(ScanRequest(type="whitebox",
                                              source=PathSource(kind="path", value="/code/x"),
                                              url="http://e", workspace="WS1"))
    assert ws == "WS1"
    assert scan_id  # T3: 返回 scan_id
    mock_client.start_workflow.assert_awaited_once()
    call = mock_client.start_workflow.call_args
    assert call.kwargs["task_queue"] == WEB_TASK_QUEUE_WHITEBOX
    assert call.kwargs["id"]  # workflow_id 由 web 算
    wf_input = call.args[1]  # (WhiteboxScanWorkflow.run, inp, id=, task_queue=)
    assert isinstance(wf_input, PipelineInput)
    assert wf_input.event_file.endswith("events.ndjson")
    # T3: workspace_name = scan_id（worker 据 event_file.parent 推导 scan_dir）
    assert wf_input.workspace_name == scan_id
    assert ("WS1", scan_id) in mgr._handles  # handle 存进 _handles(供 cancel)


@pytest.mark.asyncio
async def test_start_lands_scan_in_scans_subdir(tmp_path, monkeypatch):
    """T3: start 建 scans/<scan_id>/session.json（ws 根不再写 session.json）。"""
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    ws, scan_id = await mgr.start(ScanRequest(type="whitebox",
                                              source=PathSource(kind="path", value="/x"),
                                              url="u", workspace="WOWN"))
    scan_dir = tmp_path / "WOWN" / "scans" / scan_id
    assert (scan_dir / "session.json").exists()
    assert not (tmp_path / "WOWN" / "session.json").exists()  # ws 根无 session.json
    sess = json.loads((scan_dir / "session.json").read_text())
    assert sess.get("owner") == "web"  # _mark_owner 标 scan session


@pytest.mark.asyncio
async def test_start_writes_submitted_at(tmp_path, monkeypatch):
    """start 提交 workflow 成功后写 scan session.json submitted_at(提交宽限门锚点,防冷启动误杀).

    submitted_at 每次 start_workflow 提交刷新 -> resume 场景也准确(resume 时 created_at 是老的).
    提交失败(start_workflow 抛)不写此字段(start 已抛, 不到此分支).
    """
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    before = time.time()
    ws, scan_id = await mgr.start(ScanRequest(type="whitebox",
                                              source=PathSource(kind="path", value="/x"),
                                              url="u", workspace="WSUB"))
    after = time.time()
    sess = json.loads((tmp_path / "WSUB" / "scans" / scan_id / "session.json").read_text())
    assert "submitted_at" in sess
    assert before <= sess["submitted_at"] <= after


@pytest.mark.asyncio
async def test_start_cleanup_active_reqs_on_submit_failure(tmp_path, monkeypatch):
    """提交失败(start_workflow 抛)-> _active_reqs 必须清理, 否则 active_repo_sources 误报."""
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(side_effect=RuntimeError("temporal reject"))
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    with pytest.raises(RuntimeError, match="temporal reject"):
        await mgr.start(ScanRequest(type="whitebox",
                                    source=PathSource(kind="path", value="/x"),
                                    url="u", workspace="WFAIL"))
    # T3: key=(ws, scan_id)，提交失败已清理 -> 无 WFAIL 开头的 key
    assert not any(k[0] == "WFAIL" for k in mgr._active_reqs)
    assert mgr.active_repo_sources() == set()


@pytest.mark.asyncio
async def test_start_same_ws_two_scans_not_mutually_exclusive(tmp_path, monkeypatch):
    """T3: 同 ws 起两个 scan 不互斥（_handles 两键，不触发 TooManyScans）。"""
    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    ws1, id1 = await mgr.start(ScanRequest(type="whitebox",
                                           source=PathSource(kind="path", value="/x"),
                                           url="u", workspace="WS"))
    ws2, id2 = await mgr.start(ScanRequest(type="whitebox",
                                           source=PathSource(kind="path", value="/x"),
                                           url="u", workspace="WS"))
    assert ws1 == ws2 == "WS"
    assert id1 != id2  # 两个不同 scan_id
    assert len(mgr._handles) == 2
    assert ("WS", id1) in mgr._handles and ("WS", id2) in mgr._handles


@pytest.mark.asyncio
async def test_concurrency_limit_raises(tmp_path, monkeypatch):
    mgr = ScanManager(tmp_path, tmp_path / "r", None, max_concurrent=1)
    _patch_temporal_ok(monkeypatch, mgr)
    mgr._handles[("existing", "s1")] = object()  # 占位 1 个在跑
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
    """T3: 无 resumeAttempts -> workflow_id = {ws}-{scan_id}（无后缀）."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _make_scan_dir(tmp_path, "WS", scan_id="20260727-120000", status="running")
    assert mgr._resolve_workflow_id("WS", "20260727-120000") == "WS-20260727-120000"


def test_resolve_workflow_id_resume_appends_n(tmp_path):
    """T3: 有 N 条 resumeAttempts -> workflow_id = {ws}-{scan_id}-resume-N."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = _make_scan_dir(tmp_path, "WS", scan_id="20260727-120000")
    sess = json.loads((scan_dir / "session.json").read_text())
    sess["resumeAttempts"] = [
        {"workflowId": "WS-20260727-120000-resume-1"},
        {"workflowId": "WS-20260727-120000-resume-2"},
    ]
    (scan_dir / "session.json").write_text(json.dumps(sess))
    assert mgr._resolve_workflow_id("WS", "20260727-120000") == "WS-20260727-120000-resume-2"


# ── cancel: handle.cancel(①) + ② ③ 轨 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_web_started_scan_calls_handle_cancel(tmp_path):
    """① web 自起 scan(_handles 有) -> handle.cancel(temporal 原生), 不再 SIGINT 子进程."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mock_handle = AsyncMock()
    scan_dir = _make_scan_dir(tmp_path, "ws", scan_id="s1")
    mgr._handles[("ws", "s1")] = mock_handle
    result = await mgr.cancel("ws", "s1")
    mock_handle.cancel.assert_awaited_once()
    assert result == {"cancelled": "s1"}


@pytest.mark.asyncio
async def test_cancel_web_started_scan_marks_cancelled_and_writes_scan_end(tmp_path):
    """① web 自起 scan 取消后必须标终态 + 写 scan_end(根因修复):

    轨 ① 只调 handle.cancel(temporal 原生) 不够--worker 的 workflow except CancelledError
    分支不写 scan_end / 不更新 session(只有 try 正常完成分支调 finalize_summary)。若 web 端
    不兜底标记: ① session 卡 running -> heartbeat stale 后误显 interrupted(非 cancelled);
    ② _watch 等不到 scan_end 永不退出 -> _handles 占死 -> max_concurrent 槽位泄漏,
    新扫描再也起不来。故轨 ① 须像 ②/③ 一样调 _mark_cancelled(写 session.status=cancelled
    + scan_end) -> _status_of 终态优先显 cancelled + _watch 见 scan_end 退出释放槽位。
    """
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = _make_scan_dir(tmp_path, "WEB1", scan_id="s1", status="running")
    sess = json.loads((scan_dir / "session.json").read_text())
    sess["owner"] = "web"
    (scan_dir / "session.json").write_text(json.dumps(sess))
    mock_handle = AsyncMock()
    mgr._handles[("WEB1", "s1")] = mock_handle

    result = await mgr.cancel("WEB1", "s1")

    mock_handle.cancel.assert_awaited_once()
    assert result == {"cancelled": "s1"}
    sess = json.loads((scan_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"
    assert sess.get("completed_at") is not None
    event_text = (scan_dir / "events.ndjson").read_text()
    assert '"scan_end"' in event_text and '"cancelled"' in event_text


@pytest.mark.asyncio
async def test_cancel_host_running_writes_signal_and_marks_cancelled(tmp_path):
    """② owner=host(heartbeat fresh, web 无 handle)-> 写 cancel.requested + 标 cancelled + via:signal."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = _make_scan_dir(tmp_path, "HOST1", scan_id="s1", status="running")
    (scan_dir / "heartbeat").write_text(f"{time.time()}\n")  # fresh -> host 在跑
    result = await mgr.cancel("HOST1", "s1")
    assert result == {"cancelled": "s1", "via": "signal"}
    assert (scan_dir / "cancel.requested").exists()
    sess = json.loads((scan_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"
    assert sess["completed_at"] is not None


@pytest.mark.asyncio
async def test_cancel_dead_marks_cancelled_was_dead(tmp_path):
    """③ heartbeat stale(已死)-> 标 cancelled + was_dead:true(不写 cancel.requested)."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = _make_scan_dir(tmp_path, "DEAD1", scan_id="s1", status="running")
    (scan_dir / "heartbeat").write_text("x\n")
    old = time.time() - 3600
    import os
    os.utime(scan_dir / "heartbeat", (old, old))  # stale -> 已死
    result = await mgr.cancel("DEAD1", "s1")
    assert result == {"cancelled": "s1", "was_dead": True}
    assert not (scan_dir / "cancel.requested").exists()
    sess = json.loads((scan_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_unknown_scan_returns_none(tmp_path):
    """scan 不存在 -> None(唯一 404 情况)."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _make_scan_dir(tmp_path, "ws", scan_id="s1")
    assert await mgr.cancel("ws", "nope") is None  # scan_id 不存在


# ── _watch: tail events.ndjson 直到 scan_end ──────────────────────────────

@pytest.mark.asyncio
async def test_watch_tails_events_until_scan_end(tmp_path):
    """_watch tail events.ndjson, 见 scan_end 后退出 + 清理 _handles/_active_reqs."""
    scan_dir = _make_scan_dir(tmp_path, "ws", scan_id="s1")
    event_file = scan_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_key = ("ws", "s1")
    mgr._handles[scan_key] = MagicMock()
    mgr._active_reqs[scan_key] = ScanRequest(type="whitebox", url="u")

    async def write_end():
        await asyncio.sleep(0.15)
        event_file.write_text('{"type":"scan_end","status":"completed"}\n')

    asyncio.create_task(write_end())
    await mgr._watch(scan_key, event_file, scan_dir)
    assert scan_key not in mgr._handles  # finally 清理
    assert scan_key not in mgr._active_reqs


@pytest.mark.asyncio
async def test_watch_timeout_writes_timeout_scan_end(tmp_path):
    """scan_timeout 到且无 scan_end -> 兜底写 timeout scan_end + 清理."""
    scan_dir = _make_scan_dir(tmp_path, "wt", scan_id="s1")
    event_file = scan_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path / "r", None, scan_timeout=0.3)
    scan_key = ("wt", "s1")
    mgr._handles[scan_key] = MagicMock()
    await mgr._watch(scan_key, event_file, scan_dir)
    text = event_file.read_text()
    assert '"scan_end"' in text and '"timeout"' in text
    assert scan_key not in mgr._handles


@pytest.mark.asyncio
async def test_watch_crashed_fallback_when_no_scan_end(tmp_path):
    """_watch 超时无 scan_end -> 兜底写(scan_end 缺失时 finally 补写).

    注: 纯 cancel 场景 finally 的 await 不可靠(asyncio CancelledError 中断 await), 故
    用 timeout 路径验证兜底写逻辑(timeout 路径 finally 的 if-not-has_scan_end 同源).
    """
    scan_dir = _make_scan_dir(tmp_path, "wc", scan_id="s1")
    event_file = scan_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path / "r", None, scan_timeout=0.2)
    scan_key = ("wc", "s1")
    mgr._handles[scan_key] = MagicMock()
    await mgr._watch(scan_key, event_file, scan_dir)
    text = event_file.read_text()
    assert '"scan_end"' in text  # 兜底写了


# ── active_repo_sources / active_pids ─────────────────────────────────────

def test_active_repo_sources_tracks_running_then_clears(tmp_path):
    """active_repo_sources(): 在途 scan 引用的 (ws, repo) 出现于集合, scan 结束后消失.

    T3: _active_reqs key=(ws, scan_id)，返回 set[tuple[str,str]]--delete_repo 用
    (ws, name) in ... 判引用, ws 维度必须参与（防 ws-A 的 scan 误锁 ws-B 的同名 repo）。
    """
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    assert mgr.active_repo_sources() == set()
    mgr._active_reqs[("ws1", "s1")] = ScanRequest(
        type="whitebox", source=RepoSource(kind="repo", value="foo"), url="http://e")
    assert ("ws1", "foo") in mgr.active_repo_sources()
    mgr._active_reqs.pop(("ws1", "s1"), None)
    assert mgr.active_repo_sources() == set()


def test_active_pids_returns_empty(tmp_path):
    """C1: web 无本机 pid(扫描跑在 worker 容器), active_pids 恒空."""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mgr._handles[("ws", "s1")] = MagicMock()
    assert mgr.active_pids() == {}


# ── correlation: traversal 校验仍触发(Phase C 前 raise ValueError) ────────

@pytest.mark.asyncio
async def test_correlation_config_name_traversal_rejected(tmp_path, monkeypatch):
    """config_name="../evil" 必须被 store 遍历校验拦截(在 C1 raise ValueError 前)."""
    from supernova_web.components.multi_repo_config_store import MultiRepoConfigStore
    store = MultiRepoConfigStore(tmp_path / "configs")
    mgr = ScanManager(tmp_path, tmp_path / "r", store, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    with pytest.raises(ValueError):
        await mgr.start(ScanRequest(type="correlation", config_name="../evil"))


# ── delete: 真删目录（spec §5.1 DELETE）──────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_completed_scan_removes_dir(tmp_path):
    """非 running scan 删除：调 ScanStore.delete_scan 真删目录，返 {deleted:id}。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = _make_scan_dir(tmp_path, "ws", scan_id="s1", status="completed")
    result = await mgr.delete("ws", "s1")
    assert result == {"deleted": "s1"}
    assert not scan_dir.exists()


@pytest.mark.asyncio
async def test_delete_running_scan_raises_scan_running(tmp_path):
    """running scan 删除被拒 -> ScanRunning（端点转 409，先 cancel 再删）。

    对齐 delete_workspace：删在跑 workflow 的目录会致 _watch 描述不存在的 workflow / 槽位泄漏。
    """
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = _make_scan_dir(tmp_path, "ws", scan_id="s1", status="running")
    (scan_dir / "heartbeat").write_text(f"{time.time()}\n")  # fresh -> running
    with pytest.raises(ScanRunning):
        await mgr.delete("ws", "s1")
    assert scan_dir.exists()  # 未删


@pytest.mark.asyncio
async def test_delete_unknown_returns_none(tmp_path):
    """scan 不存在 -> None（端点据此 404）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _make_scan_dir(tmp_path, "ws", scan_id="s1")
    assert await mgr.delete("ws", "nope") is None


@pytest.mark.asyncio
async def test_delete_clears_stale_registrations(tmp_path):
    """删除成功后清理残留 _handles/_active_reqs（防御：非 running 时 _watch finally 通常已清）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _make_scan_dir(tmp_path, "ws", scan_id="s1", status="completed")
    scan_key = ("ws", "s1")
    mgr._handles[scan_key] = MagicMock()
    mgr._active_reqs[scan_key] = ScanRequest(type="whitebox", url="u")
    await mgr.delete("ws", "s1")
    assert scan_key not in mgr._handles
    assert scan_key not in mgr._active_reqs
