# Whitebox→Blackbox Flow Fix Design

**Date**: 2026-06-02
**Status**: Draft
**Scope**: Minimal fix — 3 pain points only

## Problem

When running a whitebox scan followed by a blackbox scan with shared workspace, the blackbox scanner cannot find whitebox results due to three issues:

1. Blackbox CLI lacks `--repo` parameter — no way to tell it where the code repo is
2. Deliverables path calculation is inconsistent between whitebox and blackbox
3. When whitebox results are not found, blackbox silently degrades with no user feedback

## Changes

### Change 1: Add `--repo` to blackbox CLI

**File**: `packages/blackbox/src/shannon_blackbox/cli/main.py`

Add a `--repo` / `-r` option to the `start` command:

```python
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
```

Pass it into `BlackboxPipelineInput.repo_path`.

**User-visible change**:

```bash
# Before (cannot find whitebox results)
shannon-blackbox start --url https://target.com --workspace my-scan

# After (finds whitebox results via repo path)
shannon-blackbox start --url https://target.com --repo /path/to/repo --workspace my-scan
```

**Backward compatibility**: `--repo` is optional. When omitted, behavior is unchanged.

### Change 2: Unify deliverables path resolution

**File**: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

Current blackbox path logic (lines ~73-75):

```python
deliverables = (
    Path(input.repo_path or "") / input.deliverables_subdir
    if input.repo_path
    else Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir
)
```

Whitebox writes deliverables to `<repo_path>/.shannon/deliverables/`. When blackbox has `repo_path`, this resolves correctly. The fallback path (no repo_path) doesn't match any whitebox output location.

Fix: When `repo_path` is not provided but `workspace_name` is, attempt to read `repo_path` from workspace session data (`session.json` written by whitebox). If session data contains `repo_path`, use it to compute the deliverables path.

```python
deliverables_path = None
if input.repo_path:
    deliverables_path = Path(input.repo_path) / input.deliverables_subdir
elif input.workspace_name:
    # Attempt to read repo_path from session data written by whitebox
    session_file = Path("workspaces") / input.workspace_name / "session.json"
    if session_file.exists():
        import json
        session_data = json.loads(session_file.read_text())
        saved_repo = session_data.get("repo_path")
        if saved_repo:
            deliverables_path = Path(saved_repo) / input.deliverables_subdir
if not deliverables_path:
    deliverables_path = Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir
```

Note: This is a best-effort fallback. The primary mechanism is the `--repo` CLI parameter from Change 1. The session data fallback helps when users share a workspace name but forget `--repo`.

**Prerequisite**: The session data fallback requires that the whitebox scan writes `repo_path` into session data. The `SessionManager` class in `shannon_core.session` already supports this via `create_workspace(repo_path=..., web_url=...)`, but the whitebox workflow may not currently call `SessionManager.create_workspace()` to persist session data. If this is the case, add a call in the whitebox worker's `run_scan()` function (or at the start of the whitebox workflow) to ensure `repo_path` is persisted to `session.json` when the workspace is created.

### Change 3: Add feedback when whitebox results are missing

**File**: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

Add logging at the whitebox results detection point:

```python
import logging
logger = logging.getLogger(__name__)

# After computing has_whitebox_results:
if has_whitebox_results:
    logger.info(
        "Whitebox results detected at %s for classes: %s — skipping RECON_BLACKBOX",
        deliverables,
        [vt for vt in selected_classes if (deliverables / f"{vt}_exploitation_queue.json").exists()],
    )
else:
    logger.warning(
        "No whitebox results found at %s — running RECON_BLACKBOX from scratch. "
        "Tip: pass --repo <path> to reuse whitebox scan results.",
        deliverables,
    )
```

Also update the CLI completion message to show whether whitebox results were used:

```python
# In cli/main.py, after run_scan completes:
if result.get("has_whitebox_results"):
    click.echo("Scan completed (leveraged whitebox results)")
else:
    click.echo("Scan completed (standalone — no whitebox results found)")
```

## Files Modified

| File | Change |
|------|--------|
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Add `--repo` option, update completion message |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Unify path resolution, add logging |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | No change needed (`repo_path` field already exists) |
| `packages/whitebox/src/shannon_whitebox/worker.py` (or workflow entry) | Ensure `repo_path` is persisted to session.json via `SessionManager.create_workspace()` |

## Out of Scope

- Unified `shannon scan` entry point (pain point 6)
- Temporal workflow state sharing (pain point 4)
- Configuration validation (pain point 5)
- Report merging across whitebox + blackbox

## Testing

- Verify: `shannon-blackbox start --url <url> --repo <path>` correctly finds whitebox deliverables
- Verify: `shannon-blackbox start --url <url>` (no --repo) still works with session data fallback
- Verify: `shannon-blackbox start --url <url>` (no --repo, no session data) still works standalone
- Verify: Logging output clearly indicates whether whitebox results were found
- Verify: Backward compatibility — existing blackbox-only scans work unchanged
