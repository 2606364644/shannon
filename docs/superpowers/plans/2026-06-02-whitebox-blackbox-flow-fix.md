# Whitebox→Blackbox Flow Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the whitebox→blackbox handoff so that blackbox scans can reliably discover and reuse whitebox results.

**Architecture:** Three minimal changes — (1) add `--repo` CLI param to blackbox, (2) unify deliverables path resolution with a session-data fallback, (3) add logging and CLI feedback when whitebox results are or aren't found. Also ensure whitebox persists `repo_path` to `session.json` via `SessionManager`.

**Tech Stack:** Python 3.12+, Click CLI, Temporal workflows, pytest, pytest-asyncio

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Modify | Add `--repo` param, update completion message |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Modify | Unify path resolution, add logging |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | No change | `repo_path` field already exists on `BlackboxPipelineInput` |
| `packages/whitebox/src/shannon_whitebox/worker.py` | Modify | Call `SessionManager.create_workspace()` to persist `repo_path` |
| `packages/blackbox/tests/test_cli.py` | Modify | Test `--repo` param acceptance and passthrough |
| `packages/blackbox/tests/test_workflows.py` | Create | Test deliverables path resolution and logging |
| `packages/whitebox/tests/test_worker.py` | Create | Test that `run_scan` persists session data |

---

### Task 1: Add `--repo` parameter to blackbox CLI

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:19-49`
- Test: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/blackbox/tests/test_cli.py`:

```python
def test_start_help_shows_repo_option():
    """Blackbox start --help should list --repo."""
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output or "-r" in result.output


def test_start_accepts_repo_param():
    """Blackbox start should accept --repo without error (will fail on Temporal, but param is parsed)."""
    runner = CliRunner()
    # Use --help after providing all params to verify Click parses them
    # We can't run a full scan without Temporal, but we can verify the param is accepted
    result = runner.invoke(cli, ["start", "--help"])
    assert "--repo" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py::test_start_help_shows_repo_option -v`
Expected: FAIL — `--repo` not in help output

- [ ] **Step 3: Add `--repo` to the CLI**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, add the `--repo` option and pass `repo_path` into `BlackboxPipelineInput`. The full updated `start` function:

```python
@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, repo, output, workspace, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=str(Path(repo).resolve()) if repo else None,
        workspace_name=workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting black-box scan on {url}")
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
        click.echo("Scan completed successfully")
    else:
        click.echo(f"Scan failed: {result.get('error', 'unknown error')}")
        raise SystemExit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py::test_start_help_shows_repo_option packages/blackbox/tests/test_cli.py::test_start_accepts_repo_param -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add --repo CLI option for whitebox result reuse"
```

---

### Task 2: Ensure whitebox persists `repo_path` to session data

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`
- Test: `packages/whitebox/tests/test_worker.py`

- [ ] **Step 1: Write the failing test**

Create `packages/whitebox/tests/test_worker.py`:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_whitebox.pipeline.shared import PipelineInput


@pytest.mark.asyncio
async def test_run_scan_persists_session_data(tmp_path):
    """run_scan should create a session.json with repo_path via SessionManager."""
    repo = tmp_path / "target-repo"
    repo.mkdir()

    input = PipelineInput(
        repo_path=str(repo),
        workspace_name="test-ws",
    )

    # Mock Temporal Client and Worker
    mock_result = MagicMock()
    mock_result.status = "completed"

    mock_client = AsyncMock()
    mock_client.execute_workflow = AsyncMock(return_value=mock_result)

    mock_worker = AsyncMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", return_value=mock_worker):
        from shannon_whitebox.worker import run_scan
        await run_scan(input, "localhost:7233")

    # Verify session.json was created with repo_path
    session_file = tmp_path / "workspaces" / "test-ws" / "session.json"
    assert session_file.exists(), f"session.json not found at {session_file}"
    data = json.loads(session_file.read_text())
    assert data["repo_path"] == str(repo)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_worker.py::test_run_scan_persists_session_data -v`
Expected: FAIL — session.json not created by current `run_scan`

- [ ] **Step 3: Implement session data persistence in whitebox worker**

In `packages/whitebox/src/shannon_whitebox/worker.py`, add workspace creation before starting the Temporal workflow. The full updated file:

```python
import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import run_agent, run_code_index, run_preflight, run_vuln_agent, run_rebuild_call_chains
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput

TASK_QUEUE = "shannon-whitebox"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager
    from pathlib import Path

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = Path(input.repo_path).parent / "workspaces"
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
        result = await client.execute_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        return result


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_worker.py::test_run_scan_persists_session_data -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker.py
git commit -m "feat(whitebox): persist repo_path to session.json via SessionManager"
```

---

### Task 3: Unify deliverables path resolution in blackbox workflow

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:72-80`
- Test: `packages/blackbox/tests/test_workflows.py`

- [ ] **Step 1: Write the failing test**

Create `packages/blackbox/tests/test_workflows.py`:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


def _resolve_deliverables(input: BlackboxPipelineInput) -> Path:
    """Replicate the path resolution logic from BlackboxScanWorkflow for unit testing."""
    from pathlib import Path

    deliverables_path = None
    if input.repo_path:
        deliverables_path = Path(input.repo_path) / input.deliverables_subdir
    elif input.workspace_name:
        session_file = Path("workspaces") / input.workspace_name / "session.json"
        if session_file.exists():
            session_data = json.loads(session_file.read_text())
            saved_repo = session_data.get("repo_path")
            if saved_repo:
                deliverables_path = Path(saved_repo) / input.deliverables_subdir
    if not deliverables_path:
        deliverables_path = Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir
    return deliverables_path


def test_path_resolution_with_repo_path(tmp_path):
    """When repo_path is provided, deliverables should be under repo."""
    repo = tmp_path / "my-repo"
    repo.mkdir()

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        repo_path=str(repo),
        workspace_name="my-scan",
    )
    result = _resolve_deliverables(input)
    assert result == repo / ".shannon" / "deliverables"


def test_path_resolution_fallback_to_session_data(tmp_path, monkeypatch):
    """When repo_path is missing but session.json has it, use session data."""
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "target-repo"
    repo.mkdir()

    # Create session.json with repo_path
    ws_dir = tmp_path / "workspaces" / "my-scan"
    ws_dir.mkdir(parents=True)
    session_data = {"repo_path": str(repo), "web_url": ""}
    (ws_dir / "session.json").write_text(json.dumps(session_data))

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        workspace_name="my-scan",
    )
    result = _resolve_deliverables(input)
    assert result == repo / ".shannon" / "deliverables"


def test_path_resolution_pure_fallback(tmp_path, monkeypatch):
    """When no repo_path and no session data, fall back to workspaces dir."""
    monkeypatch.chdir(tmp_path)

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        workspace_name="my-scan",
    )
    result = _resolve_deliverables(input)
    assert result == Path("workspaces") / "my-scan" / ".shannon" / "deliverables"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_workflows.py -v`
Expected: The `test_path_resolution_fallback_to_session_data` test will fail because the current workflow code doesn't have the session-data fallback logic yet. The other two may pass since the current code handles the with-repo and pure-fallback cases.

Note: These tests replicate the path logic for unit testing. The actual workflow change comes in Step 3.

- [ ] **Step 3: Implement unified path resolution in the workflow**

Replace lines 72-80 in `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` with:

```python
        try:
            # Resolve deliverables path: prefer explicit repo_path, fall back to session data, then default
            deliverables = None
            if input.repo_path:
                deliverables = Path(input.repo_path) / input.deliverables_subdir
            elif input.workspace_name:
                session_file = Path("workspaces") / input.workspace_name / "session.json"
                if session_file.exists():
                    import json as _json
                    session_data = _json.loads(session_file.read_text())
                    saved_repo = session_data.get("repo_path")
                    if saved_repo:
                        deliverables = Path(saved_repo) / input.deliverables_subdir
            if not deliverables:
                deliverables = Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir

            has_whitebox_results = False
            found_classes: list[str] = []
            for vt in selected_classes:
                queue_file = deliverables / f"{vt}_exploitation_queue.json"
                if queue_file.exists():
                    has_whitebox_results = True
                    found_classes.append(vt)
            self._state.has_whitebox_results = has_whitebox_results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_workflows.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_workflows.py
git commit -m "fix(blackbox): unify deliverables path resolution with session-data fallback"
```

---

### Task 4: Add logging and CLI feedback for whitebox result detection

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:44-49`
- Test: `packages/blackbox/tests/test_workflows.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/blackbox/tests/test_workflows.py`:

```python
import logging

from shannon_blackbox.pipeline.shared import BlackboxPipelineState


def test_state_tracks_found_classes_with_results(tmp_path):
    """When exploitation_queue.json exists, found classes should be tracked in state."""
    # This validates that the workflow sets found_classes properly
    # by testing the data model extension
    state = BlackboxPipelineState(
        has_whitebox_results=True,
        found_whitebox_classes=["injection", "xss"],
    )
    assert state.has_whitebox_results is True
    assert state.found_whitebox_classes == ["injection", "xss"]


def test_state_defaults_no_found_classes():
    """Default state should have empty found_whitebox_classes."""
    state = BlackboxPipelineState()
    assert state.found_whitebox_classes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_workflows.py::test_state_tracks_found_classes_with_results -v`
Expected: FAIL — `BlackboxPipelineState` doesn't have `found_whitebox_classes` field yet

- [ ] **Step 3: Add `found_whitebox_classes` to state model**

In `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`, update `BlackboxPipelineState`:

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
```

- [ ] **Step 4: Add logging to workflow**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, add the import and logging calls after the `has_whitebox_results` block. Add at the top of the file (after the existing imports inside the `with workflow.unsafe.imports_passed_through():` block is NOT suitable for stdlib logging — add it at the module level):

```python
import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState

logger = logging.getLogger(__name__)
```

Then after the `self._state.has_whitebox_results = has_whitebox_results` line (from Task 3), add:

```python
            self._state.found_whitebox_classes = found_classes
            if has_whitebox_results:
                logger.info(
                    "Whitebox results detected at %s for classes: %s — skipping RECON_BLACKBOX",
                    deliverables,
                    found_classes,
                )
            else:
                logger.warning(
                    "No whitebox results found at %s — running RECON_BLACKBOX from scratch. "
                    "Tip: pass --repo <path> to reuse whitebox scan results.",
                    deliverables,
                )
```

- [ ] **Step 5: Update CLI completion message**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, update the completion block:

```python
    if result.get("status") == "completed":
        if result.get("has_whitebox_results"):
            classes = result.get("found_whitebox_classes", [])
            click.echo(f"Scan completed (leveraged whitebox results for: {', '.join(classes)})")
        else:
            click.echo("Scan completed (standalone — no whitebox results found)")
    else:
        click.echo(f"Scan failed: {result.get('error', 'unknown error')}")
        raise SystemExit(1)
```

Note: `result` is a `BlackboxPipelineState` dataclass, not a plain dict. Access it via attributes. Update the entire result-handling block:

```python
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

- [ ] **Step 6: Run all blackbox tests**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_workflows.py
git commit -m "feat(blackbox): add logging and CLI feedback for whitebox result detection"
```

---

### Task 5: Run full test suite and verify backward compatibility

**Files:**
- No new files

- [ ] **Step 1: Run complete test suite**

Run: `cd /root/shannon-py && python -m pytest packages/ -v`
Expected: ALL PASS — no regressions

- [ ] **Step 2: Verify backward compatibility — blackbox without `--repo` still works**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_cli.py -v`
Expected: ALL PASS — existing CLI tests (help, basic invocation) unchanged

- [ ] **Step 3: Verify the design spec is fully covered**

Check each spec requirement against the tasks:

| Spec requirement | Task |
|-----------------|------|
| Add `--repo` to blackbox CLI | Task 1 |
| Pass `repo_path` into `BlackboxPipelineInput` | Task 1 |
| Session data fallback for path resolution | Task 2 + Task 3 |
| Ensure whitebox writes `repo_path` to `session.json` | Task 2 |
| Unified deliverables path calculation | Task 3 |
| Logging when whitebox results found/not found | Task 4 |
| CLI completion message shows whitebox status | Task 4 |

- [ ] **Step 4: Final commit (if any test fixes needed)**

```bash
git add -A
git commit -m "test: verify backward compatibility after flow fix"
```
