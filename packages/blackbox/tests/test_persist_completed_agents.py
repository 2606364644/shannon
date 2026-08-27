# packages/blackbox/tests/test_persist_completed_agents.py
"""写侧进度落盘（2026-08-27 列表进度不动修复 · 写侧，blackbox run 级）。

黑盒 workflow 的 completed_agents 原只在结束（worker result / _finalize_web）落盘
run-K/session.json → 运行中恒 []，组合扫描黑盒段 progress_pct 分子不动（钉死
55%）。本修复在每个 agent 完成点经 activity 增量落盘；与 whitebox 侧同构
（Temporal workflow 禁 IO，best-effort 吞异常）。
"""
import inspect
import json
from pathlib import Path

from supernova_blackbox.pipeline import activities, workflows
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _mk_run(tmp_path: Path) -> tuple[Path, BlackboxActivityInput]:
    run_dir = tmp_path / "scans" / "wb1" / "blackbox-runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "session.json").write_text(json.dumps({
        "status": "running", "completed_agents": [], "bb_phase": "running",
    }), encoding="utf-8")
    act = BlackboxActivityInput(
        web_url="http://t", workspace_path=str(run_dir))
    return run_dir, act


async def test_persist_updates_completed_and_keeps_other_keys(tmp_path):
    run_dir, act = _mk_run(tmp_path)
    await activities.persist_completed_agents(act, ["injection-exploit"])
    data = json.loads((run_dir / "session.json").read_text(encoding="utf-8"))
    assert data["completed_agents"] == ["injection-exploit"]
    assert data["bb_phase"] == "running"   # 其他 top-level key 不丢


async def test_persist_noop_when_session_missing(tmp_path):
    run_dir, act = _mk_run(tmp_path)
    (run_dir / "session.json").unlink()
    await activities.persist_completed_agents(act, ["xss-exploit"])
    assert not (run_dir / "session.json").exists()


def test_persist_activity_registered():
    assert hasattr(activities, "persist_completed_agents")
    assert hasattr(activities.persist_completed_agents,
                   "__temporal_activity_definition")


def test_workflow_wiring_append_points_persist():
    """源码级 wiring：每个 completed_agents.append 点后必须落盘（_persist_progress）。

    append 点（blackbox）：exploit 循环 + REPORT。漏一处则该段进度钉死。
    """
    src = inspect.getsource(workflows)
    lines = src.splitlines()
    appends = [l for l in lines if ".completed_agents.append(" in l]
    assert len(appends) >= 2, "append 点数量异常（应 ≥2：exploit 循环/REPORT）"
    for i, l in enumerate(lines):
        if ".completed_agents.append(" in l:
            window = "\n".join(lines[i:i + 6])
            assert "_persist_progress(" in window, (
                f"append 后缺 _persist_progress 调用: L{i + 1}: {l.strip()}")


def test_persist_progress_swallows_activity_error():
    src = inspect.getsource(workflows.BlackboxScanWorkflow)
    assert "async def _persist_progress" in src
    body = src.split("async def _persist_progress", 1)[1]
    body = body.split("\n    async def ", 1)[0]
    assert "except" in body, "_persist_progress 必须捕获 activity 异常"
