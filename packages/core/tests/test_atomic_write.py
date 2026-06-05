import json
from pathlib import Path

from shannon_core.utils.atomic_write import atomic_write_json


def test_atomic_write_creates_file(tmp_path):
    """atomic_write_json should create a valid JSON file."""
    target = tmp_path / "output.json"
    data = {"key": "value", "number": 42}
    atomic_write_json(target, data)

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_atomic_write_no_partial_on_error(tmp_path):
    """If write fails, original file should remain unchanged."""
    target = tmp_path / "output.json"
    original_data = {"version": 1}
    target.write_text(json.dumps(original_data), encoding="utf-8")

    from unittest.mock import patch
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        try:
            atomic_write_json(target, {"version": 2})
        except OSError:
            pass

    assert json.loads(target.read_text(encoding="utf-8")) == original_data


def test_atomic_write_no_tmp_file_left(tmp_path):
    """After successful write, no .tmp file should remain."""
    target = tmp_path / "output.json"
    atomic_write_json(target, {"key": "value"})

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_atomic_write_creates_parent_dirs(tmp_path):
    """atomic_write_json should create parent directories if needed."""
    target = tmp_path / "nested" / "dir" / "output.json"
    atomic_write_json(target, {"key": "value"})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"key": "value"}
