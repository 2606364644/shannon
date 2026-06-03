import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from shannon_core.cli.logs import LogFileHandler, COMPLETION_PATTERN


def test_completion_pattern_matches_completed():
    assert COMPLETION_PATTERN.search("Workflow COMPLETED\n")


def test_completion_pattern_matches_failed():
    assert COMPLETION_PATTERN.search("Workflow FAILED\n")


def test_completion_pattern_no_match_in_progress():
    assert not COMPLETION_PATTERN.search("some intermediate log line")


def test_log_file_handler_flush_new_content(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("line 1\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    # First flush reads from position 0
    completed = handler.flush()
    assert completed is False
    assert handler._position == len("line 1\n")


def test_log_file_handler_flush_detects_completion(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("Workflow COMPLETED\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    completed = handler.flush()
    assert completed is True


def test_log_file_handler_flush_no_new_content(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("line 1\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    handler.flush()  # consume initial content
    # No new content
    completed = handler.flush()
    assert completed is False


def test_log_file_handler_flush_missing_file(tmp_path: Path):
    log_path = tmp_path / "nonexistent.log"
    handler = LogFileHandler(log_path)
    completed = handler.flush()
    assert completed is True  # missing file treated as completion


def test_log_file_handler_incremental_flush(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("line 1\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    handler.flush()
    # Append more content
    log_path.write_text("line 1\nline 2\n", encoding="utf-8")
    with patch("sys.stdout") as mock_stdout:
        completed = handler.flush()
        assert completed is False
        mock_stdout.write.assert_called_once_with("line 2\n")


def test_tail_workflow_log_missing_workspace(tmp_path, capsys):
    from shannon_core.cli.logs import tail_workflow_log
    # Use side_effect to actually raise SystemExit when sys.exit is called
    with patch.object(sys, "exit", side_effect=SystemExit) as mock_exit:
        with pytest.raises(SystemExit):
            tail_workflow_log("nonexistent-workspace", workspaces_dir=str(tmp_path))

        # Verify sys.exit was called with error code 1
        mock_exit.assert_called_once_with(1)

    captured = capsys.readouterr()
    assert "Workflow log not found" in captured.err
