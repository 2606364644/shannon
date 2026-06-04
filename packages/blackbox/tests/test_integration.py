import json
import subprocess
import pytest
from unittest.mock import patch

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES
from shannon_core.models.metrics import AgentMetrics
from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import ClaudeRunResult
from shannon_core.prompts.manager import PromptManager

from shannon_blackbox.agents.exploit_executor import ExploitExecutor
from shannon_blackbox.agents.recon_executor import ReconExecutor
from shannon_blackbox.services.exploitation_checker import ExploitationChecker
from shannon_blackbox.services.report_assembler import ReportAssembler


@pytest.fixture
def mock_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("# Test App")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    return repo, deliverables


@pytest.fixture
def prompts_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "recon-blackbox.txt").write_text("Recon {{WEB_URL}}")
    for vt in ["injection", "xss", "auth", "ssrf", "authz", "misconfig"]:
        (prompts / f"{vt}-exploit.txt").write_text(f"Exploit {vt} {{{{VULNERABILITY_ENTRIES}}}}")
    (prompts / "report-executive.txt").write_text("Report")
    return prompts


@pytest.mark.asyncio
async def test_full_blackbox_pipeline_independent(mock_repo, prompts_dir):
    repo, deliverables = mock_repo
    web_url = "https://example.com"

    mock_result = ClaudeRunResult(text="Done", success=True, duration=1000, turns=3, cost=0.01, model="test")

    with patch("shannon_core.agents.executor.run_claude_prompt", return_value=mock_result):
        pm = PromptManager(prompts_dir)
        executor = AgentExecutor(pm)

        recon = ReconExecutor(executor)
        (deliverables / "recon_deliverable.md").write_text("# Recon")
        recon_metrics = await recon.execute(
            workspace_path=repo,
            deliverables_path=deliverables,
            web_url=web_url,
        )
        assert isinstance(recon_metrics, AgentMetrics)

        for vt in ALL_VULN_CLASSES:
            queue_data = {"vulnerabilities": [
                {"ID": f"{vt.upper()}-001", "vulnerability_type": vt, "externally_exploitable": True, "confidence": "high"},
            ]}
            (deliverables / f"{vt}_exploitation_queue.json").write_text(json.dumps(queue_data))
            # Deliverable must exist for validation to pass (Level 4 symmetry check)
            (deliverables / f"{vt}_analysis_deliverable.md").write_text(f"# {vt} analysis")
            should = await ExploitationChecker.should_exploit(deliverables, vt)
            assert should is True

            agent_name = AgentName(f"{vt}-exploit")
            (deliverables / AGENTS[agent_name].deliverable_filename).write_text(f"# {vt} evidence")

            exploit = ExploitExecutor(executor)
            metrics = await exploit.execute(
                agent_name=agent_name,
                vuln_type=vt,
                workspace_path=repo,
                deliverables_path=deliverables,
                web_url=web_url,
            )
            assert isinstance(metrics, AgentMetrics)

        report_path = deliverables / "comprehensive_security_assessment_report.md"
        await ReportAssembler.assemble(deliverables, list(ALL_VULN_CLASSES), report_path)
        assert report_path.exists()
        content = report_path.read_text()
        for vt in ALL_VULN_CLASSES:
            assert vt in content.lower() or f"{vt} evidence" in content.lower()


@pytest.mark.asyncio
async def test_full_blackbox_pipeline_continuation(mock_repo, prompts_dir):
    repo, deliverables = mock_repo

    for vt in ["injection"]:
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high",
             "source_endpoint": "/api/search"},
        ]}
        (deliverables / f"{vt}_exploitation_queue.json").write_text(json.dumps(queue_data))
        (deliverables / f"{vt}_analysis_deliverable.md").write_text(f"# {vt} analysis")

    for vt in ["xss", "auth", "ssrf", "authz"]:
        (deliverables / f"{vt}_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": []}))

    has_whitebox = any(
        (deliverables / f"{vt}_exploitation_queue.json").exists()
        for vt in ALL_VULN_CLASSES
    )
    assert has_whitebox is True

    should_inject = await ExploitationChecker.should_exploit(deliverables, "injection")
    assert should_inject is True
    should_xss = await ExploitationChecker.should_exploit(deliverables, "xss")
    assert should_xss is False
