# Report Translation Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Silence full-content logging during translation and add parallel execution with structured summary output.

**Architecture:** Add a `silent` flag that threads through `runClaudePrompt` → `processMessageStream` → `dispatchMessage` to skip `outputLines` for assistant messages. Replace the sequential `for` loop in `ReportTranslationProvider` with an inline concurrency limiter using the same pattern as `runWithConcurrencyLimit` in workflows.ts.

**Tech Stack:** TypeScript, no new dependencies.

---

### Task 1: Add `silent` flag to message dispatch chain

**Upstream change.** Three files, ~7 lines total.

**Files:**
- Modify: `apps/worker/src/ai/claude-executor.ts:152,260-262`
- Modify: `apps/worker/src/ai/message-handlers.ts:271-277,295-298`

- [x] **Step 1: Add `silent` to `MessageDispatchDeps` interface**

In `apps/worker/src/ai/message-handlers.ts`, add `silent?: boolean` to the `MessageDispatchDeps` interface:

```typescript
export interface MessageDispatchDeps {
  execContext: ExecutionContext;
  description: string;
  progress: ProgressManager;
  auditLogger: AuditLogger;
  logger: ActivityLogger;
  silent?: boolean;
}
```

- [x] **Step 2: Guard `outputLines` with silent check in `dispatchMessage`**

In `apps/worker/src/ai/message-handlers.ts`, inside `dispatchMessage` (~line 295-298), wrap the `outputLines` call:

```typescript
// Before:
if (assistantResult.cleanedContent.trim()) {
  progress.stop();
  outputLines(formatAssistantOutput(assistantResult.cleanedContent, execContext, turnCount, description));
  progress.start();
}

// After:
if (assistantResult.cleanedContent.trim()) {
  progress.stop();
  if (!deps.silent) {
    outputLines(formatAssistantOutput(assistantResult.cleanedContent, execContext, turnCount, description));
  }
  progress.start();
}
```

- [x] **Step 3: Add `silent` to `MessageLoopDeps` interface**

In `apps/worker/src/ai/claude-executor.ts`, add `silent?: boolean` to the `MessageLoopDeps` interface (~line 337-343):

```typescript
interface MessageLoopDeps {
  execContext: ReturnType<typeof detectExecutionContext>;
  description: string;
  progress: ReturnType<typeof createProgressManager>;
  auditLogger: ReturnType<typeof createAuditLogger>;
  logger: ActivityLogger;
  silent?: boolean;
}
```

- [x] **Step 4: Pass `silent` through `processMessageStream` to `dispatchMessage`**

In `apps/worker/src/ai/claude-executor.ts`, inside `processMessageStream` (~line 375-381), add `silent` to the `dispatchMessage` deps:

```typescript
// Before:
const dispatchResult = await dispatchMessage(message as { type: string; subtype?: string }, turnCount, {
  execContext,
  description,
  progress,
  auditLogger,
  logger,
});

// After:
const dispatchResult = await dispatchMessage(message as { type: string; subtype?: string }, turnCount, {
  execContext,
  description,
  progress,
  auditLogger,
  logger,
  silent: deps.silent,
});
```

- [x] **Step 5: Add `silent` parameter to `runClaudePrompt` and thread through**

In `apps/worker/src/ai/claude-executor.ts`, add `silent?: boolean` as the last parameter of `runClaudePrompt` (~line 152):

```typescript
// Before:
export async function runClaudePrompt(
  prompt: string,
  sourceDir: string,
  context: string = '',
  description: string = 'Claude analysis',
  _agentName: string | null = null,
  auditSession: AuditSession | null = null,
  logger: ActivityLogger,
  modelTier: ModelTier = 'medium',
  outputFormat?: JsonSchemaOutputFormat,
  apiKey?: string,
  deliverablesSubdir?: string,
  providerConfig?: import('../types/config.js').ProviderConfig,
): Promise<ClaudePromptResult> {

// After:
export async function runClaudePrompt(
  prompt: string,
  sourceDir: string,
  context: string = '',
  description: string = 'Claude analysis',
  _agentName: string | null = null,
  auditSession: AuditSession | null = null,
  logger: ActivityLogger,
  modelTier: ModelTier = 'medium',
  outputFormat?: JsonSchemaOutputFormat,
  apiKey?: string,
  deliverablesSubdir?: string,
  providerConfig?: import('../types/config.js').ProviderConfig,
  silent?: boolean,
): Promise<ClaudePromptResult> {
```

Then include `silent` in the `MessageLoopDeps` construction (~line 262):

```typescript
// Before:
const messageLoopResult = await processMessageStream(
  fullPrompt,
  options,
  { execContext, description, progress, auditLogger, logger },
  timer,
);

// After:
const messageLoopResult = await processMessageStream(
  fullPrompt,
  options,
  { execContext, description, progress, auditLogger, logger, silent },
  timer,
);
```

- [x] **Step 6: Build and type-check**

Run: `pnpm run check`
Expected: PASS (no type errors — `silent` is optional everywhere, existing callers unaffected)

- [x] **Step 7: Commit**

```bash
git add apps/worker/src/ai/claude-executor.ts apps/worker/src/ai/message-handlers.ts
git commit -m "feat(ai): add silent mode to runClaudePrompt message dispatch"
```

---

### Task 2: Rewrite `ReportTranslationProvider` with parallel execution and summary logging

**Own code.** Single file rewrite.

**Files:**
- Modify: `apps/worker/src/providers/report-translation-provider.ts`

- [x] **Step 1: Add imports and concurrency helper**

Replace the current imports and add a private concurrency helper at the top of the file. Import `ALL_VULN_CLASSES` for the default concurrency limit and `Timer` for per-file timing:

```typescript
import { readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runClaudePrompt } from '../ai/claude-executor.js';
import type { ReportOutputProvider } from '../interfaces/report-output-provider.js';
import { deliverablesDir } from '../paths.js';
import type { ActivityInput } from '../temporal/activities.js';
import type { ActivityLogger } from '../types/activity-logger.js';
import { ALL_VULN_CLASSES } from '../types/config.js';
import { ensureDirectory, fileExists } from '../utils/file-io.js';
import { Timer } from '../utils/metrics.js';
import { buildTranslationPrompt } from './translation-prompt.js';

/** Per-file translation result for summary logging */
interface TranslationFileResult {
  filename: string;
  cnFilename: string;
  success: boolean;
  chars: number;
  duration: number;
  error?: string;
}

/** Runs thunks with a concurrency limit. Resolves when all complete. */
async function runWithConcurrencyLimit<T>(thunks: Array<() => Promise<T>>, limit: number): Promise<PromiseSettledResult<T>[]> {
  const results: PromiseSettledResult<T>[] = [];
  const inFlight = new Set<Promise<void>>();

  for (const thunk of thunks) {
    const slot = thunk()
      .then(
        (value) => {
          results.push({ status: 'fulfilled', value });
        },
        (reason: unknown) => {
          results.push({ status: 'rejected', reason });
        },
      )
      .finally(() => {
        inFlight.delete(slot);
      });

    inFlight.add(slot);

    if (inFlight.size >= limit) {
      await Promise.race(inFlight);
    }
  }

  await Promise.allSettled(inFlight);
  return results;
}
```

- [x] **Step 2: Rewrite `generate` method**

Replace the entire `generate` method body with parallel execution, `silent: true`, and summary logging:

```typescript
export class ReportTranslationProvider implements ReportOutputProvider {
  async generate(input: ActivityInput, logger: ActivityLogger): Promise<{ outputPath?: string }> {
    // 1. Resolve source deliverables directory
    const srcDir = deliverablesDir(input.repoPath, input.deliverablesSubdir);
    const srcExists = await fileExists(srcDir);
    if (!srcExists) {
      logger.info('No deliverables directory found, skipping translation');
      return {};
    }

    // 2. Scan for markdown files
    let entries: string[];
    try {
      entries = await readdir(srcDir);
    } catch {
      logger.warn(`Failed to read deliverables directory: ${srcDir}`);
      return {};
    }

    const mdFiles = entries.filter((f) => f.endsWith('.md'));
    if (mdFiles.length === 0) {
      logger.info('No markdown files found in deliverables, skipping translation');
      return {};
    }

    // 3. Create output directory
    const cnDir = path.join(input.repoPath, '.shannon', 'deliverables-cn');
    await ensureDirectory(cnDir);

    // 4. Build translation thunks
    const maxConcurrent = ALL_VULN_CLASSES.length;
    logger.info(`Translating ${mdFiles.length} files (concurrency: ${maxConcurrent})...`);

    const thunks = mdFiles.map((filename) => {
      const srcPath = path.join(srcDir, filename);
      const cnFilename = filename.replace(/\.md$/, '-cn.md');
      const cnPath = path.join(cnDir, cnFilename);

      return async (): Promise<TranslationFileResult> => {
        const timer = new Timer(`translate-${filename}`);
        const content = await readFile(srcPath, 'utf-8');

        if (!content.trim()) {
          return { filename, cnFilename, success: true, chars: 0, duration: 0, error: 'empty' };
        }

        const prompt = buildTranslationPrompt(content, filename);
        const result = await runClaudePrompt(
          prompt,
          input.repoPath,
          '',
          `translate ${filename}`,
          null,
          null,
          logger,
          'small',
          undefined,
          input.apiKey,
          input.deliverablesSubdir,
          input.providerConfig,
          true, // silent — suppress full content in logs
        );

        const duration = timer.stop();

        if (result.success && result.result) {
          await writeFile(cnPath, result.result, 'utf-8');
          return { filename, cnFilename, success: true, chars: result.result.length, duration };
        }

        return {
          filename,
          cnFilename,
          success: false,
          chars: 0,
          duration,
          error: result.error ?? 'empty result',
        };
      };
    });

    // 5. Run translations in parallel
    const settled = await runWithConcurrencyLimit(thunks, maxConcurrent);

    // 6. Log summary
    const fileResults: TranslationFileResult[] = settled.map((r) =>
      r.status === 'fulfilled' ? r.value : { filename: 'unknown', cnFilename: '', success: false, chars: 0, duration: 0, error: String(r.reason) },
    );

    let successCount = 0;
    let failCount = 0;
    let totalChars = 0;
    let totalDuration = 0;

    for (const r of fileResults) {
      totalDuration += r.duration;
      if (r.error === 'empty') {
        logger.info(`  ⊘ ${r.filename} — empty, skipped`);
        continue;
      }
      if (r.success) {
        successCount++;
        totalChars += r.chars;
        logger.info(`  ✓ ${r.filename} → ${r.cnFilename} (${r.chars.toLocaleString()} chars, ${(r.duration / 1000).toFixed(1)}s)`);
      } else {
        failCount++;
        logger.warn(`  ✗ ${r.filename} — ${r.error}`);
      }
    }

    // 7. Return result
    if (successCount === 0) {
      logger.warn('All translations failed');
      return {};
    }

    logger.info(
      `Translation complete: ${successCount} succeeded, ${failCount} failed, ${totalChars.toLocaleString()} chars total, ${(totalDuration / 1000).toFixed(1)}s`,
    );
    return { outputPath: cnDir };
  }
}
```

- [x] **Step 3: Build and type-check**

Run: `pnpm run check`
Expected: PASS

- [x] **Step 4: Lint check**

Run: `pnpm biome`
Expected: PASS (or auto-fix with `pnpm biome:fix`)

- [x] **Step 5: Commit**

```bash
git add apps/worker/src/providers/report-translation-provider.ts
git commit -m "feat(translation): parallel execution with structured summary logging"
```
