"""session-status 同步:_watch 周期 describe() 轮询,workflow FAILED 时写 scan_end+session.failed.

场景:worker 进程崩溃/容器死/被 terminate -> workflow except 跑不到 -> finalize_summary 不调
-> scan_end 不写,_watch 永等不到。describe() 轮询发现 FAILED 时 _watch 自行落盘。

T3: _watch(scan_key, event_file, scan_dir)；_write_scan_end(..., scan_dir=) 同步写 scan session。
"""
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from supernova_web.components.scan_manager import ScanManager


def _make_handle(status) -> MagicMock:
    """伪 WorkflowHandle:describe() 返回给定 status。"""
    handle = MagicMock()
    desc = MagicMock()
    desc.status = status  # temporalio.workflow.WorkflowExecutionStatus enum
    handle.describe = AsyncMock(return_value=desc)
    return handle


def _make_scan_dir(workspaces, ws, scan_id="s1"):
    scan_dir = Path(workspaces) / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "status": "running", "scan_type": "whitebox", "created_at": time.time(),
        "web_url": "", "repo_path": "",
    }))
    return scan_dir


@pytest.mark.asyncio
async def test_watch_marks_session_failed_on_workflow_failed(tmp_path):
    """describe() 返 FAILED -> scan session.json status=failed + scan_end 写入 + _handles 清理."""
    from temporalio.client import WorkflowExecutionStatus

    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    scan_dir = _make_scan_dir(workspaces, "ghost", scan_id="s1")
    event_file = scan_dir / "events.ndjson"

    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock(), scan_timeout=0.0)
    scan_key = ("ghost", "s1")
    handle = _make_handle(WorkflowExecutionStatus.FAILED)
    mgr._handles[scan_key] = handle

    await mgr._watch(scan_key, event_file, scan_dir)

    # scan session.json 落 failed
    from supernova_core.session import SessionManager
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data["status"] == "failed", f"FAILED 后 session.status 应=failed, 实际={data.get('status')}"
    assert data.get("completed_at") is not None, "completed_at 应被写入"
    # scan_end 写 events.ndjson
    lines = event_file.read_text().splitlines()
    assert any(json.loads(l).get("type") == "scan_end" and json.loads(l).get("status") == "failed"
               for l in lines), "应写 scan_end(status=failed)"
    # _handles 清理
    assert scan_key not in mgr._handles


@pytest.mark.asyncio
async def test_watch_does_not_mark_failed_on_running(tmp_path):
    """describe() 返 RUNNING -> 不触发 failed(继续 tail).用极短 scan_timeout 让循环退出."""
    from temporalio.client import WorkflowExecutionStatus

    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    scan_dir = _make_scan_dir(workspaces, "live", scan_id="s1")
    event_file = scan_dir / "events.ndjson"

    # scan_timeout 极小让 _watch 快速走 timeout 兜底退出(不依赖 describe 触发)
    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock(), scan_timeout=0.05)
    scan_key = ("live", "s1")
    handle = _make_handle(WorkflowExecutionStatus.RUNNING)
    mgr._handles[scan_key] = handle

    await mgr._watch(scan_key, event_file, scan_dir)

    from supernova_core.session import SessionManager
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    # RUNNING 时不应写 failed(timeout 兜底写 crashed,但 session.status 不该是 failed)
    assert data.get("status") != "failed", "RUNNING 时不应标 failed"


@pytest.mark.asyncio
async def test_write_scan_end_accepts_session_status(tmp_path):
    """_write_scan_end 接受 session_status + scan_dir 参数,命中时同步写 scan session.json."""
    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    scan_dir = _make_scan_dir(workspaces, "x", scan_id="s1")
    event_file = scan_dir / "events.ndjson"
    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock())

    await mgr._write_scan_end(event_file, "failed", -1, "worker crash",
                              session_status="failed", scan_dir=scan_dir)

    from supernova_core.session import SessionManager
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data["status"] == "failed"
