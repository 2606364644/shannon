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
        const result = await runClaudePrompt(
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
