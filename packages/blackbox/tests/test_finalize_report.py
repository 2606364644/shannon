import json
import pytest
from pathlib import Path

from shannon_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue


@pytest.mark.asyncio
async def test_assemble_report_activity_generates_findings(tmp_path):
    """assemble_report should run FindingsRenderer before assembling."""
    from shannon_core.services.findings_renderer import FindingsRenderer

    deliverables = tmp_path / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="query", path="/search", sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    assert (deliverables / "injection_findings.md").exists()
    content = (deliverables / "injection_findings.md").read_text()
    assert "INJECTION-001" in content


@pytest.mark.asyncio
async def test_model_injection_in_finalize(tmp_path):
    """finalize_report should inject model info from session.json."""
    from shannon_blackbox.services.report_assembler import ReportAssembler

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    deliverables = workspace / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)

    report_path = deliverables / "comprehensive_security_assessment_report.md"
    report_path.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-03\n")

    session_path = workspace / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6"}}}
    }))

    await ReportAssembler.inject_model_info(report_path, session_path)

    content = report_path.read_text()
    assert "- **Model:** claude-sonnet-4-6" in content


@pytest.mark.asyncio
async def test_noop_output_provider(tmp_path):
    """Output provider should be NoOp by default (no side effects)."""
    from shannon_core.interfaces.report_output_provider import NoOpReportOutputProvider

    provider = NoOpReportOutputProvider()
    result = await provider.generate(tmp_path / "report.md", tmp_path / "deliverables")
    assert result["output_path"] is None
