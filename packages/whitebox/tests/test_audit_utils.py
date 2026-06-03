from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.utils import (
    format_duration,
    format_timestamp,
    format_log_time,
    sanitize_hostname,
    generate_audit_path,
    generate_log_path,
    generate_prompt_path,
    generate_workflow_log_path,
    generate_session_json_path,
    initialize_audit_structure,
)


# --- format_duration ---

def test_format_duration_milliseconds():
    assert format_duration(23) == "23ms"


def test_format_duration_sub_second():
    assert format_duration(500) == "500ms"


def test_format_duration_seconds():
    assert format_duration(1500) == "1.5s"


def test_format_duration_minutes_and_seconds():
    assert format_duration(150000) == "2m 30s"


def test_format_duration_exact_minute():
    assert format_duration(60000) == "1m 0s"


def test_format_duration_zero():
    assert format_duration(0) == "0ms"


# --- format_timestamp ---

def test_format_timestamp_returns_iso_string():
    ts = format_timestamp()
    assert ts.endswith("Z")
    assert "T" in ts


def test_format_timestamp_from_float():
    # 2026-01-01 00:00:00 UTC
    ts = format_timestamp(1767225600.0)
    assert ts.startswith("2026-01-01T")
    assert ts.endswith("Z")


# --- format_log_time ---

def test_format_log_time_format():
    lt = format_log_time()
    # Should match YYYY-MM-DD HH:MM:SS
    parts = lt.split(" ")
    assert len(parts) == 2
    date_part = parts[0]
    time_part = parts[1]
    assert len(date_part) == 10
    assert len(time_part) == 8
    assert date_part[4] == "-"
    assert time_part[2] == ":"


# --- sanitize_hostname ---

def test_sanitize_hostname_https():
    assert sanitize_hostname("https://example.com") == "example-com"


def test_sanitize_hostname_http():
    assert sanitize_hostname("http://test.example.com/path") == "test-example-com"


def test_sanitize_hostname_with_port():
    assert sanitize_hostname("https://localhost:3000") == "localhost-3000"


# --- path generation ---

def _make_meta(**kwargs) -> SessionMetadata:
    defaults = {"id": "test-session", "output_path": None}
    defaults.update(kwargs)
    return SessionMetadata(**defaults)


def test_generate_audit_path_with_output_path():
    meta = _make_meta(output_path="/tmp/workspaces")
    assert generate_audit_path(meta) == Path("/tmp/workspaces/test-session")


def test_generate_audit_path_without_output_path():
    meta = _make_meta(output_path=None)
    assert generate_audit_path(meta) == Path("workspaces/test-session")


def test_generate_log_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_log_path(meta, "recon", 1700000000, 1)
    assert path == Path("/tmp/ws/test-session/agents/1700000000_recon_attempt-1.log")


def test_generate_prompt_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_prompt_path(meta, "recon")
    assert path == Path("/tmp/ws/test-session/prompts/recon.md")


def test_generate_workflow_log_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_workflow_log_path(meta)
    assert path == Path("/tmp/ws/test-session/workflow.log")


def test_generate_session_json_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_session_json_path(meta)
    assert path == Path("/tmp/ws/test-session/session.json")


def test_initialize_audit_structure(tmp_path):
    meta = _make_meta(output_path=str(tmp_path / "ws"))
    initialize_audit_structure(meta)
    base = tmp_path / "ws" / "test-session"
    assert (base / "agents").is_dir()
    assert (base / "prompts").is_dir()
    assert (base / "deliverables").is_dir()
