import json

import pytest

from shannon_blackbox.services.report_assembler import ReportAssembler


@pytest.mark.asyncio
async def test_assemble_prefers_evidence_over_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# Injection Evidence")
    (deliverables / "injection_findings.md").write_text("# Injection Findings")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    content = report_path.read_text()
    assert "Injection Evidence" in content
    assert "Injection Findings" not in content


@pytest.mark.asyncio
async def test_assemble_falls_back_to_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_findings.md").write_text("# XSS Findings")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["xss"], report_path)
    content = report_path.read_text()
    assert "XSS Findings" in content


@pytest.mark.asyncio
async def test_assemble_multiple_vuln_classes(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# Injection")
    (deliverables / "xss_exploitation_evidence.md").write_text("# XSS")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection", "xss"], report_path)
    content = report_path.read_text()
    assert "Injection" in content
    assert "XSS" in content


@pytest.mark.asyncio
async def test_assemble_skips_missing_classes(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["auth", "authz"], report_path)
    assert report_path.exists()
    content = report_path.read_text()
    assert content.strip() == ""


@pytest.mark.asyncio
async def test_assemble_with_no_vuln_classes(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, [], report_path)
    assert report_path.exists()


@pytest.mark.asyncio
async def test_assemble_falls_back_to_analysis_deliverable(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_analysis_deliverable.md").write_text("# Injection Analysis")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    content = report_path.read_text()
    assert "Injection Analysis" in content


@pytest.mark.asyncio
async def test_assemble_priority_evidence_over_analysis(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_evidence.md").write_text("# Evidence")
    (deliverables / "injection_analysis_deliverable.md").write_text("# Analysis")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["injection"], report_path)
    content = report_path.read_text()
    assert "Evidence" in content
    assert "Analysis" not in content


@pytest.mark.asyncio
async def test_assemble_priority_findings_over_analysis(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "xss_findings.md").write_text("# Findings")
    (deliverables / "xss_analysis_deliverable.md").write_text("# Analysis")
    report_path = tmp_path / "report.md"

    await ReportAssembler.assemble(deliverables, ["xss"], report_path)
    content = report_path.read_text()
    assert "Findings" in content
    assert "Analysis" not in content


@pytest.mark.asyncio
async def test_inject_model_info_after_date_line(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n\nSome content")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6", "success": True}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "- **Model:** claude-sonnet-4-6" in content
    # Model line appears after Assessment Date
    lines = content.split("\n")
    date_idx = next(i for i, l in enumerate(lines) if "Assessment Date" in l)
    model_idx = next(i for i, l in enumerate(lines) if "**Model:**" in l)
    assert model_idx == date_idx + 1


@pytest.mark.asyncio
async def test_inject_model_info_fallback_to_executive_summary(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\nSome content without date")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-opus-4-8", "success": True}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "- **Model:** claude-opus-4-8" in content


@pytest.mark.asyncio
async def test_inject_model_info_multiple_models(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {
            "agents": {
                "recon": {"model": "claude-sonnet-4-6"},
                "injection-vuln": {"model": "claude-opus-4-8"},
            }
        }
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "claude-opus-4-8" in content
    assert "claude-sonnet-4-6" in content


@pytest.mark.asyncio
async def test_inject_model_info_skips_when_no_session(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")
    session_path = tmp_path / "nonexistent_session.json"

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "**Model:**" not in content


@pytest.mark.asyncio
async def test_inject_model_info_skips_when_no_models(tmp_path):
    report_path = tmp_path / "report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({"metrics": {"agents": {"recon": {"success": True}}}}))

    await ReportAssembler.inject_model_info(report_path, session_path)
    content = report_path.read_text()
    assert "**Model:**" not in content


@pytest.mark.asyncio
async def test_inject_model_info_skips_when_no_report(tmp_path):
    report_path = tmp_path / "nonexistent_report.md"
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6"}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)
    assert not report_path.exists()
