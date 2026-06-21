# vuln agent 差距分析

> 对比原始 Shannon(TypeScript,`/Users/mango/project/shannon-refactor/shannon`)与重构 Shannon-py(Python,`/Users/mango/project/shannon-refactor/shannon-py`)在 **vuln agent(安全漏洞挖掘 agent)** 全链路上的差异。
>
> **数据来源**:代码级核验(类型定义/注册表/prompt/schema/workflow),非仅文档。
>
> **日期**:2026-06-21
>
> **范围说明**:`misconfig`(安全配置错误)类为**重构有意裁剪**——原始 Shannon 已完整实现但默认不启用,Shannon-py 经设计决策去除。本文将其归入「有意裁剪(非 gap)」,不作为待补齐项。

---

## 1. vuln agent 体系总览

vuln agent 是白盒阶段并行执行的「按漏洞类型分科的代码审计专家」:每个类型一个 agent,读 recon/pre-recon 产物,产出分析报告 + 结构化漏洞队列,供黑盒阶段 exploit agent 消费。两边的全链路结构高度一致:

| 链路环节 | 原始 Shannon (TS) | 重构 Shannon-py (Py) |
|---|---|---|
| 类型集合 | 6 类(injection / xss / auth / ssrf / authz / **misconfig**) | 5 类(injection / xss / auth / ssrf / authz) |
| 类型枚举 | `VulnType` `agents.ts:59` | `VulnType` `agents.py:6` |
| agent 注册表 | `AGENTS` `session-manager.ts:14` | `AGENTS` dict `agents.py:51` |
| prompt 模板 | `apps/worker/prompts/vuln-*.txt` | `prompts/vuln-*.txt` |
| 结构化 schema | Zod `queue-schemas.ts` | Pydantic `queue_schemas.py` |
| 队列产物 | `{type}_exploitation_queue.json` | 同 |
| 分析报告 | `{type}_analysis_deliverable.md` | 同 |
| exploit agent | `*-exploit`(对应每个 vuln 类型) | 同 |
| 汇总报告 | `comprehensive_security_assessment_report.md` | 同 |

**一句话结论**:5 个共有类型(injection / xss / auth / ssrf / authz)的链路**一一对应、结构对齐**;差异集中在三处——① **misconfig 整链裁剪**(有意)、② 类型注册表**双重定义 + 顺序不一致**(真 gap)、③ 流水线**架构形态**(白盒/黑盒严格分离 vs 一体化 pipeline)。条件渲染、产物契约、报告汇总等维度**完全对齐**。

---

## 2. 分维度对比

### 2.1 vuln 类型覆盖

| 类型 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| injection | ✅ | ✅ | 对齐 |
| xss | ✅ | ✅ | 对齐 |
| auth | ✅ | ✅ | 对齐 |
| ssrf | ✅ | ✅ | 对齐 |
| authz | ✅ | ✅ | 对齐 |
| **misconfig** | ✅ 已注册(默认不启用,`config.ts:26-27`) | ❌ 完全无 | **有意裁剪**(见 §4) |

> 注:原始 Shannon 的 `ALL_VULN_CLASSES` 默认列表同样**排除 misconfig**(`config.ts:27`),即默认运行时两边都只跑 5 类。差异仅在「原始具备 misconfig 能力、重构不具备」。

### 2.2 类型注册表与枚举

| 子维度 | 原始 Shannon (TS) | 重构 Shannon-py (Py) | 定性 |
|---|---|---|---|
| 运行时类型 | `VulnType` `agents.ts:59`(6 值,含 misconfig) | `VulnType` `agents.py:6`(Literal 5 值) | 重构无 misconfig |
| 配置类型 | `VulnClass` `config.ts:24`(含 misconfig) | `VulnClass` `config.py:16`(Literal 5 值) | 重构无 misconfig |
| 默认启用列表 | `ALL_VULN_CLASSES` **单处**定义 `config.ts:27`(排除 misconfig) | `ALL_VULN_CLASSES` **双重**定义:`agents.py:155` + `config.py:75` | 🟡 **真 gap**(见 VA-1) |
| 顺序一致性 | — | 两处顺序**不一致**:`agents.py:155` = `[…, auth, ssrf, authz]`;`config.py:75` = `[…, auth, authz, ssrf]` | 🟡 维护隐患 |
| agent 注册表 | `AGENTS` `session-manager.ts:14` | `AGENTS` dict `agents.py:51` | 结构对应 |
| 外部覆盖机制 | `vuln_classes` 配置(`config.ts:71,89`)可启用 misconfig | CLI `--vuln-classes`(`cli/main.py:37`)+ YAML `vuln_classes`(`config.py:68`),但 Literal 限 5 值 | 形态对应;重构受 Literal 约束(无 misconfig 可启用) |

### 2.3 prompt 模板体系

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 主 prompt | `vuln-*.txt` 6 个 | `vuln-*.txt` 5 个 | 差 misconfig |
| pipeline-testing | `pipeline-testing/vuln-*.txt` 5 个(原始本身也无 misconfig 测试版) | `pipeline-testing/vuln-*.txt` 5 个 | 对齐 |
| prompt 结构 | XML 标签(role / objective / scope / methodology / deliverable / conclusion_trigger) | 同 | 对齐 |
| include 机制 | `@include()` `prompt-manager.ts` | `@include()` `manager.py` | 对齐 |
| shared 片段 | `_vuln-scope` / `_endpoint-security-context` / `_target` / `_rules` / `_code-path-rules` / `_rules-of-engagement` / `_shared-session` | 同一套 **+** `_static-dataflow-hints` **+** `_cross-route-enumeration` | 🟢 重构增强 |

### 2.4 structured output schema

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 框架 | Zod `queue-schemas.ts` | Pydantic `queue_schemas.py` | 语言差异 |
| 子类数 | 6(含 `MisconfigVulnerability` `queue-schemas.ts:93`) | 5(`queue_schemas.py:12-57`,无 misconfig、无预留位) | 差 misconfig |
| Union 类型 | `Vulnerability` union | `Vulnerability = Union[…]` `queue_schemas.py:59` | 对齐 |
| Queue wrapper | exploitation queue | `VulnerabilityQueue` `queue_schemas.py:61` | 对齐 |

### 2.5 exploit agent

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 数量 | 6(含 `misconfig-exploit` `agents.ts:29`) | 5(无 misconfig-exploit) | 差 misconfig |
| 定义位置 | `AGENTS` `session-manager.ts` | `agents.py:17-21, 93-127` | 结构对应 |
| prompt | `exploit-*.txt` 6 个 | `exploit-*.txt` 5 个 | 差 misconfig |
| queue 消费 | `exploitation-checker.ts` | `ExploitationChecker.validate_queue` `exploitation_checker.py:37` | 对齐 |

### 2.6 调度与并发架构

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 编排引擎 | Temporal workflow | Temporal workflow | 对齐 |
| 主流水线形态 | `pentestPipeline`(`workflows.ts:196`)pipeline 化:recon→vuln→queue check→exploit **自动串联**;另含独立 whitebox / blackbox workflow | **严格白盒/黑盒分离**,无一体化 pipeline,靠 `{type}_exploitation_queue.json` 文件衔接 | 🟡 架构差异(见 VA-2) |
| 白盒是否跑 exploit | `whiteboxPipelineWorkflow`(`:647`)不跑 exploit,`WHITEBOX_VULN_CLASSES`(`:645`)排除 misconfig | 白盒只到 vuln analysis + render findings,不触发 exploit | **一致**(白盒都不跑 exploit) |
| 白盒并发 | `Promise.allSettled` 全并行 | `asyncio.gather` 全并行(无 semaphore)`workflows.py:300` | 对齐 |
| 黑盒并发 | `runWithConcurrencyLimit` | `asyncio.Semaphore(max_concurrent)` `workflows.py:235` | 对齐 |
| 重试 | per-agent retry | per-vuln retry(3 次 / 30s / 5min / 2x)`workflows.py:289` | 对齐 |

### 2.7 条件渲染(if-live / if-static)

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 实现 | `stripConditionalBlocks()` `prompt-manager.ts:281` | `strip_conditional_blocks()` `manager.py:11` | ✅ **完全对齐** |
| 标签 | `<if-live>` / `<if-static>` | 同 | 对齐 |
| 判定依据 | `webUrl` 是否存在 | `has_web_url`(`manager.py:48`) | 对齐 |

### 2.8 Playwright session 隔离

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 映射机制 | `PLAYWRIGHT_SESSION_MAPPING` `session-manager.ts:176`:vuln + exploit 显式映射到 `agent1`~`agent12` | 两层:`BROWSER_SESSION_MAPPING`(=`PLAYWRIGHT_SESSION_MAPPING` 别名)`agents.py:157` 按 `AgentName` 枚举生成 `agent{N}`;+ `AGENT_SESSION_MAPPING` `playwright_config_writer.py:20` 给 5 个 exploit 语义命名(`agent-injection` 等) | ⚠️ 结构对齐、实现不同 |
| 隔离效果 | 每 agent 独立浏览器 session | 同(黑盒 `workflows.py:207` 每 exploit 写独立 config) | 功能对齐 |

### 2.9 deliverable / queue 产物契约

| 产物 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| 分析报告 | `{type}_analysis_deliverable.md` | 同 | ✅ 对齐 |
| 结构化队列 | `{type}_exploitation_queue.json` | 同 | ✅ 对齐 |
| 产物校验 | `validate_deliverable` | `validate_deliverable`(`executor.py:106`) | 对齐 |

### 2.10 报告汇总

| 子维度 | 原始 Shannon | 重构 Shannon-py | 定性 |
|---|---|---|---|
| findings 渲染 | `findings-renderer.ts`(exploit=false 时 queue→findings.md 确定性转换) | `FindingsRenderer.render_findings_from_queues` `findings_renderer.py:139` | ✅ 对齐 |
| 报告 agent | `report-executive` prompt | report prompt | 对齐 |
| 最终报告 | `comprehensive_security_assessment_report.md` | 同 | 对齐 |

---

## 3. 差距 / 差异矩阵

按性质分四类:

### A. 有意裁剪(设计决策,非 gap)

| # | 项 | 原始 | 重构 | 说明 |
|---|---|---|---|---|
| VA-0 | **misconfig 整链裁剪** | 完整实现(prompt + 枚举 + schema + agent + exploit-agent + queue),默认不启用 | 完全无 | 重构经设计决策去除,shannon-py 不做 HTTP 配置类漏洞专项分析。详见 §4 |

### B. 真正待审视的 gap

| # | 项 | 现状 | 严重度 |
|---|---|---|---|
| VA-1 | **`ALL_VULN_CLASSES` 双重定义 + 顺序不一致** | shannon-py 有两处:`agents.py:155` = `[injection, xss, auth, ssrf, authz]`、`config.py:75` = `[injection, xss, auth, authz, ssrf]`(ssrf/authz 互换);原始 Shannon 仅 `config.ts:27` 单处定义 | 🟡 中(维护隐患:改一处忘改另一处将产生隐蔽行为差异) |

### C. 架构差异(定性为设计选择,记录备查)

| # | 项 | 原始 | 重构 | 定性 |
|---|---|---|---|---|
| VA-2 | **主流水线形态** | `pentestPipeline` 一体化(recon→vuln→queue check→exploit 自动串联)+ 独立 whitebox / blackbox | 严格白盒/黑盒分离,靠 queue 文件衔接,无自动串联 | 设计选择(白盒/黑盒解耦更清晰);非缺陷 |

### D. 重构增强(非 gap,shannon-py 多出)

| # | 项 | 重构新增 |
|---|---|---|
| VA+1 | risk-scoring 阶段 + static dataflow hints | 白盒在 vuln 前先跑风险评分,生成 `_static-dataflow-hints.txt` 污点提示注入 vuln prompt |
| VA+2 | cross-route enumeration | `_cross-route-enumeration.txt` 跨路由分析指引 |
| VA+3 | shared 片段扩充 | 较原始多 `_static-dataflow-hints`、`_cross-route-enumeration` 两个 include |

---

## 4. misconfig 裁剪说明(为什么不是 gap)

### 4.1 原始 Shannon 的 misconfig 是什么

`apps/worker/prompts/vuln-misconfig.txt`(292 行)定义「Security Misconfiguration Analysis Specialist」,覆盖 6 个 HTTP 层配置错误子类型(`vuln-misconfig.txt:104`):

| 子类型 | 说明 |
|---|---|
| `Open_Redirect` | 重定向参数验证不足 |
| `Missing_Security_Headers` | CSP / HSTS / X-Frame-Options 等缺失 |
| `CORS_Misconfiguration` | 动态 Origin 反射 + credentials |
| `Missing_Cookie_Flags` | HttpOnly / Secure / SameSite 缺失 |
| `Clickjacking_Vulnerable` | frame-ancestors / X-Frame-Options 缺失 |
| `Information_Disclosure` | 栈追踪 / 版本头 / 调试模式 / source map 泄露 |

与其他 vuln 类型不同,misconfig 不做 source→sink 污点分析,而是逐端点、逐子类型的 HTTP 配置清单式检查;deliverable type 为 `MISCONFIG_ANALYSIS`(`:232`)。配套有 `misconfig-vuln` / `misconfig-exploit` agent(`agents.ts:23,29`)、`MisconfigVulnerability` schema(`queue-schemas.ts:93`)、`misconfig_exploitation_queue.json` 产物。

### 4.2 重构的裁剪

Shannon-py 经**设计决策**去除 misconfig:

- `VulnType` / `VulnClass`(Literal 5 值)无 misconfig
- 无 `vuln-misconfig.txt` prompt、无 `MisconfigVulnerability` schema、无 `misconfig-vuln` / `misconfig-exploit` agent
- `misconfig` 一词在 shannon-py 源码中几乎不出现(仅 recon prompt 作为检查项零星提及)

### 4.3 影响面

- shannon-py **不做** Open Redirect / 安全头 / CORS / Cookie 标志 / Clickjacking / 信息泄露 的专项白盒分析
- 这些配置类问题不在 vuln agent 的结构化产出(`*_exploitation_queue.json`)中,也不会进入 exploit 流程与最终报告的对应章节
- **定性**:产品范围决策,非实现遗漏;若未来需要,属「新增功能」而非「补齐 gap」

---

## 5. 关键代码路径索引

### 原始 Shannon (TS)

| 功能 | 文件 |
|---|---|
| VulnType / agent 名(含 misconfig) | `apps/worker/src/types/agents.ts:59` |
| VulnClass / ALL_VULN_CLASSES(单处) | `apps/worker/src/types/config.ts:24,27` |
| AGENTS 注册表 / session 映射 | `apps/worker/src/session-manager.ts:14,176` |
| Zod schema(含 Misconfig) | `apps/worker/src/ai/queue-schemas.ts` |
| prompt 加载 / 条件渲染 | `apps/worker/src/services/prompt-manager.ts:281,511` |
| 主流水线 / 白盒 / 黑盒 | `apps/worker/src/temporal/workflows.ts:196,647,849` |
| vuln prompt(含 misconfig) | `apps/worker/prompts/vuln-*.txt` |

### 重构 Shannon-py (Py)

| 功能 | 文件 |
|---|---|
| VulnType / AgentName / AGENTS / ALL_VULN_CLASSES / session 映射 | `packages/core/src/shannon_core/models/agents.py:6,51,155,157` |
| VulnClass / vuln_classes / ALL_VULN_CLASSES(第二处) | `packages/core/src/shannon_core/models/config.py:16,68,75` |
| Pydantic schema | `packages/core/src/shannon_core/models/queue_schemas.py` |
| prompt 加载 / 条件渲染 | `packages/core/src/shannon_core/prompts/manager.py:11,48` |
| agent 执行 | `packages/core/src/shannon_core/agents/executor.py:26` |
| exploit session 语义命名 | `packages/core/src/shannon_core/services/playwright_config_writer.py:20` |
| findings 渲染 | `packages/core/src/shannon_core/services/findings_renderer.py:139` |
| 白盒 workflow(vuln 并行 / retry) | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:43,279,289` |
| 黑盒 workflow(exploit / queue 校验) | `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:133,173,207` |
| queue 校验 | `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py:37` |
| vuln prompt | `prompts/vuln-*.txt` |

---

## 6. 交叉参考

- `docs/gap/entry-point-gap-analysis.md` — 入口点识别差距(recon 阶段,与 vuln agent 输入相关)
- `docs/gap/sink-gap-analysis-v2.md` — Sink 点差距(vuln agent 污点分析的下游)
- `docs/gap/authz-effect-gap-analysis.md` — authz vuln agent 的效果维度专项
- `docs/gap/route-analysis-binding-gap-analysis.md` — 路由分析服务与接口绑定
- `docs/whitebox-refactoring-assessment.md` — 全维度重构评估
- 本文档专注 **vuln agent 全链路** 的逐维度代码级对照
