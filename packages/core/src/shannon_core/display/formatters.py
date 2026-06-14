"""Display formatters: migrated time helpers + display-enhancement functions.

format_duration/format_log_time/format_timestamp are migrated here from
shannon_whitebox.audit.utils so core renderers can use them without a reverse
dependency on whitebox. Whitebox re-imports them for backward compatibility.
"""
from __future__ import annotations

from datetime import datetime, timezone


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
    """Human-readable local format 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Maps agent name (AgentName.value string) to a short display prefix.
# Keys are matched exactly, so auth/authz cannot collide. The original TS
# project used substring matching where authz HAD to be checked before auth;
# exact-key matching removes that hazard.
_AGENT_PREFIXES: dict[str, str] = {
    "injection-vuln": "[Injection]",
    "injection-exploit": "[Injection]",
    "xss-vuln": "[XSS]",
    "xss-exploit": "[XSS]",
    "authz-vuln": "[Authz]",
    "authz-exploit": "[Authz]",
    "auth-vuln": "[Auth]",
    "auth-exploit": "[Auth]",
    "ssrf-vuln": "[SSRF]",
    "ssrf-exploit": "[SSRF]",
}


def agent_prefix(agent_name: str) -> str:
    """Map an agent name to its display prefix, or '[Agent]' if unknown."""
    return _AGENT_PREFIXES.get(agent_name, "[Agent]")


def summarize_todo(params: dict) -> str | None:
    """Summarize a TodoWrite tool call: latest completed (✅) or first in-progress (🔄).

    Returns None if nothing noteworthy, so callers can skip emitting the line.
    """
    todos = params.get("todos")
    if not todos or not isinstance(todos, list):
        return None
    completed = [t for t in todos if t.get("status") == "completed"]
    if completed:
        return f"✅ {completed[-1].get('content', '')}"
    in_progress = [t for t in todos if t.get("status") == "in_progress"]
    if in_progress:
        return f"🔄 {in_progress[0].get('content', '')}"
    return None


def format_error_block(error_str: str) -> str:
    """Format a pipe-delimited error string into aligned multi-line text.

    Input:  "phase context|ErrorType|message|Hint: ..."
    Output: "Error:       phase context\\n             ErrorType\\n             ..."
    """
    label = "Error:       "
    indent = " " * len(label)
    segments = error_str.split("|")
    rendered = [
        f"{label}{seg.strip()}" if i == 0 else f"{indent}{seg.strip()}"
        for i, seg in enumerate(segments)
    ]
    return "\n".join(rendered) + "\n"
