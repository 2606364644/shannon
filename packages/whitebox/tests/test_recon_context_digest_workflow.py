"""Workflow wiring and worker registration for the shared recon digest."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _workflow_src() -> str:
    return (ROOT / "packages/whitebox/src/supernova_whitebox/pipeline/workflows.py").read_text()


def test_recon_digest_runs_before_vuln_agents():
    src = _workflow_src()
    digest = src.find("activities.run_recon_context_digest")
    vuln = src.find("activities.run_vuln_agent")
    assert digest != -1 and vuln != -1
    assert digest < vuln


def test_recon_digest_registered_on_both_workers():
    for rel in (
        "packages/whitebox/src/supernova_whitebox/worker.py",
        "packages/worker/src/supernova_worker/runner.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert src.count("run_recon_context_digest") >= 2, (
            f"run_recon_context_digest is not imported and registered in {rel}"
        )


def test_digest_need_skips_fully_completed_resume_and_degradable_only_mode():
    from supernova_core.models.agents import AgentName
    from supernova_whitebox.pipeline.workflows import _needs_recon_context_digest

    selected = ["injection", "xss", "ssrf", "authz", "auth"]
    completed = [AgentName(f"{v}-vuln").value for v in selected]
    assert _needs_recon_context_digest(selected, completed, True) is False

    # LLM track disabled: completed degradable agents alone need no digest.
    degradable = ["injection", "xss", "ssrf"]
    degradable_completed = [AgentName(f"{v}-vuln").value for v in degradable]
    assert _needs_recon_context_digest(degradable, degradable_completed, False) is False

    # authz/auth remain LLM-track agents when the taint LLM track is disabled.
    assert _needs_recon_context_digest(["authz"], [], False) is True
