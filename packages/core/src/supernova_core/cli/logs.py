import json
import re
import sys
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from supernova_core.display.formatters import (
    agent_body, format_duration, gitnexus_body, humanize_tool_call,
    phase_body, step_body, tag,
)


COMPLETION_PATTERN = re.compile(r"^Workflow (COMPLETED|FAILED)$", re.MULTILINE)


class LogFileHandler(FileSystemEventHandler):
    """Watches a workflow.log file and outputs new content to stdout."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._position = 0

    def flush(self) -> bool:
        """Output new content since last read. Returns True if completion marker detected."""
        try:
            size = self._path.stat().st_size
            if size <= self._position:
                return False
            content = self._path.read_text(encoding="utf-8")
            new_content = content[self._position :]
            self._position = size
            sys.stdout.write(new_content)
            sys.stdout.flush()
            return bool(COMPLETION_PATTERN.search(new_content))
        except Exception:
            return True  # File deleted or unreadable, treat as complete

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                raise SystemExit(0)


def tail_workflow_log(
    workspace_id: str,
    workspaces_dir: str = "workspaces",
    log_filename: str = "workflow.log",
) -> None:
    """Tail a log file in real-time, like tail -f. Auto-exits on Workflow COMPLETED/FAILED.

    log_filename 默认 workflow.log（display 流产物）；--diagnostic 时传 diagnostic.log
    （diagnostic.log 在 <workspace>/logs/ 子目录，workflow.log 在 <workspace>/ 直属）。
    """
    base = Path(workspaces_dir)

    def _resolve(name: str) -> Path | None:
        # workflow.log 在 workspace 直属；diagnostic.log 在 workspace/logs/ 子目录。
        sub = "logs" if name == "diagnostic.log" else ""
        ws_root = base / workspace_id / sub
        path = ws_root / name
        if path.exists():
            return path
        # 2. Try stripping resume suffix
        stripped = re.sub(r"_resume_\d+$", "", workspace_id)
        if stripped != workspace_id:
            ws_root = base / stripped / sub
            path = ws_root / name
            if path.exists():
                return path
        return None

    log_path = _resolve(log_filename)
    if log_path is None:
        print(f"ERROR: Log file not found for: {workspace_id} ({log_filename})", file=sys.stderr)
        sys.exit(1)

    handler = LogFileHandler(log_path)
    print(f"Tailing {log_filename}: {log_path}")

    # Output existing content
    if handler.flush():
        sys.exit(0)

    # Watch for changes
    observer = Observer()
    observer.schedule(handler, str(log_path.parent), recursive=False)
    observer.start()

    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()


class _Ns:
    """dict-backed namespace：formatters 访问的可选字段缺失时返回 None。

    events.ndjson 各 type 字段集不同（StepEvent 可无 error、AgentEvent 可无 cost_usd…），
    直接 SimpleNamespace(**data) 访问缺失属性会 AttributeError 让 logs --full 整体崩。
    本类 __getattr__ 兜底 → formatters 纯函数（按 dataclass 设计、对 None 容忍）安全。
    """

    def __init__(self, d: dict) -> None:
        self._d = d

    def __getattr__(self, k: str):  # 缺失键 → None，不抛 AttributeError
        return self._d.get(k)


def render_event_line(data: dict) -> str:
    """渲染一条 events.ndjson JSON 行为文本（logs --full 用）。

    DisplayEvent 复用 display/formatters 纯函数（传 _Ns）；LogEvent 用
    diagnostic 格式 [LEVEL] logger: msg（对齐 logging/diagnostic_log）。同步、无 ANSI。
    """
    ts = data.get("ts", "")
    etype = data.get("type", "")
    e = _Ns(data)
    if etype == "LogEvent":
        level = data.get("level", "INFO")
        return f"[{ts}] [{level:>5}] {data.get('logger_name', '')}: {data.get('message', '')}"
    if etype == "PhaseEvent":
        prefix = "\n" if data.get("event") == "start" else ""
        return f"{prefix}[{ts}] [{tag('PHASE')}] {phase_body(e)}"
    if etype == "StepEvent":
        return f"[{ts}] [{tag('STEP')}] {step_body(e)}"
    if etype == "AgentEvent":
        return f"[{ts}] [{tag('AGENT')}] {agent_body(e)}"
    if etype == "ToolCallEvent":
        params = humanize_tool_call(data.get("tool_name", ""), data.get("parameters") or {})
        return f"[{ts}] [TOOL]  {data.get('agent_name', '')}: {data.get('tool_name', '')}: {params}"
    if etype == "LlmTurnEvent":
        return f"[{ts}] [LLM]   {data.get('agent_name', '')}: Turn {data.get('turn', '')}: {data.get('content', '')[:200]}"
    if etype == "GitnexusLlmEvent":
        return f"[{ts}] [LLM]   [GitNexus] {gitnexus_body(e)}"
    if etype == "InfoEvent":
        label = "WARNING" if data.get("level") == "warning" else "INFO"
        return f"[{ts}] [{tag(label)}] {data.get('message', '')}"
    if etype == "ErrorEvent":
        return f"[{ts}] [ERROR] {data.get('error_type', '')}: {data.get('message', '')}"
    if etype == "SummaryEvent":
        return f"[{ts}] [SUMMARY] {data.get('status', '?')}  {format_duration(data.get('total_duration_ms') or 0)}"
    if etype == "WorkflowHeader":
        return f"[{ts}] [HEADER] {data.get('repo_path', '')}  ({data.get('mode', '')})"
    if etype == "scan_end":
        return f"[{ts}] --- scan_end: {data.get('status', '?')} ---"
    return f"[{ts}] [{etype}]"


SCAN_END_TYPE = "scan_end"


class JsonLogHandler(FileSystemEventHandler):
    """Watches events.ndjson: renders each new JSON line; exits on scan_end."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._position = 0

    def _read_new(self) -> tuple[list[str], bool]:
        """Return (rendered_lines, saw_scan_end) for new bytes since last read."""
        try:
            size = self._path.stat().st_size
            if size <= self._position:
                return [], False
            content = self._path.read_text(encoding="utf-8")
            new = content[self._position:]
            self._position = size
        except Exception:
            return [], True  # 文件不可读 → 视为完成
        rendered, saw_end = [], False
        for line in new.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            rendered.append(render_event_line(data))
            if data.get("type") == SCAN_END_TYPE:
                saw_end = True
        return rendered, saw_end

    def flush(self) -> bool:
        rendered, saw_end = self._read_new()
        for r in rendered:
            sys.stdout.write(r + "\n")
        sys.stdout.flush()
        return saw_end

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                raise SystemExit(0)


def tail_events_ndjson(
    workspace_id: str,
    workspaces_dir: str = "workspaces",
) -> None:
    """Tail events.ndjson, render each JSON line; auto-exit on scan_end (type=scan_end)."""
    base = Path(workspaces_dir)
    path = base / workspace_id / "events.ndjson"
    if not path.exists():
        stripped = re.sub(r"_resume_\d+$", "", workspace_id)
        if stripped != workspace_id:
            alt = base / stripped / "events.ndjson"
            if alt.exists():
                path = alt
    if not path.exists():
        print(f"ERROR: events.ndjson not found for: {workspace_id}", file=sys.stderr)
        sys.exit(1)

    handler = JsonLogHandler(path)
    print(f"Tailing events.ndjson (full): {path}")
    if handler.flush():  # 既有内容里已含 scan_end → 直接退出
        sys.exit(0)
    observer = Observer()
    observer.schedule(handler, str(path.parent), recursive=False)
    observer.start()
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
