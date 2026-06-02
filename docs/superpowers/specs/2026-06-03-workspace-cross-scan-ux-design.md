# Workspace Cross-Scan UX Design

**Date**: 2026-06-03
**Status**: Approved
**Scope**: `packages/core/`, `packages/whitebox/`, `packages/blackbox/`

## Problem

The refactored shannon-py project has a workspace mechanism (SessionManager + workspace directories) for storing scan results, but the cross-scan handoff UX is broken:

1. White-box scans complete without telling the user the workspace ID/name
2. Users must manually discover workspace names via `workspaces` command to pass to black-box
3. Black-box has no automatic way to find relevant white-box results
4. No workspace linking — after black-box reuses white-box results, there's no record of the relationship

## Approach: Incremental UX Improvement (A+B Fusion)

Preserve the existing file-system-based workspace architecture. Improve the user-facing flow for discovering and reusing cross-scan results through:

- Better CLI output with actionable next-step commands
- `--latest` convenience flag for black-box
- Same-target auto-detection with interactive prompts
- Session data model enhancements for workspace linking

## Design

### Section 1: CLI Output Improvements

#### White-box completion output

After white-box workflow completes, print structured next-step guidance:

```
✅ White-box scan complete.

  Workspace:     myapp-20260603-143022
  Deliverables:  /path/to/repo/.shannon/deliverables/

  Next steps:
    shannon-blackbox start --url https://myapp.com -w myapp-20260603-143022
    # or use --latest to reuse the most recent white-box results:
    shannon-blackbox start --url https://myapp.com --latest
```

**Implementation**: In the white-box Temporal workflow completion handler, collect workspace name and deliverables list, then print via `console.print()` (or `rich`).

#### Black-box startup output

Three scenarios with distinct output:

**Scenario 1 — User provided `-w` or `--latest`, results found:**
```
🔗 Found white-box results in workspace 'myapp-20260603-143022'
   Vulnerability queues found: injection, xss, auth
   Skipping recon phase — leveraging white-box findings directly.
```

**Scenario 2 — User provided `--url`, auto-detected matching white-box results:**
```
🔍 Detected white-box results for 'https://myapp.com' (workspace: myapp-20260603-143022)
   Reuse these results? [Y/n]
```

**Scenario 3 — No white-box results found:**
```
ℹ️  No white-box results found for this target. Running standalone black-box scan.
   Tip: run white-box first, then use --latest to reuse results.
```

**Implementation**: Output does not change existing log format; adds terminal-only user guidance.

### Section 2: `--latest` Parameter for Black-box

#### Behavior

`--latest` makes black-box automatically find the most recent white-box workspace with valid deliverables.

#### Lookup logic

1. Scan all subdirectories under `workspaces/`
2. Read each `session.json`, filter for:
   - `scan_type == "whitebox"` (or infer from directory contents for legacy workspaces)
   - `status == "completed"` or deliverables directory has valid exploitation queue files
3. If `--url` is provided, prioritize workspaces where `web_url` matches
4. Sort by `created_at` descending, take the most recent
5. Validate exploitation queue files: file must exist, be non-empty, and parse as valid JSON

#### Parameter priority

| Priority | Parameter | Behavior |
|----------|-----------|----------|
| 1 (highest) | `-w <name>` | Use specified workspace directly |
| 2 | `--latest` | Auto-find most recent matching workspace |
| 3 (lowest) | (none) | Auto-detect by URL, or run standalone |

If both `-w` and `--latest` are provided, `-w` takes precedence and `--latest` is ignored.

#### Error handling

- `--latest` with no workspaces at all: `"No white-box workspaces found. Run a white-box scan first."`
- `--latest` with workspaces but no valid deliverables: `"Latest workspace has no deliverables. Specify a workspace with -w."`

#### Implementation location

Add `--latest` flag to `packages/blackbox/src/shannon_blackbox/cli/main.py` argparse definition. Resolution logic lives in a new utility function in `packages/core/`.

### Section 3: Same-Target Auto-Detection

#### Behavior

When user runs `shannon-blackbox start --url <target_url>` without `-w` or `--latest`, the system checks for white-box workspaces that scanned the same target.

#### Detection logic

1. Iterate all workspace `session.json` files
2. Match `web_url` against the provided `--url` using normalized comparison:
   - Strip trailing `/` from both URLs
   - Scheme difference (`http` vs `https`) is tolerated
   - Hostname must match exactly (`example.com` ≠ `api.example.com`)
   - Port differences mean different targets
   - Path prefix match counts as same target (white-box on `/app` covers black-box on `/app/api`)
3. Filter to workspaces with valid deliverables (queue files exist, non-empty, parse as valid JSON)

#### Interactive prompt

**Single match:**
```
🔍 Detected white-box results for 'https://myapp.com' (workspace: myapp-20260603-143022)
   Reuse these results? [Y/n]
```

**Multiple matches:**
```
🔍 Found 3 white-box workspaces for 'https://myapp.com':
  [1] myapp-20260603-143022  (3 vuln queues, 2 hours ago)
  [2] myapp-20260602-091500  (2 vuln queues, 1 day ago)
  [3] myapp-20260601-170000  (5 vuln queues, 2 days ago)

Select workspace to reuse [1-3] or 'n' for standalone:
```

#### Configuration

New config option `auto_detect_whitebox: bool = True` in scan configuration. When `False`, skip auto-detection entirely and run standalone.

### Section 4: session.json Data Model Enhancements

#### New fields

```json
{
  "session": {
    "id": "myapp-20260603-143022",
    "web_url": "https://myapp.com",
    "repo_path": "/path/to/repo",
    "scan_type": "whitebox",
    "status": "completed",
    "created_at": "2026-06-03T14:30:22",
    "completed_at": "2026-06-03T15:45:00",
    "scope": { "..." : "..." }
  },
  "links": {
    "parent_workspace": null,
    "child_workspaces": []
  },
  "deliverables_summary": {
    "vuln_queues": ["injection", "xss", "auth"],
    "reports": ["injection_findings.md", "executive_summary.md"]
  },
  "metrics": { "..." : "..." }
}
```

#### Field descriptions

| Field | Type | Description |
|-------|------|-------------|
| `session.scan_type` | `"whitebox" \| "blackbox"` | Which scanner created this workspace |
| `session.status` | `"running" \| "completed" \| "failed"` | Current workflow status |
| `session.completed_at` | ISO 8601 timestamp or null | When the workflow finished |
| `links.parent_workspace` | string or null | For black-box: the white-box workspace name it reused |
| `links.child_workspaces` | string[] | For white-box: black-box workspace names that reused its results |
| `deliverables_summary.vuln_queues` | string[] | Vuln classes with non-empty exploitation queue files |
| `deliverables_summary.reports` | string[] | Generated report filenames |

#### Backward compatibility

All new fields are optional. When reading a legacy `session.json` without these fields:
- `scan_type` defaults to inference from directory contents (presence of `agents/` logs with white-box agent names → `"whitebox"`)
- `status` defaults to `"completed"` if `metrics` has agent data, else `"unknown"`
- `links` defaults to `{ "parent_workspace": null, "child_workspaces": [] }`
- `deliverables_summary` defaults to computed values from scanning the deliverables directory

#### Write timing

| Field | When written |
|-------|-------------|
| `scan_type` | Workspace creation |
| `status` | Workflow start / completion / failure |
| `completed_at` | Workflow successful completion |
| `links.parent_workspace` | Black-box reuses white-box results |
| `links.child_workspaces` | After successful reuse, appended to the white-box workspace's session.json |
| `deliverables_summary` | White-box workflow completion (scan deliverables directory) |

### Section 5: Workspace List and Query Enhancements

#### Enhanced `workspaces` command

Both `shannon-whitebox workspaces` and `shannon-blackbox workspaces` show all workspaces, grouped by `scan_type`:

```
$ shannon-whitebox workspaces

White-box workspaces:
  NAME                    TARGET              STATUS      VULN QUEUES      CREATED
  myapp-20260603-143022   https://myapp.com   completed   injection,xss,   2h ago
                                                                      auth
  myapp-20260601-170000   https://myapp.com   completed   5 classes        2d ago

Black-box workspaces (linked to white-box):
  NAME                    TARGET              STATUS      PARENT WORKSPACE          CREATED
  myapp-bb-20260603       https://myapp.com   completed   myapp-20260603-143022    1h ago
```

#### New `workspace show` subcommand

```
$ shannon-whitebox workspace show myapp-20260603-143022

Workspace: myapp-20260603-143022
  Type:           whitebox
  Target:         https://myapp.com
  Repo:           /path/to/repo
  Status:         completed
  Created:        2026-06-03 14:30:22
  Completed:      2026-06-03 15:45:00
  Duration:       1h 14m 38s

  Deliverables:
    ✅ injection_exploitation_queue.json  (3 findings)
    ✅ xss_exploitation_queue.json        (1 finding)
    ✅ auth_exploitation_queue.json       (2 findings)
    ✅ executive_summary.md

  Linked black-box scans:
    📋 myapp-bb-20260603 (completed, 1h ago)

  Reuse command:
    shannon-blackbox start --url https://myapp.com -w myapp-20260603-143022
```

#### Implementation

- Extract workspace listing logic into shared module in `packages/core/`
- `scan_type` read from session.json, inferred from directory contents as fallback
- Table formatting uses `rich` library (consistent with project style)
- `workspace show` registered as subcommand on both whitebox and blackbox CLIs

## Files to Modify

| File | Change |
|------|--------|
| `packages/core/src/shannon_core/session.py` | Add new session.json fields, backward-compatible reading |
| `packages/core/src/shannon_core/workspace.py` (new) | `find_latest_workspace()`, `find_workspaces_by_url()`, `get_workspace_info()` |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Enhanced completion output, add `workspace show` subcommand |
| `packages/whitebox/src/shannon_whitebox/cli/workspaces.py` | Grouped listing with scan_type |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Add `--latest` flag, auto-detection prompt, enhanced output |
| `packages/blackbox/src/shannon_blackbox/cli/workspaces.py` | Grouped listing with scan_type |
| White-box workflow completion activity | Write `deliverables_summary`, print next-step guidance |
| Black-box workflow start activity | Write `links.parent_workspace`, update parent's `child_workspaces` |

## Out of Scope

- Unified `shannon` CLI entry point (future iteration)
- Database-backed workspace registry (file system is sufficient for current scale)
- Workspace cleanup/archival automation
- Cross-machine workspace sharing (e.g., via S3)
