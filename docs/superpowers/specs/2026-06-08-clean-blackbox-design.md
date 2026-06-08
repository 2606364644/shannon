# Design: `./shannon clean` Command

## Summary

Add a `clean` CLI subcommand to clean up blackbox scan results from a workspace, allowing users to re-run a blackbox scan without pollution from a previous failed attempt.

## Motivation

When a blackbox scan fails or produces unsatisfactory results, users need to re-run it using the same whitebox deliverables. Currently this requires manually deleting specific files across two directories (workspace + repo deliverables), with no guidance on which files are safe to delete.

## Command Interface

```bash
# Default: clean blackbox results (these are equivalent)
./shannon clean -w <workspace> -r <repo>
./shannon clean --blackbox -w <workspace> -r <repo>
```

**Parameters:**
- `-w, --workspace <name>` — workspace name (required)
- `-r, --repo <path>` — repository path (required, locates deliverables directory)
- `--blackbox` — explicitly select blackbox cleanup (default, optional)

## Behavior

1. **Validate** — check workspace directory and repo deliverables directory exist; exit with clear error if not
2. **Scan** — enumerate files matching blackbox patterns (see File Patterns below)
3. **Preview** — list all files to be deleted, grouped by location
4. **Confirm** — prompt user with `@clack/prompts` (consistent with `stop --clean` style); abort on cancel
5. **Delete** — remove matched files and truncate `workflow.log`
6. **Summary** — print count of files deleted per location

## File Patterns

**Deleted from `{repo}/.shannon/deliverables/`:**
- `*_exploitation_evidence.md`
- `*_findings.md`
- `comprehensive_security_assessment_report.md`

**Deleted from `workspaces/{name}/agents/`:**
- `*-exploit_*.log`
- `*validate-authentication_*.log`

**Deleted from `workspaces/{name}/`:**
- `.playwright/` directory
- `.playwright-cli/` directory
- `workflow.log` — truncated (not deleted)

**Preserved (whitebox results):**
- `*_analysis_deliverable.md`
- `*_exploitation_queue.json`
- `recon_deliverable.md`
- `pre_recon_deliverable.md`
- `*-vuln_*.log`, `*pre-recon_*.log`, `*recon_*.log`
- `session.json`
- `prompts/` directory

## Architecture

### New File

`apps/cli/src/commands/clean.ts` — Pure filesystem operation, no Docker dependency.

```
clean.ts
├── clean(opts)              — main entry, exported
├── scanBlackboxFiles()      — enumerate files to delete
├── deleteFiles()            — execute deletion + truncation
└── formatPreview()          — format file list for display
```

### Integration

`apps/cli/src/index.ts`:
- Add `clean` case to command switch
- Add to help text
- Parse `-w`, `-r`, `--blackbox` flags

### No Changes To

- Worker package — cleanup is a host-side CLI operation
- Docker or Temporal — not involved
- Existing commands — purely additive

## Error Handling

- Workspace not found → clear error with directory path
- Repo deliverables dir not found → clear error; suggest running whitebox first
- No blackbox files found → informational message, exit 0 (not an error)
- Permission denied on deletion → per-file warning, continue; print summary of failures

## Future Extensibility

The `--blackbox` flag is explicit but optional (default). This leaves room for:
- `--whitebox` — clean whitebox results (different file patterns)
- `--all` — clean everything in workspace + deliverables
- `--logs-only` — clean only logs, not deliverables

No current implementation for these; just ensure the flag parsing doesn't block them.
