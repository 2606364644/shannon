/**
 * ReportTranslationProvider — translates markdown deliverables to Chinese.
 *
 * Implements the ReportOutputProvider interface to run after the report agent
 * finalizes the comprehensive security assessment report. Scans all .md files
 * in the deliverables directory, translates each via runClaudePrompt (Haiku),
 * and writes results to a parallel deliverables-cn directory.
 */

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
async function runWithConcurrencyLimit<T>(
  thunks: Array<() => Promise<T>>,
  limit: number,
): Promise<PromiseSettledResult<T>[]> {
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
          'medium',
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
      r.status === 'fulfilled'
        ? r.value
        : { filename: 'unknown', cnFilename: '', success: false, chars: 0, duration: 0, error: String(r.reason) },
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
        logger.info(
          `  ✓ ${r.filename} → ${r.cnFilename} (${r.chars.toLocaleString()} chars, ${(r.duration / 1000).toFixed(1)}s)`,
        );
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
