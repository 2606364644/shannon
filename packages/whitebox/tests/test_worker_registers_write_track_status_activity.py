"""Regression anchor: write_track_status_activity (Task 4 fail-fast) must be
registered with BOTH Temporal workers (CLI worker.py + web runner.py) -
define/call/register 3-point sync. A unit test patches the activity so it
never exercises real dispatch; this source-level check is the only thing that
catches a missing registration before a real run silently fails the scan
with 'activity not registered' -> ApplicationFailure -> workflow FAILED.
"""
from pathlib import Path


def test_write_track_status_activity_registered_in_cli_worker():
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    # Must appear in BOTH the import block AND the activities=[...] list.
    assert src.count("write_track_status_activity") >= 2, (
        "write_track_status_activity must be imported AND listed in worker.py activities"
    )


def test_write_track_status_activity_registered_in_web_worker():
    # parents[0]=tests, parents[1]=packages/whitebox, parents[2]=packages.
    # runner lives at packages/worker/src/shannon_worker/runner.py (cross-package).
    runner = Path(__file__).resolve().parents[2] / "worker/src/shannon_worker/runner.py"
    src = runner.read_text()
    assert src.count("write_track_status_activity") >= 2, (
        "write_track_status_activity must be imported AND listed in runner.py activities"
    )
