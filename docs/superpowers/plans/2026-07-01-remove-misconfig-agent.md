# Remove misconfig Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从代码库彻底移除自实现的 `misconfig` agent（`vuln-misconfig` + `misconfig-exploit`），让仓库回到官方 5 类（injection/xss/auth/authz/ssrf）状态，并同步项目文档与样本报告。

**Architecture:** 机械式、数据驱动删除。misconfig 在 service/workflow 层均以 record / 数组条目注册，无特殊分支逻辑。TypeScript 类型系统（`Record<AgentName/VulnType/VulnClass, ...>`）是主验证手段——类型收窄后,编译器会抓出所有残留引用。任务按两条类型依赖链（AgentName 链、VulnType/VulnClass 链）划分,每条链内的删除原子化,保证每个任务结束 `pnpm run check` 通过。

**Tech Stack:** TypeScript（`exactOptionalPropertyTypes` 开启）、Zod（队列 schema）、pnpm workspaces + Turborepo、Biome（lint/format）、Temporal（workflow/activity）。

**Spec:** `docs/superpowers/specs/2026-07-01-remove-misconfig-agent-design.md`

## Global Constraints

- **提交策略:** 本仓库 `CLAUDE.md` 规定 "Commit or push only when the user asks"。计划含 commit step 作为预设;若用户未授权提交,执行时跳过 commit step 并在任务末尾说明。
- **提交到分支:** 当前分支 `feat/fork`,所有 commit 落在此分支,不新建分支(除非用户要求)。
- **代码风格:** Biome — 单引号、分号、尾逗号、2 空格、120 列。改完跑 `pnpm biome:fix`。
- **必须保留的 false positive(任何任务都不得触碰):**
  - `apps/worker/prompts/vuln-auth.txt:135` 的 `session_cookie_misconfig`(auth 分类标签)
  - `COVERAGE.md:52` WSTG-CONF-14(OWASP 标准项)
  - `docs/shannon-pro.md:55` 与 `llms-full.txt:979` 的 "misconfigurations"(通用词)
  - `openspec/changes/archive/2026-05-26-add-misconfig-agent/`、`openspec/changes/local-whitebox-runner/`、`docs/superpowers/specs/*`、`docs/superpowers/plans/*`(历史档案)
- **不手动编辑** `llms-full.txt`(生成文件)。

## 验证策略(对 TDD 的适配)

本计划是**删除无测试覆盖的代码**,无可观察行为可写失败测试。验证等价物:

1. **Baseline:** Task 1 先跑 `pnpm run check` 确认起点干净,避免把 pre-existing 错误误判为本计划引入。
2. **类型检查即测试:** 每个核心任务结束跑 `pnpm run check`。`Record<AgentName/VulnType/VulnClass, ...>` 收窄后,任何漏删的 misconfig 引用都会触发编译错误——这是抓遗漏的主机制。
3. **验收 grep(Task 7):** `rg -ni misconfig` 的命中应**仅限**上面列出的保留项,这是可断言的验收准则。

---

### Task 1: Baseline 与删除 prompt 文件

**Files:**
- Delete: `apps/worker/prompts/vuln-misconfig.txt`
- Delete: `apps/worker/prompts/exploit-misconfig.txt`

**Interfaces:** 无(纯文件删除,prompt 按字符串名运行时加载,不影响 TS 编译)。

- [ ] **Step 1: 确认 baseline 编译通过**

Run: `pnpm run check`
Expected: 全包类型检查通过,无错误。若有 pre-existing 错误,记录下来,后续任务的 check 以"不新增错误"为准则。

- [ ] **Step 2: 删除两个 prompt 文件**

```bash
rm apps/worker/prompts/vuln-misconfig.txt apps/worker/prompts/exploit-misconfig.txt
```

- [ ] **Step 3: 确认编译仍通过**

Run: `pnpm run check`
Expected: 通过(prompt 文件不被 TS 编译)。

- [ ] **Step 4: Commit**

```bash
git add -A apps/worker/prompts/
git commit -m "refactor(worker): remove misconfig vuln/exploit prompt templates"
```

---

### Task 2: AgentName 链收窄 + pentest vuln 注册 + 白盒注释修正

> 本任务删除 `misconfig-vuln` / `misconfig-exploit` 这两个 AgentName 及其所有 `Record<AgentName>` 消费者,外加 pentest vuln 阶段对 misconfig 的注册。**不动** `VulnType` / `VulnClass`(留给 Task 3),故 `createVulnValidator('misconfig')` 调用删除后,VulnType 仍含未使用的 `'misconfig'` 成员——合法,不报错。

**Files:**
- Modify: `apps/worker/src/types/agents.ts`(ALL_AGENTS、PlaywrightSession)
- Modify: `apps/worker/src/session-manager.ts`(AGENTS、AGENT_PHASE_MAP、AGENT_VALIDATORS、PLAYWRIGHT_SESSION_MAPPING、report.prerequisites)
- Modify: `apps/worker/src/ai/queue-schemas.ts`(buildOutputFormats、VULN_AGENT_QUEUE_FILENAMES 的 AgentName 键)
- Modify: `apps/worker/src/temporal/workflows.ts`(buildPipelineConfigs misconfig 元素、白盒 Phase 3 注释)
- Modify: `apps/worker/src/temporal/activities.ts`(runMisconfigVulnAgent)

**Interfaces:**
- Consumes: 无
- Produces: `AgentName` 收窄为不含 `'misconfig-vuln'` / `'misconfig-exploit'`;`PlaywrightSession` 收窄为 `'agent1'|...|'agent5'`。后续任务不再能引用这两个 agent 名。

- [ ] **Step 1: `types/agents.ts` — 从 ALL_AGENTS 删除两个 agent**

在 `ALL_AGENTS` 数组(约 15–31 行)删除这两行:

```ts
  'misconfig-vuln',
```
```ts
  'misconfig-exploit',
```

- [ ] **Step 2: `types/agents.ts` — PlaywrightSession 收窄**

将(约 39 行):
```ts
export type PlaywrightSession = 'agent1' | 'agent2' | 'agent3' | 'agent4' | 'agent5' | 'agent6';
```
改为:
```ts
export type PlaywrightSession = 'agent1' | 'agent2' | 'agent3' | 'agent4' | 'agent5';
```

- [ ] **Step 3: `session-manager.ts` — 从 AGENTS 删除两个定义**

删除整个 `'misconfig-vuln'` 块(约 64–70 行):
```ts
  'misconfig-vuln': {
    name: 'misconfig-vuln',
    displayName: 'Misconfig vuln agent',
    prerequisites: ['recon'],
    promptTemplate: 'vuln-misconfig',
    deliverableFilename: 'misconfig_analysis_deliverable.md',
  },
```

删除整个 `'misconfig-exploit'` 块(约 106–112 行):
```ts
  'misconfig-exploit': {
    name: 'misconfig-exploit',
    displayName: 'Misconfig exploit agent',
    prerequisites: ['misconfig-vuln'],
    promptTemplate: 'exploit-misconfig',
    deliverableFilename: 'misconfig_exploitation_evidence.md',
  },
```

- [ ] **Step 4: `session-manager.ts` — report.prerequisites 移除 misconfig-exploit**

`report` 定义(约 113–126 行)的 `prerequisites` 数组中删除 `'misconfig-exploit',` 这一行。结果应为:
```ts
    prerequisites: [
      'injection-exploit',
      'xss-exploit',
      'auth-exploit',
      'ssrf-exploit',
      'authz-exploit',
    ],
```

- [ ] **Step 5: `session-manager.ts` — AGENT_PHASE_MAP 删除两条**

删除(约 141 行):
```ts
  'misconfig-vuln': 'vulnerability-analysis',
```
与(约 147 行):
```ts
  'misconfig-exploit': 'exploitation',
```

- [ ] **Step 6: `session-manager.ts` — PLAYWRIGHT_SESSION_MAPPING 删除两条**

删除(约 200 行):
```ts
  'vuln-misconfig': 'agent6',
```
与(约 208 行):
```ts
  'exploit-misconfig': 'agent6',
```

同时把该 mapping 上方注释 `// Phase 3: Vulnerability Analysis (5 parallel agents)` 保持不变(本就 5 个);把 `// Phase 4: Exploitation (6 parallel agents - same as vuln counterparts)` 改为 `// Phase 4: Exploitation (5 parallel agents - same as vuln counterparts)`。

- [ ] **Step 7: `session-manager.ts` — AGENT_VALIDATORS 删除两条**

删除(约 232 行):
```ts
  'misconfig-vuln': createVulnValidator('misconfig'),
```
与(约 240 行):
```ts
  'misconfig-exploit': createExploitValidator('misconfig'),
```

- [ ] **Step 8: `queue-schemas.ts` — buildOutputFormats 删除 misconfig-vuln entry**

在 `buildOutputFormats` 返回对象中(约 205–220 行)删除整个 `'misconfig-vuln': toOutputFormat(...)` 条目(含其 `z.object({ vulnerabilities: z.array(base.extend({...})) })`)。

- [ ] **Step 9: `queue-schemas.ts` — VULN_AGENT_QUEUE_FILENAMES 删除 misconfig-vuln**

删除(约 233 行):
```ts
  'misconfig-vuln': 'misconfig_exploitation_queue.json',
```

- [ ] **Step 10: `workflows.ts` — buildPipelineConfigs 删除 misconfig 元素**

删除整个数组元素(约 394–400 行):
```ts
      {
        vulnType: 'misconfig',
        vulnAgent: 'misconfig-vuln',
        exploitAgent: 'misconfig-exploit',
        runVuln: () => a.runMisconfigVulnAgent(activityInput),
        runExploit: () => a.runMisconfigExploitAgent(activityInput),
      },
```

- [ ] **Step 11: `workflows.ts` — 修正白盒 Phase 3 注释**

将(约 767 行):
```ts
    // === Phase 3: Vulnerability Analysis (6 agents) ===
```
改为:
```ts
    // === Phase 3: Vulnerability Analysis (5 agents) ===
```
> 注:白盒 `vulnAgents` 数组(约 772–782 行)本就只含 5 个(无 misconfig),此注释是 pre-existing 错误,顺手修正。**不要动** 文件头第 13 行 `3-4. Vulnerability + Exploitation (5 pipelined pairs in parallel)`——删除 misconfig 后 pentest 恰为 5 对,该注释现已正确。

- [ ] **Step 12: `activities.ts` — 删除 runMisconfigVulnAgent**

删除(约 458–461 行,含上方注释):
```ts
// misconfig has no MCP collector — the agent writes its deliverable directly.
export async function runMisconfigVulnAgent(input: ActivityInput): Promise<AgentMetrics> {
  return runAgentActivity('misconfig-vuln', input);
}
```
> 注:**暂不删** `runMisconfigExploitAgent`——它仍被 `exploitAgents` map(workflows.ts:1002)引用,留待 Task 3 与 VulnType 收窄同步删除。

- [ ] **Step 13: 类型检查**

Run: `pnpm run check`
Expected: 通过。若报错指向某处仍引用 `misconfig-vuln` / `misconfig-exploit` / `agent6`,按报错补删——编译器即验收。

- [ ] **Step 14: Commit**

```bash
git add -A apps/worker/src/
git commit -m "refactor(worker): drop misconfig-vuln agent from registry, workflow, activities"
```

---

### Task 3: VulnType/VulnClass 链收窄 + pentest exploit 注册 + queue schema 常量 + 服务层 record

> 本任务收窄 `VulnType` / `VulnClass` 去掉 `'misconfig'`,并删除所有 `Record<VulnType/VulnClass, ...>` 消费者的对应键,以及 pentest exploit 阶段对 misconfig 的注册、queue schema 的 misconfig 常量。

**Files:**
- Modify: `apps/worker/src/types/agents.ts`(VulnType)
- Modify: `apps/worker/src/types/config.ts`(VulnClass、NOTE)
- Modify: `apps/worker/src/services/queue-validation.ts`(VULN_TYPE_CONFIG)
- Modify: `apps/worker/src/services/prompt-manager.ts`(VULN_SUMMARY_SPECS)
- Modify: `apps/worker/src/services/findings-renderer.ts`(CLASSES、renderMisconfigEntry、import)
- Modify: `apps/worker/src/services/affected-endpoints-appendix.ts`(CLASS_CONFIGS 元素)
- Modify: `apps/worker/src/ai/queue-schemas.ts`(MisconfigVulnerability、MisconfigFinding)
- Modify: `apps/worker/src/temporal/workflows.ts`(exploitAgents map)
- Modify: `apps/worker/src/temporal/activities.ts`(runMisconfigExploitAgent)

**Interfaces:**
- Consumes: Task 2 产出的收窄后 `AgentName`
- Produces: `VulnType` 不含 `'misconfig'`;`VulnClass` 不含 `'misconfig'`;`MisconfigFinding` 类型与 `MisconfigVulnerability` schema 常量消失。

- [ ] **Step 1: `types/agents.ts` — VulnType 收窄**

将(约 59 行):
```ts
export type VulnType = 'injection' | 'xss' | 'auth' | 'ssrf' | 'authz' | 'misconfig';
```
改为:
```ts
export type VulnType = 'injection' | 'xss' | 'auth' | 'ssrf' | 'authz';
```

- [ ] **Step 2: `types/config.ts` — VulnClass 收窄 + 删 NOTE**

将(约 24 行):
```ts
export type VulnClass = 'injection' | 'xss' | 'auth' | 'authz' | 'ssrf' | 'misconfig';
```
改为:
```ts
export type VulnClass = 'injection' | 'xss' | 'auth' | 'authz' | 'ssrf';
```
并删除其下的 NOTE 注释行(约 26 行):
```ts
// NOTE: 'misconfig' is excluded from defaults — enable via vuln_classes config when needed.
```
> `ALL_VULN_CLASSES`(约 27 行)本就是 5 项,保持不变。

- [ ] **Step 3: `queue-validation.ts` — VULN_TYPE_CONFIG 删除 misconfig 键**

删除(约 94–97 行):
```ts
  misconfig: Object.freeze({
    deliverable: 'misconfig_analysis_deliverable.md',
    queue: 'misconfig_exploitation_queue.json',
  }),
```

- [ ] **Step 4: `prompt-manager.ts` — VULN_SUMMARY_SPECS 删除 misconfig 键**

删除(约 65–69 行):
```ts
  misconfig: {
    heading: 'Security Misconfiguration Vulnerabilities',
    evidenceSection: 'Security Misconfiguration Exploitation Evidence',
    noneFoundLabel: 'security misconfiguration',
  },
```

- [ ] **Step 5: `findings-renderer.ts` — 删除 renderMisconfigEntry 函数**

删除整个函数(约 144–154 行):
```ts
function renderMisconfigEntry(e: MisconfigFinding): string {
  const rows: Array<string | null> = [
    summaryRow('Vulnerable location', formatLocation(e.source_endpoint, e.vulnerable_code_location)),
    summaryRow('Overview', e.missing_defense),
    summaryRow('Impact', e.exploitation_hypothesis),
  ];
  if (e.vulnerable_parameter) rows.push(summaryRow('Parameter', e.vulnerable_parameter));
  if (e.redirect_sink) rows.push(summaryRow('Redirect sink', e.redirect_sink));
  if (e.existing_validation) rows.push(summaryRow('Existing validation', e.existing_validation));
  return buildEntry(e.ID, e.vulnerability_type, rows, e.notes);
}
```

- [ ] **Step 6: `findings-renderer.ts` — CLASSES 删除 misconfig 键**

删除(约 194–200 行):
```ts
  misconfig: {
    heading: 'Security Misconfiguration',
    noneFoundLabel: 'misconfiguration',
    queueFile: 'misconfig_exploitation_queue.json',
    findingsFile: 'misconfig_findings.md',
    renderEntry: (e) => renderMisconfigEntry(e as MisconfigFinding),
  },
```

- [ ] **Step 7: `findings-renderer.ts` — 删除 MisconfigFinding import**

在 `import type { ... } from '../ai/queue-schemas.js'`(约 20–27 行)中删除 `MisconfigFinding,` 这一行。

- [ ] **Step 8: `affected-endpoints-appendix.ts` — CLASS_CONFIGS 删除 misconfig 元素**

删除整个数组元素(约 85–94 行):
```ts
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
```
> 注:此 `CLASS_CONFIGS` 是 `readonly ClassAppendixConfig[]`(数组,非 record),删元素无类型约束,但语义上随 misconfig 一并移除。

- [ ] **Step 9: `queue-schemas.ts` — 删除 MisconfigVulnerability 常量**

删除(约 93–102 行):
```ts
const MisconfigVulnerability = baseVulnerability.extend({
  source_endpoint: z.string().optional(),
  vulnerable_code_location: z.string().optional(),
  missing_defense: z.string().optional(),
  exploitation_hypothesis: z.string().optional(),
  suggested_exploit_technique: z.string().optional(),
  vulnerable_parameter: z.string().optional(),
  redirect_sink: z.string().optional(),
  existing_validation: z.string().optional(),
});
```

- [ ] **Step 9b: `queue-schemas.ts` — 删除 MisconfigFinding type**

删除(约 111 行):
```ts
export type MisconfigFinding = z.infer<typeof MisconfigVulnerability>;
```
> 注:Step 7 已删除其唯一消费者(findings-renderer 的 import),故此 export 现可安全移除。

- [ ] **Step 10: `workflows.ts` — exploitAgents map 删除 misconfig 键**

在 `exploitAgents`(约 996–1003 行)中删除(约 1002 行):
```ts
      misconfig: a.runMisconfigExploitAgent,
```
> 注:`exploitAgents` 是 `Record<VulnType, ...>`,本任务已收窄 VulnType,故删键后类型一致。

- [ ] **Step 11: `activities.ts` — 删除 runMisconfigExploitAgent**

删除(约 550–553 行,含上方注释):
```ts
// misconfig has no MCP collector — the agent writes its evidence directly.
export async function runMisconfigExploitAgent(input: ActivityInput): Promise<AgentMetrics> {
  return runAgentActivity('misconfig-exploit', input);
}
```

- [ ] **Step 12: 类型检查**

Run: `pnpm run check`
Expected: 通过。报错即漏删指南。

- [ ] **Step 13: Commit**

```bash
git add -A apps/worker/src/
git commit -m "refactor(worker): drop misconfig from VulnType/VulnClass, schemas, service records"
```

---

### Task 4: 配置 schema 与示例

**Files:**
- Modify: `apps/worker/configs/config-schema.json`(约 147、150 行)
- Modify: `apps/worker/configs/example-config.yaml`(约 7–8 行)

**Interfaces:** 无(schema 是运行时校验,不被 TS 编译)。

- [ ] **Step 1: `config-schema.json` — description 数量订正**

将(约 147 行):
```json
      "description": "Vulnerability classes to test. When omitted, all six classes run. When set, only listed classes run; their vuln+exploit agents and report sections are included.",
```
改为:
```json
      "description": "Vulnerability classes to test. When omitted, all five classes run. When set, only listed classes run; their vuln+exploit agents and report sections are included.",
```

- [ ] **Step 2: `config-schema.json` — enum 删除 misconfig**

将(约 150 行):
```json
        "enum": ["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
```
改为:
```json
        "enum": ["injection", "xss", "auth", "authz", "ssrf"]
```

- [ ] **Step 3: `example-config.yaml` — 注释订正**

将(约 7–8 行):
```yaml
# Limit which vulnerability classes run end-to-end (optional, default: all six)
# vuln_classes: [injection, xss, auth, authz, ssrf, misconfig]
```
改为:
```yaml
# Limit which vulnerability classes run end-to-end (optional, default: all five)
# vuln_classes: [injection, xss, auth, authz, ssrf]
```

- [ ] **Step 4: 类型检查 + lint**

Run: `pnpm run check && pnpm biome`
Expected: 通过。

- [ ] **Step 5: Commit**

```bash
git add apps/worker/configs/config-schema.json apps/worker/configs/example-config.yaml
git commit -m "refactor(worker): drop misconfig from vuln_classes schema and example"
```

---

### Task 5: 项目文档同步

> 文档为中文 markdown。每处给定位锚点 + before/after。改完后通读相关段落确保语义连贯。

**Files:**
- Modify: `docs/pipeline-agent-orchestration.md`(6 处)
- Modify: `docs/whitebox-analysis-internals.md`(6 处)
- Modify: `docs/shannon-defects.md`(D7 表格行、5.4 节、第 7 条建议)

**Interfaces:** 无。

- [ ] **Step 1: `docs/pipeline-agent-orchestration.md` — 重写开头关于数量的说明**

将(第 3 行):
```
> 适用版本:当前 `feat/fork` 分支。注意 `CLAUDE.md` 里 "5 parallel agents" 的描述已过时——漏洞类别现为 **6 个**(新增 `misconfig`)。
```
改为:
```
> 适用版本:当前 `feat/fork` 分支。漏洞类别为 **5 个**(injection / xss / auth / authz / ssrf)。
```

- [ ] **Step 2: `docs/pipeline-agent-orchestration.md` — VulnType 列表去掉 misconfig**

将(第 14 行)中:
```
漏洞类型 `VulnType`:`injection | xss | auth | ssrf | authz | misconfig`(`apps/worker/src/types/agents.ts:59`)。
```
改为:
```
漏洞类型 `VulnType`:`injection | xss | auth | ssrf | authz`(`apps/worker/src/types/agents.ts`)。
```

- [ ] **Step 3: `docs/pipeline-agent-orchestration.md` — 伪代码遍历集去掉 misconfig**

将(第 36 行):
```
对每个 vuln 类别 ∈ {injection, xss, auth, ssrf, authz, misconfig}:
```
改为:
```
对每个 vuln 类别 ∈ {injection, xss, auth, ssrf, authz}:
```

- [ ] **Step 4: `docs/pipeline-agent-orchestration.md` — Phase 3 数量改 5、删 exploit-misconfig 表行**

将(第 135 行):
```
#### Phase 3 — `vuln-*`(6 个:injection / xss / auth / authz / ssrf / misconfig)⭐ 数量动态
```
改为:
```
#### Phase 3 — `vuln-*`(5 个:injection / xss / auth / authz / ssrf)⭐ 数量动态
```
删除 exploit 对照表里(第 154 行)的一行:
```
| exploit-misconfig | 多步自动化脚本 |
```

- [ ] **Step 5: `docs/whitebox-analysis-internals.md` — pentest 数量改 5、删过时对照**

将(第 431 行)整段中涉及 "pentest 路径有 **6 条**(含 misconfig...)" 与 "`workflows.ts:13` 注释 ... 该数已过时" 的表述,改写为 pentest 与白盒均为 5 条、`workflows.ts:13` 的 "5 pipelined pairs" 现已与 pentest 一致。具体:把
```
白盒路径的漏洞分析阶段是 **5 条**...pentest 路径有 **6 条**(含 misconfig,`buildPipelineConfigs` `workflows.ts:351-402`)。注意 `workflows.ts:13` 注释写的是"5 pipelined pairs"...该数已过时;白盒不跑 exploit、本无配对概念,此注释与白盒路径无关。
```
改为:
```
白盒路径的漏洞分析阶段是 **5 条**独立 pipeline 并行(`WHITEBOX_VULN_CLASSES` = injection / xss / auth / authz / ssrf,`workflows.ts`)。pentest 路径同为 **5 条**(`buildPipelineConfigs`,`workflows.ts`);`workflows.ts:13` 注释 "5 pipelined pairs" 与 pentest 一致。白盒不跑 exploit、本无配对概念,此注释与白盒路径无关。
```

- [ ] **Step 6: `docs/whitebox-analysis-internals.md` — 删"白盒未纳入 misconfig"表格行**

删除差异表里(第 471 行)的整行:
```
| 1 | **白盒未纳入 misconfig** | `WHITEBOX_VULN_CLASSES`...openspec proposal `2026-05-26-add-misconfig-agent` 计划纳入白盒但未落地。 |
```
> 删除后若表格剩余行编号为 2、3,无需重排(表格是差异列表,编号非序列依赖);若需整洁可把后续行号顺移。

- [ ] **Step 7: `docs/whitebox-analysis-internals.md` — 修正源码注释滞后条目**

将(第 473 行)差异表第 3 条中涉及 "对 pentest 过时(实际 6 个含 misconfig)" 的表述改为反映 pentest 现为 5 个、`workflows.ts:13` 注释已正确。把
```
| 3 | **源码注释滞后** | `workflows.ts:756` 注释写 "(6 agents)"...对 pentest 过时(实际 6 个含 misconfig)。 |
```
改为:
```
| 3 | **源码注释滞后** | `workflows.ts` Phase 3 注释曾写 "(6 agents)" 与白盒实际 5 个不符(已订正为 5)。 |
```

- [ ] **Step 8: `docs/whitebox-analysis-internals.md` — 删 vuln-misconfig.txt 引用 + 更新 ALL_VULN_CLASSES 注释**

删除(第 506 行)prompt 索引中的 `vuln-misconfig.txt` 项:
```
- `apps/worker/prompts/vuln-ssrf.txt` / `vuln-xss.txt` / `vuln-authz.txt` / `vuln-misconfig.txt` — 各专项研判
```
改为:
```
- `apps/worker/prompts/vuln-ssrf.txt` / `vuln-xss.txt` / `vuln-authz.txt` — 各专项研判
```
并将(第 518 行)`ALL_VULN_CLASSES` 说明中 ";misconfig 排除于默认值,需通过 `vuln_classes` 配置启用" 删除,改为:
```
- `apps/worker/src/types/config.ts` — `ALL_VULN_CLASSES`(5 项:injection/xss/auth/authz/ssrf)
```

- [ ] **Step 9: `docs/shannon-defects.md` — 删除 D7 表格行**

删除(第 17 行):
```
| D7 | Misconfig Agent 不一致 | 低 | prompt 风格与其他 5 个差异大 | 可能为半成品或后加入的模块 |
```

- [ ] **Step 10: `docs/shannon-defects.md` — 删除 5.4 D7 整节**

删除(第 164 行起)`### 5.4 D7:Misconfig Agent 不一致` 整节(含其下的对比表格与"推断"段落,至下一 `###` 节标题前)。

- [ ] **Step 11: `docs/shannon-defects.md` — 删除第 7 条建议**

删除(第 199 行)改进建议列表中的:
```
7. **对齐 misconfig prompt**:按其他 5 个 agent 的标准重写
```
> 若该条是列表末项,删除后前序项编号无需改动(除非文档要求连续编号,则顺移)。

- [ ] **Step 12: 通读 + Commit**

通读三个文档相关段落,确认语义连贯、无遗留 "6 个/六类/misconfig" 表述(保留项除外)。

```bash
git add docs/pipeline-agent-orchestration.md docs/whitebox-analysis-internals.md docs/shannon-defects.md
git commit -m "docs: sync misconfig removal across orchestration, whitebox internals, defects"
```

---

### Task 6: 样本报告删除 Misconfigurations 章节

**Files:**
- Modify: `sample-reports/shannon-report-crapi.md`(约 29 行)
- Modify: `sample-reports/shannon-report-juice-shop.md`(约 30 行)

**Interfaces:** 无。

- [ ] **Step 1: `shannon-report-crapi.md` — 删除 Security Misconfigurations 段**

删除(约 29 行及其下相关 [REDACTED] 占位):
```
**Security Misconfigurations:**
[REDACTED]
```
> 仅删该章节标题与其专属占位行,不动其他章节。

- [ ] **Step 2: `shannon-report-juice-shop.md` — 删除 Security Misconfigurations 段**

删除(约 30 行):
```
**Security Misconfigurations:**
[REDACTED]
```

- [ ] **Step 3: Commit**

```bash
git add sample-reports/
git commit -m "docs(reports): drop Security Misconfigurations section from samples"
```

---

### Task 7: 最终验收

**Files:** 无(纯验证)。

- [ ] **Step 1: 类型检查**

Run: `pnpm run check`
Expected: 通过。

- [ ] **Step 2: Lint / format**

Run: `pnpm biome`
Expected: 通过。若有格式问题,跑 `pnpm biome:fix` 修正后再跑一次。

- [ ] **Step 3: misconfig 残留复核**

Run: `rg -ni misconfig`
Expected 命中**仅限**(逐一核对,不得出现预期外文件):
- `apps/worker/prompts/vuln-auth.txt` 的 `session_cookie_misconfig`
- `COVERAGE.md` 的 WSTG-CONF-14 行
- `docs/shannon-pro.md` 的 "misconfigurations across image layers"
- `llms-full.txt` 的同句
- `openspec/changes/archive/2026-05-26-add-misconfig-agent/`(整目录)
- `openspec/changes/local-whitebox-runner/`(整目录)
- `docs/superpowers/specs/*` 与 `docs/superpowers/plans/*`(含本计划自身)

若出现预期外命中,判断是漏删(回到 Task 2/3 补删)还是新的 false positive(记入保留项)。

- [ ] **Step 4: agent6 残留复核**

Run: `rg -n agent6 apps/worker/src/`
Expected: 无命中。

- [ ] **Step 5: 标记完成**

若 Step 1–4 全绿,本计划完成。若过程中有 biome 修正且已 commit,记录最后一个 commit hash;否则报告"验证通过,无额外改动"。
