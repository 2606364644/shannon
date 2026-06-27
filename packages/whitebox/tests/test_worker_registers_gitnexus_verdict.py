"""Regression anchor: GitNexus-track chain verdict + auth config scan activities
must be registered with the Temporal worker (define/call/register 3-point sync).
A unit test patches activities so they never exercise real dispatch; this
source-level check is the only thing that catches a missing registration before
a real run silently no-ops the GitNexus track (the df33ec5 bug)."""
from pathlib import Path


def test_gitnexus_chain_verdict_registered_in_worker():
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    # Must appear in BOTH the import block AND the activities=[...] list.
    assert src.count("run_gitnexus_chain_verdict") >= 2, (
        "run_gitnexus_chain_verdict must be imported AND listed in worker.py activities"
    )


def test_auth_config_scan_registered_in_worker():
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    assert src.count("run_auth_config_scan") >= 2, (
        "run_auth_config_scan must be imported AND listed in worker.py activities"
    )
