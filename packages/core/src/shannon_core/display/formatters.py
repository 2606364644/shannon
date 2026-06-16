"""Display formatters: migrated time helpers + display-enhancement functions.

format_duration/format_log_time/format_timestamp are migrated here from
shannon_whitebox.audit.utils so core renderers can use them without a reverse
dependency on whitebox. Whitebox re-imports them for backward compatibility.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse


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


def default_tool_params(tool_name: str, params: dict) -> str:
    """Generic per-tool smart truncation for readable log lines."""
    tool_key_map = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Grep": "pattern",
        "Glob": "pattern",
    }
    key = tool_key_map.get(tool_name)
    if key and key in params:
        val = str(params[key])
        if len(val) > 80:
            val = val[:77] + "..."
        return f"{key}={val}"
    items = list(params.items())[:2]
    parts = [f"{k}={str(v)[:40]}" for k, v in items]
    result = ", ".join(parts)
    if len(params) > 2:
        result += ", ..."
    return result


def maybe_browser_action(params: dict) -> str | None:
    """Parse a playwright-cli Bash command into an emoji phrase. None if not browser."""
    command = params.get("command", "") if isinstance(params, dict) else ""
    match = re.match(r"playwright-cli\s+(?:-s=\S+\s+)?(\S+)(?:\s+(.*))?", command)
    if not match:
        return None
    subcommand, args = match.group(1), (match.group(2) or "").strip()

    def _domain(url: str) -> str:
        try:
            host = urlparse(url).hostname
            return host or url[:30]
        except Exception:
            return url[:30]

    if subcommand in ("open", "goto", "navigate"):
        return f"🌐 Navigating to {_domain(args)}" if args else "🌐 Opening browser"
    if subcommand in ("click", "dblclick"):
        return f"🖱️ Clicking {(args or 'element')[:25]}"
    if subcommand in ("type", "fill"):
        return f"⌨️ Typing {(args or 'text')[:20]}"
    if subcommand in ("snapshot", "screenshot"):
        return "📸 Taking page snapshot" if subcommand == "snapshot" else "📸 Taking screenshot"
    if subcommand == "reload":
        return "🔄 Reloading page"
    return f"🌐 Browser: {subcommand}"


def first_nonempty_line(text: str) -> str:
    """Return the first non-blank stripped line, or '' if none.

    Used to render an assistant turn's text as one calm live line (the full
    turn text is retained in the per-agent JSON log regardless).
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def humanize_tool_call(tool_name: str, params: dict) -> str:
    """Turn a raw tool call into a human-readable single line."""
    if not isinstance(params, dict):
        params = {}
    match tool_name:
        case "Task":
            return f"🚀 Launching {params.get('description', 'analysis agent')}"
        case "TodoWrite":
            return summarize_todo(params) or "TodoWrite"
        case "Bash":
            browser = maybe_browser_action(params)
            if browser:
                return browser
            return default_tool_params(tool_name, params)
        case _:
            return default_tool_params(tool_name, params)
