from contextlib import asynccontextmanager

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


async def test_assemble_report_writes_comprehensive_report(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "auth_analysis_deliverable.md").write_text(
        "# 认证分析报告\nAUTH-VULN-01", encoding="utf-8")
    monkeypatch.setattr(act, "_get_paths", lambda inp: (tmp_path, deliverables, tmp_path))

    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.assemble_report(ActivityInput(repo_path=str(tmp_path)))
    finally:
        clear_audit_session()

    report = deliverables / "comprehensive_security_assessment_report.md"
    assert report.exists()
    assert "认证分析报告" in report.read_text(encoding="utf-8")
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
    """assemble_report 移除了攻击链追加；攻击链章节由 inject_attack_chains 负责。"""
    from supernova_whitebox.pipeline import activities
    import json

    deliverables = tmp_path / "whitebox"
    deliverables.mkdir()
    (deliverables / "auth_findings.md").write_text(
        "## Authentication Vulnerabilities\n\n### AUTH-VULN-01\n", encoding="utf-8")
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

    report = (deliverables / "comprehensive_security_assessment_report.md").read_text(encoding="utf-8")
    assert "## 攻击链" not in report  # assemble 不再碰攻击链


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
