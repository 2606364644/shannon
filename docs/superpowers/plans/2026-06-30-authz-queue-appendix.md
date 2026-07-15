# 受影响端点清单附录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让最终安全报告确定性附带一份"完整可利用端点清单"附录(从 `*_exploitation_queue.json` 渲染),保证被 LLM findings 折叠掉的端点(如 `stock-pos-preference`)在报告中始终可见、可定位。

**Architecture:** 新增纯函数模块 `affected-endpoints-appendix.ts`,读取各漏洞类的 queue、过滤 `externally_exploitable=true`、按类渲染 markdown 附录(总览计数表 + 逐条 `Queue ID | Endpoint | Witness | Location` 表)。在 `reporting.ts` 新增 `injectAffectedEndpointsAppendix`,复用 `injectModelIntoReport` 的后置读写模式,在 report-executive agent 之后注入(确保不被清理),且在中文翻译之前(译文自动带上)。两个调用点:temporal 的 `injectReportMetadataActivity` 与 local runner 的 Phase 6 末尾。

**Tech Stack:** TypeScript(ESM,`.js` import 扩展名)、`zx`(fs/path)、Zod schema 校验过的 queue JSON、Biome 风格、新增 vitest 作为 worker 包测试基建。

## Global Constraints

- **ESM + `.js` import 扩展名**:worker 包是 ESM,所有相对 import 必须带 `.js`(如 `'./affected-endpoints-appendix.js'`、`'../paths.js'`)。
- **Biome 风格**:单引号、分号、尾逗号、2 空格缩进、120 字宽;`function` 关键字声明顶层函数;导出/顶层函数显式标注返回类型;数据用 `readonly`;`exactOptionalPropertyTypes` 开启——可选属性用 spread,不直接赋 `undefined`。
- **queue 字段不统一(已核实)**:`externally_exploitable` 所有类都有(base schema);`minimal_witness` 仅 authz 有,injection/xss 用 `witness_payload`,auth/ssrf/misconfig 无 witness;Endpoint 字段:authz=`endpoint`,auth/ssrf/misconfig=`source_endpoint`,injection/xss=`path`;Location 字段:authz/auth/ssrf/misconfig=`vulnerable_code_location`,injection=`sink_call`,xss=`sink_function`。
- **测试基建新增**:仓库当前无任何 JS/TS 测试框架。本计划在 Task 1 引入 `vitest`(worker 包 devDep)。若 `pnpm install` 触发 7 天最小龄限制(CLAUDE.md),报告受阻塞的包并停下,不要绕过。
- **时序铁律**:附录注入必须在 report-executive agent **之后**;prompt 中"完整清单见附录 A"是**前向引用**(附录当时尚未注入),由后置注入步骤实体化,二者通过固定标题 `## 附录 A:完整可利用端点清单` 对齐。
- **后置注入幂等**:注入前检测附录标题是否已存在,已存在则跳过(支持 resume 重跑)。

---

## File Structure

- **Create** `apps/worker/src/services/affected-endpoints-appendix.ts` — 纯函数:读各 queue → 过滤 → 渲染附录 markdown。单一职责:queue → markdown。
- **Create** `apps/worker/src/services/affected-endpoints-appendix.test.ts` — vitest 单元测试。
- **Modify** `apps/worker/src/services/reporting.ts` — 新增 `injectAffectedEndpointsAppendix`(后置读写注入),import 新模块。
- **Modify** `apps/worker/src/services/index.ts` — re-export `injectAffectedEndpointsAppendix`。
- **Modify** `apps/worker/src/temporal/activities.ts` — `injectReportMetadataActivity` 内、`injectModelIntoReport` 之后调用附录注入。
- **Modify** `apps/worker/src/local/runner.ts` — Phase 6 re-inject model(:507)之后调用附录注入。
- **Modify** `apps/worker/prompts/vuln-authz.txt` — 折叠 finding 写前向引用附录 A、禁用 representative 措辞。
- **Modify** `apps/worker/prompts/report-executive.txt` — 摘要里折叠 finding 引用附录 A、不自行计数。
- **Modify** `apps/worker/package.json` — 加 `vitest` devDep 与 `test` script。

---

## Task 1: 引入 vitest 测试基建(worker 包)

**Files:**
- Modify: `apps/worker/package.json`
- Create: `apps/worker/src/services/__sanity__.test.ts`
- Test: 该 sanity 测试本身

**Interfaces:**
- Produces: `pnpm --filter @shannon/worker test` 可用;后续 task 的 `*.test.ts` 可被发现运行。

- [ ] **Step 1: 给 worker package.json 加 vitest devDep 与 test script**

打开 `apps/worker/package.json`,在 `scripts` 块加入 `"test": "vitest run"`,在 `devDependencies` 块加入 `"vitest": "^2.1.9"`。修改后 `scripts` 与 `devDependencies` 形如:

```json
  "scripts": {
    "build": "tsc",
    "check": "tsc --noEmit",
    "clean": "rm -rf dist",
    "test": "vitest run"
  },
  "devDependencies": {
    "@types/node": "^25.0.3",
    "vitest": "^2.1.9"
  }
```

(若 `devDependencies` 原本不存在则新建;`@types/node` 已是根 hoisted devDep,此处按需保留即可,关键是 `vitest`。)

- [ ] **Step 2: 写一个最小冒烟测试验证 vitest 能跑**

Create `apps/worker/src/services/__sanity__.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';

describe('vitest sanity', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 3: 安装依赖**

Run: `pnpm install`
Expected: 安装成功。**若报某包未满 7 天龄(最小龄限制),停下并报告受阻塞的包给用户,不要绕过。**

- [ ] **Step 4: 跑测试,确认通过**

Run: `pnpm --filter @shannon/worker test`
Expected: PASS(1 test in `__sanity__.test.ts`)。

- [ ] **Step 5: type-check 确认未破坏构建**

Run: `pnpm --filter @shannon/worker check`
Expected: 无错误。

- [ ] **Step 6: Commit**

```bash
git add apps/worker/package.json apps/worker/src/services/__sanity__.test.ts pnpm-lock.yaml
git commit -m "test(worker): add vitest test harness"
```

---

## Task 2: 附录渲染纯函数(TDD)

**Files:**
- Create: `apps/worker/src/services/affected-endpoints-appendix.ts`
- Create: `apps/worker/src/services/affected-endpoints-appendix.test.ts`

**Interfaces:**
- Produces:
  - `collectExploitableEntries(sourceDir: string, deliverablesSubdir: string | undefined, logger: ActivityLogger): Promise<readonly ClassAppendix[]>`
  - `renderAppendixMarkdown(classes: readonly ClassAppendix[]): string`(纯函数)
  - `buildAffectedEndpointsAppendix(sourceDir: string, deliverablesSubdir: string | undefined, logger: ActivityLogger): Promise<string | null>`(返回 null 表示无可利用端点,调用方跳过注入)

- [ ] **Step 1: 写失败测试**

Create `apps/worker/src/services/affected-endpoints-appendix.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { fs, path } from 'zx';
import { collectExploitableEntries, renderAppendixMarkdown, buildAffectedEndpointsAppendix } from './affected-endpoints-appendix.js';
import type { ActivityLogger } from '../types/activity-logger.js';

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

describe('renderAppendixMarkdown', () => {
  it('renders overview counts + per-class tables', () => {
    const md = renderAppendixMarkdown([
      {
        heading: 'Authorization',
        entries: [
          { id: 'AUTHZ-VULN-49', endpoint: 'GET /asset-analysis/stock-pos-preference', witness: '?account_id=<victim>&target_uid=<victim>', location: 'match.ts:71-94' },
          { id: 'AUTHZ-VULN-50', endpoint: 'GET /asset-analysis/option-pos-preference', witness: '?account_id=<victim>', location: 'match.ts:71-94' },
        ],
      },
    ]);
    expect(md).toContain('## 附录 A:完整可利用端点清单');
    expect(md).toContain('| 漏洞类 | 可利用端点数 |');
    expect(md).toContain('| Authorization | 2 |');
    expect(md).toContain('AUTHZ-VULN-49');
    expect(md).toContain('GET /asset-analysis/stock-pos-preference');
    expect(md).toContain('?account_id=<victim>&target_uid=<victim>');
  });

  it('escapes pipe characters in cells', () => {
    const md = renderAppendixMarkdown([
      { heading: 'Injection', entries: [{ id: 'INJ-1', endpoint: 'GET /x', witness: 'a|b', location: 's.ts:1' }] },
    ]);
    expect(md).toContain('a\\|b');
  });

  it('returns the "none found" message when empty', () => {
    const md = renderAppendixMarkdown([]);
    expect(md).toContain('未识别到 externally_exploitable');
  });
});

describe('collectExploitableEntries', () => {
  it('filters externally_exploitable=false and maps per-class fields', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      await fs.writeJson(path.join(dir, 'authz_exploitation_queue.json'), {
        vulnerabilities: [
          { ID: 'AUTHZ-VULN-49', externally_exploitable: true, endpoint: 'GET /a', minimal_witness: '?id=<v>', vulnerable_code_location: 'match.ts:71' },
          { ID: 'AUTHZ-VULN-99', externally_exploitable: false, endpoint: 'GET /b', minimal_witness: '?id=<v>', vulnerable_code_location: 'match.ts:71' },
        ],
      });
      await fs.writeJson(path.join(dir, 'injection_exploitation_queue.json'), {
        vulnerabilities: [
          { ID: 'INJ-1', externally_exploitable: true, path: 'GET /i', witness_payload: 'p=<v>', sink_call: 'eval' },
        ],
      });
      const classes = await collectExploitableEntries(dir, '', noopLogger);
      const authz = classes.find((c) => c.heading === 'Authorization');
      const inj = classes.find((c) => c.heading === 'Injection');
      expect(authz?.entries.map((e) => e.id)).toEqual(['AUTHZ-VULN-49']); // false 被过滤
      expect(inj?.entries[0]).toMatchObject({ id: 'INJ-1', endpoint: 'GET /i', witness: 'p=<v>', location: 'eval' });
    } finally {
      await fs.remove(dir);
    }
  });

  it('skips missing queue files (out-of-scope class)', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      const classes = await collectExploitableEntries(dir, '', noopLogger);
      expect(classes).toEqual([]);
    } finally {
      await fs.remove(dir);
    }
  });
});

describe('buildAffectedEndpointsAppendix', () => {
  it('returns null when no exploitable entries', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      const out = await buildAffectedEndpointsAppendix(dir, '', noopLogger);
      expect(out).toBeNull();
    } finally {
      await fs.remove(dir);
    }
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm --filter @shannon/worker test affected-endpoints-appendix`
Expected: FAIL(模块不存在 / 导出未定义)。

- [ ] **Step 3: 写实现**

Create `apps/worker/src/services/affected-endpoints-appendix.ts`:

```typescript
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm --filter @shannon/worker test affected-endpoints-appendix`
Expected: PASS(全部用例)。

- [ ] **Step 5: type-check + biome**

Run: `pnpm --filter @shannon/worker check && pnpm biome apps/worker/src/services/affected-endpoints-appendix.ts apps/worker/src/services/affected-endpoints-appendix.test.ts`
Expected: 无错误(biome 若报格式,跑 `pnpm biome:fix` 后再确认)。

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/services/affected-endpoints-appendix.ts apps/worker/src/services/affected-endpoints-appendix.test.ts
git commit -m "feat(worker): render exploitable-endpoints appendix from queues"
```

---

## Task 3: 后置注入函数(TDD)

**Files:**
- Modify: `apps/worker/src/services/reporting.ts`
- Create: `apps/worker/src/services/reporting.appendix.test.ts`

**Interfaces:**
- Consumes: `buildAffectedEndpointsAppendix` from Task 2。
- Produces: `injectAffectedEndpointsAppendix(repoPath: string, deliverablesSubdir: string | undefined, logger: ActivityLogger): Promise<void>` — 读 `comprehensive_security_assessment_report.md`,在末尾追加附录,幂等(已存在则跳过)。

- [ ] **Step 1: 写失败测试**

Create `apps/worker/src/services/reporting.appendix.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { fs, path } from 'zx';
import { injectAffectedEndpointsAppendix } from './reporting.js';
import type { ActivityLogger } from '../types/activity-logger.js';

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

describe('injectAffectedEndpointsAppendix', () => {
  it('appends appendix after existing report content, idempotent', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      // ground-truth queue
      await fs.writeJson(path.join(dir, 'authz_exploitation_queue.json'), {
        vulnerabilities: [
          { ID: 'AUTHZ-VULN-49', externally_exploitable: true, endpoint: 'GET /asset-analysis/stock-pos-preference', minimal_witness: '?account_id=<v>', vulnerable_code_location: 'match.ts:71' },
        ],
      });
      // existing assembled report
      const reportPath = path.join(dir, 'comprehensive_security_assessment_report.md');
      await fs.writeFile(reportPath, '# Security Assessment Report\n\n## Authorization Findings\n\n...summary...\n');

      await injectAffectedEndpointsAppendix(dir, '', noopLogger);
      let content = await fs.readFile(reportPath, 'utf8');
      expect(content).toContain('## 附录 A:完整可利用端点清单');
      expect(content).toContain('AUTHZ-VULN-49');
      // original content preserved before appendix
      expect(content.indexOf('## Authorization Findings')).toBeLessThan(content.indexOf('## 附录 A'));

      // idempotent: second run does not duplicate
      await injectAffectedEndpointsAppendix(dir, '', noopLogger);
      content = await fs.readFile(reportPath, 'utf8');
      expect(content.match(/## 附录 A:完整可利用端点清单/g)?.length).toBe(1);
    } finally {
      await fs.remove(dir);
    }
  });

  it('skips injection when no exploitable entries', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      const reportPath = path.join(dir, 'comprehensive_security_assessment_report.md');
      await fs.writeFile(reportPath, '# Report\n');
      await injectAffectedEndpointsAppendix(dir, '', noopLogger);
      const content = await fs.readFile(reportPath, 'utf8');
      expect(content).not.toContain('附录 A');
    } finally {
      await fs.remove(dir);
    }
  });
});
```

> 注意:`deliverablesDir(repoPath, '')` 会得到 `repoPath` 本身(空串 split 为空数组),所以 fixture 直接放在 `dir` 根即可被读到。

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm --filter @shannon/worker test reporting.appendix`
Expected: FAIL(`injectAffectedEndpointsAppendix` 未导出)。

- [ ] **Step 3: 在 reporting.ts 加 import 与注入函数**

在 `apps/worker/src/services/reporting.ts` 顶部 import 区(`import { PentestError } from './error-handling.js';` 之后)加:

```typescript
import { buildAffectedEndpointsAppendix } from './affected-endpoints-appendix.js';
```

在文件末尾(`injectModelIntoReport` 函数之后)追加:

```typescript
/**
 * Append the deterministic "complete exploitable endpoints" appendix to the
 * final report. Must run AFTER report-executive — which would otherwise clean
 * the appendix away — so it mirrors injectModelIntoReport's post-report timing.
 * Idempotent: skips when the appendix heading is already present. The appendix
 * is an enhancement only: any failure is logged and never blocks the report.
 */
export async function injectAffectedEndpointsAppendix(
  repoPath: string,
  deliverablesSubdir: string | undefined,
  logger: ActivityLogger,
): Promise<void> {
  let appendix: string | null;
  try {
    appendix = await buildAffectedEndpointsAppendix(repoPath, deliverablesSubdir, logger);
  } catch (error) {
    const err = error as Error;
    logger.warn(`Failed to build affected-endpoints appendix: ${err.message}`);
    return;
  }
  if (appendix === null) {
    logger.info('No exploitable endpoints found; skipping appendix injection');
    return;
  }

  const reportPath = path.join(
    deliverablesDir(repoPath, deliverablesSubdir),
    'comprehensive_security_assessment_report.md',
  );
  if (!(await fs.pathExists(reportPath))) {
    logger.warn('Final report not found, skipping appendix injection');
    return;
  }

  const existing = await fs.readFile(reportPath, 'utf8');
  if (existing.includes('## 附录 A:完整可利用端点清单')) {
    logger.info('Appendix already present, skipping injection');
    return;
  }
  const updated = `${existing.trimEnd()}\n\n${appendix}\n`;
  await fs.writeFile(reportPath, updated);
  logger.info('Affected-endpoints appendix injected into final report');
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm --filter @shannon/worker test reporting.appendix`
Expected: PASS。

- [ ] **Step 5: type-check + biome**

Run: `pnpm --filter @shannon/worker check && pnpm biome apps/worker/src/services/reporting.ts apps/worker/src/services/reporting.appendix.test.ts`
Expected: 无错误。

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/services/reporting.ts apps/worker/src/services/reporting.appendix.test.ts
git commit -m "feat(worker): inject exploitable-endpoints appendix after report-executive"
```

---

## Task 4: 接入两个调用点 + re-export

**Files:**
- Modify: `apps/worker/src/services/index.ts:23`
- Modify: `apps/worker/src/temporal/activities.ts:40`, `:823-833`
- Modify: `apps/worker/src/local/runner.ts:13`, `:507-510`

**Interfaces:**
- Consumes: `injectAffectedEndpointsAppendix` from Task 3。

> 验证手段:本任务为 wiring,用 `pnpm check`(tsc 全量类型检查)+ biome 确认;行为正确性由 Task 6 的集成验证覆盖。

- [ ] **Step 1: re-export**

`apps/worker/src/services/index.ts:23` 现为:

```typescript
export { assembleFinalReport, injectModelIntoReport } from './reporting.js';
```

改为:

```typescript
export { assembleFinalReport, injectModelIntoReport, injectAffectedEndpointsAppendix } from './reporting.js';
```

- [ ] **Step 2: temporal activities 接入**

`apps/worker/src/temporal/activities.ts:40` 现为:

```typescript
import { assembleFinalReport, injectModelIntoReport } from '../services/reporting.js';
```

改为:

```typescript
import { assembleFinalReport, injectAffectedEndpointsAppendix, injectModelIntoReport } from '../services/reporting.js';
```

`injectReportMetadataActivity`(约 `:823-833`)现末尾仅注入 model;在 `injectModelIntoReport` 的 try/catch 之后追加附录注入。改为:

```typescript
export async function injectReportMetadataActivity(input: ActivityInput): Promise<void> {
  const { repoPath, sessionId, outputPath, deliverablesSubdir } = input;
  const logger = createActivityLogger();
  const effectiveOutputPath = outputPath ? path.join(outputPath, sessionId) : path.join('./workspaces', sessionId);
  try {
    await injectModelIntoReport(repoPath, deliverablesSubdir, effectiveOutputPath, logger);
  } catch (error) {
    const err = error as Error;
    logger.warn(`Error injecting model into report: ${err.message}`);
  }
  try {
    await injectAffectedEndpointsAppendix(repoPath, deliverablesSubdir, logger);
  } catch (error) {
    const err = error as Error;
    logger.warn(`Error injecting affected-endpoints appendix: ${err.message}`);
  }
}
```

> 时序:`injectReportMetadataActivity` 在 workflow 中紧跟 `runReportAgent` 之后调用(workflows.ts `:594→:601`、`:827→:829`、`:1056→:1058`),满足"report-executive 之后"。

- [ ] **Step 3: local runner 接入**

`apps/worker/src/local/runner.ts:13` 现为:

```typescript
import { assembleFinalReport, injectModelIntoReport } from '../services/reporting.js';
```

改为:

```typescript
import { assembleFinalReport, injectAffectedEndpointsAppendix, injectModelIntoReport } from '../services/reporting.js';
```

在 Phase 6 的 model re-inject 块(约 `:505-510`)之后插入附录注入。该块现为:

```typescript
      // Re-inject model info (report agent overwrites the assembled file)
      try {
        await injectModelIntoReport(args.repoPath, undefined, path.join(WORKSPACES_DIR, sessionId), logger);
      } catch (error) {
        logger.warn(`Model re-injection had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
```

在其后追加:

```typescript

      // Inject deterministic exploitable-endpoints appendix AFTER report-executive
      // (which overwrites the assembled file) and BEFORE translation (Phase 7) so
      // the Chinese deliverable includes the appendix via whole-document translation.
      try {
        await injectAffectedEndpointsAppendix(args.repoPath, undefined, logger);
      } catch (error) {
        logger.warn(`Appendix injection had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
```

- [ ] **Step 4: type-check + biome**

Run: `pnpm run check && pnpm biome apps/worker/src/services/index.ts apps/worker/src/temporal/activities.ts apps/worker/src/local/runner.ts`
Expected: 无错误。

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/services/index.ts apps/worker/src/temporal/activities.ts apps/worker/src/local/runner.ts
git commit -m "feat(worker): wire appendix injection into report metadata + local runner"
```

---

## Task 5: prompt 强化(前向引用附录 A)

**Files:**
- Modify: `apps/worker/prompts/vuln-authz.txt`
- Modify: `apps/worker/prompts/report-executive.txt`

**Interfaces:**
- 无代码接口;产出为 prompt 文本变更。验证:`pnpm run build`(prompts 不编译,但确保无语法干扰)+ 人工审阅措辞。

> 时序提醒(写入注释供实现者理解):附录在 report-executive 之后由代码注入,故 prompt 中"完整清单见附录 A"是**前向引用**——agent 写下引用,后置注入用固定标题 `## 附录 A:完整可利用端点清单` 把它实体化。

- [ ] **Step 1: 强化 vuln-authz.txt**

在 `apps/worker/prompts/vuln-authz.txt` 的 `<coverage_requirements>` 段(`### 6) Confidence Scoring` 之前、`</coverage_requirements>` 之前)插入一段:

```text
### Appendix Reference for Collapsed Findings

When a single finding legitimately merges multiple endpoints that share one
authorization root cause (for example a shared transparent-forward handler
spanning many routes), you MUST state in that finding: "本条为合并摘要,逐端点完整清单见附录 A". Do NOT list only a handful of representative endpoints with
"等"/"例如"/"representative"/"including but not limited to" — those signal an
exhaustive list when there is none. The exact affected-endpoint count is
produced deterministically in 附录 A by the orchestrator; do not invent a count
yourself.
```

- [ ] **Step 2: 强化 report-executive.txt**

在 `apps/worker/prompts/report-executive.txt` 的清理规则段(`<instructions>` 第 3 条 "Clean the per-class report sections" 的 KEEP 规则之后)插入一条:

```text
   - When a per-class finding merges multiple endpoints (a shared handler /
     pattern), keep its summary but ensure it references the deterministically
     generated "附录 A:完整可利用端点清单" for the full per-endpoint list. Do not
     rewrite such a finding to list only representative endpoints, and do not
     state an affected-endpoint count in the summary — the count lives in 附录 A.
```

- [ ] **Step 3: 全量构建确认未破坏**

Run: `pnpm run build`
Expected: 成功(prompts 为文本,不影响编译;确认 worker 仍可构建)。

- [ ] **Step 4: Commit**

```bash
git add apps/worker/prompts/vuln-authz.txt apps/worker/prompts/report-executive.txt
git commit -m "prompts: reference deterministic appendix A for collapsed findings"
```

---

## Task 6: 集成验证(真实 authz queue)

**Files:**
- Create: `apps/worker/src/services/affected-endpoints-appendix.integration.test.ts`

**Interfaces:**
- Consumes: Task 2/3 的导出;真实交付物 `workspaces/paper_trading_frontend_whitebox-1782723841267-deliverables/deliverables/authz_exploitation_queue.json`。

- [ ] **Step 1: 写集成测试,断言 91 个端点全在、stock-pos-preference 命中**

Create `apps/worker/src/services/affected-endpoints-appendix.integration.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { path } from 'zx';
import { collectExploitableEntries, renderAppendixMarkdown } from './affected-endpoints-appendix.js';
import type { ActivityLogger } from '../types/activity-logger.js';

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

// Real deliverable from a completed whitebox run of paper_trading_frontend.
const REAL_DELIVERABLES = path.resolve(
  import.meta.dirname,
  '../../../../workspaces/paper_trading_frontend_whitebox-1782723841267-deliverables/deliverables',
);

describe('affected-endpoints appendix (real authz queue)', () => {
  it('includes all 91 authz endpoints and the stock-pos-preference finding', async () => {
    const classes = await collectExploitableEntries(REAL_DELIVERABLES, '', noopLogger);
    const authz = classes.find((c) => c.heading === 'Authorization');
    expect(authz).toBeDefined();
    expect(authz?.entries.length).toBe(91);
    const md = renderAppendixMarkdown(classes);
    expect(md).toContain('AUTHZ-VULN-49');
    expect(md).toContain('GET /asset-analysis/stock-pos-preference');
    expect(md).toContain('| Authorization | 91 |');
  });
});
```

- [ ] **Step 2: 跑集成测试确认通过**

Run: `pnpm --filter @shannon/worker test affected-endpoints-appendix.integration`
Expected: PASS(91 条、`AUTHZ-VULN-49`、`stock-pos-preference` 均命中)。

> 若该 workspace 交付物在本机不存在(例如在 CI 跑),将本测试标记为 `it.skipIf(!existsSync(REAL_DELIVERABLES))` 跳过,避免误报。

- [ ] **Step 3: type-check + biome**

Run: `pnpm --filter @shannon/worker check && pnpm biome apps/worker/src/services/affected-endpoints-appendix.integration.test.ts`
Expected: 无错误。

- [ ] **Step 4: 全套测试 + 全量 type-check 收尾**

Run: `pnpm --filter @shannon/worker test && pnpm run check && pnpm biome`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/services/affected-endpoints-appendix.integration.test.ts
git commit -m "test(worker): integration-verify appendix against real authz queue"
```

---

## Self-Review 结论

**Spec 覆盖:** §3.1 组件 (a)→Task 2,(b)→Task 3+4,(c) 完整性断言→Task 2 `collectExploitableEntries` 过滤逻辑 + 总览计数(条目数即 externally_exploitable 计数);§3.3 错误处理→Task 2(queue 缺失/解析失败 skip+warn、缺字段 skip+warn)、Task 3(注入失败/报告缺失 warn);§3.4 测试→Task 2/3/6;§3.5 prompt→Task 5;§4 约束→Global Constraints;§5 验收→Task 6 覆盖 91 端点 + stock-pos-preference。§6 中文链路→Task 4 注释(注入在翻译前,整篇翻译带上)。

**类型一致:** `injectAffectedEndpointsAppendix(repoPath, deliverablesSubdir, logger)` 签名在 Task 3 定义、Task 4 两处调用一致;`buildAffectedEndpointsAppendix` 返回 `string | null` 在 Task 2 定义、Task 3 消费一致;附录标题 `## 附录 A:完整可利用端点清单` 在 Task 2/3/prompt(Task 5)三处完全一致。

**占位符扫描:** 无 TBD/TODO;每步含完整代码或确切命令。
