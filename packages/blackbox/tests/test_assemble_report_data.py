"""T7 黑盒管线接线：assemble_report 产 ``blackbox/report_data.json``（spec §6.1）。

确定性结构化一步（verdicts → report_data.json），追加在 assemble_report 内
（不新增 activity）；non-fatal：失败 warning，不阻塞现有 md 链路。
"""
import json

import pytest

from supernova_blackbox.pipeline import activities
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _setup(tmp_path, verdicts: dict | None = None, session: dict | None = None):
    """铺 scan 目录（deliverables/blackbox/{vc}_exploit_verdicts.json + 可选 session.json）。"""
    scan_dir = tmp_path / "bb-scan-1"
    bb = scan_dir / "deliverables" / "blackbox"
    bb.mkdir(parents=True)
    if verdicts is not None:
        (bb / "injection_exploit_verdicts.json").write_text(
            json.dumps(verdicts, ensure_ascii=False), encoding="utf-8")
    if session is not None:
        (scan_dir / "session.json").write_text(
            json.dumps(session, ensure_ascii=False), encoding="utf-8")
    inp = BlackboxActivityInput(
        web_url="https://example.com",
        workspace_path=str(scan_dir),
        deliverables_subdir="deliverables",
    )
    return bb, inp


_VERDICTS = {
    "vuln_class": "injection",
    "accepted_ids": ["INJ-VULN-01"],
    "verdicts": [{
        "vulnerability_id": "INJ-VULN-01",
        "status": "exploited",
        "severity": "critical",
        "impact": "RCE",
        "exploitation_steps": [
            "1. Authenticate as any valid user.",
            "2. curl 'http://target:4000/contributions' -X POST -d 'preTax=@@@'",
        ],
        "proof_of_impact": "HTTP 500 with SyntaxError.",
    }],
    "rejected": [],
}


@pytest.mark.asyncio
async def test_assemble_report_writes_report_data_json(tmp_path):
    """verdicts 齐全 → blackbox/report_data.json 落盘，md 链路照常。"""
    bb, inp = _setup(tmp_path, verdicts=_VERDICTS)

    await activities.assemble_report(inp)

    out = bb / "report_data.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["scan"]["track"] == "blackbox"
    # 无 session.json → id 回落 workspace 目录名
    assert data["scan"]["id"] == "bb-scan-1"
    assert len(data["vulnerabilities"]) == 1
    v = data["vulnerabilities"][0]
    assert v["id"] == "INJ-VULN-01"
    assert v["evidence"]["verification"] == "dynamic"
    assert v["poc"]["request"]["method"] == "POST"
    assert data["stats"]["by_severity"] == {"critical": 1}
    assert data["executive_summary"] is None      # T5 后续接
    # md 链路不受影响
    assert (bb / "comprehensive_security_assessment_report.md").exists()


@pytest.mark.asyncio
async def test_assemble_report_scan_meta_from_session(tmp_path):
    """session.json 在 → scan meta 从中取（id/date/cost/currency/model）。"""
    session = {
        "session": {"id": "NodeGoat-1~1", "createdAt": "2026-08-12T04:52:07.106Z"},
        "metrics": {
            "total_duration_ms": 1234,
            "total_cost_usd": 1.5,
            "cost_currency": "CNY",
            "agents": {"xss-exploit": {"model": "glm-4.6"}},
        },
    }
    bb, inp = _setup(tmp_path, verdicts=_VERDICTS, session=session)

    await activities.assemble_report(inp)

    data = json.loads((bb / "report_data.json").read_text(encoding="utf-8"))
    assert data["scan"]["id"] == "NodeGoat-1~1"
    assert data["scan"]["date"] == "2026-08-12T04:52:07.106Z"
    assert data["scan"]["duration_ms"] == 1234
    assert data["scan"]["cost"] == 1.5
    assert data["scan"]["currency"] == "CNY"
    assert data["scan"]["model"] == "glm-4.6"


@pytest.mark.asyncio
async def test_assemble_report_data_failure_nonfatal(tmp_path, monkeypatch, caplog):
    """report_data 组装失败 → warning 不阻塞：md 照常产出、activity 不抛。"""
    bb, inp = _setup(tmp_path, verdicts=_VERDICTS)

    import supernova_core.services.report_data_blackbox as bb_builder

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bb_builder, "write_blackbox_report_data", _boom)

    import logging
    with caplog.at_level(logging.WARNING,
                         logger="supernova_blackbox.pipeline.activities"):
        await activities.assemble_report(inp)   # 不抛

    assert not (bb / "report_data.json").exists()
    assert (bb / "comprehensive_security_assessment_report.md").exists()
    assert any("report_data" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_assemble_report_no_verdicts_no_report_data(tmp_path):
    """无 verdicts → 空报告（0 漏洞）也落 report_data.json，md 链路照常。"""
    bb, inp = _setup(tmp_path)

    await activities.assemble_report(inp)

    data = json.loads((bb / "report_data.json").read_text(encoding="utf-8"))
    assert data["vulnerabilities"] == []
    assert data["stats"]["by_type"] == {}
