# Workspace Cross-Scan UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix the broken cross-scan handoff UX by adding workspace discovery, linking, and actionable CLI output so users can seamlessly chain white-box → black-box scans.

**Architecture:** Extend the existing file-system workspace model with new fields (`scan_type`, `links`, `deliverables_summary`) in `session.json`. Add a shared `workspace.py` module in `packages/core/` for cross-scan discovery and URL matching. Improve both CLI outputs with actionable next-step commands. All changes are backward-compatible with existing session.json formats (both the flat `SessionManager` format and the nested Temporal workflow format).

**Tech Stack:** Python 3.12, Click (CLI), Pydantic (models), pytest (testing), file-system-based workspaces.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `packages/core/src/shannon_core/session.py` | Session data model — add `scan_type`, `status`, `completed_at`, `links`, `deliverables_summary` fields and helper methods |
| `packages/core/src/shannon_core/workspace.py` *(new)* | Cross-scan workspace discovery — `find_latest_workspace()`, `find_workspaces_by_url()`, `get_workspace_info()`, URL normalization |
| `packages/core/src/shannon_core/models/config.py` | Add `auto_detect_whitebox: bool` config option |
| `packages/core/tests/test_session.py` | Tests for new session fields and backward compatibility |
| `packages/core/tests/test_workspace.py` *(new)* | Tests for workspace discovery, URL matching, deliverables scanning |
| `packages/core/tests/test_config.py` | Tests for new config option |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Enhanced completion output with workspace name + next steps; `workspace show` subcommand |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | `--latest` flag, auto-detection prompt, `workspace show` subcommand |
| `packages/whitebox/tests/test_cli.py` | Tests for white-box completion output and `workspace show` |
| `packages/blackbox/tests/test_cli.py` | Tests for `--latest`, auto-detection, and `workspace show` |

---

### Task 1: Session Data Model Enhancements

**Files:**
- Modify: `packages/core/src/shannon_core/session.py`
- Test: `packages/core/tests/test_session.py`

- [x] **Step 1: Write failing tests for `scan_type` in `create_workspace`**

Append to `packages/core/tests/test_session.py`:

```python
def test_create_workspace_includes_scan_type(tmp_path):
    """create_workspace should accept and persist scan_type."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="whitebox")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "whitebox"


def test_create_workspace_defaults_scan_type(tmp_path):
    """create_workspace should default scan_type to 'whitebox'."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "whitebox"


def test_create_workspace_blackbox_scan_type(tmp_path):
    """create_workspace with scan_type='blackbox'."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "blackbox"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_session.py::test_create_workspace_includes_scan_type -v`
Expected: FAIL — `create_workspace()` got an unexpected keyword argument `scan_type`

- [x] **Step 3: Implement `scan_type` in `create_workspace`**

In `packages/core/src/shannon_core/session.py`, update `create_workspace` to accept `scan_type` and persist it:

```python
import json
import time
from pathlib import Path

from shannon_core.models.agents import AgentName

class SessionManager:
    def __init__(self, workspaces_dir: Path):
        self.workspaces_dir = workspaces_dir
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(
        self, web_url: str, repo_path: str, name: str | None = None, scan_type: str = "whitebox"
    ) -> Path:
        if not name:
            hostname = web_url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "-")
            session_id = f"shannon-{int(time.time() * 1000)}"
            name = f"{hostname}_{session_id}"

        ws = self.workspaces_dir / name
        ws.mkdir(parents=True, exist_ok=True)

        session_data = {
            "web_url": web_url,
            "repo_path": repo_path,
            "scan_type": scan_type,
            "status": "running",
            "created_at": time.time(),
            "completed_at": None,
            "completed_agents": [],
            "metrics": {"agents": {}},
            "links": {"parent_workspace": None, "child_workspaces": []},
            "deliverables_summary": None,
        }
        (ws / "session.json").write_text(json.dumps(session_data, indent=2), encoding="utf-8")
        return ws

    def list_workspaces(self) -> list[Path]:
        if not self.workspaces_dir.exists():
            return []
        return sorted(
            [p for p in self.workspaces_dir.iterdir() if p.is_dir() and (p / "session.json").exists()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def get_workspace(self, name: str) -> Path | None:
        ws = self.workspaces_dir / name
        if ws.exists() and (ws / "session.json").exists():
            return ws
        return None

    def get_session_data(self, workspace_path: Path) -> dict:
        session_file = workspace_path / "session.json"
        if not session_file.exists():
            return {}
        return json.loads(session_file.read_text(encoding="utf-8"))

    def update_session(self, workspace_path: Path, data: dict) -> None:
        existing = self.get_session_data(workspace_path)
        existing.update(data)
        (workspace_path / "session.json").write_text(
            json.dumps(existing, indent=2, default=str), encoding="utf-8",
        )

    def mark_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> None:
        data = self.get_session_data(workspace_path)
        completed = data.get("completed_agents", [])
        if agent_name.value not in completed:
            completed.append(agent_name.value)
        data["completed_agents"] = completed
        self.update_session(workspace_path, data)

    def is_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> bool:
        data = self.get_session_data(workspace_path)
        return agent_name.value in data.get("completed_agents", [])
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_session.py::test_create_workspace_includes_scan_type packages/core/tests/test_session.py::test_create_workspace_defaults_scan_type packages/core/tests/test_session.py::test_create_workspace_blackbox_scan_type -v`
Expected: All 3 PASS

- [x] **Step 5: Run existing session tests to verify no regressions**

Run: `uv run pytest packages/core/tests/test_session.py -v`
Expected: All existing tests + new tests PASS (8 tests total)

- [x] **Step 6: Write failing tests for `get_scan_type` with backward compatibility**

Append to `packages/core/tests/test_session.py`:

```python
def test_get_scan_type_explicit(tmp_path):
    """get_scan_type returns explicit scan_type from session.json."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    assert mgr.get_scan_type(ws) == "blackbox"


def test_get_scan_type_inferred_from_name(tmp_path):
    """get_scan_type infers from workspace name containing 'blackbox'."""
    ws = tmp_path / "workspaces" / "myapp-blackbox-123"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"web_url": "https://example.com"}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_scan_type(ws) == "blackbox"


def test_get_scan_type_defaults_whitebox(tmp_path):
    """get_scan_type defaults to whitebox when no clue exists."""
    ws = tmp_path / "workspaces" / "myapp-123"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"web_url": "https://example.com"}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_scan_type(ws) == "whitebox"


def test_get_status_from_session(tmp_path):
    """get_status reads status field from session.json."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert mgr.get_status(ws) == "running"


def test_get_status_legacy_format(tmp_path):
    """get_status handles legacy nested session.status format."""
    ws = tmp_path / "workspaces" / "legacy-ws"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({
        "session": {"id": "legacy-ws", "status": "completed"},
        "metrics": {},
    }))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_status(ws) == "completed"


def test_get_status_unknown_when_empty(tmp_path):
    """get_status returns 'unknown' when no status info exists."""
    ws = tmp_path / "workspaces" / "empty-ws"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"web_url": "https://example.com"}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_status(ws) == "unknown"
```

- [x] **Step 7: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_session.py::test_get_scan_type_explicit -v`
Expected: FAIL — `SessionManager has no attribute 'get_scan_type'`

- [x] **Step 8: Implement `get_scan_type`, `get_status`, and backward-compat helpers**

Append to `SessionManager` in `packages/core/src/shannon_core/session.py`:

```python
    def get_scan_type(self, workspace_path: Path) -> str:
        """Read scan_type from session.json, inferring from workspace name as fallback."""
        data = self.get_session_data(workspace_path)

        # Check flat format first (SessionManager-created)
        if "scan_type" in data:
            return data["scan_type"]

        # Check nested format (Temporal workflow-created)
        session = data.get("session", {})
        if "scan_type" in session:
            return session["scan_type"]

        # Infer from workspace name
        name = workspace_path.name.lower()
        if "blackbox" in name:
            return "blackbox"

        return "whitebox"

    def get_status(self, workspace_path: Path) -> str:
        """Read status from session.json, handling both flat and nested formats."""
        data = self.get_session_data(workspace_path)

        # Check flat format first
        if "status" in data:
            return data["status"]

        # Check nested format
        session = data.get("session", {})
        if "status" in session:
            return session["status"]

        # Infer from metrics
        metrics = data.get("metrics", {})
        agents = metrics.get("agents", {})
        if agents:
            return "completed"

        return "unknown"

    def get_web_url(self, workspace_path: Path) -> str | None:
        """Read web_url from session.json, handling both flat and nested formats."""
        data = self.get_session_data(workspace_path)

        if "web_url" in data:
            return data["web_url"]

        session = data.get("session", {})
        return session.get("webUrl") or session.get("web_url")

    def get_created_at(self, workspace_path: Path) -> str | None:
        """Read created_at timestamp from session.json, handling both formats."""
        data = self.get_session_data(workspace_path)

        if "created_at" in data:
            return data["created_at"]

        session = data.get("session", {})
        return session.get("createdAt") or session.get("created_at")

    def get_completed_at(self, workspace_path: Path) -> str | None:
        """Read completed_at timestamp from session.json."""
        data = self.get_session_data(workspace_path)

        if "completed_at" in data:
            return data["completed_at"]

        session = data.get("session", {})
        return session.get("completedAt") or session.get("completed_at")

    def get_links(self, workspace_path: Path) -> dict:
        """Read links from session.json, returning defaults if absent."""
        data = self.get_session_data(workspace_path)

        if "links" in data:
            return data["links"]

        return {"parent_workspace": None, "child_workspaces": []}

    def set_parent_workspace(self, workspace_path: Path, parent_name: str) -> None:
        """Set the parent workspace link for a black-box workspace."""
        links = self.get_links(workspace_path)
        links["parent_workspace"] = parent_name
        self.update_session(workspace_path, {"links": links})

    def add_child_workspace(self, workspace_path: Path, child_name: str) -> None:
        """Add a child workspace link to a white-box workspace."""
        links = self.get_links(workspace_path)
        children = links.get("child_workspaces", [])
        if child_name not in children:
            children.append(child_name)
        links["child_workspaces"] = children
        self.update_session(workspace_path, {"links": links})

    def mark_completed(self, workspace_path: Path) -> None:
        """Mark workspace status as completed with timestamp."""
        self.update_session(workspace_path, {
            "status": "completed",
            "completed_at": time.time(),
        })
```

- [x] **Step 9: Run all session tests**

Run: `uv run pytest packages/core/tests/test_session.py -v`
Expected: All 14 tests PASS

- [x] **Step 10: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(core): enhance session data model with scan_type, status, links, and backward compat"
```

---

### Task 2: Config Model — Add `auto_detect_whitebox` Option

**Files:**
- Modify: `packages/core/src/shannon_core/models/config.py`
- Test: `packages/core/tests/test_config.py`

- [x] **Step 1: Write the failing test**

Append to `packages/core/tests/test_config.py`:

```python
def test_auto_detect_whitebox_default():
    """auto_detect_whitebox should default to True."""
    c = Config()
    assert c.auto_detect_whitebox is True


def test_auto_detect_whitebox_disabled():
    """auto_detect_whitebox can be set to False."""
    c = Config(auto_detect_whitebox=False)
    assert c.auto_detect_whitebox is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_config.py::test_auto_detect_whitebox_default -v`
Expected: FAIL — `Config` has no field `auto_detect_whitebox`

- [x] **Step 3: Add `auto_detect_whitebox` field to Config**

In `packages/core/src/shannon_core/models/config.py`, add the field to the `Config` class:

```python
class Config(BaseModel):
    rules: Rules | None = None
    authentication: Authentication | None = None
    pipeline: PipelineConfig | None = None
    description: str | None = None
    vuln_classes: list[VulnClass] | None = None
    exploit: bool = True
    report: ReportConfig | None = None
    rules_of_engagement: str | None = None
    auto_detect_whitebox: bool = True
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_config.py -v`
Expected: All tests PASS (including the 2 new ones)

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/config.py packages/core/tests/test_config.py
git commit -m "feat(core): add auto_detect_whitebox config option (default True)"
```

---

### Task 3: Shared Workspace Utility Module

**Files:**
- Create: `packages/core/src/shannon_core/workspace.py`
- Create: `packages/core/tests/test_workspace.py`

- [x] **Step 1: Write failing tests for URL normalization**

Create `packages/core/tests/test_workspace.py`:

```python
import json
from pathlib import Path

import pytest

from shannon_core.workspace import (
    compute_deliverables_summary,
    find_latest_workspace,
    find_workspaces_by_url,
    get_workspace_info,
    normalize_url,
    urls_match,
)


class TestNormalizeUrl:
    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/") == "https://example.com"

    def test_strips_default_port_443(self):
        assert normalize_url("https://example.com:443/path") == "https://example.com/path"

    def test_strips_default_port_80(self):
        assert normalize_url("http://example.com:80/path") == "http://example.com/path"

    def test_keeps_non_default_port(self):
        assert normalize_url("https://example.com:8443/path") == "https://example.com:8443/path"

    def test_lowercase_hostname(self):
        assert normalize_url("https://Example.COM/Path") == "https://example.com/Path"

    def test_removes_fragment(self):
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"


class TestUrlsMatch:
    def test_exact_match(self):
        assert urls_match("https://example.com", "https://example.com") is True

    def test_scheme_tolerated(self):
        assert urls_match("http://example.com", "https://example.com") is True

    def test_trailing_slash_ignored(self):
        assert urls_match("https://example.com/", "https://example.com") is True

    def test_different_hosts(self):
        assert urls_match("https://example.com", "https://api.example.com") is False

    def test_different_ports(self):
        assert urls_match("https://example.com:8443", "https://example.com:9443") is False

    def test_path_prefix_match(self):
        assert urls_match("https://example.com/app", "https://example.com/app/api") is True

    def test_path_no_match(self):
        assert urls_match("https://example.com/app", "https://example.com/other") is False

    def test_default_port_vs_no_port(self):
        assert urls_match("https://example.com:443", "https://example.com") is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_workspace.py::TestNormalizeUrl -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.workspace'`

- [x] **Step 3: Implement `normalize_url` and `urls_match`**

Create `packages/core/src/shannon_core/workspace.py`:

```python
"""Shared workspace discovery and query utilities for cross-scan UX."""

import json
from pathlib import Path
from urllib.parse import urlparse

from shannon_core.session import SessionManager


def normalize_url(url: str) -> str:
    """Normalize a URL for comparison: strip trailing slash, default ports, lowercase host."""
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    path = parsed.path.rstrip("/")

    # Strip default ports
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None

    # Reconstruct
    netloc = host
    if port:
        netloc = f"{host}:{port}"

    normalized = f"{scheme}://{netloc}{path}"
    return normalized


def urls_match(url_a: str, url_b: str) -> bool:
    """Check if two URLs refer to the same target (scheme-tolerant, path-prefix aware).

    Rules from spec:
    - Strip trailing / from both
    - Scheme difference (http vs https) is tolerated
    - Hostname must match exactly
    - Port differences mean different targets
    - Path prefix match counts as same target
    """
    a = urlparse(url_a)
    b = urlparse(url_b)

    # Hostname must match exactly
    host_a = (a.hostname or "").lower()
    host_b = (b.hostname or "").lower()
    if host_a != host_b:
        return False

    # Port comparison (accounting for default ports)
    port_a = a.port or (443 if a.scheme == "https" else 80)
    port_b = b.port or (443 if b.scheme == "https" else 80)
    if port_a != port_b:
        return False

    # Path prefix match
    path_a = (a.path or "/").rstrip("/") or "/"
    path_b = (b.path or "/").rstrip("/") or "/"

    # One path must be a prefix of the other
    return path_a.startswith(path_b) or path_b.startswith(path_a)


def _is_valid_queue_file(filepath: Path) -> bool:
    """Check that a file exists, is non-empty, and parses as valid JSON with vulnerabilities."""
    if not filepath.exists() or filepath.stat().st_size == 0:
        return False
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        vulns = data.get("vulnerabilities", [])
        return isinstance(vulns, list)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def compute_deliverables_summary(workspace_path: Path) -> dict:
    """Scan the deliverables directory and return a summary of vuln queues and reports.

    Returns: {"vuln_queues": [...], "reports": [...]}
    """
    deliverables_dir = workspace_path / "deliverables"
    vuln_queues: list[str] = []
    reports: list[str] = []

    if not deliverables_dir.exists():
        return {"vuln_queues": vuln_queues, "reports": reports}

    # Check per-class exploitation queue files: {class}_exploitation_queue.json
    for f in sorted(deliverables_dir.iterdir()):
        if f.is_file() and f.name.endswith("_exploitation_queue.json"):
            vuln_class = f.name.replace("_exploitation_queue.json", "")
            if _is_valid_queue_file(f):
                vuln_queues.append(vuln_class)

    # Also check the generic exploitation_queue.json
    generic_queue = deliverables_dir / "exploitation_queue.json"
    if generic_queue.exists() and _is_valid_queue_file(generic_queue):
        if "" not in vuln_queues:
            vuln_queues.insert(0, "general")

    # Collect report files (*.md)
    for f in sorted(deliverables_dir.iterdir()):
        if f.is_file() and f.name.endswith(".md"):
            reports.append(f.name)

    return {"vuln_queues": vuln_queues, "reports": reports}


def find_latest_workspace(
    workspaces_dir: Path,
    scan_type: str = "whitebox",
    url: str | None = None,
) -> Path | None:
    """Find the most recent workspace matching scan_type with valid deliverables.

    Args:
        workspaces_dir: Root workspaces directory
        scan_type: Filter by scan_type ("whitebox" or "blackbox")
        url: Optional URL to prioritize matching against

    Returns: Path to the workspace directory, or None
    """
    mgr = SessionManager(workspaces_dir)
    workspaces = mgr.list_workspaces()

    # If URL provided, prioritize URL matches first
    if url:
        url_matches = [ws for ws in workspaces if urls_match(mgr.get_web_url(ws) or "", url)]
        non_url = [ws for ws in workspaces if ws not in url_matches]
        workspaces = url_matches + non_url

    for ws in workspaces:
        if mgr.get_scan_type(ws) != scan_type:
            continue
        if mgr.get_status(ws) not in ("completed", "unknown"):
            continue
        summary = compute_deliverables_summary(ws)
        if summary["vuln_queues"]:
            return ws

    return None


def find_workspaces_by_url(
    workspaces_dir: Path,
    url: str,
    scan_type: str = "whitebox",
) -> list[tuple[Path, dict]]:
    """Find all workspaces matching a target URL with valid deliverables.

    Returns: List of (workspace_path, deliverables_summary) tuples, sorted newest first.
    """
    mgr = SessionManager(workspaces_dir)
    workspaces = mgr.list_workspaces()
    results = []

    for ws in workspaces:
        ws_url = mgr.get_web_url(ws)
        if not ws_url or not urls_match(ws_url, url):
            continue
        if mgr.get_scan_type(ws) != scan_type:
            continue
        summary = compute_deliverables_summary(ws)
        if summary["vuln_queues"]:
            results.append((ws, summary))

    return results


def get_workspace_info(workspace_path: Path) -> dict:
    """Compute full workspace info for display.

    Returns a dict with all fields needed for `workspace show` and listing commands.
    """
    workspaces_dir = workspace_path.parent
    mgr = SessionManager(workspaces_dir)
    data = mgr.get_session_data(workspace_path)

    return {
        "name": workspace_path.name,
        "scan_type": mgr.get_scan_type(workspace_path),
        "status": mgr.get_status(workspace_path),
        "web_url": mgr.get_web_url(workspace_path),
        "repo_path": data.get("repo_path") or data.get("session", {}).get("repoPath"),
        "created_at": mgr.get_created_at(workspace_path),
        "completed_at": mgr.get_completed_at(workspace_path),
        "links": mgr.get_links(workspace_path),
        "deliverables_summary": compute_deliverables_summary(workspace_path),
    }
```

- [x] **Step 4: Run URL normalization tests**

Run: `uv run pytest packages/core/tests/test_workspace.py::TestNormalizeUrl packages/core/tests/test_workspace.py::TestUrlsMatch -v`
Expected: All PASS

- [x] **Step 5: Commit URL utilities**

```bash
git add packages/core/src/shannon_core/workspace.py packages/core/tests/test_workspace.py
git commit -m "feat(core): add workspace utility module with URL matching"
```

- [x] **Step 6: Write failing tests for `compute_deliverables_summary`**

Append to `packages/core/tests/test_workspace.py`:

```python
class TestComputeDeliverablesSummary:
    def test_empty_workspace(self, tmp_path):
        """Workspace with no deliverables returns empty summary."""
        ws = tmp_path / "workspaces" / "test-ws"
        ws.mkdir(parents=True)
        summary = compute_deliverables_summary(ws)
        assert summary == {"vuln_queues": [], "reports": []}

    def test_valid_queue_file(self, tmp_path):
        """Detects valid per-class exploitation queue files."""
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
        summary = compute_deliverables_summary(ws)
        assert "injection" in summary["vuln_queues"]

    def test_empty_queue_file_ignored(self, tmp_path):
        """Empty queue files (no vulnerabilities) are not counted."""
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": []}), encoding="utf-8"
        )
        summary = compute_deliverables_summary(ws)
        assert "xss" not in summary["vuln_queues"]

    def test_invalid_json_ignored(self, tmp_path):
        """Invalid JSON queue files are not counted."""
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "auth_exploitation_queue.json").write_text("not json", encoding="utf-8")
        summary = compute_deliverables_summary(ws)
        assert "auth" not in summary["vuln_queues"]

    def test_reports_collected(self, tmp_path):
        """Markdown report files are collected."""
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "executive_summary.md").write_text("# Summary", encoding="utf-8")
        (deliverables / "injection_findings.md").write_text("# Findings", encoding="utf-8")
        summary = compute_deliverables_summary(ws)
        assert "executive_summary.md" in summary["reports"]
        assert "injection_findings.md" in summary["reports"]

    def test_multiple_vuln_queues(self, tmp_path):
        """Multiple valid queue files are all detected."""
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        for vc in ["injection", "xss", "auth"]:
            (deliverables / f"{vc}_exploitation_queue.json").write_text(
                json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
            )
        summary = compute_deliverables_summary(ws)
        assert set(summary["vuln_queues"]) == {"injection", "xss", "auth"}
```

- [x] **Step 7: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_workspace.py::TestComputeDeliverablesSummary -v`
Expected: All 6 PASS

- [x] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/workspace.py packages/core/tests/test_workspace.py
git commit -m "feat(core): add compute_deliverables_summary for scanning workspace deliverables"
```

- [x] **Step 9: Write failing tests for `find_latest_workspace`**

Append to `packages/core/tests/test_workspace.py`:

```python
def _create_workspace_with_queues(
    tmp_path: Path, name: str, web_url: str, scan_type: str, vuln_classes: list[str]
) -> Path:
    """Helper: create a workspace with valid exploitation queue files."""
    from shannon_core.session import SessionManager

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace(web_url, "/repo", name=name, scan_type=scan_type)
    mgr.mark_completed(ws)

    deliverables = ws / "deliverables"
    deliverables.mkdir(parents=True)
    for vc in vuln_classes:
        (deliverables / f"{vc}_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
    return ws


class TestFindLatestWorkspace:
    def test_finds_most_recent_whitebox(self, tmp_path):
        """Returns the most recent whitebox workspace with valid deliverables."""
        ws_dir = tmp_path / "workspaces"

        _create_workspace_with_queues(tmp_path, "ws-old", "https://old.com", "whitebox", ["injection"])
        import time
        time.sleep(0.01)
        _create_workspace_with_queues(tmp_path, "ws-new", "https://new.com", "whitebox", ["xss"])

        result = find_latest_workspace(ws_dir)
        assert result is not None
        assert result.name == "ws-new"

    def test_skips_blackbox(self, tmp_path):
        """Skips blackbox workspaces when looking for whitebox."""
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "bb-ws", "https://test.com", "blackbox", ["injection"])

        result = find_latest_workspace(ws_dir, scan_type="whitebox")
        assert result is None

    def test_skips_empty_deliverables(self, tmp_path):
        """Skips workspaces with no valid exploitation queues."""
        from shannon_core.session import SessionManager

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://empty.com", "/repo", name="empty-ws")
        mgr.mark_completed(ws)
        # No deliverables dir

        result = find_latest_workspace(tmp_path / "workspaces")
        assert result is None

    def test_no_workspaces(self, tmp_path):
        """Returns None when no workspaces exist."""
        result = find_latest_workspace(tmp_path / "workspaces")
        assert result is None

    def test_url_prioritization(self, tmp_path):
        """When url is given, prioritize workspaces matching that URL."""
        ws_dir = tmp_path / "workspaces"

        _create_workspace_with_queues(tmp_path, "ws-other", "https://other.com", "whitebox", ["injection"])
        import time
        time.sleep(0.01)
        _create_workspace_with_queues(tmp_path, "ws-target", "https://target.com", "whitebox", ["xss"])

        # ws-target is newer, but we prioritize URL match for other.com
        # Both match so newest wins
        result = find_latest_workspace(ws_dir, url="https://target.com")
        assert result is not None
        assert result.name == "ws-target"
```

- [x] **Step 10: Run tests**

Run: `uv run pytest packages/core/tests/test_workspace.py::TestFindLatestWorkspace -v`
Expected: All 5 PASS

- [x] **Step 11: Commit**

```bash
git add packages/core/tests/test_workspace.py
git commit -m "test(core): add find_latest_workspace tests"
```

- [x] **Step 12: Write failing tests for `find_workspaces_by_url`**

Append to `packages/core/tests/test_workspace.py`:

```python
class TestFindWorkspacesByUrl:
    def test_finds_matching_workspaces(self, tmp_path):
        """Finds all workspaces matching a target URL."""
        ws_dir = tmp_path / "workspaces"

        _create_workspace_with_queues(tmp_path, "ws1", "https://myapp.com", "whitebox", ["injection"])
        _create_workspace_with_queues(tmp_path, "ws2", "https://other.com", "whitebox", ["xss"])
        _create_workspace_with_queues(tmp_path, "ws3", "https://myapp.com", "whitebox", ["auth"])

        results = find_workspaces_by_url(ws_dir, "https://myapp.com")
        assert len(results) == 2
        names = [r[0].name for r in results]
        assert "ws1" in names
        assert "ws3" in names

    def test_scheme_tolerant(self, tmp_path):
        """Matches http and https versions of same URL."""
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws-http", "https://myapp.com", "whitebox", ["injection"])

        results = find_workspaces_by_url(ws_dir, "http://myapp.com")
        assert len(results) == 1

    def test_excludes_no_deliverables(self, tmp_path):
        """Excludes workspaces without valid deliverables."""
        from shannon_core.session import SessionManager

        mgr = SessionManager(tmp_path / "workspaces")
        mgr.create_workspace("https://myapp.com", "/repo", name="empty-ws")
        # No deliverables

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0

    def test_no_matches(self, tmp_path):
        """Returns empty list when no URLs match."""
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws1", "https://myapp.com", "whitebox", ["injection"])

        results = find_workspaces_by_url(ws_dir, "https://other.com")
        assert len(results) == 0
```

- [x] **Step 13: Run tests**

Run: `uv run pytest packages/core/tests/test_workspace.py::TestFindWorkspacesByUrl -v`
Expected: All 4 PASS

- [x] **Step 14: Commit**

```bash
git add packages/core/tests/test_workspace.py
git commit -m "test(core): add find_workspaces_by_url tests"
```

---

### Task 4: White-Box CLI Completion Output Improvements

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Test: `packages/whitebox/tests/test_cli.py`

- [x] **Step 1: Write failing test for enhanced completion output**

Append to `packages/whitebox/tests/test_cli.py`:

```python
def test_start_shows_workspace_and_next_steps(tmp_path, monkeypatch):
    """Completion output should show workspace name, deliverables path, and next-step commands."""
    monkeypatch.chdir(tmp_path)

    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {"status": "completed", "workspace_name": "myapp-20260603-143022"}

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure),
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Workspace:" in result.output
    assert "Next steps:" in result.output
    assert "shannon-blackbox start" in result.output
    assert "--latest" in result.output
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_start_shows_workspace_and_next_steps -v`
Expected: FAIL — "Workspace:" not found in output

- [x] **Step 3: Implement enhanced completion output**

Update the `start` command in `packages/whitebox/src/shannon_whitebox/cli/main.py`:

```python
@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (supports resume)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address):
    """Start a white-box security scan."""
    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting white-box scan on {repo}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
        ws_name = result.get("workspace_name", "unknown")
        deliverables_path = result.get("deliverables_path", "")
        web_url = result.get("web_url", "<target-url>")

        click.echo("")
        click.echo("✅ White-box scan complete.")
        click.echo("")
        click.echo(f"  Workspace:     {ws_name}")
        if deliverables_path:
            click.echo(f"  Deliverables:  {deliverables_path}")
        click.echo("")
        click.echo("  Next steps:")
        click.echo(f"    shannon-blackbox start --url {web_url} -w {ws_name}")
        click.echo("    # or use --latest to reuse the most recent white-box results:")
        click.echo(f"    shannon-blackbox start --url {web_url} --latest")
    else:
        click.echo(f"Scan failed: {result.get('error', 'unknown error')}")
        raise SystemExit(1)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_start_shows_workspace_and_next_steps -v`
Expected: PASS

- [x] **Step 5: Run all whitebox CLI tests for no regressions**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): enhanced completion output with workspace name and next-step commands"
```

---

### Task 5: Black-Box `--latest` Flag

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [x] **Step 1: Write failing test for `--latest` help text**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_start_help_shows_latest_option():
    """Blackbox start --help should list --latest."""
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--latest" in result.output
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_start_help_shows_latest_option -v`
Expected: FAIL — "--latest" not found

- [x] **Step 3: Add `--latest` parameter to blackbox start command**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, update the `start` command signature and add resolution logic:

```python
@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("--latest", is_flag=True, help="Reuse the most recent white-box workspace deliverables")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput
    from shannon_core.workspace import compute_deliverables_summary, find_latest_workspace

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    # Resolve --latest: find most recent whitebox workspace with deliverables
    resolved_workspace = workspace
    if latest and not workspace:
        wb_ws = find_latest_workspace(Path("workspaces"), scan_type="whitebox", url=url)
        if wb_ws is None:
            click.echo("No white-box workspaces found. Run a white-box scan first.")
            raise SystemExit(1)
        summary = compute_deliverables_summary(wb_ws)
        if not summary["vuln_queues"]:
            click.echo("Latest workspace has no deliverables. Specify a workspace with -w.")
            raise SystemExit(1)
        resolved_workspace = wb_ws.name
        queues = ", ".join(summary["vuln_queues"])
        click.echo(f"🔗 Found white-box results in workspace '{wb_ws.name}'")
        click.echo(f"   Vulnerability queues found: {queues}")
        click.echo("   Skipping recon phase — leveraging white-box findings directly.")

    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=str(Path(repo).resolve()) if repo else None,
        workspace_name=resolved_workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.status == "completed":
        if result.has_whitebox_results:
            classes = result.found_whitebox_classes
            click.echo(f"Scan completed (leveraged whitebox results for: {', '.join(classes)})")
        else:
            click.echo("Scan completed (standalone — no whitebox results found)")
    else:
        error_msg = result.errors[-1] if result.errors else "unknown error"
        click.echo(f"Scan failed: {error_msg}")
        raise SystemExit(1)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_start_help_shows_latest_option -v`
Expected: PASS

- [x] **Step 5: Write failing tests for `--latest` resolution logic**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_latest_resolves_to_workspace(tmp_path, monkeypatch):
    """--latest should resolve to the most recent whitebox workspace with deliverables."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    # Create a whitebox workspace with deliverables
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-wb", scan_type="whitebox")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    captured_input = None

    async def fake_run_scan(input, temporal_address):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input is not None
    assert captured_input.workspace_name == "myapp-wb"
    assert "Found white-box results" in result.output


def test_latest_no_workspaces(tmp_path, monkeypatch):
    """--latest with no workspaces should print error and exit 1."""
    monkeypatch.chdir(tmp_path)

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "--latest"])

    assert result.exit_code == 1
    assert "No white-box workspaces found" in result.output


def test_w_takes_precedence_over_latest(tmp_path, monkeypatch):
    """When both -w and --latest are given, -w wins."""
    monkeypatch.chdir(tmp_path)

    captured_input = None

    async def fake_run_scan(input, temporal_address):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "-w", "my-ws", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input.workspace_name == "my-ws"
```

- [x] **Step 6: Run tests**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_latest_resolves_to_workspace packages/blackbox/tests/test_cli.py::test_latest_no_workspaces packages/blackbox/tests/test_cli.py::test_w_takes_precedence_over_latest -v`
Expected: All 3 PASS

- [x] **Step 7: Run all blackbox CLI tests**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: All PASS

- [x] **Step 8: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add --latest flag to resolve most recent whitebox workspace"
```

---

### Task 6: Same-Target Auto-Detection

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [x] **Step 1: Write failing tests for auto-detection**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_auto_detect_single_match(tmp_path, monkeypatch):
    """When one matching whitebox workspace exists, prompt user to reuse."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-wb", scan_type="whitebox")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    captured_input = None

    async def fake_run_scan(input, temporal_address):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        # Accept the default 'Y' prompt
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com"], input="Y\n")

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Detected white-box results" in result.output
    assert captured_input.workspace_name == "myapp-wb"


def test_auto_detect_declined(tmp_path, monkeypatch):
    """When user declines auto-detection, run standalone."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-wb", scan_type="whitebox")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    captured_input = None

    async def fake_run_scan(input, temporal_address):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com"], input="n\n")

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input.workspace_name is None


def test_auto_detect_no_match(tmp_path, monkeypatch):
    """When no matching workspace exists, run standalone with tip."""
    monkeypatch.chdir(tmp_path)

    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No white-box results found" in result.output
    assert "--latest" in result.output
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_auto_detect_single_match -v`
Expected: FAIL — "Detected white-box results" not in output

- [x] **Step 3: Implement auto-detection in the `start` command**

Update the `start` command in `packages/blackbox/src/shannon_blackbox/cli/main.py` — replace the full `start` function:

```python
from shannon_core.workspace import compute_deliverables_summary, find_latest_workspace, find_workspaces_by_url


@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("--latest", is_flag=True, help="Reuse the most recent white-box workspace deliverables")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    # Resolve workspace: -w > --latest > auto-detect
    resolved_workspace = workspace

    if latest and not workspace:
        # --latest: auto-find most recent whitebox workspace
        wb_ws = find_latest_workspace(Path("workspaces"), scan_type="whitebox", url=url)
        if wb_ws is None:
            click.echo("No white-box workspaces found. Run a white-box scan first.")
            raise SystemExit(1)
        summary = compute_deliverables_summary(wb_ws)
        if not summary["vuln_queues"]:
            click.echo("Latest workspace has no deliverables. Specify a workspace with -w.")
            raise SystemExit(1)
        resolved_workspace = wb_ws.name
        queues = ", ".join(summary["vuln_queues"])
        click.echo(f"🔗 Found white-box results in workspace '{wb_ws.name}'")
        click.echo(f"   Vulnerability queues found: {queues}")
        click.echo("   Skipping recon phase — leveraging white-box findings directly.")

    elif not workspace and not latest:
        # Auto-detect: find whitebox workspaces for the same target URL
        matches = find_workspaces_by_url(Path("workspaces"), url, scan_type="whitebox")

        if len(matches) == 1:
            ws_path, summary = matches[0]
            click.echo(f"🔍 Detected white-box results for '{url}' (workspace: {ws_path.name})")
            if click.confirm("   Reuse these results?", default=True):
                resolved_workspace = ws_path.name
                queues = ", ".join(summary["vuln_queues"])
                click.echo(f"   Using workspace '{ws_path.name}' ({queues})")
            else:
                click.echo("ℹ️  Running standalone black-box scan.")

        elif len(matches) > 1:
            click.echo(f"🔍 Found {len(matches)} white-box workspaces for '{url}':")
            for i, (ws_path, summary) in enumerate(matches, 1):
                queues = ", ".join(summary["vuln_queues"])
                click.echo(f"  [{i}] {ws_path.name}  ({queues})")
            click.echo("")
            choice = click.prompt(
                "Select workspace to reuse [1-{}] or 'n' for standalone".format(len(matches)),
                default="1",
            )
            if choice.strip().lower() == "n":
                click.echo("ℹ️  Running standalone black-box scan.")
            else:
                try:
                    idx = int(choice.strip()) - 1
                    if 0 <= idx < len(matches):
                        resolved_workspace = matches[idx][0].name
                        click.echo(f"   Using workspace '{resolved_workspace}'")
                    else:
                        click.echo("ℹ️  Invalid selection. Running standalone.")
                except ValueError:
                    click.echo("ℹ️  Invalid selection. Running standalone.")

        else:
            click.echo("ℹ️  No white-box results found for this target. Running standalone black-box scan.")
            click.echo("   Tip: run white-box first, then use --latest to reuse results.")

    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=str(Path(repo).resolve()) if repo else None,
        workspace_name=resolved_workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.status == "completed":
        if result.has_whitebox_results:
            classes = result.found_whitebox_classes
            click.echo(f"Scan completed (leveraged whitebox results for: {', '.join(classes)})")
        else:
            click.echo("Scan completed (standalone — no whitebox results found)")
    else:
        error_msg = result.errors[-1] if result.errors else "unknown error"
        click.echo(f"Scan failed: {error_msg}")
        raise SystemExit(1)
```

- [x] **Step 4: Run auto-detection tests**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_auto_detect_single_match packages/blackbox/tests/test_cli.py::test_auto_detect_declined packages/blackbox/tests/test_cli.py::test_auto_detect_no_match -v`
Expected: All 3 PASS

- [x] **Step 5: Run all blackbox CLI tests**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add same-target auto-detection with interactive prompt"
```

---

### Task 7: Enhanced Workspaces Listing

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/whitebox/tests/test_cli.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [x] **Step 1: Write failing test for grouped workspace listing**

Append to `packages/whitebox/tests/test_cli.py`:

```python
def test_workspaces_grouped_by_scan_type(tmp_path, monkeypatch):
    """workspaces command should group output by scan_type."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    wb = mgr.create_workspace("https://myapp.com", "/repo", name="wb-1", scan_type="whitebox")
    mgr.mark_completed(wb)
    deliverables = wb / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    bb = mgr.create_workspace("https://myapp.com", "/repo", name="bb-1", scan_type="blackbox")
    mgr.set_parent_workspace(bb, "wb-1")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces"])

    assert result.exit_code == 0
    assert "White-box workspaces:" in result.output
    assert "Black-box workspaces:" in result.output
    assert "wb-1" in result.output
    assert "bb-1" in result.output
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspaces_grouped_by_scan_type -v`
Expected: FAIL — "White-box workspaces:" not in output

- [x] **Step 3: Implement grouped workspace listing in whitebox CLI**

Update the `workspaces` command in `packages/whitebox/src/shannon_whitebox/cli/main.py`:

```python
@cli.command()
def workspaces():
    """List all workspaces grouped by scan type."""
    from shannon_core.workspace import compute_deliverables_summary

    mgr = SessionManager(Path("workspaces"))
    all_ws = mgr.list_workspaces()

    whitebox = []
    blackbox = []
    for ws in all_ws:
        info = {
            "name": ws.name,
            "url": mgr.get_web_url(ws) or "unknown",
            "status": mgr.get_status(ws),
            "scan_type": mgr.get_scan_type(ws),
            "summary": compute_deliverables_summary(ws),
            "links": mgr.get_links(ws),
        }
        if info["scan_type"] == "blackbox":
            blackbox.append(info)
        else:
            whitebox.append(info)

    if whitebox:
        click.echo("")
        click.echo("White-box workspaces:")
        click.echo(f"  {'NAME':<30} {'TARGET':<25} {'STATUS':<12} {'VULN QUEUES':<20}")
        for info in whitebox:
            queues = ", ".join(info["summary"]["vuln_queues"]) or "-"
            click.echo(f"  {info['name']:<30} {info['url']:<25} {info['status']:<12} {queues:<20}")

    if blackbox:
        click.echo("")
        click.echo("Black-box workspaces:")
        click.echo(f"  {'NAME':<30} {'TARGET':<25} {'STATUS':<12} {'PARENT WORKSPACE':<30}")
        for info in blackbox:
            parent = info["links"].get("parent_workspace") or "-"
            click.echo(f"  {info['name']:<30} {info['url']:<25} {info['status']:<12} {parent:<30}")

    if not whitebox and not blackbox:
        click.echo("No workspaces found.")
```

- [x] **Step 4: Run test**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspaces_grouped_by_scan_type -v`
Expected: PASS

- [x] **Step 5: Write failing test for blackbox grouped listing**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_workspaces_grouped_by_scan_type(tmp_path, monkeypatch):
    """workspaces command should group output by scan_type."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    wb = mgr.create_workspace("https://myapp.com", "/repo", name="wb-1", scan_type="whitebox")
    mgr.mark_completed(wb)
    deliverables = wb / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    bb = mgr.create_workspace("https://myapp.com", "/repo", name="bb-1", scan_type="blackbox")
    mgr.set_parent_workspace(bb, "wb-1")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces"])

    assert result.exit_code == 0
    assert "White-box workspaces:" in result.output
    assert "Black-box workspaces:" in result.output
```

- [x] **Step 6: Implement grouped listing in blackbox CLI**

Update the `workspaces` command in `packages/blackbox/src/shannon_blackbox/cli/main.py`:

```python
@cli.command()
def workspaces():
    """List all workspaces grouped by scan type."""
    from shannon_core.workspace import compute_deliverables_summary

    mgr = SessionManager(Path("workspaces"))
    all_ws = mgr.list_workspaces()

    whitebox = []
    blackbox = []
    for ws in all_ws:
        info = {
            "name": ws.name,
            "url": mgr.get_web_url(ws) or "unknown",
            "status": mgr.get_status(ws),
            "scan_type": mgr.get_scan_type(ws),
            "summary": compute_deliverables_summary(ws),
            "links": mgr.get_links(ws),
        }
        if info["scan_type"] == "blackbox":
            blackbox.append(info)
        else:
            whitebox.append(info)

    if whitebox:
        click.echo("")
        click.echo("White-box workspaces:")
        click.echo(f"  {'NAME':<30} {'TARGET':<25} {'STATUS':<12} {'VULN QUEUES':<20}")
        for info in whitebox:
            queues = ", ".join(info["summary"]["vuln_queues"]) or "-"
            click.echo(f"  {info['name']:<30} {info['url']:<25} {info['status']:<12} {queues:<20}")

    if blackbox:
        click.echo("")
        click.echo("Black-box workspaces:")
        click.echo(f"  {'NAME':<30} {'TARGET':<25} {'STATUS':<12} {'PARENT WORKSPACE':<30}")
        for info in blackbox:
            parent = info["links"].get("parent_workspace") or "-"
            click.echo(f"  {info['name']:<30} {info['url']:<25} {info['status']:<12} {parent:<30}")

    if not whitebox and not blackbox:
        click.echo("No workspaces found.")
```

- [x] **Step 7: Run both listing tests**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspaces_grouped_by_scan_type packages/blackbox/tests/test_cli.py::test_workspaces_grouped_by_scan_type -v`
Expected: Both PASS

- [x] **Step 8: Run all CLI tests**

Run: `uv run pytest packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py -v`
Expected: All PASS

- [x] **Step 9: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py
git commit -m "feat: enhanced workspaces listing grouped by scan_type with table layout"
```

---

### Task 8: `workspace show` Subcommand

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/whitebox/tests/test_cli.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [x] **Step 1: Write failing test for `workspace show`**

Append to `packages/whitebox/tests/test_cli.py`:

```python
def test_workspace_show(tmp_path, monkeypatch):
    """workspace show should display detailed workspace info."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-wb", scan_type="whitebox")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )
    (deliverables / "executive_summary.md").write_text("# Summary", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "myapp-wb"])

    assert result.exit_code == 0
    assert "myapp-wb" in result.output
    assert "whitebox" in result.output
    assert "https://myapp.com" in result.output
    assert "injection_exploitation_queue.json" in result.output
    assert "executive_summary.md" in result.output


def test_workspace_show_not_found(tmp_path, monkeypatch):
    """workspace show with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "nonexistent"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspace_show -v`
Expected: FAIL — `No such command "workspace"`

- [x] **Step 3: Implement `workspace show` in whitebox CLI**

Add a `workspace` group and `show` subcommand in `packages/whitebox/src/shannon_whitebox/cli/main.py`, after the existing `logs` command and before `main()`:

```python
@cli.group()
def workspace():
    """Workspace management commands."""


@workspace.command()
@click.argument("workspace_name")
def show(workspace_name):
    """Show detailed workspace information."""
    from shannon_core.workspace import compute_deliverables_summary, get_workspace_info

    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    info = get_workspace_info(ws)

    click.echo(f"\nWorkspace: {info['name']}")
    click.echo(f"  Type:           {info['scan_type']}")
    click.echo(f"  Target:         {info['web_url'] or 'unknown'}")
    click.echo(f"  Repo:           {info['repo_path'] or 'unknown'}")
    click.echo(f"  Status:         {info['status']}")

    created = info["created_at"]
    completed = info["completed_at"]
    click.echo(f"  Created:        {created or 'unknown'}")
    click.echo(f"  Completed:      {completed or 'N/A'}")

    # Duration
    if created and completed:
        try:
            c_time = float(created)
            e_time = float(completed)
            duration_secs = int(e_time - c_time)
            hours, remainder = divmod(duration_secs, 3600)
            minutes, secs = divmod(remainder, 60)
            click.echo(f"  Duration:       {hours}h {minutes}m {secs}s")
        except (ValueError, TypeError):
            pass

    # Deliverables
    summary = info["deliverables_summary"]
    if summary["vuln_queues"] or summary["reports"]:
        click.echo("\n  Deliverables:")
        deliverables_dir = ws / "deliverables"
        for vc in summary["vuln_queues"]:
            filename = f"{vc}_exploitation_queue.json"
            filepath = deliverables_dir / filename
            if filepath.exists():
                try:
                    data = json.loads(filepath.read_text(encoding="utf-8"))
                    count = len(data.get("vulnerabilities", []))
                    click.echo(f"    ✅ {filename}  ({count} findings)")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    click.echo(f"    ⚠️  {filename}  (invalid)")
            else:
                click.echo(f"    ✅ {filename}")

        for report in summary["reports"]:
            click.echo(f"    ✅ {report}")

    # Links
    links = info["links"]
    children = links.get("child_workspaces", [])
    if children:
        click.echo("\n  Linked black-box scans:")
        for child in children:
            child_ws = mgr.get_workspace(child)
            if child_ws:
                child_status = mgr.get_status(child_ws)
                click.echo(f"    📋 {child} ({child_status})")
            else:
                click.echo(f"    📋 {child}")

    parent = links.get("parent_workspace")
    if parent:
        click.echo(f"\n  Parent workspace: {parent}")

    # Reuse command
    url = info["web_url"]
    if url and info["scan_type"] == "whitebox":
        click.echo(f"\n  Reuse command:")
        click.echo(f"    shannon-blackbox start --url {url} -w {info['name']}")
```

Also add `import json` at the top of the file (needed for `workspace show` to parse queue files):

```python
import json
```

- [x] **Step 4: Run tests**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspace_show packages/whitebox/tests/test_cli.py::test_workspace_show_not_found -v`
Expected: Both PASS

- [x] **Step 5: Write failing test for blackbox `workspace show`**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_workspace_show(tmp_path, monkeypatch):
    """workspace show should display detailed workspace info."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-bb", scan_type="blackbox")
    mgr.set_parent_workspace(ws, "wb-parent")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "myapp-bb"])

    assert result.exit_code == 0
    assert "myapp-bb" in result.output
    assert "blackbox" in result.output
    assert "wb-parent" in result.output


def test_workspace_show_not_found(tmp_path, monkeypatch):
    """workspace show with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "nonexistent"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
```

- [x] **Step 6: Implement `workspace show` in blackbox CLI**

Add a `workspace` group and `show` subcommand in `packages/blackbox/src/shannon_blackbox/cli/main.py`, after the existing `logs` command and before `main()`. Use the exact same implementation as the whitebox version (identical code, shared `get_workspace_info` utility does all the work):

```python
@cli.group()
def workspace():
    """Workspace management commands."""


@workspace.command()
@click.argument("workspace_name")
def show(workspace_name):
    """Show detailed workspace information."""
    from shannon_core.workspace import compute_deliverables_summary, get_workspace_info

    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    info = get_workspace_info(ws)

    click.echo(f"\nWorkspace: {info['name']}")
    click.echo(f"  Type:           {info['scan_type']}")
    click.echo(f"  Target:         {info['web_url'] or 'unknown'}")
    click.echo(f"  Repo:           {info['repo_path'] or 'unknown'}")
    click.echo(f"  Status:         {info['status']}")

    created = info["created_at"]
    completed = info["completed_at"]
    click.echo(f"  Created:        {created or 'unknown'}")
    click.echo(f"  Completed:      {completed or 'N/A'}")

    # Duration
    if created and completed:
        try:
            c_time = float(created)
            e_time = float(completed)
            duration_secs = int(e_time - c_time)
            hours, remainder = divmod(duration_secs, 3600)
            minutes, secs = divmod(remainder, 60)
            click.echo(f"  Duration:       {hours}h {minutes}m {secs}s")
        except (ValueError, TypeError):
            pass

    # Deliverables
    summary = info["deliverables_summary"]
    if summary["vuln_queues"] or summary["reports"]:
        click.echo("\n  Deliverables:")
        deliverables_dir = ws / "deliverables"
        for vc in summary["vuln_queues"]:
            filename = f"{vc}_exploitation_queue.json"
            filepath = deliverables_dir / filename
            if filepath.exists():
                try:
                    data = json.loads(filepath.read_text(encoding="utf-8"))
                    count = len(data.get("vulnerabilities", []))
                    click.echo(f"    ✅ {filename}  ({count} findings)")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    click.echo(f"    ⚠️  {filename}  (invalid)")
            else:
                click.echo(f"    ✅ {filename}")

        for report in summary["reports"]:
            click.echo(f"    ✅ {report}")

    # Links
    links = info["links"]
    children = links.get("child_workspaces", [])
    if children:
        click.echo("\n  Linked black-box scans:")
        for child in children:
            child_ws = mgr.get_workspace(child)
            if child_ws:
                child_status = mgr.get_status(child_ws)
                click.echo(f"    📋 {child} ({child_status})")
            else:
                click.echo(f"    📋 {child}")

    parent = links.get("parent_workspace")
    if parent:
        click.echo(f"\n  Parent workspace: {parent}")

    # Reuse command
    url = info["web_url"]
    if url and info["scan_type"] == "whitebox":
        click.echo(f"\n  Reuse command:")
        click.echo(f"    shannon-blackbox start --url {url} -w {info['name']}")
```

Also add `import json` at the top of the file:

```python
import json
```

- [x] **Step 7: Run all tests**

Run: `uv run pytest packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py -v`
Expected: All PASS

- [x] **Step 8: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS across all packages

- [x] **Step 9: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py
git commit -m "feat: add workspace show subcommand to both CLIs"
```
