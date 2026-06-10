# Local Runner Report + Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing report agent and translation steps to the local whitebox runner so it produces the same deliverables as the Temporal workflow path.

**Architecture:** Single-file change to `runner.ts`. Add Phase 6 (report agent with retry) and Phase 7 (translation via `ReportTranslationProvider`) after the existing Phase 5, following the runner's existing pattern of direct service calls.

**Tech Stack:** TypeScript, existing service classes (`AgentExecutionService`, `ReportTranslationProvider`)

---

### Task 1: Add import and Phase 6 — Report Agent

**Files:**
- Modify: `apps/worker/src/local/runner.ts:1` (add import)
- Modify: `apps/worker/src/local/runner.ts:393-414` (add Phase 6 after Phase 5)

- [ ] **Step 1: Add `ReportTranslationProvider` import**

In `apps/worker/src/local/runner.ts`, add the import at line 7 (after the `AgentExecutionService` import, before `ConfigLoaderService`):

```typescript
import { ReportTranslationProvider } from '../providers/report-translation-provider.js';
```

- [ ] **Step 2: Add Phase 6 — Report Agent execution**

In `apps/worker/src/local/runner.ts`, insert a new phase block after the Phase 5 closing brace (after line 414, before `} finally {`). This goes inside the existing `try` block, as a sibling to the Phase 4/5 `if (!aborted)` block:

```typescript
    // Phase 6: Report agent (executive summary + final report)
    if (!aborted) {
      logger.info('=== Phase 6: Report ===');
      const reportResult = await runAgentWithRetry(
        'report',
        args,
        auditSession,
        logger,
        configLoader,
        deliverablesPath,
        distributedConfig,
      );
      results.push(reportResult);
      if (!reportResult.success) {
        logger.warn(`Report agent failed after ${reportResult.attempts} attempts: ${reportResult.error}`);
        // Non-fatal — assembled report from Phase 5 is still usable
      }

      // Re-inject model info (report agent overwrites the assembled file)
      try {
        await injectModelIntoReport(args.repoPath, undefined, path.join(WORKSPACES_DIR, sessionId), logger);
      } catch (error) {
        logger.warn(`Model re-injection had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
```

- [ ] **Step 3: Type-check**

Run: `pnpm run check`
Expected: PASS (no type errors)

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/local/runner.ts
git commit -m "feat(local-runner): add report agent phase (Phase 6)"
```

---

### Task 2: Add Phase 7 — Translation

**Files:**
- Modify: `apps/worker/src/local/runner.ts` (add Phase 7 after Phase 6)

- [ ] **Step 1: Add Phase 7 — Translation block**

In `apps/worker/src/local/runner.ts`, insert a new phase block after Phase 6 (still inside the `try` block, before `} finally {`):

```typescript
    // Phase 7: Translation (Chinese deliverables)
    if (!aborted) {
      logger.info('=== Phase 7: Translation ===');
      try {
        const provider = new ReportTranslationProvider();
        const translationResult = await provider.generate(
          {
            repoPath: args.repoPath,
            workflowId: sessionId,
            sessionId,
            ...(args.apiKey && { apiKey: args.apiKey }),
            ...(args.providerConfig && { providerConfig: args.providerConfig }),
          },
          logger,
        );
        if (translationResult.outputPath) {
          logger.info(`Translations written to ${translationResult.outputPath}`);
        }
      } catch (error) {
        logger.warn(`Translation had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
```

Note on the input object:
- `repoPath` is the only field the translation provider reads for file discovery
- `apiKey` and `providerConfig` are passed through to `runClaudePrompt` for model routing
- `workflowId` and `sessionId` are set to `sessionId` to satisfy the `ActivityInput` type (the translation provider doesn't use them)
- Uses spread for optional props to satisfy `exactOptionalPropertyTypes`

- [ ] **Step 2: Type-check**

Run: `pnpm run check`
Expected: PASS

- [ ] **Step 3: Lint**

Run: `pnpm biome`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/local/runner.ts
git commit -m "feat(local-runner): add translation phase (Phase 7)"
```

---

### Task 3: Verify end-to-end

**Files:**
- No changes — verification only

- [ ] **Step 1: Run the local whitebox runner against a test repo**

Run: `node apps/worker/dist/local/runner.js -r <test-repo-path>`

Expected output should include:
```
=== Phase 6: Report ===
[report] Attempt 1/3 (global: 1)
...report agent output...
=== Phase 7: Translation ===
Translating comprehensive_security_assessment_report.md...
Translating injection_findings.md...
...per-file translation messages...
Translation complete: N succeeded, 0 failed
Translations written to <repoPath>/.shannon/deliverables-cn
```

- [ ] **Step 2: Verify translated files exist**

Check that `<repoPath>/.shannon/deliverables-cn/` contains `-cn.md` versions of all `.md` files from `<repoPath>/.shannon/deliverables/`.

- [ ] **Step 3: Final commit (if any lint fixes needed)**

```bash
git add -u
git commit -m "chore: lint fixes for local runner report + translation"
```
