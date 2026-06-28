# Workspace Delete & Clean Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `workspace delete` and `workspace clean` commands to both whitebox and blackbox CLIs, backed by new SessionManager methods.

**Architecture:** Three new methods on `SessionManager` in the core package handle the data operations (delete, clean, link handling). Both CLI packages get identical `delete` and `clean` subcommands under the existing `workspace` group, differing only in the `scan_type` passed to `clean_workspace`. Tests follow the existing pattern: unit tests in `packages/core/tests/test_session.py`, CLI integration tests in each package's `tests/test_cli.py`.

**Tech Stack:** Python 3.12, click (CLI), pytest (testing), shutil (file ops)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `packages/core/src/shannon_core/session.py` | Add `delete_workspace`, `clean_workspace`, `_handle_workspace_links` methods |
| `packages/core/tests/test_session.py` | Unit tests for all three new methods |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Add `workspace delete` and `workspace clean` subcommands |
| `packages/whitebox/tests/test_cli.py` | CLI integration tests for whitebox delete/clean |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Add `workspace delete` and `workspace clean` subcommands |
| `packages/blackbox/tests/test_cli.py` | CLI integration tests for blackbox delete/clean |

---

### Task 1: SessionManager.delete_workspace — failing test

**Files:**
- Test: `packages/core/tests/test_session.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_session.py`:

```python
def test_delete_workspace_removes_directory(tmp_path):
    """delete_workspace should remove the entire workspace directory."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="to-delete")
    assert ws.exists()
    result = mgr.delete_workspace("to-delete")
    assert result is True
    assert not ws.exists()


def test_delete_workspace_returns_false_when_not_found(tmp_path):
    """delete_workspace should return False for nonexistent workspace."""
    mgr = SessionManager(tmp_path / "workspaces")
    result = mgr.delete_workspace("nonexistent")
    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_session.py::test_delete_workspace_removes_directory -v`
Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'delete_workspace'`

---

### Task 2: SessionManager.delete_workspace — implementation

**Files:**
- Modify: `packages/core/src/shannon_core/session.py`

- [ ] **Step 3: Add the import and method**

At the top of `session.py`, add `shutil` to the imports:

```python
import json
import shutil
import time
from pathlib import Path
```

Append to the `SessionManager` class (after `mark_completed`):

```python
    def delete_workspace(self, workspace_name: str) -> bool:
        """Delete a workspace directory and handle parent-child links.

        Returns True if deleted, False if workspace not found.
        """
        ws = self.get_workspace(workspace_name)
        if ws is None:
            return False
        self._handle_workspace_links(ws)
        shutil.rmtree(ws)
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_session.py::test_delete_workspace_removes_directory packages/core/tests/test_session.py::test_delete_workspace_returns_false_when_not_found -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(core): add SessionManager.delete_workspace"
```

---

### Task 3: SessionManager._handle_workspace_links — test + implementation

**Files:**
- Test: `packages/core/tests/test_session.py`
- Modify: `packages/core/src/shannon_core/session.py`

- [ ] **Step 6: Write the failing tests**

Append to `packages/core/tests/test_session.py`:

```python
def test_delete_workspace_removes_child_refs_from_parent(tmp_path):
    """Deleting a blackbox workspace should remove its name from the parent's child_workspaces."""
    mgr = SessionManager(tmp_path / "workspaces")
    parent = mgr.create_workspace("https://example.com", "/repo", name="wb-parent", scan_type="whitebox")
    child = mgr.create_workspace("https://example.com", "/repo", name="bb-child", scan_type="blackbox")
    mgr.add_child_workspace(parent, "bb-child")
    mgr.set_parent_workspace(child, "wb-parent")

    # Verify link exists
    assert "bb-child" in mgr.get_links(parent)["child_workspaces"]

    mgr.delete_workspace("bb-child")

    # Parent should no longer list the deleted child
    assert "bb-child" not in mgr.get_links(parent)["child_workspaces"]
    # Child directory should be gone
    assert not child.exists()


def test_delete_workspace_clears_parent_ref_from_children(tmp_path):
    """Deleting a whitebox workspace should clear parent_workspace in all child workspaces."""
    mgr = SessionManager(tmp_path / "workspaces")
    parent = mgr.create_workspace("https://example.com", "/repo", name="wb-parent-2", scan_type="whitebox")
    child1 = mgr.create_workspace("https://example.com", "/repo", name="bb-child-1a", scan_type="blackbox")
    child2 = mgr.create_workspace("https://example.com", "/repo", name="bb-child-2a", scan_type="blackbox")
    mgr.add_child_workspace(parent, "bb-child-1a")
    mgr.add_child_workspace(parent, "bb-child-2a")
    mgr.set_parent_workspace(child1, "wb-parent-2")
    mgr.set_parent_workspace(child2, "wb-parent-2")

    mgr.delete_workspace("wb-parent-2")

    # Children should have their parent ref cleared
    assert mgr.get_links(child1)["parent_workspace"] is None
    assert mgr.get_links(child2)["parent_workspace"] is None
    # Parent directory should be gone
    assert not parent.exists()


def test_delete_workspace_handles_already_deleted_linked_ws(tmp_path):
    """Deleting a workspace with links to already-removed workspaces should not error."""
    mgr = SessionManager(tmp_path / "workspaces")
    parent = mgr.create_workspace("https://example.com", "/repo", name="wb-orphan", scan_type="whitebox")
    # Manually add a child reference to a workspace that doesn't exist on disk
    mgr.add_child_workspace(parent, "ghost-child")

    # Should not raise
    result = mgr.delete_workspace("wb-orphan")
    assert result is True
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_session.py::test_delete_workspace_removes_child_refs_from_parent -v`
Expected: FAIL — `_handle_workspace_links` does not exist yet, but the previous `delete_workspace` will call it and raise `AttributeError`.

- [ ] **Step 8: Implement _handle_workspace_links**

Append to `SessionManager` class (right after `delete_workspace`):

```python
    def _handle_workspace_links(self, workspace_path: Path) -> None:
        """Update linked workspaces before deleting this one."""
        data = self.get_session_data(workspace_path)
        scan_type = data.get("scan_type", "")
        links = data.get("links", {})
        workspace_name = workspace_path.name

        if scan_type == "whitebox":
            # Remove parent ref from each child
            for child_name in links.get("child_workspaces", []):
                child_ws = self.get_workspace(child_name)
                if child_ws is not None:
                    child_links = self.get_links(child_ws)
                    child_links["parent_workspace"] = None
                    self.update_session(child_ws, {"links": child_links})

        elif scan_type == "blackbox":
            # Remove this child from parent's child list
            parent_name = links.get("parent_workspace")
            if parent_name:
                parent_ws = self.get_workspace(parent_name)
                if parent_ws is not None:
                    parent_links = self.get_links(parent_ws)
                    children = parent_links.get("child_workspaces", [])
                    if workspace_name in children:
                        children.remove(workspace_name)
                    parent_links["child_workspaces"] = children
                    self.update_session(parent_ws, {"links": parent_links})
```

- [ ] **Step 9: Run all link-handling tests**

Run: `uv run pytest packages/core/tests/test_session.py -k "delete_workspace" -v`
Expected: All 5 delete tests PASS

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(core): handle workspace links on delete"
```

---

### Task 4: SessionManager.clean_workspace — test + implementation

**Files:**
- Test: `packages/core/tests/test_session.py`
- Modify: `packages/core/src/shannon_core/session.py`

- [ ] **Step 11: Write the failing tests**

Append to `packages/core/tests/test_session.py`:

```python
def test_clean_workspace_whitebox(tmp_path):
    """clean_workspace with whitebox should remove artifacts but keep session.json."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-clean", scan_type="whitebox")
    mgr.mark_agent_completed(ws, __import__("shannon_core.models.agents", fromlist=["AgentName"]).AgentName.RECON)
    # Create artifacts
    (ws / "deliverables").mkdir()
    (ws / "deliverables" / "injection_exploitation_queue.json").write_text("[]", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "recon.log").write_text("log data", encoding="utf-8")
    (ws / "prompts").mkdir()
    (ws / "prompts" / "recon.txt").write_text("prompt", encoding="utf-8")
    (ws / "scratchpad").mkdir()
    (ws / "scratchpad" / "temp.txt").write_text("temp", encoding="utf-8")
    (ws / "workflow.log").write_text("workflow log", encoding="utf-8")
    (ws / ".playwright").mkdir()
    (ws / ".playwright-cli").mkdir()

    mgr.clean_workspace(ws, scan_type="whitebox")

    # session.json must survive
    assert (ws / "session.json").exists()
    # completed_agents should be reset
    data = json.loads((ws / "session.json").read_text(encoding="utf-8"))
    assert data["completed_agents"] == []
    assert data["deliverables_summary"] is None
    # Artifact dirs/files should be gone
    assert not (ws / "deliverables").exists()
    assert not (ws / "agents").exists()
    assert not (ws / "prompts").exists()
    assert not (ws / "scratchpad").exists()
    assert not (ws / "workflow.log").exists()
    assert not (ws / ".playwright").exists()
    assert not (ws / ".playwright-cli").exists()


def test_clean_workspace_blackbox(tmp_path):
    """clean_workspace with blackbox should remove blackbox-specific artifacts."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-clean", scan_type="blackbox")
    # Create blackbox-style artifacts
    (ws / "deliverables").mkdir()
    (ws / "deliverables" / "injection_exploitation_evidence.md").write_text("evidence", encoding="utf-8")
    (ws / "deliverables" / "xss_findings.md").write_text("findings", encoding="utf-8")
    (ws / "deliverables" / "comprehensive_security_assessment_report.md").write_text("report", encoding="utf-8")
    (ws / "deliverables" / "injection_exploitation_queue.json").write_text("[]", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "injection-exploit_001.log").write_text("exploit log", encoding="utf-8")
    (ws / "agents" / "ssrf-validate-authentication_002.log").write_text("auth log", encoding="utf-8")
    (ws / "agents" / "recon.log").write_text("recon log", encoding="utf-8")
    (ws / "workflow.log").write_text("workflow log", encoding="utf-8")
    (ws / ".playwright").mkdir()
    (ws / ".playwright-cli").mkdir()

    mgr.clean_workspace(ws, scan_type="blackbox")

    # session.json must survive
    assert (ws / "session.json").exists()
    # Blackbox-specific deliverables removed
    assert not (ws / "deliverables" / "injection_exploitation_evidence.md").exists()
    assert not (ws / "deliverables" / "xss_findings.md").exists()
    assert not (ws / "deliverables" / "comprehensive_security_assessment_report.md").exists()
    # Exploitation queues are NOT removed (they are whitebox deliverables)
    assert (ws / "deliverables" / "injection_exploitation_queue.json").exists()
    # Blackbox agent logs removed
    assert not (ws / "agents" / "injection-exploit_001.log").exists()
    assert not (ws / "agents" / "ssrf-validate-authentication_002.log").exists()
    # Non-blackbox agent logs kept
    assert (ws / "agents" / "recon.log").exists()
    # workflow.log truncated (empty file remains)
    assert (ws / "workflow.log").read_text() == ""
    # Playwright dirs removed
    assert not (ws / ".playwright").exists()
    assert not (ws / ".playwright-cli").exists()
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_session.py::test_clean_workspace_whitebox -v`
Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'clean_workspace'`

- [ ] **Step 13: Implement clean_workspace**

Append to `SessionManager` class (after `_handle_workspace_links`):

```python
    def clean_workspace(self, workspace_path: Path, scan_type: str) -> None:
        """Remove scan artifacts from a workspace, preserving session.json.

        scan_type controls which artifacts are removed:
          - "whitebox": removes deliverables/, agents/, prompts/, scratchpad/,
            workflow.log, .playwright/, .playwright-cli/
          - "blackbox": removes blackbox-specific deliverables and agent logs,
            truncates workflow.log, removes .playwright dirs
        """
        import fnmatch

        if scan_type == "whitebox":
            for name in ("deliverables", "agents", "prompts", "scratchpad"):
                target = workspace_path / name
                if target.is_dir():
                    shutil.rmtree(target)
            for name in ("workflow.log",):
                target = workspace_path / name
                if target.exists():
                    target.unlink()
            for name in (".playwright", ".playwright-cli"):
                target = workspace_path / name
                if target.is_dir():
                    shutil.rmtree(target)

        elif scan_type == "blackbox":
            # Remove blackbox-specific deliverables
            deliverables_dir = workspace_path / "deliverables"
            if deliverables_dir.is_dir():
                bb_deliverable_patterns = [
                    "*_exploitation_evidence.md",
                    "*_findings.md",
                    "comprehensive_security_assessment_report.md",
                ]
                for f in deliverables_dir.iterdir():
                    if any(fnmatch.fnmatch(f.name, p) for p in bb_deliverable_patterns):
                        f.unlink()

            # Remove blackbox agent logs
            agents_dir = workspace_path / "agents"
            if agents_dir.is_dir():
                bb_log_patterns = [
                    "*-exploit_*.log",
                    "*-validate-authentication_*.log",
                ]
                for f in agents_dir.iterdir():
                    if any(fnmatch.fnmatch(f.name, p) for p in bb_log_patterns):
                        f.unlink()

            # Truncate workflow.log
            workflow_log = workspace_path / "workflow.log"
            if workflow_log.exists():
                workflow_log.write_text("", encoding="utf-8")

            # Remove playwright dirs
            for name in (".playwright", ".playwright-cli"):
                target = workspace_path / name
                if target.is_dir():
                    shutil.rmtree(target)

        # Reset session metadata
        self.update_session(workspace_path, {
            "completed_agents": [],
            "deliverables_summary": None,
        })
```

- [ ] **Step 14: Run clean tests**

Run: `uv run pytest packages/core/tests/test_session.py -k "clean_workspace" -v`
Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(core): add SessionManager.clean_workspace"
```

---

### Task 5: Whitebox CLI — workspace delete command

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Test: `packages/whitebox/tests/test_cli.py`

- [ ] **Step 16: Write the failing test**

Append to `packages/whitebox/tests/test_cli.py`:

```python
def test_workspace_delete(tmp_path, monkeypatch):
    """workspace delete should remove the workspace directory."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-del")
    mgr.mark_completed(ws)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "wb-del", "--force"])

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()
    assert not ws.exists()


def test_workspace_delete_not_found(tmp_path, monkeypatch):
    """workspace delete with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "nonexistent", "--force"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_workspace_delete_confirms(tmp_path, monkeypatch):
    """workspace delete without --force should ask for confirmation."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-confirm")

    runner = CliRunner()
    # Answer 'y' to the confirmation
    result = runner.invoke(cli, ["workspace", "delete", "wb-confirm"], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()


def test_workspace_delete_cancelled(tmp_path, monkeypatch):
    """workspace delete confirmation cancelled should not delete."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-cancel")

    runner = CliRunner()
    # Answer 'n' to the confirmation
    result = runner.invoke(cli, ["workspace", "delete", "wb-cancel"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
    assert ws.exists()
```

- [ ] **Step 17: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspace_delete -v`
Expected: FAIL — `No such command: 'delete'`

- [ ] **Step 18: Implement the delete command**

Add after the `show` command in `packages/whitebox/src/shannon_whitebox/cli/main.py`:

```python
@workspace.command()
@click.argument("workspace_name")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def delete(workspace_name, force):
    """Delete a workspace and all its data."""
    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    scan_type = mgr.get_scan_type(ws)
    status = mgr.get_status(ws)
    url = mgr.get_web_url(ws) or "unknown"
    links = mgr.get_links(ws)

    click.echo(f"Workspace to delete: {workspace_name}")
    click.echo(f"  Type:   {scan_type}")
    click.echo(f"  Target: {url}")
    click.echo(f"  Status: {status}")

    if status == "running":
        click.echo("  ⚠ This workspace appears to be running.")

    children = links.get("child_workspaces", [])
    if children:
        click.echo(f"  ⚠ Has {len(children)} child workspace(s)")

    parent = links.get("parent_workspace")
    if parent:
        click.echo(f"  ⚠ Child of: {parent}")

    if not force:
        if not click.confirm("Delete this workspace?", default=False):
            click.echo("Deletion cancelled.")
            return

    if mgr.delete_workspace(workspace_name):
        click.echo(f"✅ Workspace '{workspace_name}' deleted.")
    else:
        click.echo(f"❌ Failed to delete workspace '{workspace_name}'.")
        raise SystemExit(1)
```

- [ ] **Step 19: Run delete tests**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -k "workspace_delete" -v`
Expected: All 4 tests PASS

- [ ] **Step 20: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): add workspace delete command"
```

---

### Task 6: Whitebox CLI — workspace clean command

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Test: `packages/whitebox/tests/test_cli.py`

- [ ] **Step 21: Write the failing test**

Append to `packages/whitebox/tests/test_cli.py`:

```python
def test_workspace_clean(tmp_path, monkeypatch):
    """workspace clean should remove artifacts but keep session.json."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-clean")
    (ws / "deliverables").mkdir()
    (ws / "deliverables" / "injection_exploitation_queue.json").write_text("[]", encoding="utf-8")
    (ws / "workflow.log").write_text("log data", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "wb-clean", "--force"])

    assert result.exit_code == 0
    assert "cleaned" in result.output.lower()
    # session.json survives
    assert (ws / "session.json").exists()
    # deliverables removed
    assert not (ws / "deliverables").exists()


def test_workspace_clean_not_found(tmp_path, monkeypatch):
    """workspace clean with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "nonexistent", "--force"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_workspace_clean_confirms(tmp_path, monkeypatch):
    """workspace clean without --force should ask for confirmation."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-clean-confirm")
    (ws / "workflow.log").write_text("log", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "wb-clean-confirm"], input="y\n")

    assert result.exit_code == 0
    assert "cleaned" in result.output.lower()


def test_workspace_clean_cancelled(tmp_path, monkeypatch):
    """workspace clean confirmation cancelled should not clean."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-clean-cancel")
    (ws / "workflow.log").write_text("log", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "wb-clean-cancel"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
    assert (ws / "workflow.log").exists()
```

- [ ] **Step 22: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_cli.py::test_workspace_clean -v`
Expected: FAIL — `No such command: 'clean'`

- [ ] **Step 23: Implement the clean command**

Add after the `delete` command in `packages/whitebox/src/shannon_whitebox/cli/main.py`:

```python
@workspace.command()
@click.argument("workspace_name")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def clean(workspace_name, force):
    """Clean scan artifacts from a workspace, preserving its structure."""
    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    click.echo(f"Cleaning workspace: {workspace_name}")
    click.echo(f"  Will remove:  deliverables/, agents/, prompts/, scratchpad/, workflow.log, .playwright*/")
    click.echo(f"  Will preserve: session.json")

    if not force:
        if not click.confirm("Proceed with cleaning?", default=False):
            click.echo("Cleaning cancelled.")
            return

    mgr.clean_workspace(ws, scan_type="whitebox")
    click.echo(f"✅ Workspace '{workspace_name}' cleaned.")
```

- [ ] **Step 24: Run clean tests**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -k "workspace_clean" -v`
Expected: All 4 tests PASS

- [ ] **Step 25: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): add workspace clean command"
```

---

### Task 7: Blackbox CLI — workspace delete command

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 26: Write the failing test**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_workspace_delete(tmp_path, monkeypatch):
    """workspace delete should remove the workspace directory."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-del", scan_type="blackbox")
    mgr.mark_completed(ws)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "bb-del", "--force"])

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()
    assert not ws.exists()


def test_workspace_delete_not_found(tmp_path, monkeypatch):
    """workspace delete with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "nonexistent", "--force"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_workspace_delete_confirms(tmp_path, monkeypatch):
    """workspace delete without --force should ask for confirmation."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-confirm", scan_type="blackbox")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "bb-confirm"], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()


def test_workspace_delete_cancelled(tmp_path, monkeypatch):
    """workspace delete confirmation cancelled should not delete."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-cancel", scan_type="blackbox")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "bb-cancel"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
    assert ws.exists()
```

- [ ] **Step 27: Run test to verify it fails**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_workspace_delete -v`
Expected: FAIL — `No such command: 'delete'`

- [ ] **Step 28: Implement the delete command**

Add after the `show` command in `packages/blackbox/src/shannon_blackbox/cli/main.py`:

```python
@workspace.command()
@click.argument("workspace_name")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def delete(workspace_name, force):
    """Delete a workspace and all its data."""
    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    scan_type = mgr.get_scan_type(ws)
    status = mgr.get_status(ws)
    url = mgr.get_web_url(ws) or "unknown"
    links = mgr.get_links(ws)

    click.echo(f"Workspace to delete: {workspace_name}")
    click.echo(f"  Type:   {scan_type}")
    click.echo(f"  Target: {url}")
    click.echo(f"  Status: {status}")

    if status == "running":
        click.echo("  ⚠ This workspace appears to be running.")

    children = links.get("child_workspaces", [])
    if children:
        click.echo(f"  ⚠ Has {len(children)} child workspace(s)")

    parent = links.get("parent_workspace")
    if parent:
        click.echo(f"  ⚠ Child of: {parent}")

    if not force:
        if not click.confirm("Delete this workspace?", default=False):
            click.echo("Deletion cancelled.")
            return

    if mgr.delete_workspace(workspace_name):
        click.echo(f"✅ Workspace '{workspace_name}' deleted.")
    else:
        click.echo(f"❌ Failed to delete workspace '{workspace_name}'.")
        raise SystemExit(1)
```

- [ ] **Step 29: Run delete tests**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -k "workspace_delete" -v`
Expected: All 4 tests PASS

- [ ] **Step 30: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add workspace delete command"
```

---

### Task 8: Blackbox CLI — workspace clean command

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 31: Write the failing test**

Append to `packages/blackbox/tests/test_cli.py`:

```python
def test_workspace_clean(tmp_path, monkeypatch):
    """workspace clean should remove blackbox artifacts but keep session.json."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-clean", scan_type="blackbox")
    (ws / "deliverables").mkdir()
    (ws / "deliverables" / "injection_exploitation_evidence.md").write_text("evidence", encoding="utf-8")
    (ws / "deliverables" / "injection_exploitation_queue.json").write_text("[]", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "injection-exploit_001.log").write_text("log", encoding="utf-8")
    (ws / "workflow.log").write_text("log data", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "bb-clean", "--force"])

    assert result.exit_code == 0
    assert "cleaned" in result.output.lower()
    # session.json survives
    assert (ws / "session.json").exists()
    # Blackbox deliverable removed
    assert not (ws / "deliverables" / "injection_exploitation_evidence.md").exists()
    # Whitebox queue file preserved
    assert (ws / "deliverables" / "injection_exploitation_queue.json").exists()


def test_workspace_clean_not_found(tmp_path, monkeypatch):
    """workspace clean with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "nonexistent", "--force"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_workspace_clean_confirms(tmp_path, monkeypatch):
    """workspace clean without --force should ask for confirmation."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-clean-confirm", scan_type="blackbox")
    (ws / "workflow.log").write_text("log", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "bb-clean-confirm"], input="y\n")

    assert result.exit_code == 0
    assert "cleaned" in result.output.lower()


def test_workspace_clean_cancelled(tmp_path, monkeypatch):
    """workspace clean confirmation cancelled should not clean."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-clean-cancel", scan_type="blackbox")
    (ws / "workflow.log").write_text("log", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "clean", "bb-clean-cancel"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
    assert (ws / "workflow.log").exists()
```

- [ ] **Step 32: Run test to verify it fails**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_workspace_clean -v`
Expected: FAIL — `No such command: 'clean'`

- [ ] **Step 33: Implement the clean command**

Add after the `delete` command in `packages/blackbox/src/shannon_blackbox/cli/main.py`:

```python
@workspace.command()
@click.argument("workspace_name")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def clean(workspace_name, force):
    """Clean scan artifacts from a workspace, preserving its structure."""
    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    click.echo(f"Cleaning workspace: {workspace_name}")
    click.echo(f"  Will remove:  blackbox deliverables, blackbox agent logs, workflow.log, .playwright*/")
    click.echo(f"  Will preserve: session.json, exploitation queues, recon data")

    if not force:
        if not click.confirm("Proceed with cleaning?", default=False):
            click.echo("Cleaning cancelled.")
            return

    mgr.clean_workspace(ws, scan_type="blackbox")
    click.echo(f"✅ Workspace '{workspace_name}' cleaned.")
```

- [ ] **Step 34: Run clean tests**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -k "workspace_clean" -v`
Expected: All 4 tests PASS

- [ ] **Step 35: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add workspace clean command"
```

---

### Task 9: Full test suite smoke test

- [ ] **Step 36: Run all tests**

Run: `uv run pytest packages/core/tests/test_session.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 37: Commit final state (if any fixes needed)**

```bash
git add -A
git commit -m "test: verify workspace delete/clean across all packages"
```
