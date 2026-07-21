"""Task 5: 黑盒 report 链路由测试。

`assemble_report` / `finalize_report` activity 必须把 report 写到
`deliverables/blackbox/comprehensive_security_assessment_report.md`，不再覆盖白盒报告；
findings 落 `blackbox/`，queue 从 `whitebox/` 读（Task 2/4 路由）。
"""
import json
from pathlib import Path

import pytest

from supernova_core.models.queue_schemas import InjectionVulnerability, VulnerabilityQueue
from supernova_core.utils.paths import BLACKBOX_SUBDIR, WHITEBOX_SUBDIR
from supernova_blackbox.pipeline.activities import assemble_report, finalize_report
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _make_input(repo: Path, workspace_name: str) -> BlackboxActivityInput:
    return BlackboxActivityInput(
        web_url="https://example.com",
        repo_path=str(repo),
        workspace_name=workspace_name,
        deliverables_subdir="deliverables",
    )


@pytest.mark.asyncio
async def test_assemble_report_lands_in_blackbox_dir(tmp_path, monkeypatch):
    """assemble_report activity writes report + findings under deliverables/blackbox/."""
    # locate deliverables the way the activity does (resolve_deliverables_path uses
    # SUPERNOVA_WORKER_ROOT when workspace_name is set; pin it to tmp_path/workspaces)
    workspaces_root = tmp_path / "workspaces"
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(workspaces_root))
    monkeypatch.setenv("SUPERNOVA_DELIVERABLES_SUBDIR", "deliverables")

    repo = tmp_path / "repo"
    repo.mkdir()
    inp = _make_input(repo, "bb-session")

    # resolve the deliverables root for test assertions (same algorithm the activity uses)
    from supernova_core.utils.paths import resolve_deliverables_path
    deliverables = resolve_deliverables_path(
        repo_path=inp.repo_path,
        deliverables_subdir=inp.deliverables_subdir,
        workspace_name=inp.workspace_name,
    )
    deliverables.mkdir(parents=True, exist_ok=True)

    # queue lives in whitebox/ (Task 2 routing) — close_coverage_gaps + findings read it
    wb = deliverables / WHITEBOX_SUBDIR
    wb.mkdir(parents=True)
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-BB-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="q", path="/s", sink_call="db.execute",
        ),
    ])
    (wb / "injection_exploitation_queue.json").write_text(queue.model_dump_json())

    await assemble_report(inp)

    # report must land in blackbox/ subdir
    bb_report = deliverables / BLACKBOX_SUBDIR / "comprehensive_security_assessment_report.md"
    assert bb_report.exists(), "blackbox report must land in deliverables/blackbox/"
    # findings must land in blackbox/
    bb_findings = deliverables / BLACKBOX_SUBDIR / "injection_findings.md"
    assert bb_findings.exists()
    assert "### INJECTION-BB-001" in bb_findings.read_text()


@pytest.mark.asyncio
async def test_assemble_report_does_not_overwrite_whitebox_report(tmp_path, monkeypatch):
    """A pre-existing whitebox report at deliverables/whitebox/ must coexist."""
    workspaces_root = tmp_path / "workspaces"
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(workspaces_root))
    monkeypatch.setenv("SUPERNOVA_DELIVERABLES_SUBDIR", "deliverables")

    repo = tmp_path / "repo"
    repo.mkdir()
    inp = _make_input(repo, "bb-session-2")

    from supernova_core.utils.paths import resolve_deliverables_path
    deliverables = resolve_deliverables_path(
        repo_path=inp.repo_path,
        deliverables_subdir=inp.deliverables_subdir,
        workspace_name=inp.workspace_name,
    )
    deliverables.mkdir(parents=True, exist_ok=True)

    wb = deliverables / WHITEBOX_SUBDIR
    wb.mkdir(parents=True)
    # a whitebox report already exists at whitebox/ — must not be touched
    wb_report = wb / "comprehensive_security_assessment_report.md"
    wb_report.write_text("# Whitebox report (pre-existing)")

    # queue in whitebox/
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-BB-002", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ])
    (wb / "injection_exploitation_queue.json").write_text(queue.model_dump_json())

    await assemble_report(inp)

    # blackbox report lands in blackbox/
    bb_report = deliverables / BLACKBOX_SUBDIR / "comprehensive_security_assessment_report.md"
    assert bb_report.exists()
    # whitebox report is untouched (coexistence — no overwrite)
    assert wb_report.read_text() == "# Whitebox report (pre-existing)"
    # no report at deliverables root either
    assert not (deliverables / "comprehensive_security_assessment_report.md").exists()


@pytest.mark.asyncio
async def test_assemble_report_body_contains_evidence_content(tmp_path, monkeypatch):
    """Issue 2: assembled blackbox report BODY must contain evidence-derived content.

    blackbox passes ``bb=deliverables/blackbox/`` to ReportAssembler. The whitebox
    analysis deliverable is NOT under ``bb/`` (it lives in ``whitebox/``), so the
    assembler must lean on its evidence→findings→analysis fallback chain to still
    emit a non-empty report. This anchors that: with evidence at
    ``blackbox/{vc}_exploitation_evidence.md`` + queue at ``whitebox/``, the
    assembled report body carries the evidence token. If the fallback chain ever
    silently produced an empty body (e.g. a regression that broke evidence reads),
    the token assertion would fail.
    """
    workspaces_root = tmp_path / "workspaces"
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(workspaces_root))
    monkeypatch.setenv("SUPERNOVA_DELIVERABLES_SUBDIR", "deliverables")

    repo = tmp_path / "repo"
    repo.mkdir()
    inp = _make_input(repo, "bb-session-body")

    from supernova_core.utils.paths import resolve_deliverables_path
    deliverables = resolve_deliverables_path(
        repo_path=inp.repo_path,
        deliverables_subdir=inp.deliverables_subdir,
        workspace_name=inp.workspace_name,
    )
    deliverables.mkdir(parents=True, exist_ok=True)

    # queue in whitebox/ (Task 2 routing) — drives findings rendering + coverage
    wb = deliverables / WHITEBOX_SUBDIR
    wb.mkdir(parents=True)
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-BB-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="q", path="/s", sink_call="db.execute",
        ),
    ])
    (wb / "injection_exploitation_queue.json").write_text(queue.model_dump_json())

    # evidence in blackbox/ — assembler reads bb/ first (evidence > findings > analysis)
    bb = deliverables / BLACKBOX_SUBDIR
    bb.mkdir(parents=True)
    evidence_token = "EVIDENCE-TOKEN-INJECTION-BB-001-DYNAMIC"
    (bb / "injection_exploitation_evidence.md").write_text(
        f"# Exploitation Evidence\n\n## {evidence_token}\nDynamic verify output.\n"
    )

    await assemble_report(inp)

    bb_report = deliverables / BLACKBOX_SUBDIR / "comprehensive_security_assessment_report.md"
    assert bb_report.exists(), "blackbox report must be assembled"
    body = bb_report.read_text()
    assert evidence_token in body, (
        "blackbox report body must contain the evidence token — empty/missing body "
        "means the evidence→findings→analysis fallback chain broke"
    )
    assert body.strip() != "", "report body must not be empty"


@pytest.mark.asyncio
async def test_finalize_report_writes_blackbox_dir_path(tmp_path, monkeypatch):
    """finalize_report resolves report_path under deliverables/blackbox/."""
    workspaces_root = tmp_path / "workspaces"
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(workspaces_root))
    monkeypatch.setenv("SUPERNOVA_DELIVERABLES_SUBDIR", "deliverables")

    repo = tmp_path / "repo"
    repo.mkdir()
    workspace_name = "bb-session-3"
    inp = _make_input(repo, workspace_name)

    from supernova_core.utils.paths import resolve_deliverables_path
    deliverables = resolve_deliverables_path(
        repo_path=inp.repo_path,
        deliverables_subdir=inp.deliverables_subdir,
        workspace_name=inp.workspace_name,
    )
    deliverables.mkdir(parents=True, exist_ok=True)
    bb = deliverables / BLACKBOX_SUBDIR
    bb.mkdir(parents=True)
    bb_report = bb / "comprehensive_security_assessment_report.md"
    bb_report.write_text("## Executive Summary\n\n- Assessment Date: 2026-06-30\n")

    # session.json in the session workspace dir (sibling of deliverables)
    workspace_path = workspaces_root / workspace_name
    workspace_path.mkdir(parents=True, exist_ok=True)
    session_path = workspace_path / "session.json"
    session_path.write_text(json.dumps({
        "metrics": {"agents": {"recon": {"model": "claude-sonnet-4-6"}}}
    }))
    inp.workspace_path = str(workspace_path)

    await finalize_report(inp)

    content = bb_report.read_text()
    assert "- **Model:** claude-sonnet-4-6" in content
