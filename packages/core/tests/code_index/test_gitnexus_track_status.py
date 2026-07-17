# packages/core/tests/code_index/test_gitnexus_track_status.py
from pathlib import Path
from shannon_core.code_index.gitnexus_track_status import write_track_status, read_track_status

def test_write_then_read_roundtrip(tmp_path):
    statuses = {
        "injection": {"status": "ok", "findings": 3},
        "xss": {"status": "failed", "reason": "builder raised: KeyError"},
        "authz": {"status": "ok", "findings": 0},
    }
    write_track_status(tmp_path, statuses)
    assert read_track_status(tmp_path) == statuses

def test_read_missing_file_returns_empty(tmp_path):
    assert read_track_status(tmp_path) == {}

def test_read_corrupt_file_returns_empty(tmp_path):
    (tmp_path / "gitnexus_track_status.json").write_text("{not json", encoding="utf-8")
    assert read_track_status(tmp_path) == {}

def test_write_overwrites(tmp_path):
    write_track_status(tmp_path, {"injection": {"status": "ok", "findings": 1}})
    write_track_status(tmp_path, {"injection": {"status": "failed", "reason": "x"}})
    assert read_track_status(tmp_path) == {"injection": {"status": "failed", "reason": "x"}}
