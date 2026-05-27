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
