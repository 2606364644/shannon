import json
from contextlib import asynccontextmanager

import pytest

from shannon_whitebox.audit.session_registry import clear_audit_session, set_audit_session
from shannon_whitebox.pipeline import activities


class _RecordingSession:
    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        yield


def _input(repo):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
        deliverables_subdir = None
        workspace_name = None
        workspace_path = None

    return FakeInput()


@pytest.mark.asyncio
async def test_merge_writes_exploitation_queue_from_llm_only(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "ID": "L1",
                        "vulnerability_type": "injection",
                        "externally_exploitable": True,
                        "confidence": "high",
                        "verdict": "vulnerable",
                        "source": "q",
                        "sink_call": "db.exec",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "llm-only"
    assert v["confidence"] == "needs_review"
    assert (deliverables / "injection_llm_queue.json").exists()


@pytest.mark.asyncio
async def test_merge_combines_both_tracks(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "ID": "L1",
                        "vulnerability_type": "injection",
                        "externally_exploitable": True,
                        "confidence": "high",
                        "verdict": "vulnerable",
                        "source": "q",
                        "sink_call": "db.exec",
                    }
                ]
            }
        )
    )
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "ID": "G1",
                        "vulnerability_type": "injection",
                        "externally_exploitable": True,
                        "confidence": "high",
                        "verdict": "vulnerable",
                        "source": "q",
                        "sink_call": "db.exec",
                        "evidence_chain": "q -> db.exec(L42)",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "both"
    assert v["confidence"] == "high"
    assert v["evidence_chain"] == "q -> db.exec(L42)"


@pytest.mark.asyncio
async def test_merge_skips_vuln_classes_with_no_llm_queue(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()
    assert result["merged_classes"] == []


@pytest.mark.asyncio
async def test_merge_handles_invalid_llm_queue_leniently(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text("not json")
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "injection" in result["merged_classes"]
    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert out["vulnerabilities"] == []


@pytest.mark.asyncio
async def test_merge_keeps_gitnexus_only_when_llm_queue_absent(tmp_path, monkeypatch):
    """A4: LLM queue 缺席时，GitNexus-only 发现仍并入报告（真兜底）。
    df33ec5 时此场景 continue 跳过，GitNexus 产物被丢。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 注意：不写 injection_exploitation_queue.json（LLM 轨缺席）
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "ID": "G1",
                        "vulnerability_type": "injection",
                        "externally_exploitable": True,
                        "confidence": "high",
                        "verdict": "vulnerable",
                        "source": "q",
                        "sink_call": "db.exec",
                        "evidence_chain": "q -> db.exec(L42)",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "injection" in result["merged_classes"]
    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "gitnexus-only"
    assert v["confidence"] == "needs_review"
    assert v["externally_exploitable"] is True  # 取 GitNexus 轨值，不被覆写


@pytest.mark.asyncio
async def test_merge_logs_gitnexus_only_findings(tmp_path, monkeypatch, caplog):
    """可观测: GitNexus-only 发现并入时打 info 日志（A4 生效的直接信号）。"""
    import logging
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps(
            {"vulnerabilities": [{
                "ID": "G1", "vulnerability_type": "injection",
                "externally_exploitable": True, "confidence": "high",
                "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
            }]}
        )
    )
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    with caplog.at_level(logging.INFO):
        try:
            await activities.run_merge_dual_track_queues(_input(tmp_path))
        finally:
            clear_audit_session()
    assert any(
        "gitnexus-only" in r.getMessage() and "injection" in r.getMessage()
        for r in caplog.records
    ), "GitNexus-only 并入时应打 info 日志（含 vuln 类名）"


@pytest.mark.asyncio
async def test_merge_preserves_gitnexus_only_reachability_false(tmp_path, monkeypatch):
    """铁律: GitNexus-only 发现 externally_exploitable=False（内部可达）合并后保持 False，
    不被 verdict=vulnerable 覆写（dual_track_merger.py:52-57）。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps(
            {"vulnerabilities": [{
                "ID": "G1", "vulnerability_type": "injection",
                "externally_exploitable": False,  # 内部/跨服务可达
                "confidence": "high",
                "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
            }]}
        )
    )
    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()
    out = json.loads((deliverables / "injection_exploitation_queue.json").read_text())
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "gitnexus-only"
    assert v["externally_exploitable"] is False  # 保持，不被 verdict 覆写
