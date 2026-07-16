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
    assert "Log file not found" in captured.err


def test_render_event_line_logevent_uses_diagnostic_format():
    from shannon_core.cli.logs import render_event_line
    line = render_event_line({
        "ts": "2026-07-16 02:00:00", "category": "WARNING", "type": "LogEvent",
        "logger_name": "mod.x", "level": "WARNING", "message": "careful",
    })
    assert "[WARNING]" in line
    assert "mod.x: careful" in line


def test_render_event_line_step_reuses_formatters():
    from shannon_core.cli.logs import render_event_line
    line = render_event_line({
        "ts": "2026-07-16 02:00:00", "category": "STEP", "type": "StepEvent",
        "name": "code-index", "phase": "pre-recon", "event": "complete",
        "duration_ms": 430238, "intent": "构建调用图",
    })
    assert "[STEP" in line
    assert "构建调用图" in line
    assert "430238ms" in line or "7m " in line  # format_duration


def test_render_event_line_scan_end():
    from shannon_core.cli.logs import render_event_line
    line = render_event_line({
        "ts": "2026-07-16 02:00:00", "category": "CONTROL",
        "type": "scan_end", "status": "completed",
    })
    assert "scan_end" in line
    assert "completed" in line


def test_tail_events_ndjson_renders_and_exits_on_scan_end(tmp_path, capsys):
    """一次性 flush 全量 + 遇 scan_end 立即返回（不等 watchdog）。"""
    import shannon_core.cli.logs as L
    ws = tmp_path / "ws1"
    ws.mkdir()
    ndjson = ws / "events.ndjson"
    ndjson.write_text(
        '{"ts":"2026-07-16 02:00:00","category":"STEP","type":"StepEvent","name":"x","phase":"p","event":"complete","duration_ms":12,"intent":"i"}\n'
        '{"ts":"2026-07-16 02:00:01","category":"CONTROL","type":"scan_end","status":"completed"}\n',
        encoding="utf-8",
    )
    # 直接驱动 JsonLogHandler.flush 验证渲染 + scan_end 退出（不启 watchdog，避免阻塞）
    handler = L.JsonLogHandler(ndjson)
    done = handler.flush()
    out = capsys.readouterr().out
    assert "[STEP" in out
    assert "scan_end" in out
    assert done is True
