# Report Translation Improvements

## Background

The translation phase (`ReportTranslationProvider`) runs after the report agent finalizes the security assessment. It translates all `.md` deliverables to Chinese via `runClaudePrompt` (Haiku). Two issues need addressing:

1. The full translated content is printed to workflow logs via `outputLines(formatAssistantOutput(...))` in `message-handlers.ts` — noisy and unnecessary
2. Translation runs sequentially (`for` loop), slow when many reports need translating

## Design

### 1. Silent Mode for `runClaudePrompt`

**Upstream change: minimal (3-4 lines).**

Add `silent?: boolean` to the options accepted by `runClaudePrompt`. Default `false`.

In `message-handlers.ts` (`dispatchMessage`, ~line 297), wrap the existing `outputLines` call:

```typescript
// Before
outputLines(formatAssistantOutput(assistantResult.cleanedContent, execContext, turnCount, description));

// After
if (!ctx.silent) {
  outputLines(formatAssistantOutput(assistantResult.cleanedContent, execContext, turnCount, description));
}
```

When `silent: true`:
- `outputLines` call is skipped — no full content in workflow log
- `auditLogger.logLlmResponse()` still fires — full content preserved in audit logs for debugging
- `progress` start/finish still fires — status indicator unaffected

`ReportTranslationProvider` passes `silent: true` when calling `runClaudePrompt`.

### 2. Parallel Translation with Concurrency Control

**Zero upstream changes.** All changes in `ReportTranslationProvider`.

Replace the sequential `for` loop with a simple inline concurrency limiter using `Promise.allSettled` + slot counting. The limiter is a private helper in the same file (~10 lines).

**Concurrency source**: `input.pipelineConfig?.max_concurrent_pipelines ?? ALL_VULN_CLASSES.length` — same as vuln agents (default 5).

**Execution flow**:

```
1. Scan .md files, build thunk array (one per file)
2. Run thunks with concurrency limiter
3. Collect results (success/fail, chars, duration per file)
4. Print summary log
```

**Error handling**: Per-file failures do not affect other files — same as current behavior. Failed files appear with `✗` in the summary.

### 3. Enhanced Summary Logging

Replace the current sparse logging with structured per-file and aggregate output:

```
Translating 3 files (concurrency: 5)...
  ✓ report.md → report-cn.md (12,450 chars, 8.2s)
  ✓ vuln-findings.md → vuln-findings-cn.md (5,200 chars, 3.1s)
  ✗ draft.md — empty result
Translation complete: 2 succeeded, 1 failed, 17,650 chars total, 11.3s
```

Each file's char count and duration are computed locally in the translation thunk. The summary aggregates after all thunks complete.

## Files Changed

| File | Change | Upstream? |
|------|--------|-----------|
| `apps/worker/src/providers/report-translation-provider.ts` | Parallel execution, summary logging | No |
| `apps/worker/src/ai/claude-executor.ts` | Pass through `silent` option | Yes (1 line) |
| `apps/worker/src/ai/message-handlers.ts` | Conditional `outputLines` skip | Yes (2 lines) |

## Out of Scope

- Translation quality improvements
- Language selection (Chinese only for now)
- Retry logic for failed translations (current single-attempt behavior retained)
