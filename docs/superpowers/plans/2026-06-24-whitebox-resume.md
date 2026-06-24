# Whitebox Local Runner Resume 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 whitebox local runner(`apps/worker/src/local/runner.ts`)支持断点续扫——用 `-w <旧workspace>` 重跑时跳过已成功的 agent 和已完成的无 agent 阶段,从第一个未完成处继续。

**Architecture:** 策略 A——新建 `apps/worker/src/local/resume.ts` 实现 whitebox 专用 resume(借鉴 temporal 的 `loadResumeState` 逻辑但去掉耦合),import 复用 `restoreGitCheckpoint`/`findLatestCommit`(零 Temporal 耦合的纯 git 函数),复用已有的 `AuditSession.addResumeAttempt`/`logResumeHeader`。在 `runner.ts` 的 7 个 Phase 各加 skip 闸门,无 agent 阶段(findings-rendering / report-assembly / translation)的完成状态通过 `session.json` 新字段 `completedNonAgentPhases` 持久化。不抽共享层、不碰 `temporal/workflows.ts` 和 `agent-execution.ts`。

**Tech Stack:** TypeScript(pnpm workspace + Turborepo),Biome(lint/format),`@anthropic-ai/claude-agent-sdk`。无测试框架。

## Global Constraints

(摘自 spec `docs/superpowers/specs/2026-06-24-whitebox-resume-design.md`,每个 task 隐含遵守)

- **不引入测试框架**:项目当前 0 个 test 文件、无 test script。验证一律用 `pnpm run check`(tsc 全量 type-check)+ `pnpm biome`(lint/format check)+ 临时 fixture script + 端到端 `--pipeline-testing`。
- **Biome 风格**:single quotes、semicolons、trailing commas、2-space indent、120 char line width。先写代码再跑 `pnpm biome:fix` 自动修复格式。
- **TypeScript 约定**:top-level 函数用 `function` 关键字、exported/top-level 函数显式标注返回类型、数据用 `readonly`、`exactOptionalPropertyTypes` 开启(可选属性用 spread 不直接赋 undefined)。
- **不触碰**:`apps/worker/src/temporal/workflows.ts`、`apps/worker/src/services/agent-execution.ts`(后者已写 session.json + checkpoint,是本方案数据基础)。
- **注释 timeless**:JSDoc 文件头(license 后)、exported 函数用 `/** */`。禁止对话引用/历史引用。
- **package 安装**:无新增依赖。如 `pnpm install` 因包过新失败,不要绕过,报告用户。

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `apps/worker/src/audit/metrics-tracker.ts` | session.json 读写、agent/phase 指标 | 加 `completedNonAgentPhases` 字段 + `markNonAgentPhase` 方法 |
| `apps/worker/src/audit/audit-session.ts` | audit 门面(mutex 包裹 metrics) | 加 `markNonAgentPhaseComplete` |
| `apps/worker/src/temporal/activities.ts` | temporal activities(含纯 git resume 函数) | `findLatestCommit` export;`restoreGitCheckpoint` 加可选 logger 参数 |
| `apps/worker/src/local/resume.ts` | **新建** whitebox resume 核心 | `loadResumeState` + `WHITEBOX_EXPECTED_AGENTS` |
| `apps/worker/src/local/runner.ts` | whitebox pipeline 主入口 | resume 段 + 7 Phase 闸门 + 无 agent 标记 + 全部完成短路 |
| `apps/cli/src/commands/local-start.ts` | CLI 启动 whitebox runner | resume 提示 |

依赖链:Task 1(audit 字段)→ Task 2(git 函数 export)→ Task 3(resume.ts)→ Task 4(runner 接入)→ Task 5(CLI 提示)。

---

## Task 1: audit 层 — 无 agent 阶段完成标记

**Files:**
- Modify: `apps/worker/src/audit/metrics-tracker.ts`(`SessionData` 接口 `:55-72`、`createInitialData` `:117-144`、新增方法)
- Modify: `apps/worker/src/audit/audit-session.ts`(新增方法,模式参照 `addResumeAttempt` `:258-268`)

**Interfaces:**
- Consumes: 现有 `MetricsTracker.reload()`(`:377`)、`save()`(`:369`)、`sessionMutex`(`audit-session.ts:25`)
- Produces:
  - `MetricsTracker.markNonAgentPhase(name: string): Promise<void>`
  - `AuditSession.markNonAgentPhaseComplete(name: string): Promise<void>`
  - `SessionData['metrics']['completedNonAgentPhases']: string[]`(持久化到 session.json)

- [ ] **Step 1: `metrics-tracker.ts` — 扩展 `SessionData` 接口**

在 `metrics-tracker.ts:66-71` 的 `metrics` 对象里加字段。把:

```ts
  metrics: {
    total_duration_ms: number;
    total_cost_usd: number;
    phases: Record<string, PhaseMetrics>;
    agents: Record<string, AgentAuditMetrics>;
  };
```

改为:

```ts
  metrics: {
    total_duration_ms: number;
    total_cost_usd: number;
    phases: Record<string, PhaseMetrics>;
    agents: Record<string, AgentAuditMetrics>;
    completedNonAgentPhases: string[];
  };
```

- [ ] **Step 2: `metrics-tracker.ts` — `createInitialData` 加默认值**

在 `createInitialData`(`:126-131`)的 `metrics` 里加默认空数组。把:

```ts
      metrics: {
        total_duration_ms: 0,
        total_cost_usd: 0,
        phases: {}, // Phase-level aggregations
        agents: {}, // Agent-level metrics
      },
```

改为:

```ts
      metrics: {
        total_duration_ms: 0,
        total_cost_usd: 0,
        phases: {}, // Phase-level aggregations
        agents: {}, // Agent-level metrics
        completedNonAgentPhases: [], // Non-agent phases (findings-rendering, report-assembly, translation)
      },
```

- [ ] **Step 3: `metrics-tracker.ts` — 加 `markNonAgentPhase` 方法**

在 `addResumeAttempt` 方法之后(`:292` 之后、`recalculateAggregations` `:297` 之前)插入。注意:`reload()` 读取旧 session.json 时该字段可能不存在,必须 defensive backfill。

```ts
  /**
   * Mark a non-agent phase (findings-rendering, report-assembly, translation) as completed.
   * Idempotent — duplicate marks are ignored.
   */
  async markNonAgentPhase(name: string): Promise<void> {
    if (!this.data) {
      throw new PentestError(
        'MetricsTracker not initialized',
        'validation',
        false,
        {},
        ErrorCode.AGENT_EXECUTION_FAILED,
      );
    }

    // Backfill field missing from older session.json (reload() does not initialize it)
    if (!this.data.metrics.completedNonAgentPhases) {
      this.data.metrics.completedNonAgentPhases = [];
    }

    if (!this.data.metrics.completedNonAgentPhases.includes(name)) {
      this.data.metrics.completedNonAgentPhases.push(name);
    }

    await this.save();
  }
```

- [ ] **Step 4: `audit-session.ts` — 加 `markNonAgentPhaseComplete` 门面方法**

在 `logResumeHeader` 方法之后(文件末尾 `}` 之前,`:282` 之后)插入。模式严格参照 `addResumeAttempt`(`:258-268`):`ensureInitialized` + mutex + `reload` + 调 tracker。

```ts
  /**
   * Mark a non-agent phase (findings-rendering, report-assembly, translation) as completed.
   * Persisted to session.json so resume runs can skip it.
   */
  async markNonAgentPhaseComplete(name: string): Promise<void> {
    await this.ensureInitialized();

    const unlock = await sessionMutex.lock(this.sessionId);
    try {
      await this.metricsTracker.reload();
      await this.metricsTracker.markNonAgentPhase(name);
    } finally {
      unlock();
    }
  }
```

- [ ] **Step 5: type-check + lint**

Run: `pnpm run check && pnpm biome`
Expected: PASS(0 type errors,0 lint errors)。若有格式问题先 `pnpm biome:fix` 再 `pnpm biome`。

- [ ] **Step 6: fixture 验证读写 + 旧 session 兼容**

写一个临时 script `apps/worker/src/local/_scratch-phase-mark.ts`:

```ts
import { AuditSession } from '../audit/index.js';

async function main(): Promise<void> {
  const meta = { id: 'scratch-phase-test', repoPath: '/tmp/scratch-repo', outputPath: './workspaces' };
  const s = new AuditSession(meta);
  await s.initialize('scratch-phase-test');
  await s.markNonAgentPhaseComplete('findings-rendering');
  await s.markNonAgentPhaseComplete('findings-rendering'); // idempotent
  await s.markNonAgentPhaseComplete('translation');
  const metrics = (await s.getMetrics()) as { metrics: { completedNonAgentPhases: string[] } };
  console.log('phases:', metrics.metrics.completedNonAgentPhases);
  if (metrics.metrics.completedNonAgentPhases.length !== 2) {
    throw new Error('expected 2 unique phases');
  }
  console.log('OK');
}
main().catch((e) => {
  console.error(e);
  process.exit(1);
});
```

Run: `cd apps/worker && pnpm exec tsx src/local/_scratch-phase-mark.ts`
Expected: 打印 `phases: [ 'findings-rendering', 'translation' ]` 然后 `OK`。
验证后**删除** scratch 文件:`rm apps/worker/src/local/_scratch-phase-mark.ts`。

- [ ] **Step 7: commit**

```bash
git add apps/worker/src/audit/metrics-tracker.ts apps/worker/src/audit/audit-session.ts
git commit -m "feat(audit): persist non-agent phase completion to session.json"
```

---

## Task 2: activities.ts — export 复用的 git 函数

**Files:**
- Modify: `apps/worker/src/temporal/activities.ts`(`findLatestCommit` `:833`、`restoreGitCheckpoint` `:861-906`)

**Interfaces:**
- Produces:
  - `export async function findLatestCommit(gitDir: string, commitHashes: string[]): Promise<string>`(签名不变,仅加 export)
  - `export async function restoreGitCheckpoint(repoPath, checkpointHash, incompleteAgents, deliverablesSubdir?, logger?)` —— 新增**可选** `logger` 参数,默认 `createActivityLogger()`,向后兼容唯一调用点 `workflows.ts:294`

- [ ] **Step 1: `findLatestCommit` 加 export**

`activities.ts:833`,把:

```ts
async function findLatestCommit(gitDir: string, commitHashes: string[]): Promise<string> {
```

改为:

```ts
export async function findLatestCommit(gitDir: string, commitHashes: string[]): Promise<string> {
```

- [ ] **Step 2: `restoreGitCheckpoint` 加可选 logger 参数**

`activities.ts:861-868`。把签名和写死的 logger 改为参数注入(默认值保证 `workflows.ts:294` 不传 logger 时行为不变):

```ts
export async function restoreGitCheckpoint(
  repoPath: string,
  checkpointHash: string,
  incompleteAgents: AgentName[],
  deliverablesSubdir?: string,
  logger = createActivityLogger(),
): Promise<void> {
  const deliverablesPath = deliverablesDir(repoPath, deliverablesSubdir);
  logger.info(`Restoring deliverables to ${checkpointHash}...`);
```

(删掉原来 `:868` 的 `const logger = createActivityLogger();` 这一行,函数体其余 `logger.xxx` 调用全部不变。)

- [ ] **Step 3: type-check + lint,确认 temporal 调用点未破坏**

Run: `pnpm run check && pnpm biome`
Expected: PASS。`workflows.ts:294` 的 `a.restoreGitCheckpoint(...)` 不传 logger,走默认值,行为不变。

- [ ] **Step 4: commit**

```bash
git add apps/worker/src/temporal/activities.ts
git commit -m "refactor(activities): export git resume utils and inject logger"
```

---

## Task 3: 新建 `apps/worker/src/local/resume.ts`

**Files:**
- Create: `apps/worker/src/local/resume.ts`

**Interfaces:**
- Consumes:
  - `restoreGitCheckpoint(repoPath, checkpointHash, incompleteAgents, deliverablesSubdir?, logger?)` from `../temporal/activities.js`(Task 2)
  - `findLatestCommit(gitDir, commitHashes)` from `../temporal/activities.js`(Task 2)
  - `ALL_AGENTS` from `../types/agents.js`、`AGENTS` from `../session-manager.js`、`ALL_VULN_CLASSES` from `../types/config.js`
  - `deliverablesDir`、`WORKSPACES_DIR` from `../paths.js`
  - `readJson`、`fileExists` from `../utils/file-io.js`、`executeGitCommandWithRetry` from `../services/git-manager.js`
  - `PentestError` from `../services/error-handling.js`、`ErrorCode` from `../types/errors.js`
  - `ConsoleActivityLogger` from `./console-logger.js`(whitebox logger 类型)
- Produces:
  - `WHITEBOX_EXPECTED_AGENTS: readonly AgentName[]`(8 个)
  - `interface WhiteboxResumeState { completedAgents: string[]; completedNonAgentPhases: string[]; checkpointHash: string; originalWorkflowId: string }`
  - `function loadResumeState(workspace, repoPath, logger): Promise<WhiteboxResumeState | null>` —— `null` 表示 fresh(无成功 agent),非 null 表示可 resume;有 success 标记但 deliverable 全丢则 throw

- [ ] **Step 1: 创建文件,定义常量与类型**

```ts
// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Whitebox resume — load completed-agent state from an existing workspace so the
 * local runner can skip finished phases on `-w <workspace>` re-runs.
 *
 * Mirrors the temporal `loadResumeState` (activities.ts) but is Temporal-free:
 * throws PentestError instead of ApplicationFailure, skips URL validation
 * (whitebox is offline), and returns null when nothing completed (fresh run).
 */

import path from 'node:path';
import { AGENTS } from '../session-manager.js';
import { PentestError } from '../services/error-handling.js';
import { executeGitCommandWithRetry } from '../services/git-manager.js';
import { ErrorCode } from '../types/errors.js';
import { ALL_VULN_CLASSES } from '../types/config.js';
import type { AgentName } from '../types/agents.js';
import { ALL_AGENTS } from '../types/agents.js';
import { deliverablesDir } from '../paths.js';
import { fileExists, readJson } from '../utils/file-io.js';
import { findLatestCommit, restoreGitCheckpoint } from '../temporal/activities.js';
import type { ConsoleActivityLogger } from './console-logger.js';

/** Agents a whitebox run executes, in expected completion order. */
export const WHITEBOX_EXPECTED_AGENTS: readonly AgentName[] = [
  'pre-recon',
  'recon',
  ...ALL_VULN_CLASSES.map((cls) => `${cls}-vuln` as AgentName),
  'report',
];

/** Resume state loaded from a prior whitebox run's workspace. */
export interface WhiteboxResumeState {
  /** Agent names with status=success AND deliverable present on disk. */
  readonly completedAgents: string[];
  /** Non-agent phases previously marked complete (findings-rendering, report-assembly, translation). */
  readonly completedNonAgentPhases: string[];
  /** Git checkpoint hash to reset deliverables back to. */
  readonly checkpointHash: string;
  /** Workflow id of the original run that created the workspace. */
  readonly originalWorkflowId: string;
}
```

- [ ] **Step 2: 实现 `loadResumeState`**

在常量定义之后追加。逻辑对照 `activities.ts:686-795`,关键差异:(a) 不校验 URL;(b) 无 success agent → return null(fresh);(c) throw 用 `PentestError`。

```ts
/**
 * Load whitebox resume state from a workspace's session.json.
 *
 * Returns null for a fresh workspace (no agent succeeded yet) so the caller runs
 * the full pipeline. Throws if the workspace is corrupted (success recorded but
 * no recoverable checkpoint) — that needs human intervention, not a silent re-run.
 *
 * @param workspace - Workspace name (= session id).
 * @param repoPath - Target repo path (deliverables live under <repoPath>/deliverables).
 * @param logger - Whitebox console logger.
 */
export async function loadResumeState(
  workspace: string,
  repoPath: string,
  logger: ConsoleActivityLogger,
): Promise<WhiteboxResumeState | null> {
  // 1. Read session.json
  const sessionPath = path.join('./workspaces', workspace, 'session.json');
  if (!(await fileExists(sessionPath))) {
    return null;
  }

  let session: {
    session: { originalWorkflowId?: string; id: string };
    metrics: {
      agents: Record<string, { status: string; checkpoint?: string }>;
      completedNonAgentPhases?: string[];
    };
  };
  try {
    session = await readJson(sessionPath);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new PentestError(
      `Corrupted session.json in workspace ${workspace}: ${msg}`,
      'validation',
      false,
      { workspace, phase: 'resume' },
      ErrorCode.CONFIG_PARSE_ERROR,
    );
  }

  // 2. Cross-check success agents against deliverables on disk
  const completedAgents: string[] = [];
  const agents = session.metrics.agents;

  for (const agentName of ALL_AGENTS) {
    const agentData = agents[agentName];
    if (!agentData || agentData.status !== 'success') {
      continue;
    }

    const deliverablePath = path.join(deliverablesDir(repoPath), AGENTS[agentName].deliverableFilename);
    if (!(await fileExists(deliverablePath))) {
      logger.warn(`Agent ${agentName} shows success but deliverable missing, will re-run`);
      continue;
    }
    completedAgents.push(agentName);
  }

  // 3. Fresh run — nothing succeeded
  if (completedAgents.length === 0) {
    return null;
  }

  // 4. Collect checkpoints
  const checkpoints = completedAgents
    .map((name) => agents[name]?.checkpoint)
    .filter((hash): hash is string => hash != null);

  if (checkpoints.length === 0) {
    throw new PentestError(
      `Cannot resume workspace ${workspace}: ${completedAgents.length} agent(s) show success ` +
        `(${completedAgents.join(', ')}) but their deliverable checkpoints are missing. ` +
        `Start a fresh run instead.`,
      'validation',
      false,
      { workspace, phase: 'resume' },
      ErrorCode.GIT_CHECKPOINT_FAILED,
    );
  }

  // 5. Resolve latest checkpoint commit (fall back to first hash if no git history)
  const deliverablesPath = deliverablesDir(repoPath);
  let checkpointHash: string;
  try {
    checkpointHash = await findLatestCommit(deliverablesPath, checkpoints);
  } catch {
    checkpointHash = checkpoints[0] ?? '';
  }

  logger.info('Resume state loaded', {
    workspace,
    completedAgents: completedAgents.length,
    checkpoint: checkpointHash,
  });

  return {
    completedAgents,
    completedNonAgentPhases: session.metrics.completedNonAgentPhases ?? [],
    checkpointHash,
    originalWorkflowId: session.session.originalWorkflowId || session.session.id,
  };
}

/** Re-export so runner imports resume helpers from one place. */
export { restoreGitCheckpoint, executeGitCommandWithRetry };
```

- [ ] **Step 3: 确认 ErrorCode 已对齐(无需操作)**

`SESSION_LOAD_FAILED` 不存在(`types/errors.ts` 无此成员,仅有 `CONFIG_PARSE_ERROR`、`GIT_CHECKPOINT_FAILED` 等)。Step 2 已改用现有码:corrupted session.json → `ErrorCode.CONFIG_PARSE_ERROR`;checkpoint 丢失 → `ErrorCode.GIT_CHECKPOINT_FAILED`。两者均为 `types/errors.ts` 中已定义的成员。

- [ ] **Step 4: type-check + lint**

Run: `pnpm run check && pnpm biome`
Expected: PASS。`exactOptionalPropertyTypes` 下 `completedNonAgentPhases?: string[]` 的读取用 `?? []` 已处理。先 `pnpm biome:fix` 修格式。

- [ ] **Step 5: fixture 验证四个分支**

临时 script `apps/worker/src/local/_scratch-resume.ts`,构造不同 session.json 验证:

```ts
import fs from 'node:fs';
import path from 'node:path';
import { loadResumeState } from './resume.js';
import { ConsoleActivityLogger } from './console-logger.js';

async function setup(workspace: string, agents: object): Promise<void> {
  const dir = path.join('./workspaces', workspace);
  await fs.promises.mkdir(dir, { recursive: true });
  await fs.promises.writeFile(
    path.join(dir, 'session.json'),
    JSON.stringify({ session: { id: workspace, originalWorkflowId: workspace }, metrics: { agents, phases: {} } }),
  );
}

async function expectNull(label: string, workspace: string): Promise<void> {
  const rs = await loadResumeState(workspace, '/tmp/scratch-repo', new ConsoleActivityLogger());
  console.log(label, '->', rs === null ? 'null (fresh)' : 'RESUME');
  if (rs !== null) throw new Error(`${label}: expected null`);
}

async function main(): Promise<void> {
  // Branch A: no agents → null
  await setup('scratch-resume-empty', {});
  await expectNull('empty workspace', 'scratch-resume-empty');

  // Branch B: only failed agent → null
  await setup('scratch-resume-failed', { recon: { status: 'failed' } });
  await expectNull('only-failed workspace', 'scratch-resume-failed');

  // Branch C: success agent but deliverable missing → null (re-run) — deliverable file does not exist
  await setup('scratch-resume-missing', { 'pre-recon': { status: 'success', checkpoint: 'deadbeef' } });
  await expectNull('success-but-missing-deliverable', 'scratch-resume-missing');

  console.log('OK (null branches)');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
```

Run: `cd apps/worker && pnpm exec tsx src/local/_scratch-resume.ts`
Expected: 三行 `-> null (fresh)`,最后 `OK (null branches)`。(第四个分支"success + deliverable 存在 → 返回 ResumeState"留到 Task 4 端到端验证,因为需要真实 git checkpoint。)
清理:`rm -rf ./workspaces/scratch-resume-* apps/worker/src/local/_scratch-resume.ts`。

- [ ] **Step 6: commit**

```bash
git add apps/worker/src/local/resume.ts
git commit -m "feat(local): add whitebox resume state loader"
```

---

## Task 4: runner.ts 接入 resume

**Files:**
- Modify: `apps/worker/src/local/runner.ts`(import `:1-17`、`run()` 的步骤 5/6 之间 `:331-333`、Phase 1 `:347-364`、Phase 2 `:366-383`、Phase 3 `:385-412`、Phase 4 `:414-422`、Phase 5 `:423-434`、Phase 6 `:437-461`、Phase 7 `:463-484`)

**Interfaces:**
- Consumes: `loadResumeState`、`WHITEBOX_EXPECTED_AGENTS`、`WhiteboxResumeState`、`restoreGitCheckpoint` from `./resume.js`(Task 3);`markNonAgentPhaseComplete` from `../audit/index.js`(Task 1)
- Produces: runner 在 `-w <旧workspace>` 重跑时跳过已完成阶段

- [ ] **Step 1: 加 import**

在 `runner.ts` 现有 import 块(`:1-17`)末尾追加:

```ts
import { loadResumeState, WHITEBOX_EXPECTED_AGENTS, type WhiteboxResumeState } from './resume.js';
```

- [ ] **Step 2: 在 `run()` 里 sync deny rules 之后、pipeline 之前插入 resume 段**

定位 `:329-336`:

```ts
  // 5. Sync code_path deny rules
  logger.info('Syncing code_path deny rules...');
  await syncCodePathDenyRules(args, configLoader, logger);

  // 6. Execute pipeline phases
  const pipelineStart = Date.now();
  const results: AgentResult[] = [];
  let aborted = false;
```

在「Sync code_path deny rules」之后、「Execute pipeline phases」之前插入 resume 段。改为:

```ts
  // 5. Sync code_path deny rules
  logger.info('Syncing code_path deny rules...');
  await syncCodePathDenyRules(args, configLoader, logger);

  // 5.5 Resume detection — skip already-completed agents/phases on `-w <existing-workspace>` re-runs
  const resumeState = await loadResumeState(workspaceName, args.repoPath, logger);
  let shouldSkip = (_name: string): boolean => false;
  let nonAgentDone: ReadonlySet<string> = new Set();

  if (resumeState) {
    logger.info(`Resuming: ${resumeState.completedAgents.length} agent(s) already complete`);
    const incomplete = WHITEBOX_EXPECTED_AGENTS.filter((n) => !resumeState.completedAgents.includes(n));
    await restoreGitCheckpoint(args.repoPath, resumeState.checkpointHash, incomplete, undefined, logger);
    await auditSession.addResumeAttempt(sessionId, [], resumeState.checkpointHash);
    await auditSession.logResumeHeader({
      previousWorkflowId: resumeState.originalWorkflowId,
      newWorkflowId: sessionId,
      checkpointHash: resumeState.checkpointHash,
      completedAgents: resumeState.completedAgents,
    });
    shouldSkip = (name: string): boolean => resumeState.completedAgents.includes(name);
    nonAgentDone = new Set(resumeState.completedNonAgentPhases);

    // All-done short-circuit
    const allAgentsDone = WHITEBOX_EXPECTED_AGENTS.every((n) => resumeState.completedAgents.includes(n));
    const allPhasesDone =
      nonAgentDone.has('findings-rendering') && nonAgentDone.has('report-assembly') && nonAgentDone.has('translation');
    if (allAgentsDone && allPhasesDone) {
      logger.info('All expected agents and phases already complete. Nothing to resume.');
      await auditSession.updateSessionStatus('completed');
      console.log('');
      console.log('=== Pipeline Complete (resumed, nothing to do) ===');
      console.log(`  Workspace:    ${path.join(WORKSPACES_DIR, sessionId)}`);
      console.log('');
      process.exit(0);
    }
  }

  // 6. Execute pipeline phases
  const pipelineStart = Date.now();
  const results: AgentResult[] = [];
  let aborted = false;
```

- [ ] **Step 3: Phase 1 pre-recon 加闸门**

`:347-364`,把:

```ts
    // Phase 1: Pre-recon
    if (!aborted) {
```

改为:

```ts
    // Phase 1: Pre-recon
    if (!aborted && !shouldSkip('pre-recon')) {
```

- [ ] **Step 4: Phase 2 recon 加闸门**

`:366-367`,把:

```ts
    // Phase 2: Recon (static)
    if (!aborted) {
```

改为:

```ts
    // Phase 2: Recon (static)
    if (!aborted && !shouldSkip('recon')) {
```

- [ ] **Step 5: Phase 3 vuln — filter 掉已完成,不占并发槽**

`:386-412`。把整个 Phase 3 块:

```ts
    // Phase 3: Vulnerability analysis (bounded parallel)
    if (!aborted) {
      logger.info(`=== Phase 3: Vulnerability Analysis (concurrency=${args.concurrency}) ===`);
      const semaphore = new Semaphore(args.concurrency);

      const vulnPromises = WHITEBOX_VULN_AGENTS.map((agentName) =>
        semaphore.with(async () => {
          if (aborted) {
            return { agentName, success: false, attempts: 0, durationMs: 0, costUsd: 0, error: 'Aborted' };
          }
          // Each vuln agent needs its own AuditSession for parallel safety
          const vulnAuditSession = new AuditSession(sessionMetadata);
          await vulnAuditSession.initialize(sessionId);
          return runAgentWithRetry(
            agentName,
            args,
            vulnAuditSession,
            logger,
            configLoader,
            deliverablesPath,
            distributedConfig,
          );
        }),
      );

      const vulnResults = await Promise.all(vulnPromises);
      results.push(...vulnResults);
    }
```

改为(在 map 前 filter,已完成的当 success 结果直接返回,不进 semaphore):

```ts
    // Phase 3: Vulnerability analysis (bounded parallel)
    if (!aborted) {
      logger.info(`=== Phase 3: Vulnerability Analysis (concurrency=${args.concurrency}) ===`);
      const semaphore = new Semaphore(args.concurrency);

      const vulnPromises = WHITEBOX_VULN_AGENTS.map((agentName) => {
        if (shouldSkip(agentName)) {
          return Promise.resolve({
            agentName,
            success: true,
            attempts: 0,
            durationMs: 0,
            costUsd: 0,
          });
        }
        return semaphore.with(async () => {
          if (aborted) {
            return { agentName, success: false, attempts: 0, durationMs: 0, costUsd: 0, error: 'Aborted' };
          }
          // Each vuln agent needs its own AuditSession for parallel safety
          const vulnAuditSession = new AuditSession(sessionMetadata);
          await vulnAuditSession.initialize(sessionId);
          return runAgentWithRetry(
            agentName,
            args,
            vulnAuditSession,
            logger,
            configLoader,
            deliverablesPath,
            distributedConfig,
          );
        });
      });

      const vulnResults = await Promise.all(vulnPromises);
      results.push(...vulnResults);
    }
```

- [ ] **Step 6: Phase 4 findings-rendering 加闸门 + 标记**

`:414-422`,把:

```ts
    // Phase 4: Findings rendering (no exploit agents in whitebox mode)
    if (!aborted) {
      logger.info('=== Phase 4: Findings Rendering ===');
      try {
        await renderFindingsFromQueues(args.repoPath, undefined, logger);
      } catch (error) {
        logger.warn(`Findings rendering had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
```

改为:

```ts
    // Phase 4: Findings rendering (no exploit agents in whitebox mode)
    if (!aborted && !nonAgentDone.has('findings-rendering')) {
      logger.info('=== Phase 4: Findings Rendering ===');
      try {
        await renderFindingsFromQueues(args.repoPath, undefined, logger);
        await auditSession.markNonAgentPhaseComplete('findings-rendering');
      } catch (error) {
        logger.warn(`Findings rendering had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
```

- [ ] **Step 7: Phase 5 report-assembly 加闸门 + 标记**

`:423-434` 是 `assembleFinalReport` + `injectModelIntoReport`(在同一个 `if (!aborted)` 块,紧接 Phase 4)。把:

```ts
      logger.info('=== Phase 5: Report Assembly ===');
      try {
        await assembleFinalReport(args.repoPath, undefined, logger);
      } catch (error) {
        logger.warn(`Report assembly had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
```

改为:

```ts
      logger.info('=== Phase 5: Report Assembly ===');
      if (!nonAgentDone.has('report-assembly')) {
        try {
          await assembleFinalReport(args.repoPath, undefined, logger);
          await auditSession.markNonAgentPhaseComplete('report-assembly');
        } catch (error) {
          logger.warn(`Report assembly had issues: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
```

(注意:`assembleFinalReport` 与 Phase 4 在同一个外层 `if` 块内,缩进多一层;`injectModelIntoReport` 那个 try 块紧跟其后,**保持不变**,无需闸门——它是廉价的 model 信息注入。)

- [ ] **Step 8: Phase 6 report agent 加闸门**

`:437-438`,把:

```ts
    // Phase 6: Report agent (executive summary + final report)
    if (!aborted) {
```

改为:

```ts
    // Phase 6: Report agent (executive summary + final report)
    if (!aborted && !shouldSkip('report')) {
```

- [ ] **Step 9: Phase 7 translation 加闸门 + 标记**

`:463-484`,把外层条件:

```ts
    // Phase 7: Translation (Chinese deliverables)
    if (!aborted) {
      logger.info('=== Phase 7: Translation ===');
      try {
        const provider = new ReportTranslationProvider();
        const translationResult = await provider.generate(
```

改为:

```ts
    // Phase 7: Translation (Chinese deliverables)
    if (!aborted && !nonAgentDone.has('translation')) {
      logger.info('=== Phase 7: Translation ===');
      try {
        const provider = new ReportTranslationProvider();
        const translationResult = await provider.generate(
```

并在该 try 块末尾(`if (translationResult.outputPath) { logger.info(...) }` 之后、`} catch` 之前)追加标记:

```ts
        await auditSession.markNonAgentPhaseComplete('translation');
```

- [ ] **Step 10: type-check + lint**

Run: `pnpm run check && pnpm biome`
Expected: PASS。先 `pnpm biome:fix`。

- [ ] **Step 11: 端到端验证 — 用真实 workspace resume**

用现成的 `render-sg_whitebox-1782285742007`(pre-recon success、recon failed、deliverables 空)验证 resume 跳过 pre-recon、从 recon 重跑。

Run: `./shannon start -r /root/code/backend/render-sg/ -w render-sg_whitebox-1782285742007 --pipeline-testing`
Expected: 日志出现 `Resuming: 1 agent(s) already complete`,pre-recon 阶段**不再出现** `=== Phase 1: Pre-recon ===`,直接进入 `=== Phase 2: Static Recon ===`;deliverables 目录被 `restoreGitCheckpoint` 恢复出 pre-recon 的 deliverable 文件。

⚠️ 若额度未恢复,recon 会再次失败——这符合预期(验证的是 resume 跳过 pre-recon,不是 recon 跑通)。只要看到 pre-recon 被跳过即可判定 Task 4 成功。

- [ ] **Step 12: commit**

```bash
git add apps/worker/src/local/runner.ts
git commit -m "feat(local): resume completed agents and phases in whitebox runner"
```

---

## Task 5: CLI resume 提示

**Files:**
- Modify: `apps/cli/src/commands/local-start.ts`(workspace 命名处 `:91` 附近;`isResume` 判定参照 `start.ts:153-156`)

**Interfaces:**
- Consumes: `fs.existsSync`、workspace 路径
- Produces: resume 时打印一行提示

- [ ] **Step 1: 在 workspace 确定后加提示**

先读 `local-start.ts:85-95` 确认 workspace 变量名与 session.json 路径构造方式。在 workspace 名确定后,检测 `<workspaces>/<workspace>/session.json` 是否存在,是则打印提示:

```ts
    const sessionJsonPath = path.join(workspacesDir, workspace, 'session.json');
    if (fs.existsSync(sessionJsonPath)) {
      console.log('  (Workspace exists — resuming completed phases)');
    }
```

(用 `readFileSync`/`existsSync` 与 `start.ts:153-156` 一致;`workspacesDir`/`workspace` 变量名以实际文件为准。)

- [ ] **Step 2: type-check + lint**

Run: `pnpm run check && pnpm biome`
Expected: PASS。

- [ ] **Step 3: 手动验证**

Run: `./shannon start -r /root/code/backend/render-sg/ -w render-sg_whitebox-1782285742007` (Ctrl-C 即可,只看启动横幅)
Expected: 横幅后出现 `(Workspace exists — resuming completed phases)`。

- [ ] **Step 4: commit**

```bash
git add apps/cli/src/commands/local-start.ts
git commit -m "feat(cli): indicate resume when whitebox workspace exists"
```

---

## Self-Review(plan 作者自查记录)

- **Spec 覆盖**:spec 第 4 节架构 → Task 1-5;第 5 节数据流 → Task 4 Step 2;第 6 节决策(触发方式/无 agent 标记/全部完成短路/并发 vuln/不做 scope 锁定)→ Task 3(loadResumeState null=fresh)+ Task 4 Step 2/5/9;第 7 节错误处理(corrupted→throw PentestError / 无 checkpoint→throw / deliverable 缺失→重跑)→ Task 3 Step 2;第 8 节验证策略 → 每个 task 的 check+biome+fixture+端到端。✓
- **占位符扫描**:无 TBD/TODO;每个 code step 含完整代码;每个验证 step 含确切命令与期望输出。✓
- **类型一致性**:`loadResumeState` 返回 `WhiteboxResumeState | null`(Task 3 定义,Task 4 Step 2 消费);`markNonAgentPhaseComplete(name: string)`(Task 1 定义,Task 4 Step 6/7/9 消费);`restoreGitCheckpoint(..., logger?)`(Task 2 定义,Task 3 re-export、Task 4 Step 2 消费);`WHITEBOX_EXPECTED_AGENTS`(Task 3 定义,Task 4 Step 2/5 消费)。命名全程一致。✓
- **`ErrorCode` 引用**:已确认 `types/errors.ts` 无 `SESSION_LOAD_FAILED`;Step 2 改用 `CONFIG_PARSE_ERROR`(corrupted)与 `GIT_CHECKPOINT_FAILED`(无 checkpoint),均为现有成员,无 undefined reference。
