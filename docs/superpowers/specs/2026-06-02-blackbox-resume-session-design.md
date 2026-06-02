# Blackbox Resume Session Registration

## Problem

When `--blackbox-only` reuses an existing workspace (created by a prior whitebox or full scan),
the CLI times out after 120 seconds with "Timeout waiting for workflow to start".

**Root cause:** The CLI detects `session.json` already exists and enters resume polling mode,
waiting for a new `resumeAttempts` entry in `session.json`. But `blackboxPipelineWorkflow` never
calls `recordResumeAttempt`, so the CLI poll never succeeds.

The worker's `resolveWorkspace` correctly detects `isResume: true` and passes
`resumeFromWorkspace` through `buildPipelineInput` to `blackboxPipelineWorkflow` — the pipeline
simply ignores it.

## Solution

Add resume session registration to `blackboxPipelineWorkflow` by reusing the same two activities
that `pentestPipelineWorkflow` already uses: `loadResumeState` and `recordResumeAttempt`.

### Change

**File:** `apps/worker/src/temporal/workflows.ts`

**Location:** In `blackboxPipelineWorkflow`, after authentication validation and before
`initDeliverableGit`, insert:

```typescript
// === Resume session registration (when continuing from prior whitebox) ===
if (input.resumeFromWorkspace) {
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
}
```

### Why these two activities

- `loadResumeState` — validates the prior workspace is intact (deliverables exist, agents
  completed, git checkpoint available). Returns the context needed for the resume marker.
- `recordResumeAttempt` — writes the `resumeAttempts` entry to `session.json` and a resume
  header to `workflow.log`. This is the exact signal the CLI polls for in `start.ts:185`.

### What is NOT needed

| Omitted | Reason |
|---------|--------|
| CLI (`start.ts`) changes | Polling logic is correct; worker just never wrote the signal |
| Worker entry (`worker.ts`) changes | `resolveWorkspace` and `buildPipelineInput` already pass `resumeFromWorkspace` and `terminatedWorkflows` |
| `restoreGitCheckpoint` | Blackbox doesn't modify source files; whitebox deliverables are already copied by CLI (`start.ts:79-92`) |
| `persistOrValidateRunScope` | Out of scope for this bug fix |
| `allExpectedDone` short-circuit | Blackbox exploit agents aren't in the whitebox `completedAgents` list, so this can't trigger falsely |

### Flow (after fix)

```
CLI:
  session.json exists → isResume=true
  copy .shannon/deliverables/ → workspace overlay
  poll for resumeAttempts growth...

Worker (blackboxPipelineWorkflow):
  preflight
  auth validation
  loadResumeState          → validates prior whitebox workspace
  recordResumeAttempt      → writes resumeAttempts entry   ← CLI detects this
  initDeliverableGit
  validateDeliverablesExist
  exploitation (parallel)
  reporting
```

## Testing

1. Run a whitebox scan: `./shannon start -r <repo> -u <url> -w test-ws`
2. Run blackbox reusing the same workspace: `./shannon start -r <repo> -u <url> -w test-ws --blackbox-only`
3. Expected: CLI detects workflow start within seconds, exploitation proceeds, final report
   includes both whitebox findings and blackbox exploit results
