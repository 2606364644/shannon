import json
import pytest
from pathlib import Path
from shannon_core.services.report_assembler import ReportAssembler


@pytest.mark.asyncio
async def test_assemble_uses_analysis_deliverable_fallback(tmp_path):
    """white-box 产物(*_analysis_deliverable.md)应被 assemble 读取。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text(
        "# 认证分析报告\n\n中文内容 AUTH-VULN-01", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth"], report_path)
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "认证分析报告" in content
    assert "AUTH-VULN-01" in content


@pytest.mark.asyncio
async def test_assemble_prefers_evidence_over_analysis(tmp_path):
    """exploit evidence 优先于 analysis_deliverable。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_exploitation_evidence.md").write_text("EVIDENCE", encoding="utf-8")
    (deliverables / "auth_analysis_deliverable.md").write_text("ANALYSIS", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth"], report_path)
    content = report_path.read_text(encoding="utf-8")
    assert "EVIDENCE" in content
    assert "ANALYSIS" not in content


@pytest.mark.asyncio
async def test_assemble_joins_multiple_sections_with_separator(tmp_path):
    """多 class 用 \n\n---\n\n 拼接,顺序遵循 vuln_classes。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text("AUTH", encoding="utf-8")
    (deliverables / "injection_analysis_deliverable.md").write_text("INJ", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth", "injection"], report_path)
    assert report_path.read_text(encoding="utf-8") == "AUTH\n\n---\n\nINJ"


@pytest.mark.asyncio
async def test_assemble_skips_missing_classes(tmp_path):
    """某 class 无任何产物文件时跳过,不报错。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "auth_analysis_deliverable.md").write_text("AUTH", encoding="utf-8")
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, ["auth", "injection"], report_path)
    assert report_path.read_text(encoding="utf-8") == "AUTH"


@pytest.mark.asyncio
async def test_inject_model_info_inserts_after_assessment_date(tmp_path):
    """inject_model_info 在 Assessment Date 行后插入 Model 行。"""
    report = tmp_path / "report.md"
    report.write_text("## Executive Summary\n- Assessment Date: 2026-06-22\n正文", encoding="utf-8")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"metrics": {"agents": {"a": {"model": "glm-latest"}}}}), encoding="utf-8")
    await ReportAssembler.inject_model_info(report, session)
    content = report.read_text(encoding="utf-8")
    assert "- **Model:** glm-latest" in content
    assert content.index("Assessment Date") < content.index("**Model:**")
