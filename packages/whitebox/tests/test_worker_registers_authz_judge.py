"""Regression anchor: the authz GitNexus judge activity must be registered with
the Temporal worker (the 3-point-sync gotcha: define / call / register).
A unit test patches the activity, so it never exercises real dispatch — this
source-level check is the only thing that catches a missing worker registration
before a real run silently no-ops the GitNexus track."""
from pathlib import Path


def test_authz_gitnexus_judge_registered_in_worker():
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    # Must appear in BOTH the import block AND the activities=[...] list.
    assert src.count("run_authz_gitnexus_judge") >= 2, (
        "run_authz_gitnexus_judge must be imported AND listed in worker.py activities"
    )


def test_merge_dual_track_queues_registered_in_worker():
    """The Plan 3 merger (consumes authz_gitnexus_queue.json) must be registered too."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    assert src.count("run_merge_dual_track_queues") >= 2, (
        "run_merge_dual_track_queues must be imported AND listed in worker.py activities"
    )
