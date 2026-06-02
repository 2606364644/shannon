# Blackbox Pipeline Session Registration

## Problem

`blackboxPipelineWorkflow` fails to register its session early enough for the CLI
to detect workflow startup. This causes a 120-second timeout in **all three**
blackbox-only scenarios:

| Scenario | CLI expects | Worker writes | Result |
|----------|-------------|---------------|--------|
| `-w` existing whitebox workspace | New `resumeAttempts` entry | Written **after** preflight + auth | Timeout if URL/auth slow |
| `-w` new name | `originalWorkflowId` | Never written | Timeout |
| No `-w` (auto name) | `originalWorkflowId` | Never written | Timeout |

Deliverables are already seeded correctly by CLI (`start.ts:79-92`), so the data
layer works — only the CLI polling signal is missing.

### Root cause

1. Commit `89a9119` added `recordResumeAttempt` to `blackboxPipelineWorkflow`, but
   placed it **after** preflight validation and authentication validation. When
   the target URL is slow or auth takes time, the CLI's 120s poll window expires
   before the workflow writes to `session.json`.

2. For new workspaces (no prior `session.json`), the workflow never calls
   `persistOrValidateRunScope`, so `originalWorkflowId` is never written. The
   session is only created when the first exploit agent runs — far too late.

### Comparison with `pentestPipeline` (main branch)

`pentestPipeline` calls `persistOrValidateRunScope` as the **first** activity in
the try block, then immediately does resume registration. Session registration
completes in seconds, well within the CLI's 120s window.

## Solution

### Change

**File:** `apps/worker/src/temporal/workflows.ts`

**Scope:** `blackboxPipelineWorkflow` only — no CLI, worker entry, or other
workflow changes.

Move session registration to the beginning of the try block, **before** preflight
and auth validation. Handle both paths:

```typescript
try {
  // === Session initialization (before any heavy work) ===
  if (input.resumeFromWorkspace) {
    // Resume path: write resumeAttempts entry so CLI detects growth
    const resumeState = await a.loadResumeState(
      input.resumeFromWorkspace,
      input.webUrl,
      input.repoPath,
      input.deliverablesSubdir,
    );
    await a.recordResumeAttempt(
      activityInput,
      input.terminatedWorkflows || [],
      resumeState.checkpointHash,
      resumeState.originalWorkflowId,
      resumeState.completedAgents,
    );
  } else {
    // New workspace: write originalWorkflowId so CLI detects fresh session
    await a.persistOrValidateRunScope(activityInput, [], true);
  }

  // === Preflight (full — with URL check) ===
  // ... rest of workflow unchanged ...
}
```

### Why this works

- **Resume path** (`-w` + existing `session.json`): `recordResumeAttempt` writes a
  new `resumeAttempts` entry to `session.json`. The CLI polls for
  `resumeAttempts.length > initialResumeCount` and detects it immediately.

- **New workspace path** (no `-w` or new `-w` name): `persistOrValidateRunScope`
  creates `session.json` with `originalWorkflowId`. The CLI polls for
  `!!session.session.originalWorkflowId` and detects it immediately.

- Both activities complete in seconds (file I/O only), so the CLI detects the
  signal well within its 120s window.

### What is NOT changed

| Omitted | Reason |
|---------|--------|
| CLI (`start.ts`) | Polling logic is correct for both paths |
| Worker entry (`worker.ts`) | `resolveWorkspace` and `buildPipelineInput` already handle both paths |
| `restoreGitCheckpoint` | Blackbox doesn't modify source files; deliverables already seeded by CLI |
| `whiteboxPipelineWorkflow` | Already calls `persistOrValidateRunScope` early |
| `pentestPipeline` | Already correct on main branch |

### `persistOrValidateRunScope` parameters for new workspace

`persistOrValidateRunScope(activityInput, [], true)` writes
`scope: { vulnClasses: [], exploit: true }` to `session.json`. This is a
reasonable marker for a blackbox-only run. The empty `vulnClasses` array reflects
that blackbox-only mode doesn't run vulnerability analysis — it only exploits
findings from the prior whitebox scan's deliverables.

## Flow (after fix)

```
Resume path (-w existing workspace):

  CLI:
    session.json exists → isResume=true
    seed .shannon/deliverables/ → workspace overlay
    poll for resumeAttempts growth...

  Worker (blackboxPipelineWorkflow):
    loadResumeState            → validates prior workspace
    recordResumeAttempt        → writes resumeAttempts entry  ← CLI detects
    preflight
    auth validation
    initDeliverableGit
    validateDeliverablesExist
    exploitation (parallel)
    reporting

New workspace path (no -w, or -w new name):

  CLI:
    session.json missing → isResume=false
    seed .shannon/deliverables/ → workspace overlay
    poll for originalWorkflowId...

  Worker (blackboxPipelineWorkflow):
    persistOrValidateRunScope   → creates session.json with originalWorkflowId  ← CLI detects
    preflight
    auth validation
    initDeliverableGit
    validateDeliverablesExist
    exploitation (parallel)
    reporting
```

## Files Modified

| File | Action |
|------|--------|
| `apps/worker/src/temporal/workflows.ts` | Move resume block before preflight; add `persistOrValidateRunScope` for new workspaces |

## Verification

1. Run a whitebox scan: `./shannon start -r <repo> -u <url> -w test-ws`
2. Resume with blackbox (same workspace): `./shannon start -r <repo> -u <url> -w test-ws --blackbox-only`
   - Expected: CLI detects workflow start within seconds
3. Fresh blackbox (new workspace): `./shannon start -r <repo> -u <url> --blackbox-only`
   - Expected: CLI detects workflow start within seconds
