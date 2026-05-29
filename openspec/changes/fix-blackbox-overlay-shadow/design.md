# Design: Fix Blackbox-Only Overlay Shadow Issue

## Root Cause

The CLI creates workspace overlays for Docker containers in `start.ts` (step 8):

```typescript
// Step 8: Create writable overlay directories
for (const dir of ['deliverables', 'scratchpad', '.playwright-cli', '.playwright']) {
  const dirPath = path.join(workspacePath, dir);
  fs.mkdirSync(dirPath, { recursive: true });
  fs.chmodSync(dirPath, 0o777);
}
```

Then `docker.ts` mounts these empty directories over the repo's paths:

```typescript
args.push('-v', `${workspacePath}/deliverables:${containerPath}/.shannon/deliverables`);
```

For whitebox scans, the overlay starts empty and agents populate it — correct behavior.

For blackbox-only, the workflow's `validateDeliverablesExist()` looks in `/repos/<repo>/.shannon/deliverables/` (the overlay mount) and finds nothing, because the overlay is empty and shadows the real files.

## Fix

After creating overlay directories (step 8), add a conditional block that copies existing deliverables from the repo into the overlay when running in blackbox-only mode.

### Data Flow After Fix

```
Host:
  /root/code/repo/.shannon/deliverables/
    ├── recon_deliverable.md          ← exists from prior whitebox
    ├── *_exploitation_queue.json     ← exists from prior whitebox
    └── ...

  start.ts (blackboxOnly):
    1. mkdir workspace/deliverables/   (empty overlay)
    2. cp repo/.shannon/deliverables/* → workspace/deliverables/  ← NEW
    3. docker run -v workspace/deliverables:/repos/repo/.shannon/deliverables

Container:
  /repos/repo/.shannon/deliverables/
    ├── recon_deliverable.md           ← visible via overlay ✅
    ├── *_exploitation_queue.json      ← visible via overlay ✅
    └── ...

  validateDeliverablesExist() → passes ✅
  exploit agents → read deliverables → write new outputs to same overlay ✅
```

### Implementation Location

**File:** `apps/cli/src/commands/start.ts`

**Position:** After line 77 (overlay directory creation), before line 79 (pre-create mount points).

```typescript
// 8.5. For blackbox-only: seed overlay with existing deliverables from prior whitebox
if (args.blackboxOnly) {
  const srcDir = path.join(repo.hostPath, '.shannon', 'deliverables');
  const dstDir = path.join(workspacePath, 'deliverables');
  if (fs.existsSync(srcDir)) {
    const entries = fs.readdirSync(srcDir);
    for (const entry of entries) {
      if (entry === '.git') continue;
      const src = path.join(srcDir, entry);
      const dst = path.join(dstDir, entry);
      fs.cpSync(src, dst, { recursive: true });
    }
  }
}
```

Key details:
- Skip `.git/` — the overlay will get its own `.git` via `initDeliverableGit` in the workflow
- Use `fs.cpSync` with `recursive: true` to handle subdirectories (e.g., `schemas/`)
- No error if `srcDir` doesn't exist — the workflow will catch the missing deliverables with its own validation

### Why Not Other Approaches

| Approach | Why Not |
|---|---|
| Dual mount (repo deliverables at alternate path) | Requires changing worker code to read from two paths; breaks the `deliverablesDir()` abstraction used everywhere |
| Reuse prior workspace via resume | Workspace overlay deliverables are empty even for completed whitebox runs (bare-metal writes to repo, not overlay); resume mechanism expects overlay to contain files |
| Mount repo deliverables read-only + overlay writable | Docker doesn't support union mounts; would need overlayfs or custom mount logic |

## What Does NOT Change

- `docker.ts` — mount structure unchanged
- `workflows.ts` — blackbox workflow unchanged
- `activities.ts` — `validateDeliverablesExist` unchanged
- `worker.ts` — unchanged
- Whitebox-only mode — unaffected (no copy happens)
- Full pipeline mode — unaffected (no copy happens)
- `local-start.ts` — not used for blackbox-only; no change needed

## File Change Summary

| File | Change Type | Description |
|---|---|---|
| `apps/cli/src/commands/start.ts` | Modify | Add ~10 lines: copy deliverables to overlay when `blackboxOnly` |
