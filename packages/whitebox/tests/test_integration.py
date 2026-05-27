import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.metrics import AgentMetrics
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.session import SessionManager
from shannon_whitebox.agents.runner import ClaudeRunResult


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
    return repo


@pytest.fixture
def prompts_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "pre-recon-code.txt").write_text("Analyze {{REPO_PATH}} for {{WEB_URL}}")
    (prompts / "recon.txt").write_text("Recon {{WEB_URL}}")
    for vt in ["injection", "xss", "auth", "ssrf", "authz"]:
        (prompts / f"vuln-{vt}.txt").write_text(f"Find {vt} vulns in {{REPO_PATH}}")
    return prompts


@pytest.mark.asyncio
async def test_full_pipeline_mocked(mock_repo, prompts_dir):
    mock_results: dict[str, ClaudeRunResult] = {}
    for agent in [AgentName.PRE_RECON, AgentName.RECON,
                   AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                   AgentName.AUTH_VULN, AgentName.SSRF_VULN,
                   AgentName.AUTHZ_VULN]:
        mock_results[agent.value] = ClaudeRunResult(
            text="Analysis complete",
            success=True,
            duration=1000,
            turns=5,
            cost=0.01,
            model="test-model",
        )

    call_count = 0

    async def mock_run_claude(**kwargs):
        nonlocal call_count
        call_count += 1
        return mock_results.get("pre-recon", ClaudeRunResult(success=True, text="ok"))

    with patch("shannon_whitebox.agents.executor.run_claude_prompt", side_effect=mock_run_claude):
        deliverables = mock_repo / ".shannon" / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)

        for agent_name in [AgentName.PRE_RECON, AgentName.RECON,
                            AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                            AgentName.AUTH_VULN, AgentName.SSRF_VULN,
                            AgentName.AUTHZ_VULN]:
            filename = AGENTS[agent_name].deliverable_filename
            (deliverables / filename).write_text(f"# {agent_name.value} result")

            pm = PromptManager(prompts_dir)
            executor = AgentExecutor(pm)
            metrics = await executor.execute(
                agent_name=agent_name,
                repo_path=str(mock_repo),
                web_url="https://example.com",
                deliverables_path=str(deliverables),
            )
            assert isinstance(metrics, AgentMetrics)
            assert metrics.duration_ms >= 0

    assert call_count == 7

    files = list(deliverables.glob("*.md"))
    assert len(files) == 7
