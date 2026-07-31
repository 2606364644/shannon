import json
import pytest
from pathlib import Path

from supernova_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue


@pytest.fixture(autouse=True)
def _en_lang_default(monkeypatch):
    """断言基于英文渲染（i18n 前行为）；默认 en。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")


@pytest.mark.asyncio
async def test_render_findings_activity_generates_findings(tmp_path):
    """Integration test: render_findings activity should produce findings MD from queue JSON."""
    from supernova_core.services.findings_renderer import FindingsRenderer

    repo = tmp_path / "my-repo"
    deliverables = tmp_path / "workspaces" / "wb-session" / "deliverables"
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
