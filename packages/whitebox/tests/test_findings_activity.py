import json
import pytest
from pathlib import Path

from shannon_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue


@pytest.mark.asyncio
async def test_render_findings_activity_generates_findings(tmp_path):
    """Integration test: render_findings activity should produce findings MD from queue JSON."""
    from shannon_core.services.findings_renderer import FindingsRenderer

    repo = tmp_path / "my-repo"
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="query param", path="/search", sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(indent=2)
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "injection_findings.md")
    assert findings.exists()
    content = findings.read_text()
    assert "### INJECTION-001" in content
    assert "**Sink Call:** db.execute" in content
