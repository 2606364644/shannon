# Blackbox Resume Session Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `--blackbox-only` resume timeout by registering the resume attempt in `session.json` so the CLI's poll loop can detect workflow start.

**Architecture:** Insert a `loadResumeState` + `recordResumeAttempt` activity pair into `blackboxPipelineWorkflow` between authentication validation and `initDeliverableGit`. These are the same two activities `pentestPipelineWorkflow` already uses — no new activities, no CLI changes, no new types.

**Tech Stack:** TypeScript, Temporal Workflow SDK, existing activity proxies in `apps/worker/src/temporal/workflows.ts`.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `apps/worker/src/temporal/workflows.ts` | Temporal workflow orchestration | **Modify:** insert resume block in `blackboxPipelineWorkflow` |

**No new files.** No CLI changes. No activity changes. No type changes. The fix reuses the existing `acts.loadResumeState` and `acts.recordResumeAttempt` proxies already used by `pentestPipelineWorkflow` at `workflows.ts:282` and `:313`.

### Why no unit test

The `apps/worker` package has no test framework installed (no `vitest`, `jest`, or test script in `package.json`). Testing strategy for this codebase is end-to-end (Temporal workflow + real Docker worker). The change is 13 lines of declarative activity calls with no branching — type checking and the spec's manual e2e procedure are the appropriate verification here. Adding a test framework is out of scope for this bug fix.

---

## Task 1: Insert resume registration block

**Files:**
- Modify: `apps/worker/src/temporal/workflows.ts` (inside `blackboxPipelineWorkflow`, between the `auth-validation` phase and the `Initialize Deliverables Git` block — currently around lines 922–924)

**Context for the engineer:** `blackboxPipelineWorkflow` (declared at `workflows.ts:839`) is missing the resume registration that `pentestPipelineWorkflow` does at `workflows.ts:280-322`. When the CLI reuses a workspace (`-w <name>`), it polls `session.json` for a new `resumeAttempts` entry (see `apps/cli/src/commands/start.ts:185`). Without this block, the poll times out after 120 s. The fix reuses — verbatim — the same two activity calls the pentest workflow already makes.

The `a` activity proxy is already selected at `workflows.ts:864` via `selectActivityProxy(input)` and is the correct proxy for both `loadResumeState` and `recordResumeAttempt` (the pentest workflow uses the same proxy at `:282` and `:313`).

- [ ] **Step 1: Confirm the insertion point**

Read the file region to verify line numbers (they may have drifted since this plan was written):

```bash
grep -n "Authentication Validation\|Initialize Deliverables Git\|syncCodePathDenyRules\|validate-deliverables" apps/worker/src/temporal/workflows.ts
```

Expected output (line numbers approximate):

```
918:    // === Authentication Validation ===
921:    await authValidationActs.runAuthenticationValidation(activityInput);
924:    // === Initialize Deliverables Git (idempotent) ===
925:    await a.initDeliverableGit(activityInput);
927:    // === Sync SDK deny rules ===
930:    // === Validate deliverables from prior whitebox run ===
```

The new block goes between the `state.currentAgent = null;` line that closes the auth-validation block and the `// === Initialize Deliverables Git (idempotent) ===` comment.

- [ ] **Step 2: Insert the resume registration block**

Using the Edit tool on `apps/worker/src/temporal/workflows.ts`, replace this exact existing text:

```typescript
    // === Authentication Validation ===
    state.currentPhase = 'auth-validation';
    state.currentAgent = 'validate-authentication';
    await authValidationActs.runAuthenticationValidation(activityInput);
    state.currentAgent = null;

    // === Initialize Deliverables Git (idempotent) ===
    await a.initDeliverableGit(activityInput);
```

with this replacement text:

```typescript
    // === Authentication Validation ===
    state.currentPhase = 'auth-validation';
    state.currentAgent = 'validate-authentication';
    await authValidationActs.runAuthenticationValidation(activityInput);
    state.currentAgent = null;

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

    // === Initialize Deliverables Git (idempotent) ===
    await a.initDeliverableGit(activityInput);
```

**Signature cross-check (do this before running the type checker to catch typos):**

- `a.loadResumeState(workspaceName, expectedUrl, expectedRepoPath, deliverablesSubdir?)` — defined at `apps/worker/src/temporal/activities.ts:636`. Call site passes `(input.resumeFromWorkspace, input.webUrl, input.repoPath, input.deliverablesSubdir)` — matches.
- `a.recordResumeAttempt(input, terminatedWorkflows, checkpointHash, previousWorkflowId, completedAgents)` — defined at `apps/worker/src/temporal/activities.ts:852`. Call site passes `(activityInput, input.terminatedWorkflows || [], resumeState.checkpointHash, resumeState.originalWorkflowId, resumeState.completedAgents)` — matches.
- Both fields exist on `PipelineInput` (`apps/worker/src/temporal/shared.ts:18-19`): `resumeFromWorkspace?: string`, `terminatedWorkflows?: string[]`.

- [ ] **Step 3: Type-check the worker package**

Run from repo root:

```bash
pnpm --filter @shannon/worker run check
```

Expected: command exits 0 with no output (or only the cached output of a clean check). Any TypeScript error here means the call signatures don't match — re-read the cross-check above and fix the typo.

- [ ] **Step 4: Run Biome lint + format check**

Run from repo root:

```bash
pnpm biome
```

Expected: exits 0. If it reports formatting issues on the inserted block, run `pnpm biome:fix` and re-run `pnpm biome` to confirm.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/temporal/workflows.ts
git commit -m "fix(blackbox): register resume attempt so CLI poll detects workflow start

When --blackbox-only reuses a workspace, the CLI polls session.json for a
new resumeAttempts entry. blackboxPipelineWorkflow was never writing it,
so the poll timed out after 120s. Reuse the same loadResumeState +
recordResumeAttempt pair pentestPipelineWorkflow already calls."
```

---

## Task 2: End-to-end manual verification

**Files:** (no code changes; verification only)

This task validates the fix against the exact failure scenario in the spec. The codebase's testing strategy for workflow changes is end-to-end (Temporal worker + real Docker container), not unit tests.

**Prerequisites:**
- Docker daemon running
- Local worker image built (`./shannon build`)
- `.env` populated with valid `ANTHROPIC_API_KEY`
- A target repo and URL you are authorized to test

- [ ] **Step 1: Run a whitebox scan into a named workspace**

```bash
./shannon start -r <repo> -u <url> -w test-ws
```

Expected: workflow completes the full pentest pipeline. `workspaces/<hostname>_test-ws-<sessionId>/session.json` exists and contains at least one phase completion. The `test-ws` name is now associated with this workspace in the CLI's workspace registry.

- [ ] **Step 2: Confirm whitebox workspace state before resume**

```bash
./shannon workspaces
```

Expected: `test-ws` appears in the list with status `completed` (or whatever the terminal status was).

Inspect the session metadata directly:

```bash
ls workspaces/*test-ws*/session.json
cat workspaces/*test-ws*/session.json | grep -E '"workflowId"|"completedAgents"|"resumeAttempts"'
```

Note the current `resumeAttempts` array length — you'll compare it after the next step.

- [ ] **Step 3: Run blackbox reusing the same workspace**

```bash
./shannon start -r <repo> -u <url> -w test-ws --blackbox-only
```

**Before the fix:** CLI prints "Waiting for workflow to start..." and times out after 120 s with `Timeout waiting for workflow to start`.

**After the fix:** CLI detects workflow start within seconds (typically < 5 s) and proceeds to display exploitation-phase progress.

- [ ] **Step 4: Verify session.json now records the resume attempt**

Once the blackbox workflow has started, re-read session.json:

```bash
cat workspaces/*test-ws*/session.json | grep -A 5 '"resumeAttempts"'
```

Expected: a new entry in `resumeAttempts` with the new `workflowId` and timestamp corresponding to the blackbox run. The original whitebox entry (if any) should still be present before it.

Also check the unified workflow log:

```bash
ls workspaces/*test-ws*/workflow.log
tail -50 workspaces/*test-ws*/workflow.log
```

Expected: a `=== Resume ===` (or similarly named) header written by `auditSession.logResumeHeader` near the start of the blackbox run, followed by exploitation-phase log entries.

- [ ] **Step 5: Wait for the blackbox workflow to complete and verify the final deliverable**

```bash
./shannon logs test-ws   # tail live progress; Ctrl-C when done
```

After completion, inspect the final report in the deliverables directory:

```bash
ls <repo>/.shannon/deliverables/
```

Expected: the final report includes both whitebox findings (carried over from the prior run via the CLI's workspace overlay at `apps/cli/src/commands/start.ts:79-92`) **and** blackbox exploit results from this run.

- [ ] **Step 6: (Optional) Commit verification note**

If you keep a changelog or scratchpad of verified fixes, record this verification run. Otherwise no commit — Task 1's commit is the only code change in this plan.

---

## Self-Review

**Spec coverage:**
- ✅ "Add resume session registration to `blackboxPipelineWorkflow`" → Task 1
- ✅ "reusing the same two activities that `pentestPipelineWorkflow` already uses: `loadResumeState` and `recordResumeAttempt`" → Task 1, Step 2 (uses both via the `a` proxy)
- ✅ "after authentication validation and before `initDeliverableGit`" → Task 1, Step 2 insertion point is exactly there
- ✅ Code block in the spec matches the inserted block verbatim (modulo the wrapping `if`)
- ✅ "What is NOT needed" table respected — no CLI edits, no `worker.ts` edits, no `restoreGitCheckpoint`, no `persistOrValidateRunScope`, no `allExpectedDone` short-circuit
- ✅ "Flow (after fix)" sequence matches the new code path: preflight → auth validation → loadResumeState → recordResumeAttempt → initDeliverableGit → validateDeliverablesExist → exploitation
- ✅ Testing section's three-step procedure mapped onto Task 2 (whitebox → blackbox → expected behavior)

**Placeholder scan:** none. Every step has either a complete code block, an exact command, or specific output to look for.

**Type consistency:**
- `ResumeState` fields `.checkpointHash`, `.originalWorkflowId`, `.completedAgents` — same property names as the pentest workflow call site at `workflows.ts:313-319`. ✅
- `PipelineInput` fields `.resumeFromWorkspace`, `.terminatedWorkflows`, `.webUrl`, `.repoPath`, `.deliverablesSubdir` — all defined in `apps/worker/src/temporal/shared.ts:18-25`. ✅
- Activity proxy `a` — selected via the same `selectActivityProxy(input)` helper at `workflows.ts:864` that the rest of `blackboxPipelineWorkflow` uses. ✅
