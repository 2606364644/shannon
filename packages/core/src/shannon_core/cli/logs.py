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


def tail_workflow_log(workspace_id: str, workspaces_dir: str = "workspaces") -> None:
    """Tail a workflow.log in real-time, like tail -f. Auto-exits on Workflow COMPLETED/FAILED."""
    base = Path(workspaces_dir)

    # 1. Direct match
    log_path = base / workspace_id / "workflow.log"
    if not log_path.exists():
        # 2. Try stripping resume suffix
        stripped = re.sub(r"_resume_\d+$", "", workspace_id)
        if stripped != workspace_id:
            log_path = base / stripped / "workflow.log"
        if not log_path.exists():
            print(f"ERROR: Workflow log not found for: {workspace_id}", file=sys.stderr)
            sys.exit(1)

    handler = LogFileHandler(log_path)
    print(f"Tailing workflow log: {log_path}")

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
