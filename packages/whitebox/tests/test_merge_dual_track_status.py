"""Task 5: merger per_class_counts 记 gitnexus_status(供报告标红)。

验证 `run_merge_dual_track_queues` 在构建 per_class_counts 时,从
`gitnexus_track_status.json`(Task 4 写)读 per-class 状态并注入:

- `gitnexus_status ∈ {"ok","failed","absent"}`(始终存在,默认 "absent")
- `gitnexus_fail_reason`(仅 failed 时存在)

合并逻辑不变(failed 类自然退 llm-only 或被跳过)。
"""
import json
from contextlib import asynccontextmanager

import pytest

from supernova_whitebox.audit.session_registry import clear_audit_session, set_audit_session
from supernova_whitebox.pipeline import activities


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


def _llm_finding(vid, vtype):
    return {
        "ID": vid,
        "vulnerability_type": vtype,
        "externally_exploitable": True,
        "confidence": "high",
        "verdict": "vulnerable",
        "source": "q",
        "sink_call": "db.exec",
    }


def _gitnexus_finding(vid, vtype):
    return {
        "ID": vid,
        "vulnerability_type": vtype,
        "externally_exploitable": True,
        "confidence": "high",
        "verdict": "vulnerable",
        "source": "q",
        "sink_call": "db.exec",
        "evidence_chain": "q -> db.exec(L42)",
    }


@pytest.mark.asyncio
async def test_failed_class_tagged_in_counts(tmp_path, monkeypatch):
    """GitNexus 轨 failed 的类(xss),LLM 轨有发现 -> 合并不跳过,
    per_class_counts[xss].gitnexus_status='failed' + gitnexus_fail_reason 记录原因,
    合并产物仍为 llm-only(合并逻辑不变)。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # Task 4 产物:xss GitNexus 轨 failed
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({"xss": {"status": "failed", "reason": "builder raised"}}),
        encoding="utf-8",
    )
    # xss GitNexus queue 缺(failed -> 不产 queue)
    # xss LLM 轨有发现 -> 不跳过
    (deliverables / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_llm_finding("L1", "xss")]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "xss" in result["merged_classes"]
    counts = result["per_class_counts"]["xss"]
    assert counts["gitnexus_status"] == "failed"
    assert counts["gitnexus_fail_reason"] == "builder raised"
    # 合并逻辑不变:LLM-only 发现仍并入
    assert counts["llm"] == 1
    assert counts["gitnexus"] == 0
    assert counts["llm_only"] == 1
    out = json.loads((deliverables / "xss_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    assert out["vulnerabilities"][0]["merge_source"] == "llm-only"


@pytest.mark.asyncio
async def test_ok_class_tagged_ok(tmp_path, monkeypatch):
    """GitNexus 轨 ok 的类(injection,有 queue)-> per_class_counts.gitnexus_status='ok'。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({"injection": {"status": "ok", "findings": 2}}),
        encoding="utf-8",
    )
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps({"vulnerabilities": [_gitnexus_finding("G1", "injection")]}),
        encoding="utf-8",
    )
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_llm_finding("L1", "injection")]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "injection" in result["merged_classes"]
    counts = result["per_class_counts"]["injection"]
    assert counts["gitnexus_status"] == "ok"
    # ok 不应带 gitnexus_fail_reason
    assert "gitnexus_fail_reason" not in counts


@pytest.mark.asyncio
async def test_absent_class_tagged_absent(tmp_path, monkeypatch):
    """无 track_status 条目的类(auth,GitNexus 不跟踪)-> gitnexus_status='absent'。
    用 track_status 文件存在但无 auth 条目(模拟 injection 已 ok、auth 缺席)。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({"injection": {"status": "ok", "findings": 1}}),
        encoding="utf-8",
    )
    # auth 在 track_status 中无条目
    (deliverables / "auth_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_llm_finding("L1", "auth")]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "auth" in result["merged_classes"]
    counts = result["per_class_counts"]["auth"]
    assert counts["gitnexus_status"] == "absent"
    assert "gitnexus_fail_reason" not in counts


@pytest.mark.asyncio
async def test_no_track_status_file_all_absent(tmp_path, monkeypatch):
    """track_status 文件完全缺失(read_track_status 返 {})-> 所有类 gitnexus_status='absent'。
    验证 merger 容错:文件缺不抛。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # 不写 gitnexus_track_status.json
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_llm_finding("L1", "injection")]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_merge_dual_track_queues(_input(tmp_path))
    finally:
        clear_audit_session()

    counts = result["per_class_counts"]["injection"]
    assert counts["gitnexus_status"] == "absent"


@pytest.mark.asyncio
async def test_failed_class_logged(tmp_path, monkeypatch, caplog):
    """可观测:failed 类合并时打 info 日志(标红供报告的直接信号)。"""
    import logging
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "gitnexus_track_status.json").write_text(
        json.dumps({"xss": {"status": "failed", "reason": "builder raised"}}),
        encoding="utf-8",
    )
    (deliverables / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_llm_finding("L1", "xss")]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    with caplog.at_level(logging.INFO):
        try:
            await activities.run_merge_dual_track_queues(_input(tmp_path))
        finally:
            clear_audit_session()
    assert any(
        "xss" in r.getMessage() and "failed" in r.getMessage() for r in caplog.records
    ), "GitNexus 轨 failed 类合并时应打 info 日志(含 vuln 类名 + failed)"
