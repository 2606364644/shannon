import re
import sys
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


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
