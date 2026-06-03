import json
from pathlib import Path

import pytest

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.agent_logger import AgentLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _audit_dir(tmp_path: Path) -> Path:
    return generate_audit_path(_make_meta(tmp_path))


async def test_initialize_creates_log_file(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    # Find the log file in agents/ directory
    agents_dir = _audit_dir(tmp_path) / "agents"
    log_files = list(agents_dir.glob("*_recon_attempt-1.log"))
    assert len(log_files) == 1
    await logger.close()


async def test_initialize_writes_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    agents_dir = _audit_dir(tmp_path) / "agents"
    log_file = list(agents_dir.glob("*_recon_attempt-1.log"))[0]
    content = log_file.read_text()
    assert "Agent: recon" in content
    assert "Attempt: 1" in content
    assert "Session: test-session" in content
    assert "Web URL: https://example.com" in content
    await logger.close()


async def test_initialize_writes_agent_start_event(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    agents_dir = _audit_dir(tmp_path) / "agents"
    log_file = list(agents_dir.glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    # Find the JSON line with agent_start
    json_lines = [l for l in lines if l.startswith("{")]
    assert len(json_lines) >= 1
    event = json.loads(json_lines[0])
    assert event["type"] == "agent_start"
    assert event["data"]["agentName"] == "recon"
    assert event["data"]["attemptNumber"] == 1
    assert "timestamp" in event
    await logger.close()


async def test_log_event_writes_json_line(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.log_event("tool_start", {"toolName": "Read", "parameters": {"file_path": "/tmp/test.py"}})
    await logger.close()

    log_file = list((_audit_dir(tmp_path) / "agents").glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    json_lines = [json.loads(l) for l in lines if l.startswith("{")]
    tool_events = [e for e in json_lines if e["type"] == "tool_start"]
    assert len(tool_events) == 1
    assert tool_events[0]["data"]["toolName"] == "Read"


async def test_log_event_includes_timestamp(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.log_event("agent_end", {"success": True, "duration_ms": 5000})
    await logger.close()

    log_file = list((_audit_dir(tmp_path) / "agents").glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    json_lines = [json.loads(l) for l in lines if l.startswith("{")]
    end_event = [e for e in json_lines if e["type"] == "agent_end"][0]
    assert "timestamp" in end_event
    assert end_event["timestamp"].endswith("Z")


async def test_close_prevents_further_writes(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.close()
    # log_event after close should not raise (it silently returns)
    await logger.log_event("tool_start", {"toolName": "Read"})


async def test_multiple_events_in_sequence(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.log_event("tool_start", {"toolName": "Read"})
    await logger.log_event("tool_end", {"toolName": "Read", "output": "ok"})
    await logger.log_event("agent_end", {"success": True, "duration_ms": 10000})
    await logger.close()

    log_file = list((_audit_dir(tmp_path) / "agents").glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    json_lines = [json.loads(l) for l in lines if l.startswith("{")]
    # agent_start + 3 events = 4 JSON lines
    assert len(json_lines) == 4


async def test_log_file_naming_includes_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 3)
    await logger.initialize()
    await logger.close()
    log_files = list((_audit_dir(tmp_path) / "agents").glob("*_recon_attempt-3.log"))
    assert len(log_files) == 1


async def test_save_prompt_creates_markdown_file(tmp_path: Path):
    meta = _make_meta(tmp_path)
    await AgentLogger.save_prompt(meta, "recon", "You are a security analyst.")
    prompt_file = _audit_dir(tmp_path) / "prompts" / "recon.md"
    assert prompt_file.exists()
    content = prompt_file.read_text()
    assert "agent: recon" in content
    assert "session: test-session" in content
    assert "You are a security analyst." in content


async def test_save_prompt_metadata_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    await AgentLogger.save_prompt(meta, "recon", "Test prompt content")
    content = (_audit_dir(tmp_path) / "prompts" / "recon.md").read_text()
    assert content.startswith("---")
    assert "saved:" in content
