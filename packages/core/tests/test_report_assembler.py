import json
import pytest
from pathlib import Path
from supernova_core.services.report_assembler import ReportAssembler


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
async def test_inject_model_info_inserts_after_assessment_date(tmp_path, monkeypatch):
    """inject_model_info 在 Assessment Date 行后插入 Model 行。

    断言基于英文标签（i18n 前行为），显式钉 en——默认 zh 下该断言必挂
    （对齐 blackbox test_finalize_report.py 的 _en_lang_default 先例）。
    """
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    report = tmp_path / "report.md"
    report.write_text("## Executive Summary\n- Assessment Date: 2026-06-22\n正文", encoding="utf-8")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"metrics": {"agents": {"a": {"model": "glm-latest"}}}}), encoding="utf-8")
    await ReportAssembler.inject_model_info(report, session)
    content = report.read_text(encoding="utf-8")
    assert "- **Model:** glm-latest" in content
    assert content.index("Assessment Date") < content.index("**Model:**")


@pytest.mark.asyncio
async def test_render_attack_chains_reads_intermediate(tmp_path):
    """tiering 回归：attack_chains.json 落 intermediate/（assembly_v2 写侧）->
    render_attack_chains 必须读到（曾只读平铺 -> 攻击链章节静默缺失）。"""
    deliverables = tmp_path / "deliverables"
    (deliverables / "intermediate").mkdir(parents=True)
    (deliverables / "intermediate" / "attack_chains.json").write_text(json.dumps({
        "chains": [{
            "id": "CHAIN-01",
            "name": "auth_bypass_to_data_access",
            "vuln_type": "auth",
            "severity": "critical",
            "confidence": "high",
            "steps": [{"order": 1, "endpoint": "/api/users/1", "method": "GET",
                       "description": "fetch other user"}],
        }],
    }), encoding="utf-8")

    md = await ReportAssembler.render_attack_chains(deliverables)

    assert md != ""
    assert "CHAIN-01" in md
    assert "/api/users/1" in md
