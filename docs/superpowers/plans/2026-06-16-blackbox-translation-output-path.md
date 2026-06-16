# Blackbox Translation Output Path Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--blackbox-only` scans auto-produce a Chinese report at `workspaces/<ws>/deliverables-cn/`, and make translation failures visible in `workflow.log` instead of silently swallowed.

**Architecture:** The blackbox Temporal container mounts the user repo read-only and overlays only specific `.shannon` subdirs with writable workspace-backed dirs. `deliverables-cn` is missing from that overlay list, so the translation provider's hardcoded `repo/.shannon/deliverables-cn` write target is read-only → every `writeFile` fails `EROFS`, and `Promise.allSettled` swallows it. Fix: (1) add a `deliverables-cn` overlay mount for the blackbox path so the target is writable and lands at `workspaces/<ws>/deliverables-cn/`; (2) route a translation summary line through `AuditSession` → `WorkflowLogger` so outcomes appear in `workflow.log`. Whitebox (local runner) is untouched.

**Tech Stack:** TypeScript (pnpm workspaces + Turborepo), Biome lint/format, Docker (`docker run` worker orchestration), Temporal activities.

**Spec:** `docs/superpowers/specs/2026-06-16-blackbox-translation-output-path-design.md`

**Verification approach:** This repo has **no unit-test infrastructure** (no vitest/jest, no `*.test.ts`), and the changes are Docker-mount / container-filesystem / logging-wiring — not unit-testable. Per-task verification is `pnpm run check` (type-check) + `pnpm biome` (lint/format). Final correctness is verified by an end-to-end re-run (Task 5). This matches the repo's established practices (CLAUDE.md lists `build`/`check`/`biome`, no `test`).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `apps/cli/src/commands/start.ts` | Host-side workspace/repo dir setup for blackbox runs | Pre-create `deliverables-cn` overlay source + mount point |
| `apps/cli/src/docker.ts` | `docker run` arg builder (`spawnWorker`, blackbox-only) | Add `deliverables-cn` overlay mount |
| `apps/worker/src/interfaces/report-output-provider.ts` | Provider interface + no-op default | Add optional `successCount`/`failCount` to return type |
| `apps/worker/src/providers/report-translation-provider.ts` | Translation implementation | Return the counts it already computes |
| `apps/worker/src/audit/audit-session.ts` | Per-workflow audit/logging facade over `WorkflowLogger` | Add `logWorkflowEvent()` passthrough |
| `apps/worker/src/temporal/activities.ts` | Temporal activity wrappers | Log translation summary via `AuditSession` in `generateReportOutputActivity` |

`apps/cli/src/commands/local-start.ts` is intentionally **not** modified — whitebox keeps writing to `repo/.shannon/deliverables-cn/`.

---

### Task 1: Pre-create `deliverables-cn` overlay directories

Blackbox runs mount writable workspace dirs over the read-only repo's `.shannon` subdirs. Docker cannot reliably auto-create these targets under `:ro` mounts, so `start.ts` pre-creates them on both sides. Add `deliverables-cn` to both pre-creation loops.

**Files:**
- Modify: `apps/cli/src/commands/start.ts:73` (workspace-side overlay sources)
- Modify: `apps/cli/src/commands/start.ts:96` (repo-side mount points)

- [ ] **Step 1: Add `deliverables-cn` to the workspace-side overlay sources loop**

In `apps/cli/src/commands/start.ts`, the loop at line 73 currently reads:

```typescript
  for (const dir of ['deliverables', 'scratchpad', '.playwright-cli', '.playwright']) {
    const dirPath = path.join(workspacePath, dir);
    fs.mkdirSync(dirPath, { recursive: true });
    fs.chmodSync(dirPath, 0o777);
  }
```

Change the array to include `deliverables-cn`:

```typescript
  for (const dir of ['deliverables', 'deliverables-cn', 'scratchpad', '.playwright-cli', '.playwright']) {
    const dirPath = path.join(workspacePath, dir);
    fs.mkdirSync(dirPath, { recursive: true });
    fs.chmodSync(dirPath, 0o777);
  }
```

- [ ] **Step 2: Add `deliverables-cn` to the repo-side mount-point loop**

The loop at line 96 currently reads:

```typescript
  const shannonDir = path.join(repo.hostPath, '.shannon');
  for (const dir of ['deliverables', 'scratchpad', '.playwright-cli']) {
    fs.mkdirSync(path.join(shannonDir, dir), { recursive: true });
  }
```

Change the array to include `deliverables-cn`:

```typescript
  const shannonDir = path.join(repo.hostPath, '.shannon');
  for (const dir of ['deliverables', 'deliverables-cn', 'scratchpad', '.playwright-cli']) {
    fs.mkdirSync(path.join(shannonDir, dir), { recursive: true });
  }
```

- [ ] **Step 3: Type-check and lint**

Run: `pnpm run check && pnpm biome`
Expected: both pass with no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/cli/src/commands/start.ts
git commit -m "feat(cli): pre-create deliverables-cn overlay dirs for blackbox runs"
```

---

### Task 2: Add the `deliverables-cn` overlay mount (blackbox path)

`spawnWorker` in `docker.ts` builds the `docker run` args and is used **only** by the blackbox/Temporal `start` command. Add one overlay mount next to the existing `deliverables` mount. This makes the translation provider's hardcoded `repo/.shannon/deliverables-cn` target writable and backs it by `workspaces/<ws>/deliverables-cn/`.

**Files:**
- Modify: `apps/cli/src/docker.ts:277` (overlay mount list in `spawnWorker`)

- [ ] **Step 1: Add the `deliverables-cn` overlay mount line**

In `apps/cli/src/docker.ts`, the overlay block currently reads (lines 275-280):

```typescript
  // Writable overlays: shadow .shannon/ and .playwright/ inside the :ro repo with workspace-backed dirs
  const workspacePath = path.join(opts.workspacesDir, opts.workspace);
  args.push('-v', `${path.join(workspacePath, 'deliverables')}:${opts.repo.containerPath}/.shannon/deliverables`);
  args.push('-v', `${path.join(workspacePath, 'scratchpad')}:${opts.repo.containerPath}/.shannon/scratchpad`);
  args.push('-v', `${path.join(workspacePath, '.playwright-cli')}:${opts.repo.containerPath}/.shannon/.playwright-cli`);
  args.push('-v', `${path.join(workspacePath, '.playwright')}:${opts.repo.containerPath}/.playwright`);
```

Insert the `deliverables-cn` mount immediately after the `deliverables` mount:

```typescript
  // Writable overlays: shadow .shannon/ and .playwright/ inside the :ro repo with workspace-backed dirs
  const workspacePath = path.join(opts.workspacesDir, opts.workspace);
  args.push('-v', `${path.join(workspacePath, 'deliverables')}:${opts.repo.containerPath}/.shannon/deliverables`);
  args.push('-v', `${path.join(workspacePath, 'deliverables-cn')}:${opts.repo.containerPath}/.shannon/deliverables-cn`);
  args.push('-v', `${path.join(workspacePath, 'scratchpad')}:${opts.repo.containerPath}/.shannon/scratchpad`);
  args.push('-v', `${path.join(workspacePath, '.playwright-cli')}:${opts.repo.containerPath}/.shannon/.playwright-cli`);
  args.push('-v', `${path.join(workspacePath, '.playwright')}:${opts.repo.containerPath}/.playwright`);
```

- [ ] **Step 2: Type-check and lint**

Run: `pnpm run check && pnpm biome`
Expected: both pass with no errors.

- [ ] **Step 3: Commit**

```bash
git add apps/cli/src/docker.ts
git commit -m "feat(cli): mount deliverables-cn overlay so blackbox translation is writable"
```

---

### Task 3: Return translation counts from the provider

Extend the provider interface so the activity can report a meaningful summary. The provider already computes `successCount`/`failCount` internally — just surface them.

**Files:**
- Modify: `apps/worker/src/interfaces/report-output-provider.ts:13-15` (interface return type)
- Modify: `apps/worker/src/providers/report-translation-provider.ts:180-188` (return statements)

- [ ] **Step 1: Extend the `ReportOutputProvider` return type**

In `apps/worker/src/interfaces/report-output-provider.ts`, the interface currently reads:

```typescript
export interface ReportOutputProvider {
  generate(input: ActivityInput, logger: ActivityLogger): Promise<{ outputPath?: string }>;
}
```

Add the optional count fields:

```typescript
export interface ReportOutputProvider {
  generate(
    input: ActivityInput,
    logger: ActivityLogger,
  ): Promise<{ outputPath?: string; successCount?: number; failCount?: number }>;
}
```

The `NoOpReportOutputProvider.generate()` already returns `{}`; the new fields are optional, so it needs no change.

- [ ] **Step 2: Return counts from the success and all-failed paths**

In `apps/worker/src/providers/report-translation-provider.ts`, the tail of `generate()` (lines 179-188) currently reads:

```typescript
    // 7. Return result
    if (successCount === 0) {
      logger.warn('All translations failed');
      return {};
    }

    logger.info(
      `Translation complete: ${successCount} succeeded, ${failCount} failed, ${totalChars.toLocaleString()} chars total, ${(totalDuration / 1000).toFixed(1)}s`,
    );
    return { outputPath: cnDir };
```

`successCount` and `failCount` are both in scope here (computed in the loop at lines 156-177). Surface them on both returns:

```typescript
    // 7. Return result
    if (successCount === 0) {
      logger.warn('All translations failed');
      return { successCount, failCount };
    }

    logger.info(
      `Translation complete: ${successCount} succeeded, ${failCount} failed, ${totalChars.toLocaleString()} chars total, ${(totalDuration / 1000).toFixed(1)}s`,
    );
    return { outputPath: cnDir, successCount, failCount };
```

(The early returns at lines 72/82/87 — no deliverables dir, unreadable dir, no markdown files — stay as `return {};`. Nothing was attempted, so counts are undefined, which is correct.)

- [ ] **Step 3: Type-check and lint**

Run: `pnpm run check && pnpm biome`
Expected: both pass with no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/interfaces/report-output-provider.ts apps/worker/src/providers/report-translation-provider.ts
git commit -m "feat(translation): surface success/fail counts from ReportTranslationProvider"
```

---

### Task 4: Make translation outcome visible in `workflow.log`

The provider logs its summary only via the Temporal activity logger (`Context.current().log`), which does not write to `workflow.log`. Add an `AuditSession` passthrough to `WorkflowLogger.logEvent`, then have the activity emit a `[TRANSLATION]` line so the next regression is visible from `./shannon logs`.

**Files:**
- Modify: `apps/worker/src/audit/audit-session.ts:227-229` (add `logWorkflowEvent` near `logPhaseComplete`)
- Modify: `apps/worker/src/temporal/activities.ts:1089-1099` (`generateReportOutputActivity`)

- [ ] **Step 1: Add `logWorkflowEvent` to `AuditSession`**

In `apps/worker/src/audit/audit-session.ts`, the existing `logPhaseComplete` (around line 227) reads:

```typescript
  async logPhaseComplete(phase: string): Promise<void> {
    await this.workflowLogger.logPhase(phase, 'complete');
  }
```

Add a new method immediately after it. (`workflowLogger` is a private field of type `WorkflowLogger`, set in the constructor; `WorkflowLogger.logEvent(eventType, message)` writes `[<TIMESTAMP>] [<EVENT_TYPE>] <message>` to `workflow.log` and lazily initializes.)

```typescript
  async logPhaseComplete(phase: string): Promise<void> {
    await this.workflowLogger.logPhase(phase, 'complete');
  }

  /**
   * Write a free-form event line to the unified workflow log (workflow.log).
   * Use for non-phase/non-agent events such as translation summaries, so the
   * outcome is visible from `./shannon logs` rather than only in worker stdout.
   */
  async logWorkflowEvent(eventType: string, message: string): Promise<void> {
    await this.workflowLogger.logEvent(eventType, message);
  }
```

- [ ] **Step 2: Log the translation summary from `generateReportOutputActivity`**

In `apps/worker/src/temporal/activities.ts`, `generateReportOutputActivity` (lines 1089-1099) currently reads:

```typescript
export async function generateReportOutputActivity(input: ActivityInput): Promise<void> {
  const container = getContainer(input.workflowId);
  if (!container?.reportOutputProvider) return;

  const logger = createActivityLogger();

  const result = await container.reportOutputProvider.generate(input, logger);
  if (result.outputPath) {
    logger.info(`Report output written to ${result.outputPath}`);
  }
}
```

Replace the body to also emit a workflow.log line. `AuditSession`, `buildSessionMetadata`, `getContainer`, and `createActivityLogger` are already imported in this file (used by `logWorkflowComplete`/`logPhaseTransition`).

```typescript
export async function generateReportOutputActivity(input: ActivityInput): Promise<void> {
  const container = getContainer(input.workflowId);
  if (!container?.reportOutputProvider) return;

  const logger = createActivityLogger();

  const result = await container.reportOutputProvider.generate(input, logger);
  if (result.outputPath) {
    logger.info(`Report output written to ${result.outputPath}`);
  }

  // Surface the translation outcome in workflow.log. The provider's own summary
  // logs go to the Temporal activity logger (worker stdout), which is invisible
  // from `./shannon logs` — that is how an all-failed translation went unnoticed.
  const succeeded = result.successCount ?? 0;
  const failed = result.failCount ?? 0;
  if (succeeded === 0 && failed === 0) return; // nothing attempted (no deliverables)

  const auditSession = new AuditSession(buildSessionMetadata(input));
  await auditSession.initialize(input.workflowId);

  if (failed > 0 || !result.outputPath) {
    await auditSession.logWorkflowEvent(
      'TRANSLATION',
      `WARNING — ${failed} of ${succeeded + failed} deliverable(s) failed to translate (output: ${result.outputPath ?? 'none'}). See worker stdout for per-file errors.`,
    );
  } else {
    await auditSession.logWorkflowEvent('TRANSLATION', `${succeeded} deliverable(s) translated → ${result.outputPath}`);
  }
}
```

- [ ] **Step 3: Type-check and lint**

Run: `pnpm run check && pnpm biome`
Expected: both pass with no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/audit/audit-session.ts apps/worker/src/temporal/activities.ts
git commit -m "feat(translation): log translation outcome to workflow.log"
```

---

### Task 5: Rebuild and verify end-to-end

Build the CLI (`apps/cli/dist`, for the `docker.ts`/`start.ts` changes) and the worker Docker image (for the provider/activity/audit changes), then re-run both scan modes.

- [ ] **Step 1: Build TypeScript**

Run: `pnpm run build`
Expected: Turborepo builds `apps/cli` and `apps/worker` with no errors.

- [ ] **Step 2: Rebuild the worker Docker image**

Run: `./shannon build`
Expected: image builds successfully (local mode builds `shannon-worker`).

- [ ] **Step 3: Verify blackbox translation is produced**

Re-run the blackbox scan on the existing workspace (resume), e.g.:

```bash
./shannon start -r /root/code/welfare/ -u https://test-perks.futuoa.com/ \
  -w welfare_whitebox-1781148356493 -c apps/worker/configs/test-perks-futuoa.yaml --blackbox-only
```

Expected:
- `workspaces/welfare_whitebox-1781148356493/deliverables-cn/comprehensive_security_assessment_report-cn.md` is **auto-generated** (not the hand-made one — remove the existing manual `-cn.md` first to be sure).
- `workflow.log` (`./shannon logs welfare_whitebox-1781148356493`) contains a `[TRANSLATION]` line: either `N deliverable(s) translated → …` (success) or a `WARNING — … failed to translate …` line.
- The translated report content reflects the **blackbox** assessment (target `https://test-perks.futuoa.com/`, "exploitation enabled"), not the stale whitebox static version.

- [ ] **Step 4: Verify whitebox is unaffected**

Re-run a whitebox scan, e.g.:

```bash
./shannon start -r /root/code/welfare/ -u https://test-perks.futuoa.com/ -w <fresh-whitebox-ws> --whitebox-only
```

Expected: Chinese deliverables still appear at `<repo>/.shannon/deliverables-cn/`. Also confirm *why* that path is writable for whitebox (the local runner's repo mount/mount scheme) and note it here — if it turns out whitebox has the same latent `:ro` issue, open a follow-up rather than expanding this change.

- [ ] **Step 5: Final commit (docs/spec already committed separately; no further code changes expected)**

If Steps 3-4 required any fixups, commit them. Otherwise the implementation is complete — Tasks 1-4 already committed the code.

---

## Self-Review Notes

- **Spec coverage:** Spec §1 (mount) → Task 2; §2 (pre-create dirs) → Task 1; §3 (visible failures: interface counts + AuditSession/WorkflowLogger routing) → Tasks 3-4; verification → Task 5. All spec sections covered.
- **Type consistency:** `ReportOutputProvider.generate` return type (Task 3) adds `successCount?`/`failCount?`; provider returns them (Task 3); activity reads `result.successCount`/`result.failCount` (Task 4). `AuditSession.logWorkflowEvent(eventType: string, message: string)` (Task 4) matches `WorkflowLogger.logEvent(eventType: string, message: string)`. Names consistent across tasks.
- **No placeholders:** every code step shows the exact before/after.
