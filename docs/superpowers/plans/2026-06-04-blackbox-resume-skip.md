# Blackbox Resume Skip Exploit Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `blackboxPipelineWorkflow` skip already-completed exploit agents during resume, mirroring the pattern already used in `pentestPipelineWorkflow`.

**Architecture:** Three surgical edits to `apps/worker/src/temporal/workflows.ts` — hoist `resumeState` to try-block scope, add a `shouldSkip` helper, and insert skip checks before each exploit thunk runs. No new files, no activity changes, no type changes.

**Tech Stack:** TypeScript, Temporal workflow SDK

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `apps/worker/src/temporal/workflows.ts:908-996` | Hoist `resumeState`, add `shouldSkip`, add skip logic in exploit thunks |

No new files. No test files — the worker package has no test infrastructure (`package.json` has no test script, no test directory exists). Verification is via TypeScript type-checking and manual integration testing as described in the spec.

---

### Task 1: Hoist `resumeState` to try-block scope

**Files:**
- Modify: `apps/worker/src/temporal/workflows.ts:908-918`

Currently `resumeState` is declared with `const` inside the `if (input.resumeFromWorkspace)` block at line 913, making it inaccessible to the exploitation phase at line 972. Hoist it as a `let` to the top of the `try` block so the `shouldSkip` helper can read it.

- [x] **Step 1: Add `let resumeState` declaration at the top of the try block**

At `apps/worker/src/temporal/workflows.ts`, after line 908 (`try {`) and before the `// === Session initialization` comment (line 909), add:

```typescript
  try {
    let resumeState: ResumeState | undefined;

    // === Session initialization (before any heavy work) ===
```

- [x] **Step 2: Change `const resumeState` to `resumeState` inside the if block**

In `apps/worker/src/temporal/workflows.ts:912-918`, change the `const` declaration to an assignment:

Before:
```typescript
    if (input.resumeFromWorkspace) {
      const resumeState = await a.loadResumeState(
        input.resumeFromWorkspace,
        input.webUrl,
        input.repoPath,
        input.deliverablesSubdir,
      );
```

After:
```typescript
    if (input.resumeFromWorkspace) {
      resumeState = await a.loadResumeState(
        input.resumeFromWorkspace,
        input.webUrl,
        input.repoPath,
        input.deliverablesSubdir,
      );
```

- [x] **Step 3: Verify type-check passes**

Run: `pnpm run check --filter @shannon/worker`
Expected: PASS (no type errors)

- [x] **Step 4: Commit**

```bash
git add apps/worker/src/temporal/workflows.ts
git commit -m "refactor(blackbox): hoist resumeState to try-block scope"
```

---

### Task 2: Add `shouldSkip` helper

**Files:**
- Modify: `apps/worker/src/temporal/workflows.ts` (after the session initialization block)

Add the same `shouldSkip` helper that `pentestPipelineWorkflow` uses at line 324, placed right after the session initialization block and before the preflight phase.

- [x] **Step 1: Add `shouldSkip` after the session init if/else block**

In `apps/worker/src/temporal/workflows.ts`, after the closing `}` of the `else` block (line 930: `await a.persistOrValidateRunScope(...)`) and before the `// === Preflight` comment (line 932), insert:

```typescript
    }

    const shouldSkip = (agentName: string): boolean =>
      resumeState?.completedAgents.includes(agentName) ?? false;

    // === Preflight (full — with URL check) ===
```

- [x] **Step 2: Verify type-check passes**

Run: `pnpm run check --filter @shannon/worker`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add apps/worker/src/temporal/workflows.ts
git commit -m "feat(blackbox): add shouldSkip helper for resume state"
```

---

### Task 3: Add skip check in exploit thunks

**Files:**
- Modify: `apps/worker/src/temporal/workflows.ts:972-996`

Inside the `exploitThunks.map` callback, add a skip check before `checkExploitationQueue`. This mirrors the pattern in `pentestPipelineWorkflow`'s `runVulnExploitPipeline` at line 513.

- [x] **Step 1: Insert skip check inside the exploit thunk**

In `apps/worker/src/temporal/workflows.ts`, inside the `exploitThunks.map` callback. The current code at lines 972-996:

```typescript
    const exploitThunks = vulnTypesWithQueues.map((vulnType) => {
      return async (): Promise<VulnExploitPipelineResult> => {
        const exploitAgentName = `${vulnType}-exploit`;

        const decision = await a.checkExploitationQueue(activityInput, vulnType);
        let exploitMetrics: AgentMetrics | null = null;

        if (decision.shouldExploit) {
          exploitMetrics = await exploitAgents[vulnType](activityInput);
          state.agentMetrics[exploitAgentName] = exploitMetrics;
          state.completedAgents.push(exploitAgentName);
          if (input.checkpointsEnabled) {
            await a.saveCheckpoint(activityInput, exploitAgentName, 'exploitation', state);
          }
        }

        return {
          vulnType,
          vulnMetrics: null,
          exploitMetrics,
          exploitDecision: { shouldExploit: decision.shouldExploit, vulnerabilityCount: decision.vulnerabilityCount },
          error: null,
        };
      };
    });
```

Replace with:

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

        const decision = await a.checkExploitationQueue(activityInput, vulnType);
        let exploitMetrics: AgentMetrics | null = null;

        if (decision.shouldExploit) {
          exploitMetrics = await exploitAgents[vulnType](activityInput);
          state.agentMetrics[exploitAgentName] = exploitMetrics;
          state.completedAgents.push(exploitAgentName);
          if (input.checkpointsEnabled) {
            await a.saveCheckpoint(activityInput, exploitAgentName, 'exploitation', state);
          }
        }

        return {
          vulnType,
          vulnMetrics: null,
          exploitMetrics,
          exploitDecision: { shouldExploit: decision.shouldExploit, vulnerabilityCount: decision.vulnerabilityCount },
          error: null,
        };
      };
    });
```

- [x] **Step 2: Verify type-check passes**

Run: `pnpm run check --filter @shannon/worker`
Expected: PASS

- [x] **Step 3: Run lint check**

Run: `pnpm biome`
Expected: PASS (no new violations)

- [x] **Step 4: Commit**

```bash
git add apps/worker/src/temporal/workflows.ts
git commit -m "fix(blackbox): skip completed exploit agents on resume"
```

---

## Verification (Manual Integration Test)

Per the spec, verify with a real blackbox scan:

1. Run a blackbox scan that completes 3/5 exploit agents, then cancel it
2. Resume with `-w` — verify only the 2 unfinished agents re-run
3. Verify `session.json` correctly records all 5 as completed after resume finishes
4. Verify deliverables from first run are preserved (not overwritten)
