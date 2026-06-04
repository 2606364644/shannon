# Fix: Blackbox Workflow Resume Skips Completed Exploit Agents

**Date:** 2026-06-04
**Status:** Approved
**Scope:** `apps/worker/src/temporal/workflows.ts` only

## Problem

`blackboxPipelineWorkflow` loads `resumeState.completedAgents` during resume and records it in `session.json`, but the exploitation phase ignores this data and re-runs all exploit agents from scratch. This wastes API credits and time — in the observed case, 4 out of 5 completed agents re-ran unnecessarily.

### Root Cause

The full pipeline (`pentestPipelineWorkflow`) defines a `shouldSkip` helper (line 324) and checks it before each vulnerability and exploit agent. The blackbox workflow has no such check in its exploitation loop (lines 972-996).

## Design

### Approach

Replicate the full pipeline's `shouldSkip` pattern into `blackboxPipelineWorkflow`. Minimal change, proven pattern.

### Changes (3 edits, all in `workflows.ts`)

#### 1. Hoist `resumeState` to try-block scope

Current code declares `resumeState` inside `if (input.resumeFromWorkspace)` (line 913), making it inaccessible to the exploitation phase. Hoist to the top of the `try` block:

```typescript
// Before the try block's first statement:
let resumeState: ResumeState | undefined;

// Inside the if:
if (input.resumeFromWorkspace) {
  resumeState = await a.loadResumeState(
    input.resumeFromWorkspace,
    input.webUrl,
    input.repoPath,
    input.deliverablesSubdir,
  );
  // ... rest unchanged
}
```

#### 2. Add `shouldSkip` helper

After the resume/session initialization block, before the preflight phase:

```typescript
const shouldSkip = (agentName: string): boolean =>
  resumeState?.completedAgents.includes(agentName) ?? false;
```

Identical to `pentestPipelineWorkflow` line 324.

#### 3. Add skip check in exploit thunks

Inside `exploitThunks.map`, before `checkExploitationQueue`:

```typescript
const exploitThunks = vulnTypesWithQueues.map((vulnType) => {
  return async (): Promise<VulnExploitPipelineResult> => {
    const exploitAgentName = `${vulnType}-exploit`;

    // Skip agents that completed in a prior run
    if (shouldSkip(exploitAgentName)) {
      log.info(`Skipping ${exploitAgentName} (already complete)`);
      state.completedAgents.push(exploitAgentName);
      return {
        vulnType,
        vulnMetrics: null,
        exploitMetrics: null,
        exploitDecision: { shouldExploit: false, vulnerabilityCount: 0 },
        error: null,
      };
    }

    // ... existing logic unchanged
  };
});
```

## What Does NOT Change

- `validateDeliverablesExist` — still only checks whitebox deliverables exist
- `loadResumeState` — unchanged
- `pentestPipelineWorkflow` — unaffected
- `whiteboxPipelineWorkflow` — out of scope (no resume support yet)
- Activity layer — no changes

## Testing

1. Run a blackbox scan that completes 3/5 exploit agents, then gets cancelled
2. Resume with `-w` — verify only the 2 unfinished agents re-run
3. Verify `session.json` correctly records all 5 as completed after resume finishes
4. Verify deliverables from first run are preserved (not overwritten)
