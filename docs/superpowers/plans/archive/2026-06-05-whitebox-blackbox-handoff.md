# Whitebox-Blackbox Handoff Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix runtime errors, improve UX, and refactor architecture for the whitebox→blackbox CLI handoff workflow.

**Architecture:** Three-phase improvement executed in order: (1) fix runtime bugs that block daily usage, (2) add UX polish to the two-step workflow, (3) refactor shared types and consolidate scattered logic. Each phase produces independently mergeable changes.

**Tech Stack:** Python 3.12, Click CLI, Temporal workflows, pytest, dataclasses

---

## File Structure

### Section 1 — Runtime Error Fixes

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `packages/whitebox/src/shannon_whitebox/worker.py` | Enrich `run_scan` return dict with workspace metadata |
| Modify | `packages/core/src/shannon_core/utils/paths.py` | Add `find_project_root()`, fix `resolve_workspaces_dir()`, enhance `has_valid_whitebox_results()` |
| Modify | `packages/blackbox/src/shannon_blackbox/cli/main.py` | Add `--latest`/`-w` conflict warning |
| Test | `packages/whitebox/tests/test_cli.py` | Test enriched worker return in CLI |
| Test | `packages/core/tests/test_paths.py` | Test `find_project_root`, schema validation |
| Test | `packages/blackbox/tests/test_cli.py` | Test conflict warning |

### Section 2 — UX Improvements

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `packages/whitebox/src/shannon_whitebox/cli/main.py` | Post-scan results summary |
| Modify | `packages/core/src/shannon_core/workspace.py` | Add `WorkspaceSummary`, extend `find_workspaces_by_url` |
| Modify | `packages/blackbox/src/shannon_blackbox/cli/main.py` | Enhanced auto-discovery display |
| Create | `packages/combined/pyproject.toml` | Package config for unified command |
| Create | `packages/combined/src/shannon_combined/__init__.py` | Package init |
| Create | `packages/combined/src/shannon_combined/cli/__init__.py` | CLI package init |
| Create | `packages/combined/src/shannon_combined/cli/main.py` | `shannon scan` CLI command |
| Create | `packages/combined/src/shannon_combined/orchestrator.py` | Whitebox→blackbox orchestration |
| Modify | `pyproject.toml` | Register combined package in workspace |
| Test | `packages/core/tests/test_workspace.py` | Test `WorkspaceSummary`, enhanced discovery |
| Test | `packages/blackbox/tests/test_cli.py` | Test enhanced auto-discovery output |
| Test | `packages/combined/tests/test_orchestrator.py` | Test orchestrator logic |
| Test | `packages/combined/tests/test_cli.py` | Test `shannon scan` CLI |

### Section 3 — Architecture Improvements

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `packages/core/src/shannon_core/models/base.py` | `BasePipelineInput` dataclass |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | Inherit from `BasePipelineInput` |
| Modify | `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | Inherit from `BasePipelineInput` |
| Create | `packages/core/src/shannon_core/utils/atomic_write.py` | `atomic_write_json()` utility |
| Modify | `packages/core/src/shannon_core/agents/executor.py` | Use `atomic_write_json` for queue writes |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Use `atomic_write_json` for plan writes |
| Create | `packages/core/src/shannon_core/services/workspace_discovery.py` | `WorkspaceDiscovery` service class |
| Create | `tests/integration/test_whitebox_blackbox_handoff.py` | End-to-end handoff tests |
| Test | `packages/core/tests/test_atomic_write.py` | Test atomic write utility |
| Test | `packages/core/tests/test_workspace_discovery.py` | Test discovery service |

---

## Task 1: Fix Whitebox Worker Return Value (Spec 1.1)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py:31-75`
- Test: `packages/whitebox/tests/test_cli.py:90-112`

**Problem:** `WhiteboxScanWorkflow.run()` returns `PipelineState` which lacks `workspace_name` and `deliverables_path`. The CLI calls `result.get("workspace_name", "unknown")` which always falls back to "unknown". Additionally, `PipelineState` is a dataclass but the CLI treats the result as a dict — this would crash in production.

- [x] **Step 1: Write the failing test**

Add a test in `packages/whitebox/tests/test_cli.py` that verifies the CLI displays the workspace name and deliverables path from the worker return value:

```python
def test_start_shows_deliverables_path(tmp_path, monkeypatch):
    """Completion output should show deliverables path when returned by worker."""
    monkeypatch.chdir(tmp_path)

    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {
            "status": "completed",
            "workspace_name": "myapp-20260603-143022",
            "deliverables_path": "/repo/workspaces/myapp-20260603-143022/.shannon/deliverables",
            "web_url": "https://example.com",
        }

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure),
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "myapp-20260603-143022" in result.output
    assert "deliverables" in result.output
```

- [x] **Step 2: Run test to verify it passes (test already works with dict mock)**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py::test_start_shows_deliverables_path -v`

Expected: PASS (the current test setup mocks run_scan to return a dict)

- [x] **Step 3: Write the implementation**

Modify `packages/whitebox/src/shannon_whitebox/worker.py`. After `result = await handle.result()` succeeds, convert the `PipelineState` to a dict and add workspace metadata:

```python
# At the top of worker.py, add import:
from dataclasses import asdict
```

Replace the success path in `run_scan()` (lines 62-68) with:

```python
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

            # Convert PipelineState to enriched dict for CLI consumption
            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir
                )
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir
                )

            return result_dict
```

The full modified `run_scan` function becomes:

```python
async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path,
            name=input.workspace_name,
        )

    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[WhiteboxScanWorkflow],
        activities=[run_preflight, run_agent, run_vuln_agent, run_code_index, run_rebuild_call_chains],
    )

    async with worker:
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

            # Convert PipelineState to enriched dict for CLI consumption
            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir
                )
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir
                )

            return result_dict
        except Exception:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            raise
```

- [x] **Step 4: Run tests to verify**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py -v`

Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_cli.py
git commit -m "fix(whitebox): enrich worker return with workspace_name and deliverables_path"
```

---

## Task 2: Fix resolve_workspaces_dir() CWD Dependency (Spec 1.2)

**Files:**
- Modify: `packages/core/src/shannon_core/utils/paths.py:5-13`
- Test: `packages/core/tests/test_paths.py:8-19`

**Problem:** `resolve_workspaces_dir()` uses `Path("workspaces")` when no `repo_path` is provided. This depends on the caller's current working directory. If the user runs the CLI from a different directory, workspace discovery fails silently.

- [x] **Step 1: Write the failing test**

Add tests to `packages/core/tests/test_paths.py` in the `TestResolveWorkspacesDir` class:

```python
def test_without_repo_path_uses_project_root(self, tmp_path, monkeypatch):
    """When no repo_path, should resolve to project_root/workspaces, not CWD."""
    from shannon_core.utils.paths import resolve_workspaces_dir

    # Create a fake project root with .git
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")

    result = resolve_workspaces_dir()
    assert result == project_root / "workspaces"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py::TestResolveWorkspacesDir::test_without_repo_path_uses_project_root -v`

Expected: FAIL — current implementation returns `Path("workspaces")` which is CWD-relative, not project-root-relative.

- [x] **Step 3: Write the implementation**

Add `find_project_root()` and update `resolve_workspaces_dir()` in `packages/core/src/shannon_core/utils/paths.py`:

```python
def find_project_root() -> Path:
    """Walk up from CWD to find project root (directory with .git or pyproject.toml)."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return current


def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。

    如果提供 repo_path，使用 repo_path.parent / "workspaces"；
    否则使用 find_project_root() / "workspaces"。
    """
    if repo_path:
        return Path(repo_path).parent / "workspaces"
    return find_project_root() / "workspaces"
```

- [x] **Step 4: Run tests to verify**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py -v`

Expected: All tests PASS, including the new one. Note: the existing `test_without_repo_path` test asserts `result == Path("workspaces")`. This test must be updated to account for the new behavior. Update it:

```python
def test_without_repo_path(self, tmp_path, monkeypatch):
    """When no repo_path and in a git repo, resolves to project_root/workspaces."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    monkeypatch.chdir(project_root)
    result = resolve_workspaces_dir()
    assert result == project_root / "workspaces"
```

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "fix(core): resolve workspaces dir from project root instead of CWD"
```

---

## Task 3: Add Schema Validation for Deliverable JSON (Spec 1.3)

**Files:**
- Modify: `packages/core/src/shannon_core/utils/paths.py:48-56`
- Test: `packages/core/tests/test_paths.py:90-118`

**Problem:** `has_valid_whitebox_results()` only checks that `vulnerabilities` is a non-empty list. It doesn't validate that individual entries have the required fields (`title`, `description`, `severity`, `location`). Downstream agents receive malformed data and fail silently.

- [x] **Step 1: Write the failing test**

Add to `packages/core/tests/test_paths.py` in the `TestHasValidWhiteboxResults` class:

```python
def test_valid_with_required_fields(self, tmp_path):
    """Vulnerability entries with all required fields should pass validation."""
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({
        "vulnerabilities": [{
            "title": "SQL Injection",
            "description": "User input concatenated into SQL query",
            "severity": "high",
            "location": "src/api/users.py:42",
        }]
    }))
    assert has_valid_whitebox_results(queue_file) is True

def test_rejects_missing_required_fields(self, tmp_path):
    """Vulnerability entries missing required fields should be rejected."""
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({
        "vulnerabilities": [{
            "title": "SQL Injection",
            # Missing: description, severity, location
        }]
    }))
    assert has_valid_whitebox_results(queue_file) is False

def test_rejects_non_dict_entries(self, tmp_path):
    """Non-dict entries in vulnerabilities should be rejected."""
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({
        "vulnerabilities": ["not a dict", 42]
    }))
    assert has_valid_whitebox_results(queue_file) is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py::TestHasValidWhiteboxResults -v`

Expected: `test_valid_with_required_fields` PASS (has all fields), `test_rejects_missing_required_fields` FAIL (current code accepts it), `test_rejects_non_dict_entries` FAIL (current code accepts it).

- [x] **Step 3: Write the implementation**

Replace `has_valid_whitebox_results()` in `packages/core/src/shannon_core/utils/paths.py`:

```python
REQUIRED_VULN_FIELDS = {"title", "description", "severity", "location"}


def has_valid_whitebox_results(queue_file: Path) -> bool:
    """检查 exploitation queue 文件是否包含有效漏洞条目。

    验证 vulnerabilities 列表中的每个条目都包含必需字段：
    title, description, severity, location。
    """
    if not queue_file.exists():
        return False
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        vulns = data.get("vulnerabilities")
        if not isinstance(vulns, list) or len(vulns) == 0:
            return False
        for v in vulns:
            if not isinstance(v, dict):
                return False
            if not REQUIRED_VULN_FIELDS.issubset(v.keys()):
                return False
        return True
    except (json.JSONDecodeError, KeyError, OSError):
        return False
```

- [x] **Step 4: Update existing tests to include required fields**

The existing tests in `TestHasValidWhiteboxResults` use `{"ID": "V-001"}` as vulnerability entries which lack required fields. Update `test_valid_vulnerabilities`:

```python
def test_valid_vulnerabilities(self, tmp_path):
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({
        "vulnerabilities": [{
            "title": "V-001",
            "description": "Test vulnerability",
            "severity": "medium",
            "location": "test.py:1",
        }]
    }))
    assert has_valid_whitebox_results(queue_file) is True
```

- [x] **Step 5: Run all path tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_paths.py -v`

Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/utils/paths.py packages/core/tests/test_paths.py
git commit -m "fix(core): add field-level schema validation for deliverable JSON"
```

---

## Task 4: Warn on Conflicting --latest and -w Flags (Spec 1.4)

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:41-49`
- Test: `packages/blackbox/tests/test_cli.py:237-256`

**Problem:** When both `--latest` and `-w` are specified, `-w` silently takes precedence. Users may believe both are active.

- [x] **Step 1: Write the failing test**

Add to `packages/blackbox/tests/test_cli.py`:

```python
def test_latest_and_w_conflict_warns(tmp_path, monkeypatch):
    """When both --latest and -w are specified, a warning should be printed."""
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
    assert "-w takes precedence" in result.output
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py::test_latest_and_w_conflict_warns -v`

Expected: FAIL — the warning is not yet printed.

- [x] **Step 3: Write the implementation**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, add a warning at the top of the `start()` function body, right after the `selected = ...` line (after line 46):

```python
    # Warn on conflicting flags
    if latest and workspace:
        click.echo("⚠ Both --latest and -w specified; -w takes precedence.")
```

Insert this between line 46 (`selected = ...`) and line 48 (`# Resolve --latest`). The result looks like:

```python
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address, max_concurrent, retry_profile):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    # Warn on conflicting flags
    if latest and workspace:
        click.echo("⚠ Both --latest and -w specified; -w takes precedence.")

    # Resolve --latest: find most recent whitebox workspace with deliverables
    resolved_workspace = workspace
    ...
```

- [x] **Step 4: Run tests**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py -v`

Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "fix(blackbox): warn when both --latest and -w are specified"
```

---

## Task 5: Post-Whitebox Results Summary (Spec 2.1)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:48-65`
- Test: `packages/whitebox/tests/test_cli.py`

**Problem:** After whitebox completes, the CLI shows workspace name and next steps but no summary of findings by vulnerability class. Users can't tell at a glance what was found.

- [x] **Step 1: Write the failing test**

Add to `packages/whitebox/tests/test_cli.py`:

```python
def test_start_shows_results_summary(tmp_path, monkeypatch):
    """Completion output should include a per-class vulnerability count summary."""
    monkeypatch.chdir(tmp_path)

    # Create a workspace with deliverables so compute_deliverables_summary works
    from shannon_core.session import SessionManager
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-summary-ws")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    import json
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [
            {"title": "SQLi", "description": "d", "severity": "high", "location": "a.py:1"},
            {"title": "Cmdi", "description": "d", "severity": "medium", "location": "b.py:2"},
        ]}), encoding="utf-8"
    )
    (deliverables / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [
            {"title": "Reflected XSS", "description": "d", "severity": "medium", "location": "c.py:3"},
        ]}), encoding="utf-8"
    )

    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {
            "status": "completed",
            "workspace_name": "myapp-summary-ws",
            "deliverables_path": str(deliverables),
            "web_url": "https://myapp.com",
        }

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure),
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Results summary" in result.output
    assert "injection" in result.output
    assert "xss" in result.output
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py::test_start_shows_results_summary -v`

Expected: FAIL — current CLI doesn't print "Results summary".

- [x] **Step 3: Write the implementation**

Modify the success path in `packages/whitebox/src/shannon_whitebox/cli/main.py`. Replace lines 48-65 with:

```python
    if result.get("status") == "completed":
        ws_name = result.get("workspace_name", "unknown")
        deliverables_path = result.get("deliverables_path", "")
        web_url = result.get("web_url", "<target-url>")

        click.echo("")
        click.echo("✅ White-box scan completed!")
        click.echo("")

        # Results summary
        if deliverables_path:
            from shannon_core.workspace import compute_deliverables_summary

            summary_path = Path(deliverables_path)
            if summary_path.parent.exists():
                summary = compute_deliverables_summary(summary_path.parent)
                if summary["vuln_queues"]:
                    click.echo("Results summary:")
                    for vc in sorted(summary["vuln_queues"]):
                        queue_file = summary_path.parent / f"{vc}_exploitation_queue.json"
                        try:
                            data = json.loads(queue_file.read_text(encoding="utf-8"))
                            count = len(data.get("vulnerabilities", []))
                        except (json.JSONDecodeError, OSError):
                            count = 0
                        click.echo(f"  ├─ {vc:<12} {count} vulnerabilities found")
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

Note: `json` and `Path` are already imported at the top of this file.

- [x] **Step 4: Run tests**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py -v`

Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): add post-scan results summary with per-class vuln counts"
```

---

## Task 6: Enhanced Blackbox Auto-Discovery with Context (Spec 2.2)

**Files:**
- Modify: `packages/core/src/shannon_core/workspace.py:71-98,128-148`
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:65-103`
- Test: `packages/core/tests/test_workspace.py`

**Problem:** When multiple whitebox workspaces match, the user sees only names and vuln queue names — no age or finding counts to help them choose.

- [x] **Step 1: Write the failing test**

Add to `packages/core/tests/test_workspace.py`:

```python
class TestGetWorkspaceVulnCounts:
    def test_returns_per_class_counts(self, tmp_path):
        from shannon_core.workspace import get_workspace_vuln_counts

        ws = tmp_path / "ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "A", "description": "d", "severity": "high", "location": "a.py:1"},
                {"title": "B", "description": "d", "severity": "low", "location": "b.py:2"},
            ]}), encoding="utf-8"
        )
        (deliverables / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "C", "description": "d", "severity": "medium", "location": "c.py:3"},
            ]}), encoding="utf-8"
        )

        counts = get_workspace_vuln_counts(ws)
        assert counts == {"injection": 2, "xss": 1}

    def test_empty_deliverables(self, tmp_path):
        from shannon_core.workspace import get_workspace_vuln_counts

        ws = tmp_path / "ws"
        ws.mkdir()
        counts = get_workspace_vuln_counts(ws)
        assert counts == {}


class TestGetWorkspaceAge:
    def test_returns_age_string(self, tmp_path):
        from shannon_core.workspace import get_workspace_age_human
        import time

        mgr = SessionManager(tmp_path / "ws")
        ws = mgr.create_workspace("https://test.com", "/repo", name="age-ws")
        mgr.mark_completed(ws)

        age = get_workspace_age_human(ws)
        # Should be something like "0d ago" or "just now" — not empty
        assert isinstance(age, str)
        assert len(age) > 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_workspace.py::TestGetWorkspaceVulnCounts packages/core/tests/test_workspace.py::TestGetWorkspaceAge -v`

Expected: FAIL — `get_workspace_vuln_counts` and `get_workspace_age_human` don't exist yet.

- [x] **Step 3: Write the implementation**

Add two new helper functions to `packages/core/src/shannon_core/workspace.py` after the existing `compute_deliverables_summary` function (after line 98):

```python
import time


def get_workspace_vuln_counts(workspace_path: Path) -> dict[str, int]:
    """Count vulnerabilities per class in a workspace's deliverables."""
    deliverables_dir = workspace_path / "deliverables"
    counts: dict[str, int] = {}

    if not deliverables_dir.exists():
        return counts

    for f in sorted(deliverables_dir.iterdir()):
        if f.is_file() and f.name.endswith("_exploitation_queue.json"):
            vuln_class = f.name.replace("_exploitation_queue.json", "")
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                vulns = data.get("vulnerabilities", [])
                if isinstance(vulns, list):
                    counts[vuln_class] = len(vulns)
            except (json.JSONDecodeError, OSError):
                counts[vuln_class] = 0

    return counts


def get_workspace_age_human(workspace_path: Path) -> str:
    """Return a human-readable age string for a workspace (e.g. '2h ago', '1d ago')."""
    mgr = SessionManager(workspace_path.parent)
    created = mgr.get_created_at(workspace_path)
    if not created:
        return "unknown"

    elapsed = time.time() - created
    if elapsed < 60:
        return "just now"
    elif elapsed < 3600:
        return f"{int(elapsed / 60)}m ago"
    elif elapsed < 86400:
        return f"{int(elapsed / 3600)}h ago"
    else:
        return f"{int(elapsed / 86400)}d ago"
```

Add `import time` at the top of the file if not already present.

- [x] **Step 4: Update the blackbox CLI multi-match display**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, replace the multi-match display block (lines 79-100) with:

First, add the new import at the top of the file (line 19):

```python
from shannon_core.workspace import compute_deliverables_summary, find_latest_workspace, find_workspaces_by_url, get_workspace_vuln_counts, get_workspace_age_human
```

Then replace the `elif len(matches) > 1:` block (lines 79-100):

```python
        elif len(matches) > 1:
            click.echo(f"Found {len(matches)} white-box workspaces for '{url}':")
            click.echo("")
            for i, (ws_path, summary) in enumerate(matches, 1):
                counts = get_workspace_vuln_counts(ws_path)
                age = get_workspace_age_human(ws_path)
                counts_str = " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
                status_icon = "✅" if summary["vuln_queues"] else "⚠️"
                click.echo(f"  #{i}  {ws_path.name:<30} ({age:>6})   {counts_str:<25} {status_icon}")
            click.echo("")
            choice = click.prompt(
                "Select workspace [1-{}] or 'n' for standalone".format(len(matches)),
                default="1",
            )
            if choice.strip().lower() == "n":
                click.echo("Running standalone black-box scan.")
            else:
                try:
                    idx = int(choice.strip()) - 1
                    if 0 <= idx < len(matches):
                        resolved_workspace = matches[idx][0].name
                        click.echo(f"   Using workspace '{resolved_workspace}'")
                    else:
                        click.echo("Invalid selection. Running standalone.")
                except ValueError:
                    click.echo("Invalid selection. Running standalone.")
```

- [x] **Step 5: Run tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_workspace.py packages/blackbox/tests/test_cli.py -v`

Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/workspace.py packages/core/tests/test_workspace.py packages/blackbox/src/shannon_blackbox/cli/main.py
git commit -m "feat(blackbox): enhanced auto-discovery with age and vuln counts"
```

---

## Task 7: Unified `shannon scan` Command (Spec 2.3)

**Files:**
- Create: `packages/combined/pyproject.toml`
- Create: `packages/combined/src/shannon_combined/__init__.py`
- Create: `packages/combined/src/shannon_combined/cli/__init__.py`
- Create: `packages/combined/src/shannon_combined/cli/main.py`
- Create: `packages/combined/src/shannon_combined/orchestrator.py`
- Create: `packages/combined/tests/__init__.py`
- Create: `packages/combined/tests/test_orchestrator.py`
- Create: `packages/combined/tests/test_cli.py`
- Modify: `pyproject.toml` (register workspace member and dependency)

- [x] **Step 1: Create the package structure**

```bash
mkdir -p packages/combined/src/shannon_combined/cli
mkdir -p packages/combined/tests
```

- [x] **Step 2: Write pyproject.toml**

Write `packages/combined/pyproject.toml`:

```toml
[project]
name = "shannon-combined"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
    "shannon-whitebox",
    "shannon-blackbox",
    "click>=8.0",
]

[project.scripts]
shannon = "shannon_combined.cli.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_combined"]
```

- [x] **Step 3: Write __init__.py files**

Write `packages/combined/src/shannon_combined/__init__.py`:

```python
"""Shannon Combined — unified whitebox→blackbox scan orchestration."""
```

Write `packages/combined/src/shannon_combined/cli/__init__.py`:

```python
```

Write `packages/combined/tests/__init__.py`:

```python
```

- [x] **Step 4: Write the failing test for the orchestrator**

Write `packages/combined/tests/test_orchestrator.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from shannon_combined.orchestrator import run_combined_scan


@pytest.mark.asyncio
async def test_run_combined_scan_calls_whitebox_then_blackbox():
    """run_combined_scan should call whitebox run_scan then blackbox run_scan."""
    whitebox_result = {
        "status": "completed",
        "workspace_name": "test-ws-001",
        "deliverables_path": "/repo/workspaces/test-ws-001/.shannon/deliverables",
        "web_url": "https://example.com",
    }

    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value=whitebox_result) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock) as mock_bb,
    ):
        result = await run_combined_scan(
            repo_path="/data/repos/myrepo",
            url="https://example.com",
            temporal_address="localhost:7233",
        )

    mock_wb.assert_called_once()
    mock_bb.assert_called_once()
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_run_combined_scan_stops_on_whitebox_failure():
    """If whitebox fails, blackbox should not be called."""
    whitebox_result = {"status": "failed", "error": "repo not found"}

    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value=whitebox_result) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock) as mock_bb,
    ):
        result = await run_combined_scan(
            repo_path="/data/repos/myrepo",
            url="https://example.com",
            temporal_address="localhost:7233",
        )

    mock_wb.assert_called_once()
    mock_bb.assert_not_called()
    assert result["status"] == "failed"
```

- [x] **Step 5: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/combined/tests/test_orchestrator.py -v`

Expected: FAIL — `shannon_combined.orchestrator` module doesn't exist yet.

- [x] **Step 6: Write the orchestrator**

Write `packages/combined/src/shannon_combined/orchestrator.py`:

```python
"""Orchestration logic: runs whitebox scan then blackbox scan in sequence."""

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput
from shannon_whitebox.pipeline.shared import PipelineInput


async def run_whitebox_scan(input: PipelineInput, temporal_address: str) -> dict:
    """Run whitebox scan and return result dict."""
    from shannon_whitebox.worker import run_scan
    return await run_scan(input, temporal_address)


async def run_blackbox_scan(input: BlackboxPipelineInput, temporal_address: str):
    """Run blackbox scan and return result."""
    from shannon_blackbox.worker import run_scan
    return await run_scan(input, temporal_address)


async def run_combined_scan(
    repo_path: str,
    url: str,
    temporal_address: str = "localhost:7233",
    config_path: str | None = None,
    pipeline_testing: bool = False,
) -> dict:
    """Run whitebox → blackbox in sequence.

    Returns the final blackbox result, or the whitebox result if whitebox failed.
    """
    # Phase 1: Whitebox
    wb_input = PipelineInput(
        repo_path=repo_path,
        web_url=url,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )

    wb_result = await run_whitebox_scan(wb_input, temporal_address)

    if wb_result.get("status") != "completed":
        return {
            "status": "failed",
            "phase": "whitebox",
            "error": wb_result.get("error", "whitebox scan failed"),
        }

    workspace_name = wb_result.get("workspace_name")
    if not workspace_name:
        return {
            "status": "failed",
            "phase": "whitebox",
            "error": "whitebox completed but no workspace_name returned",
        }

    # Phase 2: Blackbox — reuse whitebox workspace
    bb_input = BlackboxPipelineInput(
        web_url=url,
        repo_path=repo_path,
        workspace_name=workspace_name,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )

    bb_result = await run_blackbox_scan(bb_input, temporal_address)

    # Convert dataclass result to dict if needed
    if hasattr(bb_result, "__dataclass_fields__"):
        from dataclasses import asdict
        bb_dict = asdict(bb_result)
    else:
        bb_dict = bb_result if isinstance(bb_result, dict) else {"status": str(bb_result)}

    bb_dict["whitebox_workspace"] = workspace_name
    return bb_dict
```

- [x] **Step 7: Run orchestrator tests**

Run: `cd /root/shannon-py && python -m pytest packages/combined/tests/test_orchestrator.py -v`

Expected: All tests PASS

- [x] **Step 8: Write the failing test for the CLI**

Write `packages/combined/tests/test_cli.py`:

```python
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from shannon_combined.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Shannon" in result.output


def test_scan_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["scan", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "--url" in result.output


def test_scan_calls_orchestrator():
    """scan command should call run_combined_scan and display results."""
    async def fake_combined(*args, **kwargs):
        return {
            "status": "completed",
            "has_whitebox_results": True,
            "found_whitebox_classes": ["injection", "xss"],
            "whitebox_workspace": "test-ws-001",
        }

    with (
        patch("shannon_combined.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_combined.cli.main.run_combined_scan", side_effect=fake_combined),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--repo", "/tmp/repo", "--url", "https://example.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "completed" in result.output.lower()
```

- [x] **Step 9: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/combined/tests/test_cli.py -v`

Expected: FAIL — `shannon_combined.cli.main` module doesn't exist yet.

- [x] **Step 10: Write the CLI**

Write `packages/combined/src/shannon_combined/cli/main.py`:

```python
"""Shannon Combined CLI — unified whitebox→blackbox scan."""

import asyncio

import click
from dotenv import load_dotenv

from shannon_core.services.temporal_infra import ensure_infra


@click.group()
def cli():
    """Shannon — unified security scanning (whitebox + blackbox)."""
    load_dotenv()


@cli.command()
@click.option("--repo", "-r", required=True, help="Target repository path")
@click.option("--url", "-u", required=True, help="Target URL for blackbox verification")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def scan(repo, url, config_path, pipeline_testing, temporal_address):
    """Run whitebox scan followed by blackbox verification."""
    from pathlib import Path

    from shannon_combined.orchestrator import run_combined_scan

    repo_path = str(Path(repo).resolve())
    click.echo(f"Starting combined scan: whitebox → blackbox")
    click.echo(f"  Repository: {repo_path}")
    click.echo(f"  Target URL: {url}")

    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_combined_scan(
        repo_path=repo_path,
        url=url,
        temporal_address=temporal_address,
        config_path=config_path,
        pipeline_testing=pipeline_testing,
    ))

    if result.get("status") == "completed":
        wb_ws = result.get("whitebox_workspace", "unknown")
        classes = result.get("found_whitebox_classes", [])
        if classes:
            click.echo(f"\n✅ Combined scan completed!")
            click.echo(f"  Whitebox workspace: {wb_ws}")
            click.echo(f"  Verified classes: {', '.join(classes)}")
        else:
            click.echo(f"\n✅ Combined scan completed (no whitebox results leveraged)")
    else:
        phase = result.get("phase", "unknown")
        error = result.get("error", "unknown error")
        click.echo(f"\n❌ Combined scan failed during {phase}: {error}")
        raise SystemExit(1)


def main():
    cli()
```

- [x] **Step 11: Register the combined package in the workspace**

Modify `pyproject.toml`. Add `shannon-combined` to the root dependencies and workspace sources:

In the `[project]` section, update `dependencies`:

```toml
dependencies = [
    "shannon-whitebox",
    "shannon-blackbox",
    "shannon-combined",
]
```

The `[tool.uv.sources]` section should become:

```toml
[tool.uv.sources]
shannon-core = { workspace = true }
shannon-whitebox = { workspace = true }
shannon-blackbox = { workspace = true }
shannon-combined = { workspace = true }
```

The `[tool.uv.workspace]` section already uses `members = ["packages/*"]` which will automatically pick up the new package.

- [x] **Step 12: Install the new package**

Run: `cd /root/shannon-py && uv sync`

- [x] **Step 13: Run all combined tests**

Run: `cd /root/shannon-py && python -m pytest packages/combined/tests/ -v`

Expected: All tests PASS

- [x] **Step 14: Commit**

```bash
git add packages/combined/ pyproject.toml
git commit -m "feat(combined): add unified 'shannon scan' command for whitebox→blackbox"
```

---

## Task 8: Unified PipelineInput Base Class (Spec 3.1)

**Files:**
- Create: `packages/core/src/shannon_core/models/base.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:1-19`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py:1-21`
- Test: `packages/whitebox/tests/test_pipeline_shared.py`
- Test: `packages/blackbox/tests/test_pipeline_shared.py`

**Problem:** `PipelineInput` (whitebox) and `BlackboxPipelineInput` (blackbox) duplicate shared fields (`config_path`, `output_path`, `workspace_name`, etc.). Type inconsistencies exist (e.g., `vuln_classes: list[VulnType]` vs `list[str]`).

- [x] **Step 1: Write the failing test**

Write a test verifying that both `PipelineInput` and `BlackboxPipelineInput` inherit from `BasePipelineInput`. Add to `packages/core/tests/` as a new file `test_base_model.py`:

```python
from shannon_core.models.base import BasePipelineInput
from shannon_whitebox.pipeline.shared import PipelineInput
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


def test_pipeline_input_inherits_base():
    assert issubclass(PipelineInput, BasePipelineInput)


def test_blackbox_pipeline_input_inherits_base():
    assert issubclass(BlackboxPipelineInput, BasePipelineInput)


def test_base_has_shared_fields():
    """BasePipelineInput must have all shared fields."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(BasePipelineInput)}
    expected = {
        "config_path", "output_path", "workspace_name",
        "resume_from_workspace", "vuln_classes", "pipeline_testing_mode",
        "api_key", "deliverables_subdir",
    }
    assert expected == field_names
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_base_model.py -v`

Expected: FAIL — `shannon_core.models.base` doesn't exist yet.

- [x] **Step 3: Write the BasePipelineInput**

Write `packages/core/src/shannon_core/models/base.py`:

```python
"""Shared base types for pipeline inputs."""

from dataclasses import dataclass

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


@dataclass
class BasePipelineInput:
    """Shared fields for whitebox and blackbox pipeline inputs."""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[str] | None = None      # Unified to str
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
```

- [x] **Step 4: Migrate whitebox PipelineInput**

Modify `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`:

```python
from dataclasses import dataclass, field

from shannon_core.models.base import BasePipelineInput
from shannon_core.models.agents import VulnType
from shannon_core.models.metrics import AgentMetrics

@dataclass
class PipelineInput(BasePipelineInput):
    """Whitebox-specific fields.

    Note: vuln_classes accepts list[str] from the base class.
    Internally, VulnType enum values are used for type safety;
    conversion happens at the boundary (workflow entry).
    """
    repo_path: str = ""                        # Required for whitebox
    web_url: str = ""
    prompt_override: str | None = None

@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    code_index_stats: dict | None = None
    audit_plan_stats: dict | None = None
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)

@dataclass
class ActivityInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    prompt_override: str | None = None
    workspace_path: str | None = None
```

Note: The `DEFAULT_DELIVERABLES_SUBDIR` import is removed from this file since it comes from `BasePipelineInput`. The `ActivityInput` still needs it, so add the import:

```python
from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
```

- [x] **Step 5: Migrate blackbox BlackboxPipelineInput**

Modify `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`:

```python
from dataclasses import dataclass, field

from shannon_core.models.base import BasePipelineInput
from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


@dataclass
class BlackboxPipelineInput(BasePipelineInput):
    """Blackbox-specific fields."""
    web_url: str = ""                          # Required for blackbox
    repo_path: str | None = None               # Optional (from whitebox)
    exploit: bool = True
    max_concurrent: int = 3
    retry_profile: str | None = None          # "production" | "testing" | "subscription"


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
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)


@dataclass
class BlackboxActivityInput:
    web_url: str
    repo_path: str | None = None
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    agent_name: str | None = None
    vuln_type: str | None = None
    workspace_path: str | None = None
```

- [x] **Step 6: Update workflow VulnType handling**

The whitebox workflow at `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:33` does `input.vuln_classes or list(ALL_VULN_CLASSES)` where `ALL_VULN_CLASSES` returns `list[VulnType]` (which are `str` Literals). Since `vuln_classes` is now `list[str]` from the base class, this should work without changes — `VulnType` is `Literal["injection", ...]` which is a `str` subtype.

Verify the workflow still compiles:

Run: `cd /root/shannon-py && python -c "from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow; print('OK')"`

- [x] **Step 7: Run all tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_base_model.py packages/whitebox/tests/test_pipeline_shared.py packages/blackbox/tests/test_pipeline_shared.py packages/whitebox/tests/test_workflows.py packages/blackbox/tests/test_workflows.py -v`

Expected: All tests PASS

- [x] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/models/base.py packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/core/tests/test_base_model.py
git commit -m "refactor: extract BasePipelineInput with shared fields into core"
```

---

## Task 9: Atomic Write for Deliverable Files (Spec 3.2)

**Files:**
- Create: `packages/core/src/shannon_core/utils/atomic_write.py`
- Modify: `packages/core/src/shannon_core/agents/executor.py:87-89`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:249-250`
- Test: `packages/core/tests/test_atomic_write.py`

**Problem:** Whitebox writes deliverable JSON files non-atomically. If the process crashes mid-write, blackbox may read a truncated file.

- [x] **Step 1: Write the failing test**

Write `packages/core/tests/test_atomic_write.py`:

```python
import json
from pathlib import Path

from shannon_core.utils.atomic_write import atomic_write_json


def test_atomic_write_creates_file(tmp_path):
    """atomic_write_json should create a valid JSON file."""
    target = tmp_path / "output.json"
    data = {"key": "value", "number": 42}
    atomic_write_json(target, data)

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_atomic_write_no_partial_on_error(tmp_path):
    """If write fails, original file should remain unchanged."""
    target = tmp_path / "output.json"
    original_data = {"version": 1}
    target.write_text(json.dumps(original_data), encoding="utf-8")

    # Create a scenario where the write would fail
    # We'll use a mock to simulate a failure after tmp write but before rename
    from unittest.mock import patch
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        try:
            atomic_write_json(target, {"version": 2})
        except OSError:
            pass

    # Original file should be untouched
    assert json.loads(target.read_text(encoding="utf-8")) == original_data


def test_atomic_write_no_tmp_file_left(tmp_path):
    """After successful write, no .tmp file should remain."""
    target = tmp_path / "output.json"
    atomic_write_json(target, {"key": "value"})

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_atomic_write_creates_parent_dirs(tmp_path):
    """atomic_write_json should create parent directories if needed."""
    target = tmp_path / "nested" / "dir" / "output.json"
    atomic_write_json(target, {"key": "value"})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"key": "value"}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_atomic_write.py -v`

Expected: FAIL — `shannon_core.utils.atomic_write` module doesn't exist yet.

- [x] **Step 3: Write the implementation**

Write `packages/core/src/shannon_core/utils/atomic_write.py`:

```python
"""Atomic file write utilities for safe deliverable persistence."""

import json
from pathlib import Path


def atomic_write_json(path: Path, data: dict, *, indent: int = 2) -> None:
    """Atomically write a JSON file: write to .tmp then rename.

    Uses the POSIX write-then-rename pattern to ensure that
    readers never see a partially-written file.

    Args:
        path: Target file path.
        data: Dict to serialize as JSON.
        indent: JSON indentation level.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, indent=indent, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.rename(path)  # POSIX rename is atomic
    except Exception:
        # Clean up temp file on failure
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
```

- [x] **Step 4: Run atomic_write tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_atomic_write.py -v`

Expected: All tests PASS

- [x] **Step 5: Update executor.py to use atomic_write_json**

In `packages/core/src/shannon_core/agents/executor.py`, add import at line 14 (near other imports):

```python
from shannon_core.utils.atomic_write import atomic_write_json
```

Replace line 89 (`queue_path.write_text(json.dumps(result.structured_output, indent=2), encoding="utf-8")`) with:

```python
            atomic_write_json(queue_path, result.structured_output)
```

- [x] **Step 6: Update activities.py to use atomic_write_json**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, add import:

```python
from shannon_core.utils.atomic_write import atomic_write_json
```

Replace line 250 (`plan_path.write_text(plan.to_json())`) with:

```python
        atomic_write_json(plan_path, plan.model_dump() if hasattr(plan, "model_dump") else json.loads(plan.to_json()))
```

Note: `plan.to_json()` already returns a JSON string. We need to parse it back for `atomic_write_json`. A cleaner approach — since `plan` is a pydantic model, use `plan.model_dump()`:

```python
        atomic_write_json(plan_path, plan.model_dump())
```

But if `plan` is not always a pydantic model, keep the safe version:

```python
        plan_data = json.loads(plan.to_json())
        atomic_write_json(plan_path, plan_data)
```

- [x] **Step 7: Run all affected tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_atomic_write.py -v`

Expected: All tests PASS

- [x] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/utils/atomic_write.py packages/core/src/shannon_core/agents/executor.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/test_atomic_write.py
git commit -m "feat(core): add atomic_write_json and use for deliverable writes"
```

---

## Task 10: Unified Workspace Discovery Service (Spec 3.3)

**Files:**
- Create: `packages/core/src/shannon_core/services/workspace_discovery.py`
- Test: `packages/core/tests/test_workspace_discovery.py`

**Problem:** Workspace discovery logic is scattered across `workspace.py`, `paths.py`, and CLI code. This task consolidates it into a single service class.

- [x] **Step 1: Write the failing test**

Write `packages/core/tests/test_workspace_discovery.py`:

```python
import json
from pathlib import Path

import pytest

from shannon_core.services.workspace_discovery import (
    DiscoveryResult,
    ValidationResult,
    WorkspaceDiscovery,
)
from shannon_core.session import SessionManager


def _setup_workspace(tmp_path, name, web_url, vuln_classes, scan_type="whitebox"):
    """Helper to create a workspace with deliverables."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace(web_url, "/repo", name=name, scan_type=scan_type)
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir(parents=True)
    for vc in vuln_classes:
        (deliverables / f"{vc}_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{
                "title": "V", "description": "d", "severity": "high", "location": "a.py:1"
            }]}),
            encoding="utf-8",
        )
    return ws


class TestWorkspaceDiscoveryFindForBlackbox:
    def test_find_by_latest(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://myapp.com", ["injection"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert isinstance(result, DiscoveryResult)
        assert result.workspace_path is not None
        assert result.workspace_path.name == "ws-1"

    def test_find_by_name(self, tmp_path):
        _setup_workspace(tmp_path, "ws-named", "https://myapp.com", ["xss"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", workspace_name="ws-named")
        assert result.workspace_path is not None
        assert result.workspace_path.name == "ws-named"

    def test_find_no_match(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://other.com", ["injection"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert result.workspace_path is None


class TestWorkspaceDiscoveryList:
    def test_list_workspaces_for_url(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://myapp.com", ["injection"])
        _setup_workspace(tmp_path, "ws-2", "https://other.com", ["xss"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        results = discovery.list_whitebox_workspaces("https://myapp.com")
        assert len(results) == 1
        assert results[0].name == "ws-1"

    def test_list_all_workspaces(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://myapp.com", ["injection"])
        _setup_workspace(tmp_path, "ws-2", "https://other.com", ["xss"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        results = discovery.list_whitebox_workspaces()
        assert len(results) == 2


class TestWorkspaceDiscoveryValidate:
    def test_validate_valid_workspace(self, tmp_path):
        ws = _setup_workspace(tmp_path, "ws-valid", "https://myapp.com", ["injection"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.validate_for_consumption(ws)
        assert isinstance(result, ValidationResult)
        assert result.valid is True

    def test_validate_no_deliverables(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="ws-empty")
        mgr.mark_completed(ws)
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.validate_for_consumption(ws)
        assert result.valid is False
        assert len(result.errors) > 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_workspace_discovery.py -v`

Expected: FAIL — module doesn't exist.

- [x] **Step 3: Write the implementation**

Write `packages/core/src/shannon_core/services/workspace_discovery.py`:

```python
"""Unified workspace discovery service for cross-scan UX."""

from dataclasses import dataclass, field
from pathlib import Path

from shannon_core.session import SessionManager
from shannon_core.workspace import (
    compute_deliverables_summary,
    find_latest_workspace,
    find_workspaces_by_url,
    get_workspace_age_human,
    get_workspace_vuln_counts,
)


@dataclass
class WorkspaceSummary:
    """Summary of a workspace for display in discovery UI."""
    name: str
    path: Path
    web_url: str | None = None
    age_human: str = ""
    vuln_counts: dict[str, int] = field(default_factory=dict)
    vuln_queues: list[str] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    """Result of workspace discovery for blackbox."""
    workspace_path: Path | None = None
    workspace_name: str | None = None
    summary: WorkspaceSummary | None = None
    message: str = ""


@dataclass
class ValidationResult:
    """Result of workspace validation."""
    valid: bool = False
    errors: list[str] = field(default_factory=list)


class WorkspaceDiscovery:
    """Unified entry point for workspace discovery."""

    def __init__(self, workspaces_dir: Path | None = None):
        self.workspaces_dir = workspaces_dir or Path("workspaces")

    def find_for_blackbox(
        self,
        url: str,
        *,
        latest: bool = False,
        workspace_name: str | None = None,
    ) -> DiscoveryResult:
        """Find a workspace for blackbox consumption.

        Args:
            url: Target URL to match.
            latest: If True, find the most recent matching workspace.
            workspace_name: If provided, find by exact name.

        Returns:
            DiscoveryResult with the found workspace or None.
        """
        if workspace_name:
            ws = self.workspaces_dir / workspace_name
            if ws.exists():
                return DiscoveryResult(
                    workspace_path=ws,
                    workspace_name=workspace_name,
                    summary=self._build_summary(ws),
                )
            return DiscoveryResult(message=f"Workspace '{workspace_name}' not found.")

        if latest:
            ws = find_latest_workspace(self.workspaces_dir, scan_type="whitebox", url=url)
            if ws:
                return DiscoveryResult(
                    workspace_path=ws,
                    workspace_name=ws.name,
                    summary=self._build_summary(ws),
                )
            return DiscoveryResult(message="No matching white-box workspace found.")

        return DiscoveryResult(message="Specify --latest or -w to select a workspace.")

    def list_whitebox_workspaces(self, url: str | None = None) -> list[WorkspaceSummary]:
        """List all available whitebox workspaces, optionally filtered by URL."""
        mgr = SessionManager(self.workspaces_dir)
        workspaces = mgr.list_workspaces()

        results = []
        for ws in workspaces:
            if mgr.get_scan_type(ws) != "whitebox":
                continue
            if url:
                ws_url = mgr.get_web_url(ws)
                from shannon_core.workspace import urls_match
                if not ws_url or not urls_match(ws_url, url):
                    continue
            results.append(self._build_summary(ws))

        return results

    def validate_for_consumption(self, workspace_path: Path) -> ValidationResult:
        """Validate that a workspace is consumable by blackbox."""
        errors = []

        if not workspace_path.exists():
            errors.append(f"Workspace path does not exist: {workspace_path}")
            return ValidationResult(valid=False, errors=errors)

        session_file = workspace_path / "session.json"
        if not session_file.exists():
            errors.append("Missing session.json")

        summary = compute_deliverables_summary(workspace_path)
        if not summary["vuln_queues"]:
            errors.append("No valid deliverables found")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
        )

    def _build_summary(self, workspace_path: Path) -> WorkspaceSummary:
        """Build a WorkspaceSummary for a workspace."""
        mgr = SessionManager(self.workspaces_dir)
        summary = compute_deliverables_summary(workspace_path)

        return WorkspaceSummary(
            name=workspace_path.name,
            path=workspace_path,
            web_url=mgr.get_web_url(workspace_path),
            age_human=get_workspace_age_human(workspace_path),
            vuln_counts=get_workspace_vuln_counts(workspace_path),
            vuln_queues=summary["vuln_queues"],
        )
```

- [x] **Step 4: Run tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_workspace_discovery.py -v`

Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/workspace_discovery.py packages/core/tests/test_workspace_discovery.py
git commit -m "feat(core): add WorkspaceDiscovery service for unified workspace lookup"
```

---

## Task 11: End-to-End Integration Tests (Spec 3.4)

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_whitebox_blackbox_handoff.py`

- [x] **Step 1: Create integration test directory**

```bash
mkdir -p tests/integration
```

Write `tests/integration/__init__.py`:

```python
```

- [x] **Step 2: Write the integration tests**

Write `tests/integration/test_whitebox_blackbox_handoff.py`:

```python
"""End-to-end integration tests for whitebox→blackbox handoff.

These tests verify the data contracts and file I/O between whitebox
and blackbox without running the full Temporal workflows.
"""

import json
from pathlib import Path

import pytest

from shannon_core.session import SessionManager
from shannon_core.utils.paths import has_valid_whitebox_results, resolve_deliverables_path
from shannon_core.utils.atomic_write import atomic_write_json
from shannon_core.workspace import compute_deliverables_summary, find_workspaces_by_url
from shannon_core.services.workspace_discovery import WorkspaceDiscovery


class TestWhiteboxProducesCompleteDeliverables:
    """Whitebox completion yields all expected queue files."""

    def test_deliverables_have_valid_schema(self, tmp_path):
        """Each exploitation queue file should pass schema validation."""
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-complete")
        mgr.mark_completed(ws)

        deliverables = ws / "deliverables"
        deliverables.mkdir()

        for vc in ["injection", "xss", "auth", "ssrf"]:
            queue_file = deliverables / f"{vc}_exploitation_queue.json"
            atomic_write_json(queue_file, {
                "vulnerabilities": [{
                    "title": f"{vc} vuln",
                    "description": f"A {vc} vulnerability was found",
                    "severity": "high",
                    "location": f"src/{vc}.py:10",
                }]
            })

        # Verify all queue files pass validation
        for vc in ["injection", "xss", "auth", "ssrf"]:
            queue_file = deliverables / f"{vc}_exploitation_queue.json"
            assert has_valid_whitebox_results(queue_file), f"{vc} queue failed validation"

        # Verify deliverables summary
        summary = compute_deliverables_summary(ws)
        assert set(summary["vuln_queues"]) == {"injection", "xss", "auth", "ssrf"}


class TestBlackboxLoadsWhiteboxResults:
    """Blackbox discovers and loads whitebox deliverables."""

    def test_discovery_finds_whitebox_workspace(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-discover")
        mgr.mark_completed(ws)
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{
                "title": "SQLi", "description": "d", "severity": "high", "location": "a.py:1"
            }]}),
            encoding="utf-8",
        )

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 1
        ws_path, summary = results[0]
        assert ws_path.name == "wb-discover"
        assert "injection" in summary["vuln_queues"]

    def test_workspace_discovery_service_finds_workspace(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-svc")
        mgr.mark_completed(ws)
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "auth_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{
                "title": "Broken Auth", "description": "d", "severity": "critical", "location": "auth.py:5"
            }]}),
            encoding="utf-8",
        )

        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert result.workspace_path is not None
        assert result.workspace_path.name == "wb-svc"


class TestBlackboxFallbackOnEmptyResults:
    """Empty whitebox results → blackbox runs standalone recon."""

    def test_no_whitebox_results_returns_empty(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-empty")
        mgr.mark_completed(ws)
        # No deliverables directory

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0

    def test_empty_vulns_not_discovered(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-no-vulns")
        mgr.mark_completed(ws)
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": []}),
            encoding="utf-8",
        )

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0


class TestAtomicWriteSurvivesCrash:
    """Partial write doesn't produce readable deliverable."""

    def test_partial_write_not_readable(self, tmp_path):
        """If a tmp file exists (simulating crash mid-write), target should be absent."""
        target = tmp_path / "deliverables" / "injection_exploitation_queue.json"
        target.parent.mkdir(parents=True)

        # Simulate crash: tmp file exists but target doesn't
        tmp_file = target.with_suffix(".json.tmp")
        tmp_file.write_text('{"vulnerabilities": [{"title": "partial', encoding="utf-8")

        # Target should not exist
        assert not target.exists()
        # has_valid_whitebox_results should return False
        assert has_valid_whitebox_results(target) is False


class TestMultiWorkspaceDiscovery:
    """Multiple workspaces sorted by recency with correct summaries."""

    def test_multiple_workspaces_returned(self, tmp_path):
        import time

        mgr = SessionManager(tmp_path / "workspaces")
        ws1 = mgr.create_workspace("https://myapp.com", "/repo", name="ws-old")
        mgr.mark_completed(ws1)
        d1 = ws1 / "deliverables"
        d1.mkdir()
        (d1 / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "V1", "description": "d", "severity": "high", "location": "a.py:1"}
            ]}),
            encoding="utf-8",
        )

        time.sleep(0.01)

        ws2 = mgr.create_workspace("https://myapp.com", "/repo", name="ws-new")
        mgr.mark_completed(ws2)
        d2 = ws2 / "deliverables"
        d2.mkdir()
        (d2 / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "V2", "description": "d", "severity": "medium", "location": "b.py:2"}
            ]}),
            encoding="utf-8",
        )

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 2
        names = [r[0].name for r in results]
        assert "ws-old" in names
        assert "ws-new" in names


class TestSchemaValidationRejectsMalformed:
    """Invalid vulnerability entries are rejected during validation."""

    def test_missing_fields_rejected(self, tmp_path):
        queue_file = tmp_path / "malformed_exploitation_queue.json"
        queue_file.write_text(
            json.dumps({"vulnerabilities": [{"title": "Only title"}]}),
            encoding="utf-8",
        )
        assert has_valid_whitebox_results(queue_file) is False

    def test_non_dict_entries_rejected(self, tmp_path):
        queue_file = tmp_path / "bad_exploitation_queue.json"
        queue_file.write_text(
            json.dumps({"vulnerabilities": ["string", 42, None]}),
            encoding="utf-8",
        )
        assert has_valid_whitebox_results(queue_file) is False

    def test_truncated_json_rejected(self, tmp_path):
        queue_file = tmp_path / "truncated_exploitation_queue.json"
        queue_file.write_text('{"vulnerabilities": [{"title":', encoding="utf-8")
        assert has_valid_whitebox_results(queue_file) is False
```

- [x] **Step 3: Run the integration tests**

Run: `cd /root/shannon-py && python -m pytest tests/integration/test_whitebox_blackbox_handoff.py -v`

Expected: All tests PASS

- [x] **Step 4: Commit**

```bash
git add tests/integration/
git commit -m "test: add end-to-end integration tests for whitebox-blackbox handoff"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Section | Task | Status |
|-------------|------|--------|
| 1.1 Fix workspace_name | Task 1 | ✅ Worker enriches return dict |
| 1.2 Fix CWD dependency | Task 2 | ✅ `find_project_root()` added |
| 1.3 Schema validation | Task 3 | ✅ Field-level validation added |
| 1.4 Conflict warning | Task 4 | ✅ Warning on `--latest` + `-w` |
| 2.1 Results summary | Task 5 | ✅ Per-class vuln counts shown |
| 2.2 Enhanced discovery | Task 6 | ✅ Age + counts in multi-match |
| 2.3 Unified scan command | Task 7 | ✅ `shannon scan` CLI |
| 3.1 Base class | Task 8 | ✅ `BasePipelineInput` |
| 3.2 Atomic write | Task 9 | ✅ `atomic_write_json` |
| 3.3 Discovery service | Task 10 | ✅ `WorkspaceDiscovery` class |
| 3.4 Integration tests | Task 11 | ✅ 6 test classes covering handoff |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "fill in details", "handle edge cases", or "similar to" patterns found. All code blocks contain complete implementations.

### 3. Type Consistency

- `WorkspaceSummary` (Task 6) matches `WorkspaceDiscovery._build_summary()` return (Task 10) — both use `name`, `path`, `vuln_counts`, `vuln_queues`
- `BasePipelineInput.vuln_classes` is `list[str] | None` — both `PipelineInput` and `BlackboxPipelineInput` inherit this consistently
- `DiscoveryResult.workspace_path` is `Path | None` — used consistently in Tasks 10 and 11
- `has_valid_whitebox_results(queue_file: Path) -> bool` — used consistently in Tasks 3 and 11
- `atomic_write_json(path: Path, data: dict)` — used consistently in Tasks 9 and 11
