# Workspace Cross-Scan UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the handoff UX between white-box and black-box scans so users can discover and reuse cross-scan results seamlessly.

**Architecture:** Preserve the existing file-system-based workspace mechanism. Add new fields to session.json for scan linking, create a shared workspace discovery module in core, and enhance CLI output on both scanners with actionable next-step guidance.

**Tech Stack:** Python 3.12+, Click (CLI), Pydantic (models), pytest (testing), Temporal (workflows — not modified for discovery logic)

---

## File Structure

| File | Responsibility | Status |
|------|---------------|--------|
| `packages/core/src/shannon_core/session.py` | SessionManager with enhanced fields | Modify |
| `packages/core/src/shannon_core/workspace.py` | Workspace discovery: `find_latest_workspace`, `find_workspaces_by_url`, `get_workspace_info`, URL matching | Create |
| `packages/core/tests/test_session.py` | SessionManager tests | Modify |
| `packages/core/tests/test_workspace.py` | Workspace discovery tests | Create |
| `packages/whitebox/src/shannon_whitebox/worker.py` | Always create workspace, return workspace name | Modify |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Enhanced completion output, `workspace show` command | Modify |
| `packages/blackbox/src/shannon_blackbox/worker.py` | Always create workspace, return workspace name | Modify |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | `--latest` flag, auto-detection, enhanced output, `workspace show` command | Modify |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | Add `parent_workspace_name` to `BlackboxPipelineInput` | Modify |

---

### Task 1: Enhance SessionManager with new session.json fields

**Files:**
- Modify: `packages/core/src/shannon_core/session.py`
- Modify: `packages/core/tests/test_session.py`

This task adds the new session.json fields (`scan_type`, `status`, `completed_at`, `links`, `deliverables_summary`) and methods to write them. It also adds backward-compatible reading so old session.json files work without migration.

- [ ] **Step 1: Write failing tests for enhanced create_workspace**

Add to `packages/core/tests/test_session.py`:

```python
import time

def test_create_workspace_with_scan_type(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="whitebox")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "whitebox"
    assert data["status"] == "running"
    assert data["completed_at"] is None
    assert data["links"] == {"parent_workspace": None, "child_workspaces": []}
    assert data["deliverables_summary"] == {"vuln_queues": [], "reports": []}


def test_create_workspace_blackbox(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "blackbox"


def test_get_session_data_backward_compatible(tmp_path):
    """Legacy session.json without new fields should get defaults."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = tmp_path / "workspaces" / "legacy-ws"
    ws.mkdir(parents=True)
    legacy_data = {
        "web_url": "https://legacy.com",
        "repo_path": "/repo",
        "created_at": time.time(),
        "completed_agents": ["recon"],
        "metrics": {"agents": {}},
    }
    (ws / "session.json").write_text(json.dumps(legacy_data))
    data = mgr.get_session_data(ws)
    assert data["scan_type"] == "whitebox"
    assert data["status"] == "completed"
    assert data["links"] == {"parent_workspace": None, "child_workspaces": []}
    assert data["deliverables_summary"] == {"vuln_queues": [], "reports": []}


def test_update_scan_status(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="whitebox")
    mgr.update_scan_status(ws, "completed")
    data = json.loads((ws / "session.json").read_text())
    assert data["status"] == "completed"
    assert data["completed_at"] is not None


def test_write_deliverables_summary(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="whitebox")
    mgr.write_deliverables_summary(ws, {"vuln_queues": ["injection", "xss"], "reports": ["summary.md"]})
    data = json.loads((ws / "session.json").read_text())
    assert data["deliverables_summary"]["vuln_queues"] == ["injection", "xss"]
    assert data["deliverables_summary"]["reports"] == ["summary.md"]


def test_link_parent_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    parent_ws = mgr.create_workspace("https://example.com", "/repo", scan_type="whitebox")
    child_ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    mgr.link_parent_workspace(child_ws, parent_ws.name)
    mgr.add_child_workspace(parent_ws, child_ws.name)
    child_data = json.loads((child_ws / "session.json").read_text())
    parent_data = json.loads((parent_ws / "session.json").read_text())
    assert child_data["links"]["parent_workspace"] == parent_ws.name
    assert child_ws.name in parent_data["links"]["child_workspaces"]


def test_get_session_data_no_file(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    empty_dir = tmp_path / "workspaces" / "empty"
    empty_dir.mkdir(parents=True)
    data = mgr.get_session_data(empty_dir)
    assert data == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_session.py -v`
Expected: FAIL — `create_workspace` doesn't accept `scan_type`, new methods don't exist.

- [ ] **Step 3: Implement enhanced SessionManager**

Replace `packages/core/src/shannon_core/session.py` with:

```python
import json
import time
from pathlib import Path

from shannon_core.models.agents import AgentName

_DEFAULT_LINKS = {"parent_workspace": None, "child_workspaces": []}
_DEFAULT_DELIVERABLES_SUMMARY = {"vuln_queues": [], "reports": []}


class SessionManager:
    def __init__(self, workspaces_dir: Path):
        self.workspaces_dir = workspaces_dir
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(
        self,
        web_url: str,
        repo_path: str,
        name: str | None = None,
        scan_type: str = "whitebox",
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
            "created_at": time.time(),
            "scan_type": scan_type,
            "status": "running",
            "completed_at": None,
            "completed_agents": [],
            "links": {"parent_workspace": None, "child_workspaces": []},
            "deliverables_summary": {"vuln_queues": [], "reports": []},
            "metrics": {"agents": {}},
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
        data = json.loads(session_file.read_text(encoding="utf-8"))
        # Backward compatibility: fill defaults for legacy session.json files
        data.setdefault("scan_type", self._infer_scan_type(workspace_path, data))
        data.setdefault(
            "status",
            "completed" if data.get("completed_agents") else "unknown",
        )
        data.setdefault("completed_at", None)
        data.setdefault("links", dict(_DEFAULT_LINKS))
        data.setdefault("deliverables_summary", dict(_DEFAULT_DELIVERABLES_SUMMARY))
        return data

    def update_session(self, workspace_path: Path, data: dict) -> None:
        existing = self.get_session_data(workspace_path)
        existing.update(data)
        (workspace_path / "session.json").write_text(
            json.dumps(existing, indent=2, default=str), encoding="utf-8",
        )

    def update_scan_status(self, workspace_path: Path, status: str) -> None:
        updates = {"status": status}
        if status == "completed":
            updates["completed_at"] = time.time()
        self.update_session(workspace_path, updates)

    def write_deliverables_summary(self, workspace_path: Path, summary: dict) -> None:
        self.update_session(workspace_path, {"deliverables_summary": summary})

    def link_parent_workspace(self, workspace_path: Path, parent_name: str) -> None:
        data = self.get_session_data(workspace_path)
        data["links"]["parent_workspace"] = parent_name
        self.update_session(workspace_path, {"links": data["links"]})

    def add_child_workspace(self, workspace_path: Path, child_name: str) -> None:
        data = self.get_session_data(workspace_path)
        children = data["links"]["child_workspaces"]
        if child_name not in children:
            children.append(child_name)
        self.update_session(workspace_path, {"links": data["links"]})

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

    @staticmethod
    def _infer_scan_type(workspace_path: Path, data: dict) -> str:
        agents_dir = workspace_path / "agents"
        if agents_dir.exists():
            log_names = [p.name for p in agents_dir.iterdir()]
            if any("recon-blackbox" in n for n in log_names):
                return "blackbox"
        return "whitebox"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_session.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run full core test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v`
Expected: All existing tests still pass (new `scan_type` param has default value).

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(core): enhance SessionManager with scan_type, status, links, deliverables_summary

Add new session.json fields for cross-scan linking:
- scan_type (whitebox/blackbox)
- status (running/completed/failed)
- completed_at timestamp
- links (parent/child workspace references)
- deliverables_summary (vuln queues and reports)

Backward compatible: legacy session.json files get default values."
```

---

### Task 2: Create workspace discovery module

**Files:**
- Create: `packages/core/src/shannon_core/workspace.py`
- Create: `packages/core/tests/test_workspace.py`

This task creates the shared workspace discovery module with URL matching, latest-workspace finding, and by-URL search.

- [ ] **Step 1: Write failing tests for URL matching**

Create `packages/core/tests/test_workspace.py`:

```python
import json
import time

import pytest
from pathlib import Path

from shannon_core.workspace import normalize_url, urls_match


class TestNormalizeUrl:
    def test_basic_https(self):
        scheme, host, port, path = normalize_url("https://example.com")
        assert scheme == "https"
        assert host == "example.com"
        assert port == 443
        assert path == "/"

    def test_http_with_port(self):
        scheme, host, port, path = normalize_url("http://localhost:3000")
        assert scheme == "http"
        assert host == "localhost"
        assert port == 3000
        assert path == "/"

    def test_with_path(self):
        _, _, _, path = normalize_url("https://example.com/app/api")
        assert path == "/app/api"

    def test_trailing_slash_stripped(self):
        _, _, _, path = normalize_url("https://example.com/app/")
        assert path == "/app"


class TestUrlsMatch:
    def test_same_url(self):
        assert urls_match("https://example.com", "https://example.com") is True

    def test_scheme_tolerated(self):
        assert urls_match("http://example.com", "https://example.com") is True

    def test_different_host(self):
        assert urls_match("https://example.com", "https://api.example.com") is False

    def test_different_port(self):
        assert urls_match("http://localhost:3000", "http://localhost:4000") is False

    def test_path_prefix_match(self):
        assert urls_match("https://example.com/app", "https://example.com/app/api") is True

    def test_path_no_match(self):
        assert urls_match("https://example.com/app", "https://example.com/other") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py::TestNormalizeUrl packages/core/tests/test_workspace.py::TestUrlsMatch -v`
Expected: FAIL — module `shannon_core.workspace` does not exist.

- [ ] **Step 3: Implement URL matching in workspace.py**

Create `packages/core/src/shannon_core/workspace.py`:

```python
from pathlib import Path
from urllib.parse import urlparse

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
from shannon_core.session import SessionManager
from shannon_core.utils.paths import has_valid_whitebox_results


def normalize_url(url: str) -> tuple[str, str, int, str]:
    """Normalize a URL for comparison.

    Returns (scheme, host, port, path).
    Default ports: 443 for https, 80 for http.
    Trailing slashes are stripped from the path; bare host gets "/".
    """
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path.rstrip("/") or "/"
    return (scheme, host, port, path)


def urls_match(url_a: str, url_b: str) -> bool:
    """Check if two URLs refer to the same target.

    Rules: hostname must match exactly, port must match,
    scheme difference is tolerated, path prefix match counts as same target.
    """
    scheme_a, host_a, port_a, path_a = normalize_url(url_a)
    scheme_b, host_b, port_b, path_b = normalize_url(url_b)
    if host_a != host_b:
        return False
    if port_a != port_b:
        return False
    # Path prefix match: either path is a prefix of the other
    if not path_b.startswith(path_a) and not path_a.startswith(path_b):
        return False
    return True
```

- [ ] **Step 4: Run URL matching tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py::TestNormalizeUrl packages/core/tests/test_workspace.py::TestUrlsMatch -v`
Expected: All PASS.

- [ ] **Step 5: Write failing tests for scan_deliverables and find functions**

Append to `packages/core/tests/test_workspace.py`:

```python
from shannon_core.workspace import scan_deliverables, find_latest_workspace, find_workspaces_by_url, get_workspace_info


class TestScanDeliverables:
    def test_empty_dir(self, tmp_path):
        result = scan_deliverables(tmp_path / "nonexistent")
        assert result == {"vuln_queues": [], "reports": []}

    def test_with_valid_queue(self, tmp_path):
        d = tmp_path / "deliverables"
        d.mkdir()
        (d / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"ID": "V-001"}]})
        )
        (d / "summary.md").write_text("# Report")
        result = scan_deliverables(d)
        assert result["vuln_queues"] == ["injection"]
        assert "summary.md" in result["reports"]

    def test_skips_empty_queue(self, tmp_path):
        d = tmp_path / "deliverables"
        d.mkdir()
        (d / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": []})
        )
        result = scan_deliverables(d)
        assert result["vuln_queues"] == []


def _make_workspace(workspaces_dir, name, web_url, repo_path, scan_type, vuln_queues=None):
    """Helper to create a test workspace with session.json."""
    mgr = SessionManager(workspaces_dir)
    ws = mgr.create_workspace(web_url, repo_path, name=name, scan_type=scan_type)
    if vuln_queues:
        from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
        deliverables = Path(repo_path) / DEFAULT_DELIVERABLES_SUBDIR
        deliverables.mkdir(parents=True, exist_ok=True)
        for vq in vuln_queues:
            (deliverables / f"{vq}_exploitation_queue.json").write_text(
                json.dumps({"vulnerabilities": [{"ID": "V-001"}]})
            )
        mgr.write_deliverables_summary(ws, {"vuln_queues": vuln_queues, "reports": []})
    return ws


class TestFindLatestWorkspace:
    def test_finds_most_recent_whitebox(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-old", "https://example.com", str(repo), "whitebox", vuln_queues=["xss"])
        _make_workspace(ws_dir, "ws-new", "https://example.com", str(repo), "whitebox", vuln_queues=["injection"])
        result = find_latest_workspace(ws_dir)
        assert result is not None
        assert result["name"] == "ws-new"

    def test_finds_by_url(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-a", "https://a.com", str(repo), "whitebox", vuln_queues=["xss"])
        _make_workspace(ws_dir, "ws-b", "https://b.com", str(repo), "whitebox", vuln_queues=["injection"])
        result = find_latest_workspace(ws_dir, url="https://b.com")
        assert result is not None
        assert result["name"] == "ws-b"

    def test_returns_none_when_no_workspaces(self, tmp_path):
        result = find_latest_workspace(tmp_path / "workspaces")
        assert result is None

    def test_skips_blackbox(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-bb", "https://example.com", str(repo), "blackbox", vuln_queues=["xss"])
        result = find_latest_workspace(ws_dir)
        assert result is None

    def test_skips_workspace_without_deliverables(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-empty", "https://example.com", str(repo), "whitebox")
        result = find_latest_workspace(ws_dir)
        assert result is None


class TestFindWorkspacesByUrl:
    def test_finds_matching(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-1", "https://example.com", str(repo), "whitebox", vuln_queues=["xss"])
        _make_workspace(ws_dir, "ws-2", "https://other.com", str(repo), "whitebox", vuln_queues=["injection"])
        results = find_workspaces_by_url(ws_dir, "https://example.com")
        assert len(results) == 1
        assert results[0]["name"] == "ws-1"

    def test_finds_multiple(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-a", "https://example.com", str(repo), "whitebox", vuln_queues=["xss"])
        _make_workspace(ws_dir, "ws-b", "https://example.com", str(repo), "whitebox", vuln_queues=["injection"])
        results = find_workspaces_by_url(ws_dir, "https://example.com")
        assert len(results) == 2

    def test_empty_when_no_match(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "ws-1", "https://example.com", str(repo), "whitebox", vuln_queues=["xss"])
        results = find_workspaces_by_url(ws_dir, "https://nomatch.com")
        assert results == []


class TestGetWorkspaceInfo:
    def test_returns_full_info(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        deliverables = repo / ".shannon" / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"ID": "V-001"}]})
        )
        mgr = SessionManager(ws_dir)
        ws = mgr.create_workspace("https://example.com", str(repo), name="test-ws", scan_type="whitebox")
        mgr.update_scan_status(ws, "completed")
        mgr.write_deliverables_summary(ws, {"vuln_queues": ["injection"], "reports": []})
        info = get_workspace_info(ws)
        assert info["name"] == "test-ws"
        assert info["scan_type"] == "whitebox"
        assert info["status"] == "completed"
        assert info["web_url"] == "https://example.com"
        assert info["deliverables_summary"]["vuln_queues"] == ["injection"]
        assert info["reuse_command"] is not None

    def test_blackbox_with_parent(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(ws_dir)
        parent = mgr.create_workspace("https://example.com", str(repo), name="wb-1", scan_type="whitebox")
        child = mgr.create_workspace("https://example.com", str(repo), name="bb-1", scan_type="blackbox")
        mgr.link_parent_workspace(child, parent.name)
        mgr.add_child_workspace(parent, child.name)
        info = get_workspace_info(parent)
        assert child.name in info["child_workspaces"]
        info_bb = get_workspace_info(child)
        assert info_bb["parent_workspace"] == parent.name
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py -v`
Expected: FAIL — `scan_deliverables`, `find_latest_workspace`, etc. not defined yet.

- [ ] **Step 7: Implement remaining workspace functions**

Append to `packages/core/src/shannon_core/workspace.py`:

```python
def scan_deliverables(deliverables_dir: Path) -> dict:
    """Scan a deliverables directory for valid queue files and report files.

    Returns {"vuln_queues": [...], "reports": [...]}.
    """
    vuln_queues: list[str] = []
    reports: list[str] = []
    if not deliverables_dir.exists():
        return {"vuln_queues": vuln_queues, "reports": reports}
    for f in sorted(deliverables_dir.iterdir()):
        if f.name.endswith("_exploitation_queue.json"):
            if has_valid_whitebox_results(f):
                vuln_type = f.name.replace("_exploitation_queue.json", "")
                vuln_queues.append(vuln_type)
        elif f.suffix == ".md":
            reports.append(f.name)
    return {"vuln_queues": vuln_queues, "reports": reports}


def _resolve_deliverables_for_workspace(session_data: dict, workspace_path: Path) -> Path:
    """Resolve the deliverables directory for a workspace from its session data."""
    repo_path = session_data.get("repo_path")
    if repo_path:
        return Path(repo_path) / DEFAULT_DELIVERABLES_SUBDIR
    return workspace_path / DEFAULT_DELIVERABLES_SUBDIR


def find_latest_workspace(workspaces_dir: Path, url: str | None = None) -> dict | None:
    """Find the most recent whitebox workspace with valid deliverables.

    If url is provided, only consider workspaces whose web_url matches.
    Returns a dict with name, workspace_path, session_data, deliverables_path,
    deliverables_summary — or None if nothing found.
    """
    mgr = SessionManager(workspaces_dir)
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        if data.get("scan_type") != "whitebox":
            continue
        if url and data.get("web_url"):
            if not urls_match(url, data["web_url"]):
                continue
        deliverables = _resolve_deliverables_for_workspace(data, ws)
        summary = scan_deliverables(deliverables)
        if not summary["vuln_queues"]:
            continue
        return {
            "name": ws.name,
            "workspace_path": ws,
            "session_data": data,
            "deliverables_path": deliverables,
            "deliverables_summary": summary,
        }
    return None


def find_workspaces_by_url(workspaces_dir: Path, url: str) -> list[dict]:
    """Find all whitebox workspaces whose web_url matches the given URL
    and that have valid deliverables.

    Returns a list of dicts (same shape as find_latest_workspace return value),
    ordered by most recent first.
    """
    mgr = SessionManager(workspaces_dir)
    results: list[dict] = []
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        if data.get("scan_type") != "whitebox":
            continue
        if not data.get("web_url"):
            continue
        if not urls_match(url, data["web_url"]):
            continue
        deliverables = _resolve_deliverables_for_workspace(data, ws)
        summary = scan_deliverables(deliverables)
        if not summary["vuln_queues"]:
            continue
        results.append({
            "name": ws.name,
            "workspace_path": ws,
            "session_data": data,
            "deliverables_path": deliverables,
            "deliverables_summary": summary,
        })
    return results


def get_workspace_info(workspace_path: Path) -> dict:
    """Get comprehensive information about a workspace.

    Returns a dict with name, scan_type, web_url, repo_path, status,
    created_at, completed_at, deliverables_summary, parent_workspace,
    child_workspaces, and reuse_command.
    """
    from shannon_core.session import SessionManager

    mgr = SessionManager(workspace_path.parent)
    data = mgr.get_session_data(workspace_path)

    deliverables = _resolve_deliverables_for_workspace(data, workspace_path)
    summary = data.get("deliverables_summary") or scan_deliverables(deliverables)
    if not summary.get("vuln_queues"):
        summary = scan_deliverables(deliverables)

    reuse_command = None
    web_url = data.get("web_url", "")
    if data.get("scan_type") == "whitebox" and web_url:
        reuse_command = f"shannon-blackbox start --url {web_url} -w {workspace_path.name}"

    return {
        "name": workspace_path.name,
        "scan_type": data.get("scan_type", "unknown"),
        "web_url": web_url,
        "repo_path": data.get("repo_path", ""),
        "status": data.get("status", "unknown"),
        "created_at": data.get("created_at"),
        "completed_at": data.get("completed_at"),
        "deliverables_path": deliverables,
        "deliverables_summary": summary,
        "parent_workspace": data.get("links", {}).get("parent_workspace"),
        "child_workspaces": data.get("links", {}).get("child_workspaces", []),
        "reuse_command": reuse_command,
    }
```

- [ ] **Step 8: Run all workspace tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py -v`
Expected: All PASS.

- [ ] **Step 9: Run full core test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ -v`
Expected: All PASS (no regressions).

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/shannon_core/workspace.py packages/core/tests/test_workspace.py
git commit -m "feat(core): add workspace discovery module with URL matching and search

New shannon_core.workspace module:
- normalize_url / urls_match for same-target URL comparison
- scan_deliverables to inspect queue files and reports
- find_latest_workspace for --latest flag support
- find_workspaces_by_url for auto-detection
- get_workspace_info for workspace show command"
```

---

### Task 3: Ensure whitebox worker always creates workspace and returns workspace name

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`

Currently the whitebox worker only creates a workspace when `workspace_name` is provided. This task ensures a workspace is always created (auto-generating the name if needed) and the name is returned in the result.

- [ ] **Step 1: Write failing test for auto-generated workspace**

Add to `packages/whitebox/tests/test_worker.py` (append to existing file):

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from shannon_whitebox.pipeline.shared import PipelineInput


@pytest.mark.asyncio
async def test_run_scan_always_creates_workspace(tmp_path):
    """When workspace_name is None, workspace should be auto-created."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()

    input = PipelineInput(
        repo_path=str(repo),
        web_url="https://example.com",
        workspace_name=None,
    )

    mock_client = MagicMock()
    mock_worker = MagicMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)
    mock_client.execute_workflow = AsyncMock(return_value={"status": "completed"})

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", return_value=mock_worker):
        result = await run_scan(input, "localhost:7233")

    assert result.get("workspace_name") is not None
    assert len(result["workspace_name"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_worker.py::test_run_scan_always_creates_workspace -v`
Expected: FAIL — `result` dict doesn't have `workspace_name` key.

- [ ] **Step 3: Modify whitebox worker to always create workspace**

Replace `packages/whitebox/src/shannon_whitebox/worker.py` with:

```python
import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import run_agent, run_code_index, run_preflight, run_vuln_agent, run_rebuild_call_chains
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput
from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path
from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR

TASK_QUEUE = "shannon-whitebox"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager
    from shannon_core.workspace import scan_deliverables

    # Always create workspace so blackbox can discover results
    workspaces_dir = resolve_workspaces_dir(input.repo_path)
    mgr = SessionManager(workspaces_dir)
    ws_path = mgr.create_workspace(
        web_url=input.web_url or "",
        repo_path=input.repo_path,
        name=input.workspace_name,
        scan_type="whitebox",
    )
    workspace_name = ws_path.name
    input.workspace_name = workspace_name

    # Resolve deliverables path for the result
    deliverables_path = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
    )

    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[WhiteboxScanWorkflow],
        activities=[run_preflight, run_agent, run_vuln_agent, run_code_index, run_rebuild_call_chains],
    )

    async with worker:
        result = await client.execute_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=workspace_name,
            task_queue=TASK_QUEUE,
        )

    # Update session with completion status and deliverables summary
    mgr.update_scan_status(ws_path, "completed")
    summary = scan_deliverables(deliverables_path)
    if summary["vuln_queues"]:
        mgr.write_deliverables_summary(ws_path, summary)

    # Build return dict
    result_dict = result if isinstance(result, dict) else {
        "status": getattr(result, "status", "unknown"),
        "completed_agents": getattr(result, "completed_agents", []),
        "errors": getattr(result, "errors", []),
    }
    result_dict["workspace_name"] = workspace_name
    result_dict["deliverables_path"] = str(deliverables_path)
    result_dict["web_url"] = input.web_url or ""
    return result_dict


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_worker.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker.py
git commit -m "feat(whitebox): always create workspace and return workspace name

Worker now creates workspace on every run (auto-generates name if needed),
writes scan_type/status/deliverables_summary to session.json, and returns
workspace_name in result dict for CLI consumption."
```

---

### Task 4: Whitebox CLI enhanced completion output

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`

This task enhances the whitebox `start` command output to show workspace info and next-step guidance after scan completion. Also adds the `workspace show` subcommand.

- [ ] **Step 1: Write failing test for enhanced output**

Add to `packages/whitebox/tests/test_cli.py` (append to existing file):

```python
from click.testing import CliRunner
from unittest.mock import patch, AsyncMock, MagicMock


def test_start_prints_workspace_info_on_success():
    """After scan completes, CLI should print workspace name and next steps."""
    from shannon_whitebox.cli.main import cli

    runner = CliRunner()
    mock_result = {
        "status": "completed",
        "workspace_name": "test-ws-123",
        "deliverables_path": "/repo/.shannon/deliverables",
        "web_url": "https://example.com",
        "completed_agents": ["recon"],
        "errors": [],
    }
    with patch("shannon_whitebox.worker.run_scan", AsyncMock(return_value=mock_result)):
        result = runner.invoke(cli, ["start", "-r", "/repo"])
    assert result.exit_code == 0
    assert "test-ws-123" in result.output
    assert "shannon-blackbox start" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py::test_start_prints_workspace_info_on_success -v`
Expected: FAIL — current output doesn't include workspace name.

- [ ] **Step 3: Implement enhanced whitebox CLI output**

Replace `packages/whitebox/src/shannon_whitebox/cli/main.py` with:

```python
import asyncio
import click
from pathlib import Path

from dotenv import load_dotenv

from shannon_core.session import SessionManager
from shannon_whitebox.pipeline.shared import PipelineInput

@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""
    load_dotenv()

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
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
        ws_name = result.get("workspace_name", "unknown")
        deliverables = result.get("deliverables_path", "unknown")
        web_url = result.get("web_url", "")
        click.echo("")
        click.echo("✅ White-box scan complete.")
        click.echo("")
        click.echo(f"  Workspace:     {ws_name}")
        click.echo(f"  Deliverables:  {deliverables}")
        click.echo("")
        click.echo("  Next steps:")
        if web_url:
            click.echo(f"    shannon-blackbox start --url {web_url} -w {ws_name}")
            click.echo(f"    # or use --latest to reuse the most recent white-box results:")
            click.echo(f"    shannon-blackbox start --url {web_url} --latest")
        else:
            click.echo(f"    shannon-blackbox start --url <target-url> -w {ws_name}")
    else:
        click.echo(f"Scan failed: {result.get('error', result.get('errors', ['unknown error']))}")
        raise SystemExit(1)

@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo("No logs found")

@cli.command("workspaces")
def list_workspaces():
    """List all workspaces."""
    mgr = SessionManager(Path("workspaces"))
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        url = data.get("web_url", "unknown")
        agents = len(data.get("completed_agents", []))
        scan_type = data.get("scan_type", "unknown")
        status = data.get("status", "unknown")
        click.echo(f"  {ws.name}  type={scan_type}  url={url}  status={status}  agents={agents}")


@cli.command("show")
@click.argument("workspace_name")
def show_workspace(workspace_name):
    """Show detailed workspace information."""
    from shannon_core.workspace import get_workspace_info

    ws_dir = Path("workspaces")
    ws_path = ws_dir / workspace_name
    if not ws_path.exists() or not (ws_path / "session.json").exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    info = get_workspace_info(ws_path)
    click.echo(f"Workspace: {info['name']}")
    click.echo(f"  Type:           {info['scan_type']}")
    click.echo(f"  Target:         {info['web_url']}")
    click.echo(f"  Repo:           {info['repo_path']}")
    click.echo(f"  Status:         {info['status']}")
    click.echo(f"  Created:        {info['created_at']}")
    click.echo(f"  Completed:      {info['completed_at']}")
    click.echo("")
    summary = info.get("deliverables_summary", {})
    vuln_queues = summary.get("vuln_queues", [])
    reports = summary.get("reports", [])
    if vuln_queues or reports:
        click.echo("  Deliverables:")
        for vq in vuln_queues:
            click.echo(f"    ✅ {vq}_exploitation_queue.json")
        for rpt in reports:
            click.echo(f"    ✅ {rpt}")
    if info.get("child_workspaces"):
        click.echo("")
        click.echo("  Linked black-box scans:")
        for child in info["child_workspaces"]:
            click.echo(f"    📋 {child}")
    if info.get("reuse_command"):
        click.echo("")
        click.echo("  Reuse command:")
        click.echo(f"    {info['reuse_command']}")


def main():
    cli()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): enhanced CLI output with workspace name and next steps

After white-box scan completes, print workspace name, deliverables path,
and actionable next-step commands for running black-box verification.

Also adds 'workspace show' subcommand for detailed workspace inspection."
```

---

### Task 5: Blackbox worker always creates workspace and writes links

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

This task ensures the blackbox worker always creates a workspace with `scan_type="blackbox"`, and when reusing whitebox results, writes the parent/child link to both workspaces.

- [ ] **Step 1: Add parent_workspace_name to BlackboxPipelineInput**

In `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`, add one field to `BlackboxPipelineInput`:

```python
@dataclass
class BlackboxPipelineInput:
    web_url: str
    workspace_name: str | None = None
    config_path: str | None = None
    output_path: str | None = None
    repo_path: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[str] | None = None
    exploit: bool = True
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
    parent_workspace_name: str | None = None  # NEW: whitebox workspace being reused
```

- [ ] **Step 2: Write failing test for blackbox workspace creation**

Add to `packages/blackbox/tests/test_integration.py` or create a new test in the appropriate file:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


@pytest.mark.asyncio
async def test_run_scan_creates_workspace(tmp_path):
    """Blackbox worker should always create a workspace."""
    repo = tmp_path / "myrepo"
    repo.mkdir()

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        repo_path=str(repo),
        workspace_name=None,
    )

    mock_client = MagicMock()
    mock_worker = MagicMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    mock_state = MagicMock()
    mock_state.status = "completed"
    mock_state.has_whitebox_results = False
    mock_state.found_whitebox_classes = []
    mock_state.errors = []
    mock_client.execute_workflow = AsyncMock(return_value=mock_state)

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", return_value=mock_worker):
        result = await run_scan(input, "localhost:7233")

    assert result.workspace_name is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_integration.py::test_run_scan_creates_workspace -v`
Expected: FAIL — `BlackboxPipelineState` doesn't have `workspace_name` field, worker doesn't create workspace.

- [ ] **Step 4: Add workspace_name to BlackboxPipelineState**

In `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`, add `workspace_name` to `BlackboxPipelineState`:

```python
@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    found_whitebox_classes: list[str] = field(default_factory=list)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    workspace_name: str | None = None  # NEW
```

- [ ] **Step 5: Modify blackbox worker**

Replace `packages/blackbox/src/shannon_blackbox/worker.py` with:

```python
import asyncio
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import (
    run_blackbox_preflight,
    run_recon,
    run_exploit_agent,
    assemble_report,
    run_report_agent,
)
from .pipeline.workflows import BlackboxScanWorkflow
from .pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState
from shannon_core.utils.paths import resolve_workspaces_dir

TASK_QUEUE = "shannon-blackbox"


async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233") -> BlackboxPipelineState:
    from shannon_core.session import SessionManager

    # Always create workspace
    workspaces_dir = resolve_workspaces_dir(input.repo_path)
    mgr = SessionManager(workspaces_dir)
    ws_path = mgr.create_workspace(
        web_url=input.web_url,
        repo_path=input.repo_path or "",
        name=input.workspace_name,
        scan_type="blackbox",
    )
    workspace_name = ws_path.name
    input.workspace_name = workspace_name

    # Write parent link if reusing whitebox results
    if input.parent_workspace_name:
        mgr.link_parent_workspace(ws_path, input.parent_workspace_name)
        parent_ws = mgr.get_workspace(input.parent_workspace_name)
        if parent_ws:
            mgr.add_child_workspace(parent_ws, workspace_name)

    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[BlackboxScanWorkflow],
        activities=[run_blackbox_preflight, run_recon, run_exploit_agent, assemble_report, run_report_agent],
    )

    async with worker:
        result = await client.execute_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=workspace_name,
            task_queue=TASK_QUEUE,
        )

    # Update session status
    mgr.update_scan_status(ws_path, getattr(result, "status", "unknown"))
    result.workspace_name = workspace_name
    return result


def main():
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
    asyncio.run(run_scan(BlackboxPipelineInput(web_url=url)))
```

- [ ] **Step 6: Run blackbox tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/ -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/blackbox/tests/test_integration.py
git commit -m "feat(blackbox): always create workspace and write parent/child links

Worker creates workspace on every run with scan_type=blackbox.
When parent_workspace_name is provided, writes bidirectional links
between whitebox and blackbox workspaces."
```

---

### Task 6: Blackbox CLI — `--latest` flag and auto-detection

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`

This task adds `--latest` and `--no-auto-detect` flags to the blackbox CLI, and wires in the auto-detection + interactive prompt flow.

- [ ] **Step 1: Write failing test for --latest**

Add to `packages/blackbox/tests/test_cli.py` (append to existing):

```python
from click.testing import CliRunner
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path


def test_start_latest_resolves_workspace(tmp_path):
    """--latest flag should resolve workspace name before running."""
    from shannon_blackbox.cli.main import cli

    # Create a fake workspace
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    ws = ws_dir / "test-wb"
    ws.mkdir()
    import json
    (ws / "session.json").write_text(json.dumps({
        "web_url": "https://example.com",
        "repo_path": str(tmp_path / "repo"),
        "scan_type": "whitebox",
        "status": "completed",
        "created_at": 1234567890.0,
    }))
    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = repo / ".shannon" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"ID": "V-001"}]})
    )

    mock_state = MagicMock()
    mock_state.status = "completed"
    mock_state.has_whitebox_results = True
    mock_state.found_whitebox_classes = ["injection"]
    mock_state.errors = []
    mock_state.workspace_name = "test-wb"

    with patch("shannon_blackbox.cli.main.resolve_workspaces_dir", return_value=ws_dir), \
         patch("shannon_core.workspace.find_latest_workspace", return_value={
             "name": "test-wb",
             "workspace_path": ws,
             "session_data": {"web_url": "https://example.com", "repo_path": str(repo)},
             "deliverables_path": deliverables,
             "deliverables_summary": {"vuln_queues": ["injection"], "reports": []},
         }), \
         patch("shannon_blackbox.worker.run_scan", AsyncMock(return_value=mock_state)):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://example.com", "--latest",
                                      "--temporal-address", "localhost:7233"])
    assert result.exit_code == 0
    assert "test-wb" in result.output


def test_start_no_whitebox_prints_tip(tmp_path):
    """Without --latest and no auto-detect, should print tip about whitebox."""
    from shannon_blackbox.cli.main import cli

    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()

    mock_state = MagicMock()
    mock_state.status = "completed"
    mock_state.has_whitebox_results = False
    mock_state.found_whitebox_classes = []
    mock_state.errors = []
    mock_state.workspace_name = "bb-1"

    with patch("shannon_blackbox.cli.main.resolve_workspaces_dir", return_value=ws_dir), \
         patch("shannon_core.workspace.find_latest_workspace", return_value=None), \
         patch("shannon_core.workspace.find_workspaces_by_url", return_value=[]), \
         patch("shannon_blackbox.worker.run_scan", AsyncMock(return_value=mock_state)):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://example.com",
                                      "--no-auto-detect",
                                      "--temporal-address", "localhost:7233"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py -v`
Expected: FAIL — `--latest` option doesn't exist yet.

- [ ] **Step 3: Implement enhanced blackbox CLI**

Replace `packages/blackbox/src/shannon_blackbox/cli/main.py` with:

```python
import asyncio
from pathlib import Path

import click

from dotenv import load_dotenv

from shannon_core.models.agents import ALL_VULN_CLASSES
from shannon_core.session import SessionManager
from shannon_core.utils.paths import resolve_workspaces_dir


@click.group()
def cli():
    """Shannon Black-Box Scanner - Runtime vulnerability verification."""
    load_dotenv()


@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--latest", is_flag=True, help="Reuse most recent white-box workspace results")
@click.option("--no-auto-detect", is_flag=True, help="Skip auto-detection of white-box results")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, repo, output, workspace, config_path, vuln_classes, no_exploit, latest, no_auto_detect, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput
    from shannon_core.workspace import find_latest_workspace, find_workspaces_by_url

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)
    parent_workspace_name = None

    # Priority: -w > --latest > auto-detect
    if latest and not workspace:
        workspaces_dir = resolve_workspaces_dir(repo if repo else None)
        found = find_latest_workspace(workspaces_dir, url=url)
        if found is None:
            click.echo("No white-box workspaces found. Run a white-box scan first.")
            raise SystemExit(1)
        workspace = found["name"]
        if not repo:
            repo = found["session_data"].get("repo_path")
        parent_workspace_name = found["name"]
        click.echo(f"🔗 Found white-box results in workspace '{found['name']}'")
        click.echo(f"   Vulnerability queues found: {', '.join(found['deliverables_summary']['vuln_queues'])}")
        click.echo("   Skipping recon phase — leveraging white-box findings directly.")
    elif not workspace and not no_auto_detect:
        # Auto-detect: check for matching white-box workspaces by URL
        workspaces_dir = resolve_workspaces_dir(repo if repo else None)
        matches = find_workspaces_by_url(workspaces_dir, url)
        if len(matches) == 1:
            match = matches[0]
            click.echo(f"🔍 Detected white-box results for '{url}' (workspace: {match['name']})")
            if click.confirm("   Reuse these results?", default=True):
                workspace = match["name"]
                if not repo:
                    repo = match["session_data"].get("repo_path")
                parent_workspace_name = match["name"]
        elif len(matches) > 1:
            click.echo(f"🔍 Found {len(matches)} white-box workspaces for '{url}':")
            for i, m in enumerate(matches, 1):
                queues = ", ".join(m["deliverables_summary"]["vuln_queues"])
                click.echo(f"  [{i}] {m['name']}  ({queues})")
            click.echo("")
            choice = click.prompt("Select workspace to reuse [1-{}] or 'n' for standalone".format(len(matches)), default="n")
            if choice.isdigit() and 1 <= int(choice) <= len(matches):
                match = matches[int(choice) - 1]
                workspace = match["name"]
                if not repo:
                    repo = match["session_data"].get("repo_path")
                parent_workspace_name = match["name"]
                click.echo(f"🔗 Using white-box workspace '{match['name']}'")
            else:
                click.echo("ℹ️  Running standalone black-box scan.")
        else:
            click.echo("ℹ️  No white-box results found for this target. Running standalone black-box scan.")
            click.echo("   Tip: run white-box first, then use --latest to reuse results.")

    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=str(Path(repo).resolve()) if repo else None,
        workspace_name=workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
        parent_workspace_name=parent_workspace_name,
    )
    click.echo(f"Starting black-box scan on {url}")
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


@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo("No logs found")


@cli.command("workspaces")
def list_workspaces():
    """List all workspaces."""
    mgr = SessionManager(Path("workspaces"))
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        url = data.get("web_url", "unknown")
        agents = len(data.get("completed_agents", []))
        scan_type = data.get("scan_type", "unknown")
        status = data.get("status", "unknown")
        click.echo(f"  {ws.name}  type={scan_type}  url={url}  status={status}  agents={agents}")


@cli.command("show")
@click.argument("workspace_name")
def show_workspace(workspace_name):
    """Show detailed workspace information."""
    from shannon_core.workspace import get_workspace_info

    ws_dir = Path("workspaces")
    ws_path = ws_dir / workspace_name
    if not ws_path.exists() or not (ws_path / "session.json").exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    info = get_workspace_info(ws_path)
    click.echo(f"Workspace: {info['name']}")
    click.echo(f"  Type:           {info['scan_type']}")
    click.echo(f"  Target:         {info['web_url']}")
    click.echo(f"  Repo:           {info['repo_path']}")
    click.echo(f"  Status:         {info['status']}")
    click.echo(f"  Created:        {info['created_at']}")
    click.echo(f"  Completed:      {info['completed_at']}")
    click.echo("")

    if info.get("parent_workspace"):
        click.echo(f"  Parent workspace: {info['parent_workspace']}")

    summary = info.get("deliverables_summary", {})
    vuln_queues = summary.get("vuln_queues", [])
    reports = summary.get("reports", [])
    if vuln_queues or reports:
        click.echo("  Deliverables:")
        for vq in vuln_queues:
            click.echo(f"    ✅ {vq}_exploitation_queue.json")
        for rpt in reports:
            click.echo(f"    ✅ {rpt}")

    if info.get("child_workspaces"):
        click.echo("")
        click.echo("  Linked black-box scans:")
        for child in info["child_workspaces"]:
            click.echo(f"    📋 {child}")

    if info.get("reuse_command"):
        click.echo("")
        click.echo("  Reuse command:")
        click.echo(f"    {info['reuse_command']}")


def main():
    cli()
```

- [ ] **Step 4: Run blackbox CLI tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full blackbox test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/ -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add --latest flag and same-target auto-detection

CLI enhancements:
- --latest flag auto-finds most recent whitebox workspace
- Auto-detection prompts when matching URL found without -w or --latest
- --no-auto-detect to skip auto-detection
- 'workspace show' subcommand for detailed inspection
- Enhanced 'workspaces' listing with scan_type and status"
```

---

### Task 7: Enhanced workspaces command with grouped listing

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py` (already done in Task 4 — verify)
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py` (already done in Task 6 — verify)

Both CLIs now include `scan_type` and `status` in the `workspaces` listing output. This task verifies the implementation and adds a test to confirm grouping behavior.

- [ ] **Step 1: Verify workspaces command shows scan_type**

Both CLIs were updated in Tasks 4 and 6 to output `type=` in the `workspaces` command. Verify by reading the current code.

The whitebox CLI `workspaces` command (from Task 4) outputs:
```
  {name}  type={scan_type}  url={url}  status={status}  agents={agents}
```

The blackbox CLI `workspaces` command (from Task 6) outputs the same format.

- [ ] **Step 2: Write test for grouped workspace listing**

Add to `packages/core/tests/test_workspace.py`:

```python
from shannon_core.workspace import list_grouped_workspaces


class TestListGroupedWorkspaces:
    def test_groups_by_scan_type(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_workspace(ws_dir, "wb-1", "https://a.com", str(repo), "whitebox", vuln_queues=["xss"])
        _make_workspace(ws_dir, "bb-1", "https://a.com", str(repo), "blackbox")
        result = list_grouped_workspaces(ws_dir)
        assert "whitebox" in result
        assert "blackbox" in result
        assert len(result["whitebox"]) == 1
        assert len(result["blackbox"]) == 1
        assert result["whitebox"][0]["name"] == "wb-1"

    def test_empty_when_no_workspaces(self, tmp_path):
        result = list_grouped_workspaces(tmp_path / "workspaces")
        assert result == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py::TestListGroupedWorkspaces -v`
Expected: FAIL — `list_grouped_workspaces` not defined.

- [ ] **Step 4: Implement list_grouped_workspaces**

Add to `packages/core/src/shannon_core/workspace.py`:

```python
def list_grouped_workspaces(workspaces_dir: Path) -> dict[str, list[dict]]:
    """List all workspaces grouped by scan_type.

    Returns {"whitebox": [...], "blackbox": [...]}.
    """
    mgr = SessionManager(workspaces_dir)
    groups: dict[str, list[dict]] = {}
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        scan_type = data.get("scan_type", "unknown")
        if scan_type not in groups:
            groups[scan_type] = []
        groups[scan_type].append({
            "name": ws.name,
            "web_url": data.get("web_url", ""),
            "status": data.get("status", "unknown"),
            "completed_agents": data.get("completed_agents", []),
            "scan_type": scan_type,
        })
    return groups
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/workspace.py packages/core/tests/test_workspace.py
git commit -m "feat(core): add list_grouped_workspaces for grouped CLI output"
```

---

### Task 8: End-to-end verification

**Files:** None (verification only)

This task runs the full test suite across all packages to confirm no regressions, and verifies the CLI help text shows the new options.

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/ packages/whitebox/tests/ packages/blackbox/tests/ -v`
Expected: All PASS.

- [ ] **Step 2: Verify blackbox CLI help shows new options**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m shannon_blackbox.cli.main start --help`
Expected: Output includes `--latest` and `--no-auto-detect` options.

- [ ] **Step 3: Verify whitebox CLI help shows show command**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m shannon_whitebox.cli.main --help`
Expected: Output includes `show` command.

- [ ] **Step 4: Verify blackbox CLI help shows show command**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m shannon_blackbox.cli.main --help`
Expected: Output includes `show` command.

- [ ] **Step 5: Commit any remaining changes**

```bash
git add -A
git commit -m "test: verify cross-scan UX — all tests pass, CLI options visible"
```

---

## Self-Review Checklist

**1. Spec coverage:**

| Spec Section | Task |
|---|---|
| Section 1: CLI Output Improvements | Task 4 (whitebox), Task 6 (blackbox) |
| Section 2: --latest parameter | Task 6 |
| Section 3: Same-target auto-detection | Task 6 |
| Section 4: session.json data model | Task 1 |
| Section 5: Workspace list/query enhancements | Task 4, Task 6, Task 7 |

All sections covered. ✅

**2. Placeholder scan:** No TBD, TODO, or vague instructions found. ✅

**3. Type consistency:** All function signatures, field names, and dataclass attributes are consistent across tasks:
- `BlackboxPipelineInput.parent_workspace_name` defined in Task 5, used in Task 6 ✅
- `BlackboxPipelineState.workspace_name` defined in Task 5, accessed in Task 6 ✅
- `find_latest_workspace` return type matches usage in Task 6 ✅
- `get_workspace_info` return dict keys match CLI usage in Tasks 4 and 6 ✅
