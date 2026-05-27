import json
import subprocess
import pytest
from unittest.mock import AsyncMock

from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics
from shannon_blackbox.agents.exploit_executor import ExploitExecutor
from shannon_blackbox.agents.recon_executor import ReconExecutor


@pytest.fixture
def mock_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    return repo, deliverables


@pytest.mark.asyncio
async def test_exploit_executor_reads_queue(mock_repo):
    repo, deliverables = mock_repo
    queue_data = {"vulnerabilities": [
        {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
         "externally_exploitable": True, "confidence": "high",
         "source_endpoint": "/api/search", "sink_call": "db.execute"},
    ]}
    (deliverables / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
    (deliverables / "injection_exploitation_evidence.md").write_text("# Evidence")

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1000, cost_usd=0.01, num_turns=3)

    ex = ExploitExecutor(mock_executor)
    metrics = await ex.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        vuln_type="injection",
        workspace_path=repo,
        deliverables_path=deliverables,
        web_url="https://example.com",
    )
    assert isinstance(metrics, AgentMetrics)
    mock_executor.execute.assert_called_once()
    call_kwargs = mock_executor.execute.call_args
    assert call_kwargs.kwargs["agent_name"] == AgentName.INJECTION_EXPLOIT
    assert "vulnerability_entries" in call_kwargs.kwargs.get("prompt_variables", {})


@pytest.mark.asyncio
async def test_recon_executor_delegates(mock_repo):
    repo, deliverables = mock_repo
    (deliverables / "recon_deliverable.md").write_text("# Recon")

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=2000, cost_usd=0.02, num_turns=5)

    recon = ReconExecutor(mock_executor)
    metrics = await recon.execute(
        workspace_path=repo,
        deliverables_path=deliverables,
        web_url="https://example.com",
    )
    assert isinstance(metrics, AgentMetrics)
    mock_executor.execute.assert_called_once_with(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        config_path=None,
        api_key=None,
        pipeline_testing=False,
    )
