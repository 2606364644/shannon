# Whitebox Local Runner 断点续扫(Resume)设计

- **日期**:2026-06-24
- **状态**:已确认,待实现
- **范围**:`apps/worker/src/local/runner.ts`(whitebox 本地扫描)
- **实现策略**:A — runner 内自实现 + 复用纯函数(不抽共享层,不碰 temporal/)

## 1. 背景与动机

Shannon 的 blackbox/temporal 模式已支持断点续扫:中断后用相同 workspace 重跑,跳过已成功的 agent,从第一个未完成阶段继续。实现于 `apps/worker/src/temporal/workflows.ts`(input.resumeFromWorkspace → shouldSkip)与 `apps/worker/src/temporal/activities.ts`(`loadResumeState`/`restoreGitCheckpoint`/`recordResumeAttempt`)。

whitebox 本地扫描(`apps/worker/src/local/runner.ts`)**没有 resume 能力**。它从 Phase 1 pre-recon 到 Phase 7 translation 顺序全跑,没有任何跳过已完成阶段的逻辑。任何中断(进程崩溃、Ctrl-C、模型额度耗尽导致 agent 失败)后重跑,都必须从头开始。

这对 whitebox 模式尤其浪费:whitebox 的 recon 阶段是静态代码分析,单个 agent 常跑 1 小时以上(实测 render-sg 项目 recon 跑了 61 分钟后因额度不足失败)。中断后从头重跑 pre-recon + recon 的成本很高。

## 2. 目标与非目标

**目标**

- whitebox runner 支持 resume:用 `-w <旧workspace>` 重跑时,跳过已成功的 agent,从第一个未完成阶段继续
- 无 agent 阶段(findings-rendering、report-assembly、translation)若上次已完成则跳过
- 全部完成时短路收尾
- 复用现有共享组件,改动集中、风险隔离

**非目标**

- 不做 scope 锁定(whitebox vuln 范围固定,config 变更场景少)
- 不抽 temporal/whitebox 共享 resume 核心层(两端语义不同,过度抽象,违背 CLAUDE.md 风格)
- 不引入测试框架(项目当前无测试约定)
- 不改动 `agent-execution.ts` 或 temporal workflow 逻辑

## 3. 可行性基础(为什么能做)

关键洞察:whitebox 和 blackbox 共用同一个 `AgentExecutionService.execute()`(`apps/worker/src/services/agent-execution.ts`)。该函数在每个 agent 成功时:

- 向 deliverables git 仓库提交 success commit(`commitGitSuccess`,`agent-execution.ts:243`)
- 读取 git HEAD 作为 checkpoint hash(`getGitCommitHash`,`agent-execution.ts:244`)
- 通过 `auditSession.endAgent(agentName, {success:true, checkpoint})` 把 status=success + checkpoint 写入 session.json(`agent-execution.ts:254` → `apps/worker/src/audit/metrics-tracker.ts:204-215,229` 落盘)

因此 blackbox resume 依赖的"已完成 agent"数据,**whitebox 模式下已经在磁盘上**。whitebox runner 只是从来没去读它。

**resume 相关函数的复用分级:**

| 函数 | 位置 | Temporal 耦合 | 复用方式 |
|---|---|---|---|
| `restoreGitCheckpoint` | `activities.ts:861` | 零(纯 git,仅 logger 写死) | 改 logger 参数 + import 复用 |
| `findLatestCommit` | `activities.ts:833` | 零 | 加 export + import 复用 |
| `loadResumeState` | `activities.ts:686` | 4 处 `ApplicationFailure` + logger | 借鉴逻辑,whitebox 版重写于 `local/resume.ts` |
| `recordResumeAttempt` | `activities.ts:911` | 签名带 `ActivityInput` | 不复用,改用 `AuditSession.addResumeAttempt` |

`AuditSession`(`apps/worker/src/audit/audit-session.ts`)本就是 temporal + local 共享层,已提供 `addResumeAttempt`(:258)、`logResumeHeader`(:274)、`getMetrics`(:211)。

## 4. 架构

### 4.1 新建/改动文件

**新建 `apps/worker/src/local/resume.ts`** — whitebox resume 核心

- `loadResumeState(workspace, repoPath, logger): Promise<ResumeState>` — 借鉴 `activities.ts:686` 逻辑,剥掉 `ApplicationFailure`(换成 throw `Error`/`PentestError`),去掉 webUrl 校验(whitebox 恒 undefined)
- `WHITEBOX_EXPECTED_AGENTS` 常量:`['pre-recon', 'recon', ...WHITEBOX_VULN_AGENTS, 'report']`(共 8 个)
- import 复用 `restoreGitCheckpoint`、`findLatestCommit`

**`apps/worker/src/temporal/activities.ts`** — 2 处最小改动

- `findLatestCommit`(:833)加 `export`
- `restoreGitCheckpoint`(:861)的 `createActivityLogger()`(:868)改成接收注入的 logger 参数;temporal 调用点传 `createActivityLogger()` 保持原行为
- 其余 resume 函数(`loadResumeState`/`recordResumeAttempt`/`persistOrValidateRunScope`)留给 temporal 专用,不动

**`apps/worker/src/audit/`(`metrics-tracker.ts` + `audit-session.ts`)** — 新增无 agent 阶段标记

- `SessionData.metrics` 加 `completedNonAgentPhases: string[]`(`metrics-tracker.ts:66-71` 区域),`initialize` 时默认 `[]`
- `MetricsTracker` 加 `markNonAgentPhase(name)`
- `AuditSession` 加 `markNonAgentPhaseComplete(name)`,沿用现有 mutex(`audit-session.ts:185` reload-then-write)+ atomicWrite 落盘

**`apps/worker/src/local/runner.ts`** — 主改造(见第 5 节数据流)

**`apps/cli/src/commands/local-start.ts`** — 轻量:检测到 resume 时打印 "Workspace exists, resuming..."

### 4.2 复用关系

```
runner.ts ──┬─> local/resume.ts ──> restoreGitCheckpoint  (activities.ts, logger 参数化)
           │                      └─> findLatestCommit     (activities.ts, export)
           ├─> AuditSession.addResumeAttempt / logResumeHeader / getMetrics / markNonAgentPhaseComplete
           └─> AgentExecutionService  (不动,已写 session.json + checkpoint)
```

## 5. 数据流(runner.ts `run()` 改造)

在现有 session 初始化(`auditSession.initialize`)之后、`initDeliverableGit` 之前,插入 resume 段:

```ts
const metrics = await auditSession.getMetrics();
const isResume = Object.values(metrics.agents).some((a) => a.status === 'success');
let shouldSkip = (_name: string) => false;

if (isResume) {
  const rs = await loadResumeState(workspace, repoPath, logger);
  const incomplete = WHITEBOX_EXPECTED_AGENTS.filter((n) => !rs.completedAgents.includes(n));
  await restoreGitCheckpoint(repoPath, rs.checkpointHash, incomplete, undefined, logger);
  await auditSession.addResumeAttempt(sessionId, [], rs.checkpointHash);
  await auditSession.logResumeHeader({
    previousWorkflowId: rs.originalWorkflowId,
    newWorkflowId: sessionId,
    checkpointHash: rs.checkpointHash,
    completedAgents: rs.completedAgents,
  });
  shouldSkip = (name) => rs.completedAgents.includes(name);

  // 全部完成短路
  const nonAgent = metrics.completedNonAgentPhases ?? [];
  const allDone =
    WHITEBOX_EXPECTED_AGENTS.every((n) => rs.completedAgents.includes(n)) &&
    nonAgent.includes('findings-rendering') &&
    nonAgent.includes('report-assembly') &&
    nonAgent.includes('translation');
  if (allDone) {
    await auditSession.updateSessionStatus('completed');
    // 打印 summary 后 return
  }
}

// 之后:initDeliverableGit(幂等)→ syncCodePathDenyRules → 各 Phase 加闸门
```

**各 Phase 闸门:**

| Phase | 闸门逻辑 |
|---|---|
| 1 pre-recon | `if (!aborted && !shouldSkip('pre-recon'))` |
| 2 recon | `if (!aborted && !shouldSkip('recon'))` |
| 3 vuln(并发) | `WHITEBOX_VULN_AGENTS.filter((n) => !shouldSkip(n))` 后再进 Semaphore —— 已完成的**不占并发槽** |
| 4 findings-rendering | `if (!aborted && !nonAgent.includes('findings-rendering'))`;成功后 `markNonAgentPhaseComplete('findings-rendering')` |
| 5 report-assembly | `if (!aborted && !nonAgent.includes('report-assembly'))`;成功后 `markNonAgentPhaseComplete('report-assembly')`(`assembleFinalReport` + `injectModel`) |
| 6 report | `if (!aborted && !shouldSkip('report'))` |
| 7 translation | 同 Phase 4 模式,标记 `'translation'` |

## 6. 关键设计决策

1. **触发方式**:自动检测,无 `--resume` flag。session.json 有 success agent → resume;全新 workspace agents 为空 → fresh。复用 blackbox 语义,用户 `-w <旧workspace>` 即触发。
2. **无 agent 阶段标记**:`SessionData.metrics.completedNonAgentPhases: string[]`,落盘 session.json。findings-rendering / report-assembly / translation 成功后 append,resume 时检查跳过。
3. **全部完成短路**:期望 = 8 agents + 3 non-agent phases(findings-rendering、report-assembly、translation)全 done → 跳过所有 Phase 直接收尾。
4. **并发 vuln resume**:已 success 的 vuln agent 在进 `Semaphore.with` 前 filter 掉,既跳过又不占并发槽。
5. **不做 scope 锁定**:whitebox vuln 范围固定(`ALL_VULN_CLASSES`,5 个,无 exploit),config 变更场景少,YAGNI。

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| session.json 损坏 / 解析失败 | `loadResumeState` throw → runner 捕获,报错 + `process.exit(1)`,提示删 session.json 重来或换 workspace |
| 无可恢复 checkpoint(所有 success agent 的 deliverable 都丢) | throw → 报错 "无可恢复的 checkpoint,建议 fresh run"(不静默从头跑,避免在脏 workspace 上浪费) |
| `restoreGitCheckpoint` 失败 | `executeGitCommandWithRetry` 已内置重试;彻底失败 throw → 报错退出,用户可手动 git 或 fresh |
| 并发写 session.json(Phase3 5 个 vuln) | 现有 mutex(`audit-session.ts:185`)保护;`markNonAgentPhaseComplete` 走同一 mutex |
| agents 全是 failed / in-progress | `isResume=false` → 当 fresh 从头跑(无成功成果可复用) |
| `-w` 指向不存在的 workspace | `local-start.ts` 会 mkdir + 新建空 session → `isResume=false` → fresh(不会误 resume 到无关 workspace) |
| resume 时部分 deliverable 缺失 | `loadResumeState` 内建交叉校验:success 但 deliverable 丢 → 不计入 completedAgents → 自动重跑该 agent |

## 8. 验证策略

项目当前**无测试框架**(0 个 test 文件、无 test script、仅 `jest-worker` 为构建用)。采用务实验证,不引入测试框架:

1. **类型安全**:`pnpm run check`(tsc 全量 type-check)
2. **lint / 格式**:`pnpm biome` + `pnpm biome:fix`
3. **端到端 resume**:`--pipeline-testing`(最小 prompt + 10s retry)构造断点场景,验证 pre-recon 跳过、recon 重跑、全部完成短路、并发 vuln 跳过
4. **fixture 验证**:手动构造 `session.json`(pre-recon success + checkpoint hash)+ deliverables git 历史,直接验证 `loadResumeState` / `restoreGitCheckpoint` 各分支(corrupted、无 checkpoint、deliverable 缺失),不必跑完整 pipeline
5. **真实场景验证**:用 `render-sg_whitebox-1782285742007` workspace(pre-recon success、recon failed、deliverables 空)做端到端 resume 测试 —— 这是现成的真实 fixture

## 9. 改动文件清单

| 文件 | 改动 |
|---|---|
| `apps/worker/src/local/resume.ts` | 新建,whitebox resume 核心 |
| `apps/worker/src/local/runner.ts` | `run()` 插入 resume 段 + 各 Phase 闸门 + 无 agent 阶段标记 |
| `apps/worker/src/temporal/activities.ts` | `findLatestCommit` export + `restoreGitCheckpoint` logger 参数化 |
| `apps/worker/src/audit/metrics-tracker.ts` | `SessionData` 加 `completedNonAgentPhases` + `markNonAgentPhase` |
| `apps/worker/src/audit/audit-session.ts` | `markNonAgentPhaseComplete` 包装 |
| `apps/cli/src/commands/local-start.ts` | resume 提示 |

## 10. 风险

1. **deliverables 目录"为空"≠无 checkpoint**:recon 失败后 `failAgent` 的 `rollbackGitWorkspace`(`git reset --hard` + `clean -fd`)清空工作树,但 git 历史(pre-recon 的 success commit)仍在。`restoreGitCheckpoint` 的 reset 会恢复 pre-recon 的 deliverable 文件。需在用户文档说明,避免误以为数据丢失。
2. **并发 vuln agent 的 git checkpoint**:5 个 vuln agent 并发向同一 deliverables git 仓库 commit。`executeGitCommandWithRetry` 有 git lock 重试,且各 agent deliverable 文件名不冲突(`AGENTS[agentName].deliverableFilename` 各异)。风险低,建议压测一次 5 并发 resume。
3. **translation / findings-rendering 幂等性**:这两个阶段被标记跳过的前提是重跑幂等。需确认 `renderFindingsFromQueues` 从 queue 文件重新生成 findings.md、`ReportTranslationProvider` 重新翻译,均覆盖旧产物而非追加。
