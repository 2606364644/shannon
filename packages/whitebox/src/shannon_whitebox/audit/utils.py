from pathlib import Path

from shannon_core.display.formatters import (
    format_duration,
    format_log_time,
    format_timestamp,
)
from shannon_core.models.metrics import SessionMetadata


def sanitize_hostname(url: str) -> str:
    """Extract and sanitize hostname from URL for use as a directory-safe identifier."""
    hostname = url.replace("https://", "").replace("http://", "").split("/")[0]
    return hostname.replace(".", "-").replace(":", "-")


def generate_audit_path(meta: SessionMetadata) -> Path:
    """Root directory for a session's audit artifacts."""
    if meta.output_path:
        base = Path(meta.output_path)
    else:
        base = Path("workspaces")
    return base / meta.id


def generate_log_path(meta: SessionMetadata, agent_name: str, timestamp: int, attempt: int) -> Path:
    """Path to an agent's JSON Lines log file."""
    return generate_audit_path(meta) / "agents" / f"{timestamp}_{agent_name}_attempt-{attempt}.log"


def generate_prompt_path(meta: SessionMetadata, agent_name: str) -> Path:
    """Path to an agent's prompt snapshot markdown file."""
    return generate_audit_path(meta) / "prompts" / f"{agent_name}.md"


def generate_workflow_log_path(meta: SessionMetadata) -> Path:
    """Path to the human-readable workflow log."""
    return generate_audit_path(meta) / "workflow.log"


def generate_session_json_path(meta: SessionMetadata) -> Path:
    """Path to the session.json metrics file."""
    return generate_audit_path(meta) / "session.json"


def initialize_audit_structure(meta: SessionMetadata) -> None:
    """Create the directory structure for a session's audit artifacts."""
    base = generate_audit_path(meta)
    (base / "agents").mkdir(parents=True, exist_ok=True)
    (base / "prompts").mkdir(parents=True, exist_ok=True)
    (base / "deliverables").mkdir(parents=True, exist_ok=True)
