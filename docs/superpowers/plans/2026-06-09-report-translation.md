# Report Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically translate all markdown deliverables to Chinese after a scan completes, outputting to a `deliverables-cn/` directory.

**Architecture:** Implement `ReportOutputProvider` interface with a `ReportTranslationProvider` that scans `deliverables/*.md`, translates each via `runClaudePrompt` (Haiku/small tier), and writes results to `deliverables-cn/`. Registered via `setContainerFactory()` in a new `providers/` module, activated by a single import line in `worker.ts`.

**Tech Stack:** TypeScript, `@anthropic-ai/claude-agent-sdk`, existing `runClaudePrompt` from `apps/worker/src/ai/claude-executor.ts`, `file-io` utils.

---

### Task 1: Translation Prompt Template

**Files:**
- Create: `apps/worker/src/providers/translation-prompt.ts`

- [ ] **Step 1: Create the translation prompt module**

Create `apps/worker/src/providers/translation-prompt.ts`:

```typescript
/**
 * Translation prompt builder for markdown deliverable translation.
 *
 * Produces a prompt that instructs Claude to translate security assessment
 * reports from English to Chinese while preserving technical terms.
 */

/**
 * Build a translation prompt for a given markdown file.
 *
 * @param content - The English markdown content to translate
 * @param filename - Source filename for context in the prompt
 * @returns The full prompt string for runClaudePrompt
 */
export function buildTranslationPrompt(content: string, filename: string): string {
  return `You are a professional security report translator. Translate the following markdown document from English to Chinese.

## Translation Rules

1. **Preserve all markdown formatting exactly** — headings, lists, tables, code blocks, bold, links, images
2. **Keep these in English (do NOT translate):**
   - Vulnerability IDs (e.g., INJ-VULN-01, AUTH-VULN-10, XSS-VULN-02)
   - HTTP methods, paths, status codes, header names
   - URLs, file paths, code snippets, JSON field names
   - Technical abbreviations (XSS, SSRF, CSRF, RBAC, IDOR, SSO, BFF, SPA, OAuth, HMAC, AES, CSP, etc.)
   - Command names and CLI flags
3. **Severity levels — use bilingual format:** 严重 (Critical), 高 (High), 中 (Medium), 低 (Low)
4. **Translate narrative and descriptive text to natural, professional Chinese**
5. **Add a translation note** at the very top of the output as a blockquote:
   > 说明：本报告为英文版安全评估报告的中文翻译版。代码、命令、漏洞编号、HTTP 方法/状态码、文件路径、URL、header 名、JSON 字段名及标准技术缩写均保留英文原文，仅叙述性文字译为中文。
6. **Output ONLY the translated markdown** — no preamble, no explanation, no wrapping

## Source File: ${filename}

${content}`;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `pnpm --filter @shannon/worker exec tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors referencing `translation-prompt.ts`

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/providers/translation-prompt.ts
git commit -m "feat: add translation prompt template for report translation"
```

---

### Task 2: Report Translation Provider

**Files:**
- Create: `apps/worker/src/providers/report-translation-provider.ts`

- [ ] **Step 1: Create the ReportTranslationProvider**

Create `apps/worker/src/providers/report-translation-provider.ts`:

```typescript
/**
 * ReportTranslationProvider — translates markdown deliverables to Chinese.
 *
 * Implements the ReportOutputProvider interface to run after the report agent
 * finalizes the comprehensive security assessment report. Scans all .md files
 * in the deliverables directory, translates each via runClaudePrompt (Haiku),
 * and writes results to a parallel deliverables-cn directory.
 */

import path from 'node:path';
import { runClaudePrompt, type ClaudePromptResult } from '../ai/claude-executor.js';
import type { ReportOutputProvider } from '../interfaces/report-output-provider.js';
import { deliverablesDir } from '../paths.js';
import type { ActivityInput } from '../temporal/activities.js';
import type { ActivityLogger } from '../types/activity-logger.js';
import { ensureDirectory, fileExists } from '../utils/file-io.js';
import { buildTranslationPrompt } from './translation-prompt.js';

/** Suffix for translated output directory, relative to repoPath/.shannon/ */
const CN_DIR_SUBDIR = 'deliverables-cn';

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
    const { readdir } = await import('node:fs/promises');
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
    const cnDir = path.join(input.repoPath, '.shannon', CN_DIR_SUBDIR);
    await ensureDirectory(cnDir);

    // 4. Translate each file
    const { readFile, writeFile } = await import('node:fs/promises');
    let successCount = 0;
    let failCount = 0;

    for (const filename of mdFiles) {
      const srcPath = path.join(srcDir, filename);
      const cnFilename = filename.replace(/\.md$/, '-cn.md');
      const cnPath = path.join(cnDir, cnFilename);

      try {
        const content = await readFile(srcPath, 'utf-8');
        if (!content.trim()) {
          logger.info(`Skipping empty file: ${filename}`);
          continue;
        }

        logger.info(`Translating ${filename}...`);

        const prompt = buildTranslationPrompt(content, filename);
        const result: ClaudePromptResult = await runClaudePrompt(
          prompt,
          input.repoPath,
          '', // context
          `translate ${filename}`, // description
          null, // _agentName
          null, // auditSession
          logger,
          'small', // modelTier — Haiku is sufficient for translation
          undefined, // outputFormat
          input.apiKey,
          input.deliverablesSubdir,
          input.providerConfig,
        );

        if (result.success && result.result) {
          await writeFile(cnPath, result.result, 'utf-8');
          successCount++;
          logger.info(`Translated: ${filename} → ${cnFilename}`);
        } else {
          failCount++;
          logger.warn(`Translation failed for ${filename}: ${result.error ?? 'empty result'}`);
        }
      } catch (err) {
        failCount++;
        const msg = err instanceof Error ? err.message : String(err);
        logger.warn(`Error translating ${filename}: ${msg}`);
      }
    }

    // 5. Return result
    if (successCount === 0) {
      logger.warn('All translations failed');
      return {};
    }

    logger.info(`Translation complete: ${successCount} succeeded, ${failCount} failed`);
    return { outputPath: cnDir };
  }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `pnpm --filter @shannon/worker exec tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors referencing `report-translation-provider.ts`

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/providers/report-translation-provider.ts
git commit -m "feat: add ReportTranslationProvider implementing ReportOutputProvider"
```

---

### Task 3: Provider Registration Module

**Files:**
- Create: `apps/worker/src/providers/register.ts`

- [ ] **Step 1: Create the registration module**

Create `apps/worker/src/providers/register.ts`:

```typescript
/**
 * Provider registration — injects custom providers into the DI container.
 *
 * Called once at worker startup via side-effect import in worker.ts.
 * Overrides the default container factory to include ReportTranslationProvider.
 */

import type { SessionMetadata } from '../audit/utils.js';
import { Container, setContainerFactory } from '../services/container.js';
import type { ContainerConfig } from '../types/config.js';
import { ReportTranslationProvider } from './report-translation-provider.js';

/**
 * Create a Container with the translation provider injected.
 * Matches the setContainerFactory() parameter signature:
 * (workflowId: string, sessionMetadata: SessionMetadata, config: ContainerConfig) => Container
 */
function createContainerWithTranslation(
  _workflowId: string,
  sessionMetadata: SessionMetadata,
  config: ContainerConfig,
): Container {
  return new Container({
    sessionMetadata,
    config,
    reportOutputProvider: new ReportTranslationProvider(),
  });
}

/**
 * Register custom providers by overriding the container factory.
 *
 * Call once at worker startup. Subsequent calls overwrite the previous factory.
 */
export function registerProviders(): void {
  setContainerFactory(createContainerWithTranslation);
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `pnpm --filter @shannon/worker exec tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors referencing `register.ts`

- [ ] **Step 3: Commit**

```bash
git add apps/worker/src/providers/register.ts
git commit -m "feat: add provider registration module with translation provider"
```

---

### Task 4: Wire Into Worker Entry Point

**Files:**
- Modify: `apps/worker/src/temporal/worker.ts` (line 33, import section)

- [ ] **Step 1: Add the import line in worker.ts**

In `apps/worker/src/temporal/worker.ts`, add the following line after the existing imports (after line 43, after the `import type { PipelineInput, ... } from './shared.js';` line):

```typescript
import '../providers/register.js';
```

This side-effect import calls `registerProviders()` which calls `setContainerFactory()` before any workflow starts.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `pnpm --filter @shannon/worker exec tsc --noEmit --pretty 2>&1 | head -20`
Expected: Clean compilation, no errors

- [ ] **Step 3: Verify build succeeds**

Run: `pnpm run build 2>&1 | tail -10`
Expected: All packages build successfully

- [ ] **Step 4: Commit**

```bash
git add apps/worker/src/temporal/worker.ts
git commit -m "feat: wire translation provider into worker entry point"
```

---

### Task 5: End-to-End Verification

**Files:**
- No new files

- [ ] **Step 1: Run full type check**

Run: `pnpm run check 2>&1 | tail -15`
Expected: No type errors across all packages

- [ ] **Step 2: Run linter**

Run: `pnpm biome 2>&1 | tail -15`
Expected: No lint errors in the new files. If formatting issues, run `pnpm biome:fix` and commit the fix.

- [ ] **Step 3: Verify import chain is correct**

Run: `node -e "import('./apps/worker/dist/providers/register.js').then(m => console.log('registerProviders:', typeof m.registerProviders))"` (after build)
Expected: Prints `registerProviders: function`

- [ ] **Step 4: Final commit (if any lint fixes)**

```bash
git add -A
git commit -m "chore: lint fixes for report translation provider"
```
