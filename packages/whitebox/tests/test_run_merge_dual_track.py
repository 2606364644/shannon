import json
from contextlib import asynccontextmanager

import pytest

from supernova_core.utils.paths import INTERMEDIATE_SUBDIR
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


def _wb(tmp_path):
    """白盒桶根（tiering SSOT：queue json 落桶内 intermediate/）。"""
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _inter(tmp_path):
    d = _wb(tmp_path) / INTERMEDIATE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


_LLM_VULN = {
    "ID": "L1",
    "vulnerability_type": "injection",
    "externally_exploitable": True,
    "confidence": "high",
    "verdict": "vulnerable",
    "source": "q",
    "sink_call": "db.exec",
}

_GN_VULN = {
    "ID": "G1",
    "vulnerability_type": "injection",
    "externally_exploitable": True,
    "confidence": "high",
    "verdict": "vulnerable",
    "source": "q",
    "sink_call": "db.exec",
    "evidence_chain": "q -> db.exec(L42)",
}


async def _run(tmp_path):
    monkeypatch_set = activities
    from unittest.mock import patch
    with patch.object(monkeypatch_set, "_get_paths",
                      lambda i: (tmp_path, _wb(tmp_path), tmp_path)):
        set_audit_session(_RecordingSession())
        try:
            return await activities.run_merge_dual_track_queues(_input(tmp_path))
        finally:
            clear_audit_session()


# ── tiering 主路径：queue 在桶内 intermediate/ ───────────────────────────────
@pytest.mark.asyncio
async def test_merge_reads_intermediate_llm_queue(tmp_path):
    """tiering 回归（主死因链）：LLM 轨 queue 由 executor auto-write 落
    intermediate/（executor.py intermediate_path），merge 平铺拼接读不到 →
    llm_findings 恒空。此处必须读到并写回 intermediate/。"""
    _inter(tmp_path).joinpath("injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_LLM_VULN]})
    )

    result = await _run(tmp_path)

    assert "injection" in result["merged_classes"]
    out = json.loads(
        (_inter(tmp_path) / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "llm-only"
    assert v["confidence"] == "needs_review"
    assert (_inter(tmp_path) / "injection_llm_queue.json").exists(), (
        "LLM 轨原始 queue 副本应落 intermediate/（*_llm_queue.json 属中间产物 tier）")


@pytest.mark.asyncio
async def test_merge_reads_tiering_mixed_layout(tmp_path):
    """真实混合态：LLM queue 在 intermediate/（executor 写侧）、GitNexus queue
    平铺（写侧未迁移/老结构）→ 两轨都必须读到，verdict OR 不丢边。"""
    _inter(tmp_path).joinpath("injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_LLM_VULN]})
    )
    _wb(tmp_path).joinpath("injection_gitnexus_queue.json").write_text(
        json.dumps({"vulnerabilities": [_GN_VULN]})
    )

    result = await _run(tmp_path)

    assert "injection" in result["merged_classes"]
    out = json.loads(
        (_inter(tmp_path) / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "both"
    assert v["evidence_chain"] == "q -> db.exec(L42)"


@pytest.mark.asyncio
async def test_merge_combines_both_tracks(tmp_path):
    deliverables = _inter(tmp_path)
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_LLM_VULN]})
    )
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps({"vulnerabilities": [_GN_VULN]})
    )

    await _run(tmp_path)

    out = json.loads(
        (deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "both"
    assert v["confidence"] == "high"
    assert v["evidence_chain"] == "q -> db.exec(L42)"


# ── 老平铺结构兜底（旧 session 存量 queue 在桶根）──────────────────────────
@pytest.mark.asyncio
async def test_merge_flat_layout_fallback(tmp_path):
    """老结构：queue 平铺桶根 → 兜底读到；合并结果写回 intermediate/（SSOT），
    下游 resolve_intermediate（intermediate 优先）读到的是合并版。"""
    wb = _wb(tmp_path)
    (wb / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [_LLM_VULN]})
    )

    result = await _run(tmp_path)

    assert "injection" in result["merged_classes"]
    out = json.loads(
        (_inter(tmp_path) / "injection_exploitation_queue.json").read_text())
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "llm-only"
    assert (wb / "injection_exploitation_queue.json").exists(), (
        "老平铺输入是 executor 已提交的历史产物，不应被删")


@pytest.mark.asyncio
async def test_merge_skips_vuln_classes_with_no_llm_queue(tmp_path, monkeypatch):
    _wb(tmp_path)
    result = await _run(tmp_path)
    assert result["merged_classes"] == []


@pytest.mark.asyncio
async def test_merge_handles_invalid_llm_queue_leniently(tmp_path):
    (_inter(tmp_path) / "injection_exploitation_queue.json").write_text("not json")

    result = await _run(tmp_path)

    assert "injection" in result["merged_classes"]
    out = json.loads(
        (_inter(tmp_path) / "injection_exploitation_queue.json").read_text())
    assert out["vulnerabilities"] == []


@pytest.mark.asyncio
async def test_merge_keeps_gitnexus_only_when_llm_queue_absent(tmp_path):
    """A4: LLM queue 缺席时，GitNexus-only 发现仍并入报告（真兜底）。
    df33ec5 时此场景 continue 跳过，GitNexus 产物被丢。"""
    deliverables = _inter(tmp_path)
    # 注意：不写 injection_exploitation_queue.json（LLM 轨缺席）
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps({"vulnerabilities": [_GN_VULN]})
    )

    result = await _run(tmp_path)

    assert "injection" in result["merged_classes"]
    out = json.loads(
        (deliverables / "injection_exploitation_queue.json").read_text())
    assert len(out["vulnerabilities"]) == 1
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "gitnexus-only"
    assert v["confidence"] == "needs_review"
    assert v["externally_exploitable"] is True  # 取 GitNexus 轨值，不被覆写


@pytest.mark.asyncio
async def test_merge_logs_gitnexus_only_findings(tmp_path, monkeypatch, caplog):
    """可观测: GitNexus-only 发现并入时打 info 日志（A4 生效的直接信号）。"""
    import logging
    (_inter(tmp_path) / "injection_gitnexus_queue.json").write_text(
        json.dumps({"vulnerabilities": [{
            "ID": "G1", "vulnerability_type": "injection",
            "externally_exploitable": True, "confidence": "high",
            "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
        }]})
    )
    with caplog.at_level(logging.INFO):
        await _run(tmp_path)
    assert any(
        "gitnexus-only" in r.getMessage() and "injection" in r.getMessage()
        for r in caplog.records
    ), "GitNexus-only 并入时应打 info 日志（含 vuln 类名）"


@pytest.mark.asyncio
async def test_merge_preserves_gitnexus_only_reachability_false(tmp_path):
    """铁律: GitNexus-only 发现 externally_exploitable=False（内部可达）合并后保持 False，
    不被 verdict=vulnerable 覆写（dual_track_merger.py:52-57）。"""
    deliverables = _inter(tmp_path)
    (deliverables / "injection_gitnexus_queue.json").write_text(
        json.dumps({"vulnerabilities": [{
            "ID": "G1", "vulnerability_type": "injection",
            "externally_exploitable": False,  # 内部/跨服务可达
            "confidence": "high",
            "verdict": "vulnerable", "source": "q", "sink_call": "db.exec",
        }]})
    )

    await _run(tmp_path)

    out = json.loads(
        (deliverables / "injection_exploitation_queue.json").read_text())
    v = out["vulnerabilities"][0]
    assert v["merge_source"] == "gitnexus-only"
    assert v["externally_exploitable"] is False  # 保持，不被 verdict 覆写
