# Local Whitebox Runner: Report Agent + Translation

Add the missing report agent and translation steps to `apps/worker/src/local/runner.ts`.

## Problem

The local whitebox runner (`apps/worker/src/local/runner.ts`) stops after findings rendering and report assembly. It skips:

1. **Report agent** — no executive summary is generated
2. **Translation** — no Chinese translations of deliverables are produced

The Temporal workflow path (`whiteboxPipelineWorkflow`) includes both steps, but the local runner bypasses the entire Temporal/DI container system.

## Scope

Add two steps to the local runner, following its existing pattern of direct service calls:

1. Run the `report` agent (with retry) after Phase 5 report assembly
2. Run `ReportTranslationProvider.generate()` to translate all `.md` deliverables

No architectural changes — no DI container extraction, no shared modules. The runner already calls services directly (`renderFindingsFromQueues`, `assembleFinalReport`, `injectModelIntoReport`) and this follows the same pattern.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/worker/src/local/runner.ts` | Modify | Add report agent execution and translation step |

Single file change. All dependencies (`ReportTranslationProvider`, `runAgentWithRetry`, `ConsoleActivityLogger`) are already available.

## Detailed Changes

### 1. Import `ReportTranslationProvider`

Add import at the top of `runner.ts`:

```typescript
import { ReportTranslationProvider } from '../providers/report-translation-provider.js';
```

### 2. Add Phase 6 — Report Agent

After the existing Phase 5 block (line ~414), add a new phase that runs the report agent:

```typescript
// Phase 6: Report agent (executive summary + cleanup)
if (!aborted) {
  logger.info('=== Phase 6: Report ===');
  const result = await runAgentWithRetry(
    'report',
    args,
    auditSession,
    logger,
    configLoader,
    deliverablesPath,
    distributedConfig,
  );
  results.push(result);
  if (!result.success) {
    logger.warn(`Report agent failed after ${result.attempts} attempts: ${result.error}`);
    // Non-fatal — continue to translation with assembled report
  }

  // Re-inject model info after report agent overwrites the file
  try {
    await injectModelIntoReport(args.repoPath, undefined, path.join(WORKSPACES_DIR, sessionId), logger);
  } catch (error) {
    logger.warn(`Model re-injection had issues: ${error instanceof Error ? error.message : String(error)}`);
  }
}
```

Key points:

- Uses existing `runAgentWithRetry` — same retry logic (3 attempts, exponential backoff) as all other agents
- Report failure is **non-fatal** — the assembled report from Phase 5 is still usable
- Model injection is re-run because the report agent overwrites `comprehensive_security_assessment_report.md`
- Shares the same `auditSession` (report is sequential, not parallel)

### 3. Add Phase 7 — Translation

After the report agent phase, add translation:

```typescript
// Phase 7: Translation
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

Key points:

- Directly instantiates `ReportTranslationProvider` — no DI container needed
- Passes a minimal `ActivityInput`-compatible object with the fields the provider actually uses (`repoPath`, `apiKey`, `providerConfig`)
- Wrapped in try-catch — translation failure is non-fatal
- Uses `ConsoleActivityLogger` (same as the rest of the runner)

### 4. Update Final Summary

The `results` array now includes the `report` agent, so the final summary will show 8 agents instead of 7. No code change needed — the summary loop already iterates over `results`.

## Execution Order After Fix

```
Phase 1: Pre-recon           (sequential, existing)
Phase 2: Static Recon        (sequential, existing)
Phase 3: Vulnerability Analysis (parallel, existing)
Phase 4: Findings Rendering  (sequential, existing)
Phase 5: Report Assembly     (sequential, existing)
Phase 6: Report Agent        (sequential, NEW)
  └── Model re-injection
Phase 7: Translation         (sequential, NEW)
  └── deliverables-cn/*.md
=== Pipeline Complete ===
```

## Error Handling

| Failure | Behavior |
|---------|----------|
| Report agent fails all retries | Log warning, continue with Phase 5 assembled report |
| Model re-injection fails | Log warning, continue |
| Translation of single file fails | Provider logs warning, continues with remaining files |
| Translation of all files fails | Provider logs warning, returns empty |

All new steps are non-fatal — the pipeline always reaches the final summary.

## Design Decisions

1. **Why direct `ReportTranslationProvider()` instead of going through DI container?** The local runner doesn't use the DI container system. It calls services directly. Mixing in container creation for one step would be inconsistent.

2. **Why re-run `injectModelIntoReport` after the report agent?** The report agent overwrites `comprehensive_security_assessment_report.md`. The model injection from Phase 5 is lost. Re-running ensures the model line appears in the final file.

3. **Why non-fatal for report agent failure?** The assembled report from Phase 5 already contains all per-class findings. The report agent adds an executive summary — valuable but not essential. Pipeline should still reach translation.
