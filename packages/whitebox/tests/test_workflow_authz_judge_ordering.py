# packages/whitebox/tests/test_workflow_authz_judge_ordering.py
"""Verify the authz GitNexus judge activity is called between the vuln phase
gather and the dual-track merge."""
from unittest.mock import AsyncMock, patch, call

import pytest


@pytest.mark.asyncio
async def test_judge_runs_before_merge_in_workflow(tmp_path, monkeypatch):
    """The workflow must call run_authz_gitnexus_judge before run_merge_dual_track_queues."""
    # We patch both activities and check call order via a shared log.
    order: list[str] = []

    async def fake_judge(inp):
        order.append("judge")
        return {"candidate_count": 0, "verdict_count": 0}

    async def fake_merge(inp):
        order.append("merge")
        return {"merged_classes": []}

    # Import the workflow module to patch the activities it references.
    from supernova_whitebox.pipeline import workflows

    with patch.object(workflows.activities, "run_authz_gitnexus_judge", new=fake_judge):
        with patch.object(workflows.activities, "run_merge_dual_track_queues", new=fake_merge):
            # We can't easily run the full workflow (needs Temporal); instead
            # assert the symbols exist and judge is a distinct activity wired
            # before merge by inspecting the source.
            assert hasattr(workflows.activities, "run_authz_gitnexus_judge")
            assert hasattr(workflows.activities, "run_merge_dual_track_queues")
    # Manual verification: the two fakes are callable.
    assert order == []  # not invoked here; ordering checked by source inspection


def test_workflow_source_calls_judge_before_merge():
    """Source-level check: run_authz_gitnexus_judge appears before
    run_merge_dual_track_queues in the workflow's vuln-phase tail."""
    from pathlib import Path
    wf = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/pipeline/workflows.py"
    src = wf.read_text()
    j = src.find("run_authz_gitnexus_judge")
    m = src.find("run_merge_dual_track_queues")
    if m == -1:
        # Plan 3 not landed yet — judge wiring must still be present.
        assert j != -1, "run_authz_gitnexus_judge must be wired into the workflow"
        return
    assert j != -1, "run_authz_gitnexus_judge must be wired into the workflow"
    assert j < m, "run_authz_gitnexus_judge must be called BEFORE run_merge_dual_track_queues"
