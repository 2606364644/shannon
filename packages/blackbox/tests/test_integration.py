import json
import subprocess
import pytest

from supernova_core.models.agents import ALL_VULN_CLASSES
from supernova_blackbox.services.exploitation_checker import ExploitationChecker


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
    deliverables = tmp_path / "workspaces" / "bb-session" / "deliverables"
    deliverables.mkdir(parents=True)
    return repo, deliverables


@pytest.fixture
def prompts_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for vt in ["injection", "xss", "auth", "ssrf", "authz"]:
        (prompts / f"{vt}-exploit.txt").write_text(f"Exploit {vt} {{{{VULNERABILITY_ENTRIES}}}}")
    (prompts / "report-executive.txt").write_text("Report")
    return prompts


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
