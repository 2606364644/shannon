# 设计:移除自实现的 misconfig agent

**日期:** 2026-07-01
**状态:** 待审查
**范围:** `apps/worker`(类型 / 注册表 / 工作流 / 活动 / 服务层 / prompt / 配置 schema) + 项目文档 + 样本报告

## 1. 背景

官方 Shannon 的漏洞分析阶段只有 **5 类** agent:`injection` / `xss` / `auth` / `authz` / `ssrf`。`misconfig` 是通过 OpenSpec 变更 `2026-05-26-add-misconfig-agent` **后加入的第 6 类**,由本仓库自行实现,不属于上游官方代码。

**它当前的状态(实测):**

- 以一对 agent 形式存在:`vuln-misconfig` + `misconfig-exploit`,贯穿 `AGENTS` 注册表、`ALL_AGENTS`、`VulnType` / `VulnClass` 类型、队列 schema、工作流编排、活动包装、服务层渲染器、配置 schema 与两个 prompt 文件。
- **默认不运行**:`ALL_VULN_CLASSES`(`types/config.ts:27`)已将其排除,仅当用户在配置里显式写 `vuln_classes: [..., misconfig]` 时才启用。
- **半成品性质**:`docs/shannon-defects.md` 的 D7 缺陷明确记录其 prompt 风格与其他 5 个差异极大(89 行 vs 290–370 行),"可能是后来加入的模块,尚未按其他 5 个 agent 标准对齐"。
- 独占 `agent6` Playwright 会话(无其他 agent 使用)。
- 无任何测试覆盖(无 `.test.ts` 引用)。

**决策:** 既然非官方、默认关闭、半成品、无测试,将其从代码库彻底移除,让仓库回到干净的官方 5 类状态。

## 2. 目标与非目标

**目标**

- misconfig agent 功能彻底消失:删除两个 prompt 文件,从所有数据结构(类型 / 注册表 / schema / 服务层 map)移除其条目。
- 同步更新项目文档与样本报告,使文档与代码一致。
- 收窄因 misconfig 而存在的类型分支(`PlaywrightSession` 的 `agent6`)。
- 删除后 `pnpm run check` 与 `pnpm biome` 通过。

**非目标(YAGNI)**

- 不动 git 历史(提交记录保留)。
- 不删除 OpenSpec 变更档案与历史设计文档(见 §5)。
- 不手动编辑生成文件 `llms-full.txt`(如需同步应重新生成,本轮不动)。
- 不重构与 misconfig 无关的代码,即使沿途发现可改进之处。

## 3. 设计

删除是**机械的、数据驱动的**:misconfig 在 service / workflow 层均以 record / 数组条目形式注册,没有 `=== 'misconfig'` 的特殊分支逻辑,因此绝大多数改动是"删除一个键 / 一条数组元素"。行号为撰写时的参考,实现以实际代码为准。

### 3.1 整文件删除(2 个 prompt)

- `apps/worker/prompts/vuln-misconfig.txt`
- `apps/worker/prompts/exploit-misconfig.txt`

### 3.2 核心代码——从数据结构移除条目

| 文件 | 改动 |
|---|---|
| `apps/worker/src/types/agents.ts` | `ALL_AGENTS` 删 `'misconfig-vuln'`、`'misconfig-exploit'`;`VulnType` 去掉 `'misconfig'`;`PlaywrightSession` 收窄为 `'agent1' \| ... \| 'agent5'`(去掉仅 misconfig 使用的 `'agent6'`) |
| `apps/worker/src/types/config.ts` | `VulnClass` 去掉 `'misconfig'`;删除第 26 行 NOTE 注释;`ALL_VULN_CLASSES` 已是 5 项,保持不变 |
| `apps/worker/src/session-manager.ts` | `AGENTS` 删 `misconfig-vuln` / `misconfig-exploit` 两条;`AGENT_PHASE_MAP` 删两条;`PLAYWRIGHT_SESSION_MAPPING` 删 `vuln-misconfig` / `exploit-misconfig` 两条;`AGENT_VALIDATORS` 删两条;`report` 的 `prerequisites` 移除 `'misconfig-exploit'` |
| `apps/worker/src/ai/queue-schemas.ts` | 删 `MisconfigVulnerability`、`MisconfigFinding`、`buildOutputFormats` 内的 `'misconfig-vuln'` 条目、`VULN_AGENT_QUEUE_FILENAMES` 的 `'misconfig-vuln'` |
| `apps/worker/src/services/findings-renderer.ts` | 删 `MisconfigFinding` import、`renderMisconfigEntry()` 函数、`VULN_RENDERERS.misconfig` 条目 |
| `apps/worker/src/services/prompt-manager.ts` | 删 `misconfig` 条目(约 65–69 行) |
| `apps/worker/src/services/queue-validation.ts` | 删 `misconfig` 条目(约 94–97 行) |
| `apps/worker/src/services/affected-endpoints-appendix.ts` | 删 misconfig 数组条目(约 85 行起) |
| `apps/worker/src/temporal/workflows.ts` | 删 `buildPipelineConfigs` 内 misconfig 条目(约 394–400)、exploit agent map 的 `misconfig` 键(约 1002);同步更新过时注释(文件头 "5 pipelined pairs"、"(6 agents)" 等,使其与 5 类一致) |
| `apps/worker/src/temporal/activities.ts` | 删 `runMisconfigVulnAgent`(约 458–461)、`runMisconfigExploitAgent`(约 550–553)两个函数及其上方注释 |

**说明:**

- 白盒路径 `WHITEBOX_VULN_CLASSES` 本就不含 misconfig,无需改动;只有 pentest 路径 `buildPipelineConfigs` 含 misconfig 条目需删除。
- `apps/worker/src/local/runner.ts`、`config-parser.ts`、`mcp-server/*`、`providers/*` 不含 "misconfig" 字面量,靠 `ALL_VULN_CLASSES` / `VulnClass` 动态遍历,类型与常量一旦收窄,这些文件自动正确,无需改动。

### 3.3 配置 schema 与示例

- `apps/worker/configs/config-schema.json`:`vuln_classes.items.enum` 去掉 `"misconfig"`;`description` 中的 `"all six classes"` 改为 `"all five classes"`(`minItems: 1` 与数量无关,不动)。
- `apps/worker/configs/example-config.yaml`:注释里的 `vuln_classes` 示例去掉 `misconfig`;`"default: all six"` 改为 `"default: all five"`。

### 3.4 项目文档同步

- `docs/pipeline-agent-orchestration.md`(6 处):重写开头关于 CLAUDE.md "5 parallel agents" 过时的段落——删除 misconfig 后 CLAUDE.md 的 5 反而**正确**了;`VulnType` 列表、伪代码遍历集、Phase 3 "6 个" 改 "5 个"、exploit 表删除 `exploit-misconfig` 行,均去掉 misconfig。
- `docs/whitebox-analysis-internals.md`(6 处):pentest 路径 "6 条含 misconfig" 改为 "5 条";删除"白盒未纳入 misconfig"的表格行(主体已不存在,无需再区分);删除 `vuln-misconfig.txt` 引用;更新 `ALL_VULN_CLASSES` 相关注释。
- `docs/shannon-defects.md`:删除 D7 缺陷整条(表格行 + 5.4 节 + 第 7 条改进建议"对齐 misconfig prompt")——缺陷主体已不存在。

### 3.5 样本报告

- `sample-reports/shannon-report-crapi.md`、`sample-reports/shannon-report-juice-shop.md`:删除 `**Security Misconfigurations:**` 章节(及其下的 `[REDACTED]` 占位)。

## 4. 必须保留(false positive 与历史档案)

删除时**不得**触碰以下出现 "misconfig" 字样的位置——它们与 misconfig agent 无关或属于应保留的历史记录:

- `apps/worker/prompts/vuln-auth.txt:135` 的 `session_cookie_misconfig` —— **auth agent 的漏洞分类标签**(session cookie 配置失误),盲删会破坏 auth agent。
- `COVERAGE.md:52` WSTG-CONF-14 "Test Other HTTP Security Header Misconfigurations" —— OWASP WSTG 标准测试项名称。
- `docs/shannon-pro.md:55` 与 `llms-full.txt:979` 的 "misconfigurations across image layers" —— 容器扫描能力的通用英文词,非 agent 名。
- `openspec/changes/archive/2026-05-26-add-misconfig-agent/` —— OpenSpec 变更档案(当初添加 misconfig 的设计记录)。
- `openspec/changes/local-whitebox-runner/` —— 已完成的 OpenSpec 变更,其 tasks / spec 中提及 misconfig 属历史描述,保留。
- `docs/superpowers/specs/*` 与 `docs/superpowers/plans/*` —— 历史 brainstorming 设计档案,沿途中提及 misconfig 属上下文,保留。

## 5. 验证

1. `pnpm run check` —— 类型检查。重点确认 `VulnType` / `VulnClass` / `AgentName` / `PlaywrightSession` 收窄后**无残留引用**(任何遗漏的 `misconfig-vuln` / `'misconfig'` / `agent6` 都会触发编译错误,这是发现遗漏的兜底)。
2. `pnpm biome` —— lint / format / import 排序。
3. `rg -ni "misconfig"` 复核全仓:命中应**仅限** §4 列出的保留项。若出现预期外命中,逐一判断是漏删还是 false positive。
4. `rg -n "agent6" apps/worker/src/` 应无命中(确认 PlaywrightSession 收窄落地)。

## 6. 风险与回滚

- **风险低:** misconfig 默认关闭、无测试、集成数据驱动,删除是线性的。
- **主要风险点是 §4 的 false positive**,尤其 `vuln-auth.txt` 的 `session_cookie_misconfig`——实现时需逐文件审视命中上下文,不可全局替换。
- **回滚:** 全部改动集中在工作树,git revert / reset 即可;因不涉及数据迁移或外部状态,无额外清理。
