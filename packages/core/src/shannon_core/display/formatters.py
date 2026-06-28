"""Display formatters: migrated time helpers + display-enhancement functions.

format_duration/format_log_time/format_timestamp are migrated here from
shannon_whitebox.audit.utils so core renderers can use them without a reverse
dependency on whitebox. Whitebox re-imports them for backward compatibility.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from rich.cells import cell_len

from shannon_core.display.symbols import (
    AGENT_DONE, AGENT_FAIL, AGENT_START,
    STEP_DONE, STEP_FAIL, STEP_PENDING,
)


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
    """Parse a browser-CLI Bash command into an emoji phrase. None if not browser.

    Recognises both ``playwright-cli -s=<id> <sub>`` and
    ``agent-browser --session <id> <sub>`` command shapes.
    """
    command = params.get("command", "") if isinstance(params, dict) else ""

    # agent-browser: `agent-browser --session <id> <subcommand> [args]`
    ab_match = re.match(
        r"agent-browser\s+(?:--session\s+\S+\s+)?(\S+)(?:\s+(.*))?", command
    )
    # playwright-cli: `playwright-cli -s=<id> <subcommand> [args]`
    pw_match = re.match(r"playwright-cli\s+(?:-s=\S+\s+)?(\S+)(?:\s+(.*))?", command)

    match = ab_match or pw_match
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


PHASE_RULE_WIDTH = 36  # PHASE 行 text + 分隔线的目标显示列宽


def pad_rule(text: str, col: int = PHASE_RULE_WIDTH) -> str:
    """在 text 右侧填充 ─，使同一 col 下所有调用的显示宽度恒定（右端对齐）。

    用 cell_len 按显示宽度计算（中文 intent 算 2 列）。文字超长时兜底至少 2 个 ─。
    """
    width = cell_len(text)
    n = max(2, col - width)
    return f"{text} {'─' * n}"


LABEL_WIDTH = 5  # PHASE/AGENT=5，STEP 补齐到 5，让标签列等宽


def tag(label: str, width: int = LABEL_WIDTH) -> str:
    """补齐到固定宽度的标签内容：tag("STEP") -> "STEP "。

    rich 与 file 共用：终端 [cyan]{tag}[/] 无字面方括号，file [{tag}] 方括号内补齐。
    """
    return label.ljust(width)


def step_body(e) -> str:
    """STEP 正文：○/✓/✗ + 意图（fallback name）+ duration/error suffix。

    纯文本、无颜色、无标签列、无换行 —— rich 与 file 共用的单一来源。
    """
    label = e.intent or e.name
    if e.event == "start":
        return f"{STEP_PENDING} {label}"
    if e.error:
        return f"{STEP_FAIL} {label}  — {e.error}"
    suffix = f"  {format_duration(e.duration_ms)}" if e.duration_ms is not None else ""
    return f"{STEP_DONE} {label}{suffix}"


def phase_body(e) -> str:
    """PHASE 正文：verb + phase，如 'Starting setup'。纯文本，rich/file 共用。"""
    verb = "Starting" if e.event == "start" else "Completed"
    return f"{verb} {e.phase}"


def agent_title(agent_name: str) -> str:
    """'[Prefix] name' 或未知 agent 直接 'name'。

    取代 rich 的 _agent_panel_title 与 file 的 _prefixed（两者逻辑相同），统一为单一来源。
    """
    pfx = agent_prefix(agent_name)
    if pfx == "[Agent]":
        return agent_name
    return f"{pfx} {agent_name}"


def agent_body(e) -> str:
    """AGENT 正文：▶/✗/✓ + title + (attempt)/failed/metrics。纯文本，rich/file 共用。"""
    title = agent_title(e.agent_name)
    if e.event == "start":
        return f"{AGENT_START} {title} started (attempt {e.attempt})"
    if e.success is False:
        dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
        err = f" — {e.error}" if e.error else ""
        return f"{AGENT_FAIL} {title} failed ({dur}){err}"
    parts = []
    if e.duration_ms is not None:
        parts.append(format_duration(e.duration_ms))
    if e.cost_usd is not None:
        parts.append(f"${e.cost_usd:.4f}")
    metrics = f" ({', '.join(parts)})" if parts else ""
    return f"{AGENT_DONE} {title} Completed{metrics}"
