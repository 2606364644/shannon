# packages/whitebox/tests/test_persist_completed_agents.py
"""写侧进度落盘（2026-08-27 列表进度不动修复 · 写侧）。

progress_pct 分子 completed_agents 原只在 workflow 结束（finalize_summary）落盘，
运行中 session.json 恒 [] → 列表/详情/仪表盘 progress_pct 阶段内钉死（组合扫描
白盒段全程 5%）。本修复在每个 agent 完成点经 activity 增量落盘——Temporal
workflow 禁 IO，必须走 activity；best-effort（失败不阻塞扫描，workflow 侧吞）。
"""
import inspect
import json
from pathlib import Path

from supernova_whitebox.pipeline import activities, workflows
from supernova_whitebox.pipeline.shared import ActivityInput


def _mk_scan(tmp_path: Path, name: str = "s1") -> tuple[Path, ActivityInput]:
    scan_dir = tmp_path / "scans" / name
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "status": "running", "completed_agents": [],
        "repo_path": "/x", "combined": True,
    }), encoding="utf-8")
    act = ActivityInput(repo_path="/x", web_url="", workspace_path=str(scan_dir))
    return scan_dir, act


async def test_persist_updates_completed_and_keeps_other_keys(tmp_path):
    """落盘 completed_agents 且保留其他 top-level key（read-modify-write 语义）。"""
    scan_dir, act = _mk_scan(tmp_path)
    await activities.persist_completed_agents(act, ["pre-recon", "recon"])
    data = json.loads((scan_dir / "session.json").read_text(encoding="utf-8"))
    assert data["completed_agents"] == ["pre-recon", "recon"]
    assert data["combined"] is True
    assert data["status"] == "running"


async def test_persist_noop_when_session_missing(tmp_path):
    """session.json 缺失（异常路径）→ no-op 不创建空壳文件。

    update_session 对缺失文件会写出只含 completed_agents 的残缺 session，
    破坏后续 get_session_data 的字段期望，必须先判存在。
    """
    scan_dir, act = _mk_scan(tmp_path, name="s2")
    (scan_dir / "session.json").unlink()
    await activities.persist_completed_agents(act, ["recon"])
    assert not (scan_dir / "session.json").exists()


def test_persist_activity_registered():
    """必须是 @activity.defn（worker 才能调度）。"""
    assert hasattr(activities, "persist_completed_agents")
    assert hasattr(activities.persist_completed_agents,
                   "__temporal_activity_definition")


def test_workflow_wiring_append_points_persist():
    """源码级 wiring：每个 completed_agents.append 点后必须落盘（_persist_progress）。

    append 点（whitebox）：pre-recon(242) / recon(320) / vuln 循环(436)——漏一处
    则该 agent 完成后列表进度仍钉死到下一个落盘点。
    """
    src = inspect.getsource(workflows)
    lines = src.splitlines()
    appends = [l for l in lines if ".completed_agents.append(" in l]
    assert len(appends) >= 3, "append 点数量异常（应 ≥3：pre-recon/recon/vuln 循环）"
    for i, l in enumerate(lines):
        if ".completed_agents.append(" in l:
            window = "\n".join(lines[i:i + 6])
            assert "_persist_progress(" in window, (
                f"append 后缺 _persist_progress 调用: L{i + 1}: {l.strip()}")


def test_persist_progress_swallows_activity_error():
    """_persist_progress 源码必须吞异常（best-effort：进度落盘失败不 fail 扫描）。"""
    src = inspect.getsource(workflows.WhiteboxScanWorkflow)
    assert "async def _persist_progress" in src
    body = src.split("async def _persist_progress", 1)[1]
    body = body.split("\n    async def ", 1)[0]
    assert "except" in body, "_persist_progress 必须捕获 activity 异常"
