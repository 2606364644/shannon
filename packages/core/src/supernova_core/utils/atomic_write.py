"""Atomic file write utilities for safe deliverable persistence."""

import json
from pathlib import Path


def atomic_write_json(path: Path, data: dict, *, indent: int = 2) -> None:
    """Atomically write a JSON file: write to .tmp then rename.

    Uses the POSIX write-then-rename pattern to ensure that
    readers never see a partially-written file.

    Args:
        path: Target file path.
        data: Dict to serialize as JSON.
        indent: JSON indentation level.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, indent=indent, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.rename(path)  # POSIX rename is atomic
    except Exception:
        # Clean up temp file on failure
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write a text file: write to .tmp then rename.

    Symmetric to atomic_write_json; use for markdown/plain deliverables so
    concurrent readers (e.g. vuln agents starting after risk scoring) never
    observe a partially-written file.

    Args:
        path: Target file path.
        text: Text content to write (UTF-8).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.rename(path)  # POSIX rename is atomic
    except Exception:
        # Clean up temp file on failure
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
