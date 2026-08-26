from contextlib import asynccontextmanager

import pytest

import supernova_whitebox.pipeline.activities as act
from supernova_whitebox.pipeline.shared import ActivityInput
from supernova_whitebox.audit.session_registry import (
    set_audit_session, clear_audit_session,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, str]] = []  # (name, phase, event)

    async def log_step(self, name: str, phase: str, event: str, **kw) -> None:
        self.steps.append((name, phase, event))

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        await self.log_step(name, phase, "start")
        try:
            yield
        except Exception:
            await self.log_step(name, phase, "complete", error="x")
            raise
        await self.log_step(name, phase, "complete")


async def test_assemble_report_writes_rd_and_findings_not_md(tmp_path, monkeypatch):
    """spec 2026-08-26-report-single-source-rendering §3：assemble 产
    report_data.json（根交付物）+ 分项 findings.md（从 rd 单点渲染）；
    不再产 comprehensive md（移至 export activity）。"""
    import json

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "intermediate").mkdir()
    (deliverables / "intermediate" / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{
            "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
            "externally_exploitable": True, "confidence": "high",
            "severity": "high", "title": "存储型 XSS：POST /memos",
            "report_poc": {"curl": "curl -X POST http://t/memos",
                           "request": {"method": "POST", "url": "http://t/memos"}},
        }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    # rd.json：根交付物（单源 SSOT）
    data = json.loads(
        (deliverables / "report_data.json").read_text(encoding="utf-8"))
    assert data["vulnerabilities"][0]["id"] == "XSS-VULN-01"
    # findings.md：从 rd 单点渲染（同一渲染函数，含卡与 POC 节）
    findings = (deliverables / "xss_findings.md").read_text(encoding="utf-8")
    assert "### XSS-VULN-01" in findings
    assert "```bash" in findings  # POC 写回已在 queue → 卡原生 POC 节
    # comprehensive md 不再由 assemble 产
    assert not (deliverables / "comprehensive_security_assessment_report.md").exists()
    events = [(n, e) for (n, _ph, e) in rec.steps]
    assert ("assemble-report", "start") in events
    assert ("assemble-report", "complete") in events


async def test_inject_attack_chains_appends_section(tmp_path, monkeypatch) -> None:
    """report-executive 之后注入：attack_chains.json → ## 攻击链 章节追加到最终报告。"""
    import json
    from supernova_whitebox.pipeline import activities

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("# 安全评估报告\n\n## 执行摘要\n\n正文...\n", encoding="utf-8")
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [
            {"id": "llm-chain-1", "name": "enum→idor", "vuln_type": "authz",
             "severity": "critical", "confidence": "high",
             "steps": [{"order": 1, "endpoint": "/api/x", "method": "GET", "description": "d"}]},
        ]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(activities, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))
    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))

    content = report.read_text(encoding="utf-8")
    assert "## 攻击链（多步利用路径）" in content
    assert "### llm-chain-1: enum→idor" in content
    # 原文保留
    assert "## 执行摘要" in content


async def test_inject_attack_chains_idempotent(tmp_path, monkeypatch) -> None:
    from supernova_whitebox.pipeline import activities
    import json

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("body\n", encoding="utf-8")
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [{"id": "llm-chain-1", "name": "n"}]}), encoding="utf-8",
    )
    monkeypatch.setattr(activities, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))
    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))
    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))  # 再跑一次

    content = report.read_text(encoding="utf-8")
    assert content.count("## 攻击链（多步利用路径）") == 1


async def test_inject_attack_chains_noop_when_missing(tmp_path, monkeypatch) -> None:
    from supernova_whitebox.pipeline import activities
    import json

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    report = deliverables / "comprehensive_security_assessment_report.md"
    report.write_text("body\n", encoding="utf-8")
    # 无 attack_chains.json
    monkeypatch.setattr(activities, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))
    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))
    assert report.read_text(encoding="utf-8") == "body\n"  # 不变

    # 空 chains
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": []}), encoding="utf-8")
    await activities.inject_attack_chains(ActivityInput(repo_path=str(tmp_path)))
    assert report.read_text(encoding="utf-8") == "body\n"


async def test_assemble_report_no_longer_appends_attack_chains(tmp_path, monkeypatch) -> None:
    """assemble 不产 comprehensive md——攻击链由 export activity 从 rd.attack_chains
    渲染（spec §3 退役清单：inject_attack_chains md 注入退役）。"""
    from supernova_whitebox.pipeline import activities
    import json

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    (deliverables / "intermediate").mkdir()
    (deliverables / "intermediate" / "auth_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{
            "ID": "AUTH-VULN-01", "vulnerability_type": "Auth",
            "externally_exploitable": True, "confidence": "high",
            "severity": "medium", "title": "t",
        }]}), encoding="utf-8")
    (deliverables / "attack_chains.json").write_text(
        json.dumps({"chains": [{"id": "llm-chain-1", "name": "n"}]}), encoding="utf-8",
    )
    monkeypatch.setattr(activities, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await activities.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    # assemble 阶段不产任何 comprehensive md（攻击链章节亦无从注入）
    assert not (deliverables / "comprehensive_security_assessment_report.md").exists()


async def test_render_attack_chains_zh_labels(tmp_path):
    """render_attack_chains 默认 zh：中文标签。"""
    import json
    from supernova_core.services.report_assembler import ReportAssembler

    d = tmp_path / "deliverables"
    d.mkdir()
    (d / "attack_chains.json").write_text(json.dumps({"chains": [
        {"id": "c1", "name": "n", "vuln_type": "xss", "severity": "high",
         "confidence": "confirmed",
         "steps": [{"order": 1, "endpoint": "/x", "method": "GET", "description": "d"}]},
    ]}), encoding="utf-8")
    out = await ReportAssembler.render_attack_chains(d)
    assert "## 攻击链（多步利用路径）" in out
    assert "- **类型:**" in out
    assert "- **步骤:**" in out


async def test_render_attack_chains_en_labels(tmp_path, monkeypatch):
    """en 模式：英文标签。"""
    import json
    from supernova_core.services.report_assembler import ReportAssembler

    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    d = tmp_path / "deliverables"
    d.mkdir()
    (d / "attack_chains.json").write_text(json.dumps({"chains": [
        {"id": "c1", "name": "n", "vuln_type": "xss", "severity": "high",
         "confidence": "confirmed",
         "steps": [{"order": 1, "endpoint": "/x", "method": "GET", "description": "d"}]},
    ]}), encoding="utf-8")
    out = await ReportAssembler.render_attack_chains(d)
    assert "## Attack Chains" in out
    assert "- **Type:**" in out
    assert "- **Steps:**" in out
    assert "## 攻击链" not in out


async def test_inject_model_info_zh_anchor(tmp_path):
    """中文锚点（## 执行摘要 / - 评估日期:）后注入中文 Model 行。"""
    import json
    from supernova_core.services.report_assembler import ReportAssembler

    report = tmp_path / "report.md"
    report.write_text("# 安全评估报告\n\n## 执行摘要\n\n- 评估日期: 2026-07-31\n\n正文\n",
                      encoding="utf-8")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"metrics": {"agents": {"a": {"model": "glm-5.2"}}}}),
                       encoding="utf-8")
    await ReportAssembler.inject_model_info(report, session)
    assert "- **模型:** glm-5.2" in report.read_text(encoding="utf-8")


async def test_inject_model_info_en_anchor(tmp_path, monkeypatch):
    """en 模式：英文锚点 + 英文 Model 标签。"""
    import json
    from supernova_core.services.report_assembler import ReportAssembler

    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    report = tmp_path / "report.md"
    report.write_text(
        "# Security Assessment Report\n\n## Executive Summary\n\n- Assessment Date: 2026-07-31\n\nbody\n",
        encoding="utf-8")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"metrics": {"agents": {"a": {"model": "glm-5.2"}}}}),
                       encoding="utf-8")
    await ReportAssembler.inject_model_info(report, session)
    assert "- **Model:** glm-5.2" in report.read_text(encoding="utf-8")


async def test_assemble_report_writes_report_data(tmp_path, monkeypatch):
    """T1（spec 2026-08-26-report-generation-agent §3）：assemble 产
    report_data.json（SSOT）——单源化后是根交付物（md/前端都吃它）。"""
    import json

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "intermediate").mkdir()
    (deliverables / "intermediate" / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{
            "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
            "externally_exploitable": True, "confidence": "high",
            "severity": "high", "title": "t",
        }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report_data = deliverables / "report_data.json"
    assert report_data.exists()
    data = json.loads(report_data.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["vulnerabilities"][0]["id"] == "XSS-VULN-01"
    assert data["stats"]["by_type"]["xss"]["count"] == 1


async def test_assemble_report_report_data_failure_is_fatal(tmp_path, monkeypatch):
    """单源化语义翻转（spec §3）：rd.json 是根交付物——组装失败 fatal
    （md/前端都吃它，失败=部署问题显式暴露；不再是「md 主链路 non-fatal」）。"""
    import supernova_core.services.report_data_builder as builder

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    async def _boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(builder, "build_report_data", _boom)

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        with pytest.raises(Exception):
            await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    assert not (deliverables / "report_data.json").exists()
