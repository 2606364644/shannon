import json
import pytest
from pathlib import Path

from supernova_core.models.metrics import SessionMetadata
from supernova_core.models.audit import AgentEndResult
from supernova_whitebox.audit.metrics_tracker import MetricsTracker
from supernova_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _audit_dir(tmp_path: Path) -> Path:
    return generate_audit_path(_make_meta(tmp_path))


def _read_session_json(tmp_path: Path) -> dict:
    return json.loads((_audit_dir(tmp_path) / "session.json").read_text())


async def test_initialize_creates_session_json(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize(workflow_id="wf-123")
    data = _read_session_json(tmp_path)
    assert data["session"]["id"] == "test-session"
    assert data["session"]["status"] == "in-progress"
    assert data["session"]["originalWorkflowId"] == "wf-123"
    assert data["session"]["webUrl"] == "https://example.com"
    assert "createdAt" in data["session"]
    assert data["session"]["resumeAttempts"] == []
    assert "metrics" in data


async def test_initialize_without_workflow_id(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    data = _read_session_json(tmp_path)
    assert data["session"]["originalWorkflowId"] is None


async def test_start_agent_records_agent(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    metrics = tracker.get_metrics()
    assert "recon" in metrics["agents"]
    assert metrics["agents"]["recon"]["attempts"] == 1


async def test_end_agent_updates_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05, model="claude-sonnet-4-6")
    await tracker.end_agent("recon", result)
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["recon"]["duration_ms"] == 5000
    assert data["metrics"]["agents"]["recon"]["cost_usd"] == 0.05
    assert data["metrics"]["agents"]["recon"]["success"] is True
    assert data["metrics"]["agents"]["recon"]["model"] == "claude-sonnet-4-6"
    assert data["metrics"]["total_duration_ms"] == 5000
    assert data["metrics"]["total_cost_usd"] == 0.05


async def test_end_agent_accumulates_totals(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    tracker.start_agent("injection", 1)
    await tracker.end_agent("injection", AgentEndResult(success=True, duration_ms=3000, cost_usd=0.03))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["total_duration_ms"] == 8000
    assert data["metrics"]["total_cost_usd"] == 0.08


async def test_end_agent_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    result = AgentEndResult(success=False, duration_ms=1000, cost_usd=0.01, error="Rate limited")
    await tracker.end_agent("recon", result)
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["recon"]["error"] == "Rate limited"


async def test_end_agent_persists_cost_currency_and_tokens(tmp_path: Path):
    """cost 定价(spec 2026-07-09): end_agent 落盘 cost_currency + 4 档 token 到 session.json。"""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(
        success=True, duration_ms=100, cost_usd=0.5, cost_currency="CNY",
        input_tokens=1000, output_tokens=500, cache_read_tokens=100, cache_creation_tokens=0,
    ))
    data = _read_session_json(tmp_path)
    m = data["metrics"]
    # 顶层汇总
    assert m["cost_currency"] == "CNY"
    assert m["total_input_tokens"] == 1000
    assert m["total_output_tokens"] == 500
    assert m["total_cache_read_tokens"] == 100
    assert m["total_cache_creation_tokens"] == 0
    # agent 级
    assert m["agents"]["recon"]["cost_currency"] == "CNY"
    assert m["agents"]["recon"]["input_tokens"] == 1000
    assert m["agents"]["recon"]["cache_read_tokens"] == 100


async def test_update_session_status(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.update_session_status("completed")
    data = _read_session_json(tmp_path)
    # 内层(向后兼容)
    assert data["session"]["status"] == "completed"
    # 回归测试(hr_20260713-104726):顶层 status 必须同步。历史只写内层 → 顶层永留
    # create_workspace 的 "running" → SessionManager.get_status(顶层优先)读 running
    # → _status_of 非终态+心跳 stale 兜底成 interrupted → 扫描完成后 web 显示"已中断"。
    assert data["status"] == "completed"
    # 终态必须落 completed_at(与 SessionManager.mark_completed/_mark_cancelled 同源)
    assert data["completed_at"] is not None


async def test_update_session_status_non_terminal_keeps_completed_at_null(tmp_path: Path):
    """非终态(如 paused)镜像顶层 status 但不写 completed_at。"""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.update_session_status("paused")
    data = _read_session_json(tmp_path)
    assert data["session"]["status"] == "paused"
    assert data["status"] == "paused"
    assert data.get("completed_at") is None


async def test_add_resume_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-new", ["recon"], checkpoint="hash123")
    data = _read_session_json(tmp_path)
    assert len(data["session"]["resumeAttempts"]) == 1
    attempt = data["session"]["resumeAttempts"][0]
    assert attempt["workflowId"] == "wf-new"
    assert attempt["terminatedAgents"] == ["recon"]
    assert attempt["checkpoint"] == "hash123"


async def test_add_resume_attempt_without_checkpoint(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-new", ["recon"])
    data = _read_session_json(tmp_path)
    assert data["session"]["resumeAttempts"][0]["checkpoint"] is None


async def test_multiple_resume_attempts(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-2", ["recon"])
    await tracker.add_resume_attempt("wf-3", ["recon", "injection"])
    data = _read_session_json(tmp_path)
    assert len(data["session"]["resumeAttempts"]) == 2


async def test_reload_reads_from_disk(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # Simulate external modification
    data = _read_session_json(tmp_path)
    data["session"]["status"] = "externally-modified"
    session_path = _audit_dir(tmp_path) / "session.json"
    session_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    await tracker.reload()
    # get_metrics is from in-memory state; verify through internal _data
    assert tracker._data["session"]["status"] == "externally-modified"


async def test_get_metrics_returns_dict(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    metrics = tracker.get_metrics()
    assert "agents" in metrics
    assert "total_duration_ms" in metrics
    assert metrics["total_duration_ms"] == 5000


async def test_atomic_write_uses_temp_file(tmp_path: Path):
    """Verify no stale .tmp files remain after write."""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # After initialize, no .tmp file should remain
    audit_dir = _audit_dir(tmp_path)
    tmp_files = list(audit_dir.glob("*.tmp"))
    assert len(tmp_files) == 0


async def test_end_agent_populates_phases(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    data = _read_session_json(tmp_path)
    assert "phases" in data["metrics"]
    assert "recon" in data["metrics"]["phases"]
    assert data["metrics"]["phases"]["recon"]["duration_ms"] == 30000
    assert data["metrics"]["phases"]["recon"]["cost_usd"] == 0.20
    assert data["metrics"]["phases"]["recon"]["agent_count"] == 1


async def test_end_agent_accumulates_across_phases(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.10))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["phases"]["recon"]["duration_ms"] == 30000
    assert data["metrics"]["phases"]["recon"]["agent_count"] == 1
    assert data["metrics"]["phases"]["vulnerability-analysis"]["duration_ms"] == 15000
    assert data["metrics"]["phases"]["vulnerability-analysis"]["agent_count"] == 1


async def test_end_agent_calculates_duration_percentages(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.10))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["total_duration_ms"] == 45000
    assert data["metrics"]["phases"]["recon"]["duration_percentage"] == pytest.approx(66.67, abs=0.1)
    assert data["metrics"]["phases"]["vulnerability-analysis"]["duration_percentage"] == pytest.approx(33.33, abs=0.1)


async def test_end_agent_skips_failed_agents_in_phase_aggregation(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=False, duration_ms=1000, cost_usd=0.01, error="failed"))
    data = _read_session_json(tmp_path)
    # Failed agent should NOT be counted in phases
    assert "vulnerability-analysis" not in data["metrics"]["phases"]
    assert data["metrics"]["phases"]["recon"]["duration_percentage"] == 100.0


async def test_end_agent_multiple_agents_same_phase(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=True, duration_ms=10000, cost_usd=0.10))
    tracker.start_agent("xss-vuln", 1)
    await tracker.end_agent("xss-vuln", AgentEndResult(success=True, duration_ms=8000, cost_usd=0.08))
    data = _read_session_json(tmp_path)
    phase = data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["duration_ms"] == 18000
    assert phase["cost_usd"] == pytest.approx(0.18)
    assert phase["agent_count"] == 2
    assert phase["duration_percentage"] == 100.0


async def test_initialize_creates_empty_phases(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    data = _read_session_json(tmp_path)
    assert data["metrics"]["phases"] == {}


async def test_phases_backward_compatible_missing_field(tmp_path: Path):
    """Reading a session.json without 'phases' should not crash."""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # Manually strip phases to simulate old format
    data = _read_session_json(tmp_path)
    del data["metrics"]["phases"]
    session_path = _audit_dir(tmp_path) / "session.json"
    session_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    await tracker.reload()
    metrics = tracker.get_metrics()
    # Should not crash; phases defaults to empty
    assert metrics.get("phases", {}) == {}


async def test_end_agent_clears_error_on_success(tmp_path: Path):
    """spec 2026-08-08 bug2: 同一 agent 先失败（error）再成功 → error 残留清除（None）。

    修复前 ``if result.error`` 守护导致 success 时不清 error → attempt-1 失败的 error
    残留到 attempt-2 成功后（session.json report success:true + 旧 error 共存 → live 红标）。"""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("report", 1)
    await tracker.end_agent("report", AgentEndResult(
        success=False, duration_ms=1000, cost_usd=0.01, error="attempt-1 failed"))
    # attempt-2 成功（report agent 重试成功）
    await tracker.end_agent("report", AgentEndResult(
        success=True, duration_ms=2000, cost_usd=0.02, error=None))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["report"]["success"] is True
    assert data["metrics"]["agents"]["report"].get("error") is None, (
        "success 后 error 应清除（修复前残留 attempt-1 的 error）")
