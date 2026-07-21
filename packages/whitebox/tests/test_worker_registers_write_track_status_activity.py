"""Regression anchor: write_track_status_activity (Task 4 fail-fast) must be
registered with BOTH Temporal workers (CLI worker.py + web runner.py) -
define/call/register 3-point sync. A unit test patches the activity so it
never exercises real dispatch; this source-level check is the only thing that
catches a missing registration before a real run silently fails the scan
with 'activity not registered' -> ApplicationFailure -> workflow FAILED.
"""
from pathlib import Path


def test_write_track_status_activity_registered_in_cli_worker():
    worker = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/worker.py"
    src = worker.read_text()
    # Must appear in BOTH the import block AND the activities=[...] list.
    assert src.count("write_track_status_activity") >= 2, (
        "write_track_status_activity must be imported AND listed in worker.py activities"
    )


def test_write_track_status_activity_registered_in_web_worker():
    # parents[0]=tests, parents[1]=packages/whitebox, parents[2]=packages.
    # runner lives at packages/worker/src/supernova_worker/runner.py (cross-package).
    runner = Path(__file__).resolve().parents[2] / "worker/src/supernova_worker/runner.py"
    src = runner.read_text()
    assert src.count("write_track_status_activity") >= 2, (
        "write_track_status_activity must be imported AND listed in runner.py activities"
    )


def test_inject_gitnexus_track_status_registered_in_cli_worker():
    """inject_gitnexus_track_status (fail-fast plan Task 6,report-executive 之后
    注入 GitNexus 轨判定状态 banner)同样须在 CLI worker.py define/call/register 3-point sync,
    否则 workflow 编排到该步骤 -> 'activity not registered' -> ApplicationFailure -> FAILED。
    """
    worker = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/worker.py"
    src = worker.read_text()
    assert src.count("inject_gitnexus_track_status") >= 2, (
        "inject_gitnexus_track_status must be imported AND listed in worker.py activities"
    )


def test_inject_gitnexus_track_status_registered_in_web_worker():
    runner = Path(__file__).resolve().parents[2] / "worker/src/supernova_worker/runner.py"
    src = runner.read_text()
    assert src.count("inject_gitnexus_track_status") >= 2, (
        "inject_gitnexus_track_status must be imported AND listed in runner.py activities"
    )
