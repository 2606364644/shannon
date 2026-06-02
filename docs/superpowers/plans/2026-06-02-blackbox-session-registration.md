# Blackbox Session Registration Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `blackboxPipelineWorkflow`'s session registration before preflight/auth so the CLI detects workflow startup within its 120s polling window.

**Architecture:** Replace the current "preflight → auth → resume registration" order with "session registration → preflight → auth". For new workspaces, add `persistOrValidateRunScope` to create `session.json` with `originalWorkflowId`.

**Tech Stack:** TypeScript, Temporal workflows, Node.js

**Spec:** `docs/superpowers/specs/2026-06-02-blackbox-resume-session-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `apps/worker/src/temporal/workflows.ts` | Modify lines 908-940 | Move resume block to top of try; add else branch with `persistOrValidateRunScope` |

No other files change.

---

### Task 1: Move session registration before preflight

**Files:**
- Modify: `apps/worker/src/temporal/workflows.ts:908-940`

The current code at lines 908-940 has this order:

```
try {
  preflight (909-913)
  playwright stealth (915-916)
  auth validation (918-922)
  resume registration (924-940)   ← too late
  ...
```

We need to change it to:

```
try {
  session registration (909-932)  ← first thing
  preflight (934-938)
  playwright stealth (940-941)
  auth validation (943-947)
  ...
```

- [ ] **Step 1: Apply the edit**

In `apps/worker/src/temporal/workflows.ts`, replace lines 908-940 (from `try {` through the closing `}` of the `if (input.resumeFromWorkspace)` block) with:

```typescript
  try {
    // === Session initialization (before any heavy work) ===
    // Must run first so the CLI detects the workflow within its 120s polling window.
    // Resume path: write resumeAttempts entry. New workspace: write originalWorkflowId.
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
    } else {
      // New workspace: initialize session.json so CLI sees originalWorkflowId
      await a.persistOrValidateRunScope(activityInput, [], true);
    }

    // === Preflight (full — with URL check) ===
    state.currentPhase = 'preflight';
    state.currentAgent = null;
    await preflightActs.runPreflightValidation(activityInput);
    log.info('Preflight validation passed');

    // === Playwright stealth config ===
    await preflightActs.syncPlaywrightStealthConfig(activityInput);

    // === Authentication Validation ===
    state.currentPhase = 'auth-validation';
    state.currentAgent = 'validate-authentication';
    await authValidationActs.runAuthenticationValidation(activityInput);
    state.currentAgent = null;
```

The old code after line 940 (`// === Initialize Deliverables Git ===`) and everything below stays unchanged.

- [ ] **Step 2: Verify the edit is correct**

Read `apps/worker/src/temporal/workflows.ts` lines 906-960 and confirm:
1. The `try {` block starts with session initialization (not preflight)
2. The `if (input.resumeFromWorkspace)` branch has `loadResumeState` + `recordResumeAttempt`
3. The `else` branch has `persistOrValidateRunScope(activityInput, [], true)`
4. Preflight, stealth config, and auth validation follow after
5. The old resume block (lines 924-940 in the original) is gone — no duplication
6. `initDeliverableGit` and everything after it is untouched

- [ ] **Step 3: Run TypeScript type check**

Run: `pnpm run check`
Expected: All packages pass with no errors

- [ ] **Step 4: Run Biome lint and format check**

Run: `pnpm biome`
Expected: "Checked N files in Xms. No fixes applied."

- [ ] **Step 5: Build all packages**

Run: `pnpm run build`
Expected: All packages build successfully

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/temporal/workflows.ts
git commit -m "fix(blackbox): move session registration before preflight to prevent CLI timeout

Session initialization now runs as the first activity in the try block,
matching pentestPipeline's pattern. This ensures session.json is written
within seconds, well inside the CLI's 120s polling window.

Resume path: loadResumeState + recordResumeAttempt write resumeAttempts.
New workspace path: persistOrValidateRunScope writes originalWorkflowId."
```

---

## Self-Review

**Spec coverage:**
- Resume path (move `recordResumeAttempt` before preflight): Task 1 ✅
- New workspace path (add `persistOrValidateRunScope`): Task 1 ✅
- No CLI/worker/other workflow changes: Confirmed ✅

**Placeholder scan:** No TBD, TODO, or vague steps. All code shown inline. ✅

**Type consistency:** `persistOrValidateRunScope(activityInput, [], true)` matches the function signature `(input: ActivityInput, vulnClasses: VulnClass[], exploit: boolean): Promise<void>`. The `a` variable (activity proxy) already has `persistOrValidateRunScope` registered as an activity. ✅
