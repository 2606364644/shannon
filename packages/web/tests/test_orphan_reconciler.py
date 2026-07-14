# packages/web/tests/test_orphan_reconciler.py
"""孤儿 scan 对账：容器重启后 scan_manager._watch 丢失，session 卡 running 且无 scan_end。
reconcile_orphaned 应补 scan_end(interrupted)+失败原因+session completed_at，且幂等/不误伤。
"""
import json
import os
import time

import pytest

from shannon_web.components.orphan_reconciler import reconcile_orphaned, _has_scan_end


def _make_ws(tmp_path, status="running", scan_end_status=None, with_activity_log=False):
    """构造一个**死掉的孤儿** ws(无 fresh heartbeat):写完所有文件后把 mtime 设远古
    (历史遗留;新判活只看 heartbeat,故无 heartbeat 即死)。活 scan 场景另建 fresh heartbeat。"""
    ws_dir = tmp_path / "workspaces" / "ws1"
    ws_dir.mkdir(parents=True)
    session = {
        "web_url": "", "repo_path": "/repo", "scan_type": "whitebox",
        "status": status, "completed_at": None,
        "session": {"id": "ws1", "status": "in-progress"},
    }
    (ws_dir / "session.json").write_text(json.dumps(session), encoding="utf-8")
    if scan_end_status:
        (ws_dir / "events.ndjson").write_text(
            json.dumps({"type": "scan_end", "status": scan_end_status,
                        "ts": "t", "category": "CONTROL"}) + "\n",
            encoding="utf-8",
        )
    if with_activity_log:
        (ws_dir / "activity_failures.log").write_text(
            "temporalio.exceptions.TimeoutError: activity StartToClose timeout\n",
            encoding="utf-8",
        )
    old = time.time() - 3600  # 远古 mtime = scan 早已停写
    for f in (ws_dir / "session.json", ws_dir / "events.ndjson", ws_dir / "activity_failures.log"):
        if f.exists():
            os.utime(f, (old, old))
    return ws_dir


@pytest.mark.asyncio
async def test_orphan_running_not_alive_writes_scan_end(tmp_path):
    ws_dir = _make_ws(tmp_path, status="running")
    wrote = await reconcile_orphaned(ws_dir, is_running=False)
    assert wrote is True
    ef = ws_dir / "events.ndjson"
    assert ef.exists()
    line = json.loads(ef.read_text(encoding="utf-8").strip())
    assert line["type"] == "scan_end"
    assert line["status"] == "interrupted"
    # session 标完成时间 + interrupted
    sess = json.loads((ws_dir / "session.json").read_text(encoding="utf-8"))
    assert sess["completed_at"] is not None
    assert sess["status"] == "interrupted"


@pytest.mark.asyncio
async def test_orphan_attaches_activity_failure_tail(tmp_path):
    ws_dir = _make_ws(tmp_path, status="running", with_activity_log=True)
    await reconcile_orphaned(ws_dir, is_running=False)
    line = json.loads((ws_dir / "events.ndjson").read_text(encoding="utf-8").strip())
    assert "StartToClose timeout" in line["stderr_tail"]


@pytest.mark.asyncio
async def test_orphan_alive_skip(tmp_path):
    ws_dir = _make_ws(tmp_path, status="running")
    wrote = await reconcile_orphaned(ws_dir, is_running=True)
    assert wrote is False
    assert not (ws_dir / "events.ndjson").exists()


@pytest.mark.asyncio
async def test_orphan_completed_skip(tmp_path):
    ws_dir = _make_ws(tmp_path, status="completed")
    wrote = await reconcile_orphaned(ws_dir, is_running=False)
    assert wrote is False
    assert not (ws_dir / "events.ndjson").exists()


@pytest.mark.asyncio
async def test_orphan_idempotent_has_scan_end(tmp_path):
    ws_dir = _make_ws(tmp_path, status="running", scan_end_status="crashed")
    wrote = await reconcile_orphaned(ws_dir, is_running=False)
    assert wrote is False
    # 不重复写
    assert _has_scan_end(ws_dir / "events.ndjson")
    lines = [l for l in ws_dir.joinpath("events.ndjson").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_orphan_no_session_skip(tmp_path):
    """非 scan 工作区（无 session.json，如 default/ logs/）不处理。"""
    ws_dir = tmp_path / "workspaces" / "default"
    ws_dir.mkdir(parents=True)
    wrote = await reconcile_orphaned(ws_dir, is_running=False)
    assert wrote is False


@pytest.mark.asyncio
async def test_orphan_recently_active_skip(tmp_path):
    """回归:heartbeat fresh(host CLI 起的活 scan,web 的 scan_manager 看不到其 pid)→
    reconcile_orphaned 不得据「无 pid」就写假 scan_end
    (kol_mapping_service_20260708-193139 被误显 interrupted 即此 bug)。
    判活信号源已从 workflow.log 换成 heartbeat(进程级、不受 LLM 卡顿影响)。"""
    ws_dir = _make_ws(tmp_path, status="running")  # session.json 已被设远古 mtime
    # scan 进程仍存活:HeartbeatManager 刚写了 heartbeat(mtime=now)
    (ws_dir / "heartbeat").write_text(f"{time.time()}\n", encoding="utf-8")
    wrote = await reconcile_orphaned(ws_dir, is_running=False)  # web 看不到 host pid
    assert wrote is False
    assert not _has_scan_end(ws_dir / "events.ndjson")  # 没写假 scan_end
    # session 也不应被改成 interrupted
    sess = json.loads((ws_dir / "session.json").read_text(encoding="utf-8"))
    assert sess["status"] == "running"


@pytest.mark.asyncio
async def test_reconcile_skips_within_submit_grace(tmp_path):
    """提交宽限内(刚 start_workflow, worker 还没写首个 heartbeat)→ 不写假 scan_end.

    覆盖冷启动窗口:此前提交后 1s 内前端首次 poll /events 即触发 reconcile, 那时 worker 连首个
    heartbeat 都没写 → 误判 interrupted, 且 _status_of 终态优先致误杀不可逆(hr_1784014329 即此).
    """
    ws_dir = tmp_path / "workspaces" / "ws1"
    ws_dir.mkdir(parents=True)
    (ws_dir / "session.json").write_text(json.dumps({
        "status": "running", "submitted_at": time.time(),
    }), encoding="utf-8")
    # 无 heartbeat + 无 scan_end + 非终态 + submitted_at 新 → 宽限内 → 不干预
    wrote = await reconcile_orphaned(ws_dir, is_running=False)
    assert wrote is False
    assert not (ws_dir / "events.ndjson").exists()
    sess = json.loads((ws_dir / "session.json").read_text(encoding="utf-8"))
    assert sess["status"] == "running"  # 未被改 interrupted


@pytest.mark.asyncio
async def test_reconcile_reason_mentions_worker_not_started(tmp_path):
    """超宽限 + 无 heartbeat 真孤儿 → scan_end.stderr_tail 指向「worker 容器可能未启动」.

    取代笼统「扫描因服务重启被中断」——后者误导(非服务重启, 是 worker 没起/已退出),
    直接指向最常见根因, 便于用户定位(本 bug 即 worker 容器从未被 up.sh 启动).
    """
    ws_dir = tmp_path / "workspaces" / "ws1"
    ws_dir.mkdir(parents=True)
    old = time.time() - 3600
    (ws_dir / "session.json").write_text(json.dumps({
        "status": "running", "submitted_at": old,  # 提交已久 → 超宽限
    }), encoding="utf-8")
    wrote = await reconcile_orphaned(ws_dir, is_running=False)
    assert wrote is True
    line = json.loads((ws_dir / "events.ndjson").read_text(encoding="utf-8").strip())
    assert "worker 容器可能未启动" in line["stderr_tail"]
