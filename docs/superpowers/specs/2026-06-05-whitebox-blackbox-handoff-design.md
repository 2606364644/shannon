# Whitebox-Blackbox Handoff Improvement Design

**Date**: 2026-06-05
**Status**: Draft
**Scope**: CLI manual two-step execution (whitebox → blackbox)

## Problem Statement

When running whitebox independently then using its results for blackbox verification via CLI, there are three categories of issues:

1. **Runtime errors**: CLI crashes, missing fields, path resolution failures
2. **UX friction**: Manual two-step operation, unclear prompts, missing summaries
3. **Architecture gaps**: Inconsistent input types, no atomic writes, scattered discovery logic

## Design

### Section 1: Fix Runtime Errors

#### 1.1 Fix Whitebox CLI "Next Steps" Missing workspace_name

**File**: `packages/whitebox/src/shannon_whitebox/cli/main.py`

The whitebox CLI tries to get `workspace_name` from the workflow result, but `PipelineState` doesn't include this field, so it always falls back to "unknown".

**Fix**: Return `workspace_name` and `deliverables_path` from the whitebox workflow's completion result:

```python
# In WhiteboxScanWorkflow.run() return value
return {
    "status": "completed",
    "workspace_name": self._state.workspace_name,
    "deliverables_path": str(self._state.deliverables_path),
    "web_url": input.web_url,
}
```

#### 1.2 Fix resolve_workspaces_dir() CWD Dependency

**File**: `packages/core/src/shannon_core/utils/paths.py`

`resolve_workspaces_dir()` uses `Path("workspaces")` when no `repo_path` is provided, which depends on the caller's current working directory. If the user runs the CLI from a different directory, workspace discovery fails silently.

**Fix**: Default to project root (git root or `pyproject.toml` location) instead of CWD:

```python
def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    if repo_path:
        return Path(repo_path).parent / "workspaces"
    project_root = find_project_root()  # Walk up to find .git or pyproject.toml
    return project_root / "workspaces"
```

#### 1.3 Add Basic Schema Validation for Deliverable JSON

**File**: `packages/core/src/shannon_core/utils/paths.py`

`has_valid_whitebox_results()` only checks that `vulnerabilities` is a non-empty list. It doesn't validate that individual entries have the required fields. Downstream agents receive malformed data and fail silently.

**Fix**: Add field-level validation:

```python
REQUIRED_VULN_FIELDS = {"title", "description", "severity", "location"}

def has_valid_whitebox_results(queue_file: Path) -> bool:
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

#### 1.4 Warn on Conflicting --latest and -w Flags

**File**: `packages/blackbox/src/shannon_blackbox/cli/main.py`

When both `--latest` and `-w` are specified, `-w` silently takes precedence. Users may believe both are active.

**Fix**: Add explicit warning:

```python
if latest and workspace:
    click.echo("⚠ Both --latest and -w specified; -w takes precedence.")
```

### Section 2: UX Improvements

#### 2.1 Post-Whitebox Results Summary

**File**: `packages/whitebox/src/shannon_whitebox/cli/main.py`

After whitebox completes, display a summary of findings by vulnerability class:

```
✅ White-box scan completed!

Results summary:
  ├─ auth:        3 vulnerabilities found
  ├─ injection:   1 vulnerability found
  ├─ ssrf:        0 vulnerabilities found
  └─ xss:         2 vulnerabilities found

Next steps:
  shannon-blackbox start --url https://example.com -w my-project_1717526400000
  # or use --latest:
  shannon-blackbox start --url https://example.com --latest
```

**Implementation**: Reuse `compute_deliverables_summary()` from `shannon_core.workspace` to scan deliverables directory and count entries per class.

#### 2.2 Enhanced Blackbox Auto-Discovery with Context

**File**: `packages/blackbox/src/shannon_blackbox/cli/main.py`

When multiple whitebox workspaces match the target URL, show each workspace's summary (age, vuln counts) to help the user choose:

```
Found 3 white-box workspaces for https://example.com:

  #1  my-project_1717526400000   (2h ago)   auth:3 injection:1 xss:2  ✅
  #2  my-project_1717519200000   (1d ago)   auth:1 injection:0 xss:0  ✅
  #3  my-project_1717432800000   (2d ago)   (no deliverables)          ⚠️

Select workspace [1-3] or press Enter to run standalone scan:
```

**Implementation**: Extend `find_workspaces_by_url()` to return `WorkspaceSummary` objects including age and deliverable stats.

#### 2.3 Unified `shannon scan` Command

**File**: New `packages/combined/` package

Add a top-level CLI command that orchestrates whitebox → blackbox in sequence:

```bash
shannon scan --repo /path/to/repo --url https://example.com
```

This is equivalent to running:
1. `shannon-whitebox start --repo /path/to/repo`
2. `shannon-blackbox start --url https://example.com --latest`

**Package structure**:
```
packages/combined/
├── src/shannon_combined/
│   ├── __init__.py
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py        # `shannon scan` command
│   └── orchestrator.py    # Orchestration logic
└── pyproject.toml
```

The orchestrator calls whitebox's `run_scan()`, waits for completion, then calls blackbox's `run_scan()` with the resolved workspace.

### Section 3: Architecture Improvements

#### 3.1 Unified PipelineInput Base Class

**File**: `packages/core/src/shannon_core/models/base.py`

Extract shared fields into a base class:

```python
@dataclass
class BasePipelineInput:
    """Shared fields for whitebox and blackbox"""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[str] | None = None      # Unified to str
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR

@dataclass
class WhiteboxPipelineInput(BasePipelineInput):
    """Whitebox-specific fields"""
    repo_path: str = ""                        # Required for whitebox
    web_url: str = ""
    prompt_override: str | None = None

@dataclass
class BlackboxPipelineInput(BasePipelineInput):
    """Blackbox-specific fields"""
    web_url: str = ""                          # Required for blackbox
    repo_path: str | None = None               # Optional (from whitebox)
    exploit: bool = True
    max_concurrent: int = 3
    retry_profile: str | None = None
```

**Migration**: Update all references from the old `PipelineInput` / `BlackboxPipelineInput` to the new classes. The whitebox's `VulnType` enum can still be used internally; conversion happens at the boundary.

#### 3.2 Atomic Write for Deliverable Files

**File**: `packages/core/src/shannon_core/utils/atomic_write.py`

Whitebox writes deliverable JSON files non-atomically. If the process crashes mid-write, blackbox may read a truncated file.

**Fix**: Use write-then-rename pattern:

```python
import json
from pathlib import Path

async def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write a JSON file: write to .tmp then rename."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_path.rename(path)  # POSIX rename is atomic
```

All whitebox deliverable writes should use this function instead of direct `write_text()`.

#### 3.3 Unified Workspace Discovery Service

**File**: `packages/core/src/shannon_core/services/workspace_discovery.py`

Extract scattered workspace discovery logic into a single service:

```python
class WorkspaceDiscovery:
    def __init__(self, workspaces_dir: Path | None = None):
        self.workspaces_dir = workspaces_dir or resolve_workspaces_dir()

    def find_for_blackbox(self, url: str, *,
                          latest: bool = False,
                          workspace_name: str | None = None,
                          repo_path: str | None = None) -> DiscoveryResult:
        """Unified discovery entry point for blackbox CLI."""
        ...

    def list_whitebox_workspaces(self, url: str | None = None) -> list[WorkspaceSummary]:
        """List all available whitebox workspaces with summaries."""
        ...

    def validate_for_consumption(self, workspace_path: Path) -> ValidationResult:
        """Validate a workspace is consumable by blackbox."""
        ...
```

**Consumers**: Both blackbox CLI and the new `shannon scan` orchestrator use this service instead of calling scattered utility functions.

#### 3.4 End-to-End Integration Tests

**File**: `tests/integration/test_whitebox_blackbox_handoff.py`

| Test | Description |
|------|-------------|
| test_whitebox_produces_complete_deliverables | Whitebox completion yields all expected queue files |
| test_blackbox_loads_whitebox_results | Blackbox discovers and loads whitebox deliverables |
| test_blackbox_fallback_on_empty_results | Empty whitebox results → blackbox runs standalone recon |
| test_atomic_write_survives_crash | Partial write doesn't produce readable deliverable |
| test_multi_workspace_discovery | Multiple workspaces sorted by recency with correct summaries |
| test_schema_validation_rejects_malformed | Invalid vulnerability entries are rejected during validation |

## Implementation Order

1. Fix runtime errors (Section 1) — highest priority, unblocks daily usage
2. UX improvements (Section 2) — improves daily workflow
3. Architecture improvements (Section 3) — reduces future maintenance burden

Each section can be implemented and merged independently.

## Out of Scope

- Merging whitebox and blackbox into a single package
- Changing the Temporal workflow engine
- Adding new vulnerability classes
- CI/CD pipeline automation
