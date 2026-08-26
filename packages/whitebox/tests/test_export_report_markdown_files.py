# packages/whitebox/tests/test_export_report_markdown_files.py
"""export_report_markdown_files activity（spec 2026-08-26-report-single-source-rendering
§3/§6）：终版 report_data.json → comprehensive md + poc_collection.md 双产物 +
同构校验（mismatch 写 qa.checks 不静默；rd 缺失/损坏 fatal）。
"""
import json
from contextlib import asynccontextmanager

import pytest

from supernova_whitebox.pipeline import activities
from supernova_whitebox.pipeline.shared import ActivityInput
from supernova_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    @asynccontextmanager
    async def track_step(self, phase, name, intent=None):
        yield


class _FakeInput:
    def __init__(self, tmp_path):
        self.agent_name = None
        self.web_url = ""
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.workspace_path = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None
        self.provider_config = None
        self.vuln_classes = None


def _wb(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    (d / "intermediate").mkdir(parents=True, exist_ok=True)
    return d


def _write_rd(d, vulnerabilities, quick_reference=None):
    """极简 report_data.json fixture（export activity 只吃 rd，不读 queue）。"""
    payload = {
        "schema_version": 1,
        "scan": {"id": "s1", "track": "whitebox"},
        "stats": {"by_type": {}, "by_severity": {}},
        "vulnerabilities": vulnerabilities,
        "quick_reference": quick_reference or [],
    }
    (d / "report_data.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _card(vuln_id="XSS-VULN-01", **over):
    card = {
        "id": vuln_id, "type": "xss", "title": "存储型 XSS",
        "severity": "high", "confidence": "high",
        "narrative": {"cause": "c", "impact": "i", "remediation": "r"},
        "problem_points": [{"location": "app.js:1", "description": "d",
                            "snippet": "x"}],
        "endpoints": [{"method": "POST", "path": "/memos",
                       "params": ["memo"], "sink_location": "app.js:1"}],
        "poc": {"curl": "curl -X POST http://t/memos",
                "raw_http": "POST /memos HTTP/1.1",
                "request": {"method": "POST", "url": "http://t/memos"}},
        "raw": {"ID": vuln_id, "title": "存储型 XSS", "severity": "high",
                "confidence": "high", "verification": "static_analysis",
                "notes": "c", "impact": "i", "remediation": "r"},
    }
    card.update(over)
    return card


async def test_export_writes_both_mds(tmp_path, monkeypatch):
    """rd.json → comprehensive md（含卡）+ poc_collection.md（PoC 单源）。"""
    d = _wb(tmp_path)
    _write_rd(d, [_card()])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.export_report_markdown_files(_FakeInput(tmp_path))
    finally:
        clear_audit_session()

    md = (d / "comprehensive_security_assessment_report.md").read_text(
        encoding="utf-8")
    assert "### XSS-VULN-01" in md
    poc_md = (d / "exploitable_poc_collection.md").read_text(encoding="utf-8")
    assert "XSS-VULN-01" in poc_md
    assert "```bash" in poc_md and "```http" in poc_md
    # 同构完好 → 不新增 qa 缺口
    data = json.loads((d / "report_data.json").read_text(encoding="utf-8"))
    assert data.get("qa") is None or all(
        c["check"] not in ("md_json_isomorphic", "quick_reference_isomorphic")
        for c in data["qa"].get("checks", []))


async def test_export_isomorphic_mismatch_records_qa(tmp_path, monkeypatch):
    """md 卡数 != rd 卡数 → md_json_isomorphic 写 qa.checks（不抛、不静默）。"""
    d = _wb(tmp_path)
    _write_rd(d, [_card(), _card("XSS-VULN-02")])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    # 换掉导出函数：只渲染 1 卡（模拟导出漂移），产物照落盘
    import supernova_core.services.report_markdown_exporter as exporter

    _orig_export = exporter.export_report_markdown

    def _one_card_md(rd):
        return _orig_export(rd).replace(
            "### XSS-VULN-02", "### XSS-VULN-XX")  # 第二卡 ID 被吞

    monkeypatch.setattr(exporter, "export_report_markdown", _one_card_md)
    set_audit_session(_RecordingSession())
    try:
        await activities.export_report_markdown_files(_FakeInput(tmp_path))
    finally:
        clear_audit_session()

    data = json.loads((d / "report_data.json").read_text(encoding="utf-8"))
    iso = next(c for c in data["qa"]["checks"]
               if c["check"] == "md_json_isomorphic")
    assert "XSS-VULN-02" in iso["failed_ids"]
    assert data["qa"]["passed"] is False


async def test_export_quick_reference_mismatch_records_qa(tmp_path, monkeypatch):
    """速查表行 id 不在卡集合 → quick_reference_isomorphic 缺口。"""
    d = _wb(tmp_path)
    _write_rd(d, [_card()], quick_reference=[
        {"id": "XSS-VULN-01", "title": "存储型 XSS"},
        {"id": "XSS-GHOST-99", "title": "幽灵行"},
    ])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.export_report_markdown_files(_FakeInput(tmp_path))
    finally:
        clear_audit_session()

    data = json.loads((d / "report_data.json").read_text(encoding="utf-8"))
    qr = next(c for c in data["qa"]["checks"]
              if c["check"] == "quick_reference_isomorphic")
    assert "XSS-GHOST-99" in qr["failed_ids"]


async def test_export_missing_rd_is_fatal(tmp_path, monkeypatch):
    """rd.json 缺失 → 异常抛出（fatal：确定性导出失败=部署问题显式暴露）。"""
    d = _wb(tmp_path)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        with pytest.raises(Exception):
            await activities.export_report_markdown_files(_FakeInput(tmp_path))
    finally:
        clear_audit_session()
