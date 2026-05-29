# Proposal: Fix Blackbox-Only Overlay Shadow Issue

## Problem

When running `shannon start --blackbox-only`, the Docker container fails with:

```
MissingDeliverablesError
Blackbox-only requires prior whitebox deliverables.
Missing: /repos/<repo>/.shannon/deliverables/recon_deliverable.md
```

The root cause is a **volume mount shadowing bug**: the CLI creates empty workspace overlay directories and mounts them over the repo's `.shannon/deliverables/` path inside the container. This empty overlay hides the real whitebox deliverables that exist in the repo on the host.

```
Container mount layout:

  -v /repo:/repos/repo:ro                          ← repo is read-only
  -v workspace/deliverables:/repos/repo/.shannon/deliverables  ← EMPTY overlay shadows real files!

  Container sees: /repos/repo/.shannon/deliverables/ = empty
  Host has:       /repo/.shannon/deliverables/ = full of whitebox outputs
```

## Proposed Solution

In the CLI layer (`start.ts`), after creating the empty overlay directories, detect `blackboxOnly` mode and copy all existing deliverables from the repo's `.shannon/deliverables/` into the workspace overlay directory before the container starts.

The fix is **a single conditional block** (~10 lines) added after overlay directory creation in `start.ts`. No changes to Docker mount architecture, worker code, or workflow logic.

## Scope

### In Scope

- Copy existing deliverables from repo to workspace overlay when `--blackbox-only` is set
- Applies to `start.ts` (Temporal/Docker path)

### Out of Scope

- Modifying Docker mount architecture
- Modifying worker or workflow code
- Changing whitebox-only or full pipeline behavior
- The `local-start.ts` npx path (only used for whitebox-only)

## Dependencies

- Existing `--blackbox-only` flag and `blackboxPipelineWorkflow` (already implemented)
