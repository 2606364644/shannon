// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Deterministic renderer for the "complete exploitable endpoints" appendix.
 *
 * Reads each *_exploitation_queue.json (ground truth, SDK-validated) and renders
 * a markdown appendix listing every externally-exploitable endpoint with its
 * witness and code location. Guarantees endpoints that LLM per-class findings
 * collapse into representative examples (e.g. a shared handler spanning ~124
 * routes) remain visible and locatable in the final report.
 *
 * No LLM in the loop — every field maps directly from a JSON key.
 */

import { fs, path } from 'zx';
import { deliverablesDir } from '../paths.js';
import type { ActivityLogger } from '../types/activity-logger.js';

/** One appendix row, normalized across vuln-class schemas. */
interface AppendixEntry {
  readonly id: string;
  readonly endpoint: string;
  readonly witness: string;
  readonly location: string;
}

/** Per-class config: queue file plus field extractors (schemas differ per class). */
interface ClassAppendixConfig {
  readonly heading: string;
  readonly queueFile: string;
  readonly extract: (raw: Record<string, unknown>) => AppendixEntry;
}

/** A class's collected exploitable entries. */
interface ClassAppendix {
  readonly heading: string;
  readonly entries: readonly AppendixEntry[];
}

/** Coerce an unknown JSON value to a trimmed string, '' when absent or blank. */
function str(value: unknown): string {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : '';
}

/** Escape a table cell: pipes and newlines would break the markdown table. */
function cell(value: string): string {
  return value.replace(/\|/g, '\\|').replace(/\n/g, ' ');
}

const CLASS_CONFIGS: readonly ClassAppendixConfig[] = [
  {
    heading: 'Authorization',
    queueFile: 'authz_exploitation_queue.json',
    extract: (r) => ({
      id: str(r.ID),
      endpoint: str(r.endpoint),
      witness: str(r.minimal_witness),
      location: str(r.vulnerable_code_location),
    }),
  },
  {
    heading: 'Authentication',
    queueFile: 'auth_exploitation_queue.json',
    extract: (r) => ({
      id: str(r.ID),
      endpoint: str(r.source_endpoint),
      witness: '',
      location: str(r.vulnerable_code_location),
    }),
  },
  {
    heading: 'SSRF',
    queueFile: 'ssrf_exploitation_queue.json',
    extract: (r) => ({
      id: str(r.ID),
      endpoint: str(r.source_endpoint),
      witness: '',
      location: str(r.vulnerable_code_location),
    }),
  },
  {
    heading: 'Security Misconfiguration',
    queueFile: 'misconfig_exploitation_queue.json',
    extract: (r) => ({
      id: str(r.ID),
      endpoint: str(r.source_endpoint),
      witness: '',
      location: str(r.vulnerable_code_location),
    }),
  },
  {
    heading: 'Injection',
    queueFile: 'injection_exploitation_queue.json',
    extract: (r) => ({
      id: str(r.ID),
      endpoint: str(r.path),
      witness: str(r.witness_payload),
      location: str(r.sink_call),
    }),
  },
  {
    heading: 'XSS',
    queueFile: 'xss_exploitation_queue.json',
    extract: (r) => ({
      id: str(r.ID),
      endpoint: str(r.path),
      witness: str(r.witness_payload),
      location: str(r.sink_function),
    }),
  },
];

/** Read all queues and collect externally-exploitable entries, grouped by class. */
export async function collectExploitableEntries(
  sourceDir: string,
  deliverablesSubdir: string | undefined,
  logger: ActivityLogger,
): Promise<readonly ClassAppendix[]> {
  const dir = deliverablesDir(sourceDir, deliverablesSubdir);
  const result: ClassAppendix[] = [];

  for (const config of CLASS_CONFIGS) {
    const queuePath = path.join(dir, config.queueFile);
    if (!(await fs.pathExists(queuePath))) {
      continue; // class out of scope this run
    }
    try {
      const doc = (await fs.readJson(queuePath)) as { vulnerabilities?: unknown[] };
      const raws = Array.isArray(doc.vulnerabilities) ? doc.vulnerabilities : [];
      const entries: AppendixEntry[] = [];
      for (const raw of raws) {
        if (typeof raw !== 'object' || raw === null) continue;
        const record = raw as Record<string, unknown>;
        if (record.externally_exploitable !== true) continue;
        const entry = config.extract(record);
        if (entry.endpoint === '' && entry.id === '') {
          logger.warn(`${config.heading}: skipped queue entry with no endpoint/ID`);
          continue;
        }
        entries.push(entry);
      }
      result.push({ heading: config.heading, entries });
    } catch (error) {
      const err = error as Error;
      logger.warn(`${config.heading}: failed to read ${config.queueFile}: ${err.message}`);
    }
  }
  return result;
}

/** Render the appendix markdown. Pure (no I/O) so it snapshots cleanly. */
export function renderAppendixMarkdown(classes: readonly ClassAppendix[]): string {
  const lines: string[] = [];
  lines.push('## 附录 A:完整可利用端点清单');
  lines.push('');
  lines.push(
    '> 由各 `*_exploitation_queue.json` 确定性生成。每个 `externally_exploitable=true` 的端点均在此列出,不受摘要折叠影响。',
  );
  lines.push('');

  const nonEmpty = classes.filter((c) => c.entries.length > 0);
  if (nonEmpty.length === 0) {
    lines.push('本次评估未识别到 externally_exploitable 端点。');
    return lines.join('\n').trimEnd();
  }

  // Overview counts
  lines.push('### 总览');
  lines.push('');
  lines.push('| 漏洞类 | 可利用端点数 |');
  lines.push('|---|---|');
  for (const c of nonEmpty) {
    lines.push(`| ${c.heading} | ${c.entries.length} |`);
  }
  lines.push('');

  // Per-class endpoint tables
  for (const c of nonEmpty) {
    lines.push(`### ${c.heading}`);
    lines.push('');
    lines.push('| Queue ID | Endpoint | Witness | Location |');
    lines.push('|---|---|---|---|');
    for (const e of c.entries) {
      lines.push(`| ${cell(e.id)} | ${cell(e.endpoint)} | ${cell(e.witness)} | ${cell(e.location)} |`);
    }
    lines.push('');
  }
  return lines.join('\n').trimEnd();
}

/**
 * Build the full appendix markdown from all queues. Returns null when no
 * exploitable entries exist anywhere, so the caller can skip injection.
 */
export async function buildAffectedEndpointsAppendix(
  sourceDir: string,
  deliverablesSubdir: string | undefined,
  logger: ActivityLogger,
): Promise<string | null> {
  const classes = await collectExploitableEntries(sourceDir, deliverablesSubdir, logger);
  const total = classes.reduce((sum, c) => sum + c.entries.length, 0);
  if (total === 0) {
    return null;
  }
  return renderAppendixMarkdown(classes);
}
