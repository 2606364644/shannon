"""session-status 同步:_watch 周期 describe() 轮询,workflow FAILED 时写 scan_end+session.failed.

场景:worker 进程崩溃/容器死/被 terminate → workflow except 跑不到 → finalize_summary 不调
→ scan_end 不写,_watch 永等不到。describe() 轮询发现 FAILED 时 _watch 自行落盘。
"""
import json
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


@pytest.mark.asyncio
async def test_watch_marks_session_failed_on_workflow_failed(tmp_path):
    """describe() 返 FAILED → session.json status=failed + scan_end 写入 + _handles 清理."""
    from temporalio.client import WorkflowExecutionStatus

    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws = "ghost"
    (workspaces / ws).mkdir()
    event_file = workspaces / ws / "events.ndjson"

    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock(), scan_timeout=0.0)
    handle = _make_handle(WorkflowExecutionStatus.FAILED)
    mgr._handles[ws] = handle

    await mgr._watch(ws, event_file)

    # session.json 落 failed
    from supernova_core.session import SessionManager
    data = SessionManager(workspaces).get_session_data(workspaces / ws)
    assert data["status"] == "failed", f"FAILED 后 session.status 应=failed, 实际={data.get('status')}"
    assert data.get("completed_at") is not None, "completed_at 应被写入"
    # scan_end 写 events.ndjson
    lines = event_file.read_text().splitlines()
    assert any(json.loads(l).get("type") == "scan_end" and json.loads(l).get("status") == "failed"
               for l in lines), "应写 scan_end(status=failed)"
    # _handles 清理
    assert ws not in mgr._handles


@pytest.mark.asyncio
async def test_watch_does_not_mark_failed_on_running(tmp_path):
    """describe() 返 RUNNING → 不触发 failed(继续 tail).用极短 scan_timeout 让循环退出."""
    from temporalio.client import WorkflowExecutionStatus

    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws = "live"
    (workspaces / ws).mkdir()
    event_file = workspaces / ws / "events.ndjson"

    # scan_timeout 极小让 _watch 快速走 timeout 兜底退出(不依赖 describe 触发)
    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock(), scan_timeout=0.05)
    handle = _make_handle(WorkflowExecutionStatus.RUNNING)
    mgr._handles[ws] = handle

    await mgr._watch(ws, event_file)

    from supernova_core.session import SessionManager
    data = SessionManager(workspaces).get_session_data(workspaces / ws)
    # RUNNING 时不应写 failed(timeout 兜底写 crashed,但 session.status 不该是 failed)
    assert data.get("status") != "failed", "RUNNING 时不应标 failed"


@pytest.mark.asyncio
async def test_write_scan_end_accepts_session_status(tmp_path):
    """_write_scan_end 接受 session_status 参数,命中时同步写 session.json."""
    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws = "x"
    (workspaces / ws).mkdir()
    event_file = workspaces / ws / "events.ndjson"
    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock())

    await mgr._write_scan_end(event_file, "failed", -1, "worker crash",
                              session_status="failed", workspace_name=ws,
                              workspaces_dir=workspaces)

    from supernova_core.session import SessionManager
    data = SessionManager(workspaces).get_session_data(workspaces / ws)
    assert data["status"] == "failed"
