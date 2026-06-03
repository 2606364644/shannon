from datetime import datetime, timezone
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata


def format_duration(ms: int) -> str:
    """Convert milliseconds to human-readable: '23ms', '1.5s', '2m 30s'."""
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}m {remaining}s"


def format_timestamp(ts: float | None = None) -> str:
    """ISO 8601 UTC string with milliseconds. Defaults to now."""
    if ts is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_log_time() -> str:
    """Human-readable local format 'YYYY-MM-DD HH:MM:SS' for workflow.log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
