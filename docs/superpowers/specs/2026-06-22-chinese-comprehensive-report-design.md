# 中文综合报告设计 — white-box 报告生成接入 + 全中文改造

- **日期**:2026-06-22
- **分支**:feat/fork-py
- **状态**:待实现(已通过 brainstorming 设计确认)
- **触发问题**:white-box 扫描完成后 `deliverables/` 下没有 `comprehensive_security_assessment_report.md`,且各分项 `*_analysis_deliverable.md` 为英文。

---

## 1. 背景与问题

一次完整的 white-box 扫描(85 分钟,$52.94)跑完后,`deliverables/` 目录只有各漏洞类别的 `*_analysis_deliverable.md`(英文),**缺少原项目 `/root/shannon` 会生成的 `comprehensive_security_assessment_report.md` 综合报告,且无任何中文产物**。

经对照排查,这是**两个独立的缺失**:

### 缺失 A:white-box 流程没接报告生成

- white-box reporting phase 只有一个 `render-findings` step,它调用 `FindingsRenderer.render_findings_from_queues()`(`packages/core/src/shannon_core/services/findings_renderer.py:203`)。
- 该函数仅在 `*_queue.json` 存在时把它渲染成 `*_findings.md`。white-box 的 vuln agent 直接写 `*_analysis_deliverable.md`,**既无 queue 文件也无 findings 文件** → 每个 class 都命中 skip 分支 → 日志里"渲染最终报告 7ms"几乎 no-op,什么都不落盘。
- 把各分项**拼装**成综合报告的逻辑(`ReportAssembler.assemble`),shannon-py **只在 `packages/blackbox/` 里实现了**,white-box pipeline 从未调用。
- 原项目对照:`/root/shannon/apps/worker/src/services/reporting.ts` 的 `assembleFinalReport()` 是确定性的拼接步骤。

### 缺失 B:没有中文产物

- 原项目的中文来自一个独立的 **Phase 7 翻译阶段**:`ReportTranslationProvider`(`/root/shannon/apps/worker/src/providers/report-translation-provider.ts`)用小模型把 `.md` deliverable 逐文件翻译为中文,输出到 `deliverables-cn/`。shannon-py **整个翻译阶段未移植**。
- 且 vuln agent 的 prompt(`prompts/vuln-*.txt`)产出英文 deliverable(`# Authentication Analysis Report`),没有任何中文输出约束。

### 当前模型(决定中文化策略的关键事实)

shannon-py 当前 active profile 为 `ark-coding`(`.env`:`SHANNON_PROFILE=ark-coding`),vuln/report agent 实际跑的模型为 `glm-latest`(MEDIUM/LARGE tier)与 `deepseek-v4-flash`(SMALL tier)——**均为中文原生模型**。这决定了中文化策略(见 §4.2)。

---

## 2. 目标与非目标

### 目标

1. white-box 扫描完成后,自动产出 `deliverables/comprehensive_security_assessment_report.md`。
2. 该报告及各分项 `*_analysis_deliverable.md` 均为**简体中文**(叙述文字中文,技术标识保留原文)。
3. 综合报告形态:**拼接 + LLM 执行摘要**(顶部中文执行摘要 + 按漏洞类型汇总,下接各漏洞中文详述)。
4. 改造对 blackbox 流程**零行为影响**(blackbox 继续按原逻辑工作)。

### 非目标

- **不**中文化 `pre_recon_deliverable.md` / `recon_deliverable.md`(技术中间产物,供 agent 消费;报告只引用其结论)。
- **不**改 `*-exploit.txt` 系列 prompt(white-box 默认不跑 exploitation;若日后启用 exploitation 流程,再一并中文化)。
- **不**复刻原项目的"翻译阶段 / `deliverables-cn/`"机制(本设计采用原生中文 prompt,无需中英两套目录)。
- **不**改动 attack-chain 阶段产物为空的问题(`attack_chains.json`/`route_chains.json` 为 `[]` 是另一独立问题,见 [[shared-knowledge-injection-lost-in-refactor]],不在本 spec 范围)。

---

## 3. 架构与数据流

改造后 white-box reporting phase 的数据流:

```
vuln agent(5 类,prompts/vuln-*.txt,原生中文输出)
  → auth/authz/injection/xss/ssrf_analysis_deliverable.md   (中文)
        │
        │  ① assemble-report step
        ↓  ReportAssembler.assemble()  确定性拼接(复用已验证组件)
  comprehensive_security_assessment_report.md   (拼接版·中文)
        │
        │  ② run-report-agent step
        ↓  REPORT agent · prompts/report-executive.txt  LLM 加执行摘要 + 清理
  comprehensive_security_assessment_report.md   (最终版·中文执行摘要 + 中文详述)
```

两个 step 都复用 shannon-py 已有的组件(`ReportAssembler`、`AgentExecutor`/`PromptManager`),不引入新引擎。

---

## 4. 设计

### 4.1 轴 1 — white-box reporting 接报告生成(确定性)

#### 4.1.1 ReportAssembler 提升到 core

`ReportAssembler` 当前在 `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`。为让 white-box 共用、并遵循"共享服务放 core"的既有约定(参照已共享的 `FindingsRenderer`),将其**提升到 `packages/core/src/shannon_core/services/report_assembler.py`**。

- `ReportAssembler.assemble()` 已实现三级文件回退(`*_exploitation_evidence.md` → `*_findings.md` → `*_analysis_deliverable.md`),**天然支持 white-box 产物**,逻辑无需改动。
- `ReportAssembler.inject_model_info()`(注入模型信息到执行摘要)一并迁移。
- blackbox 的 import 从 `shannon_blackbox.services.report_assembler` 改为 `shannon_core.services.report_assembler`;**blackbox 行为不变**。

#### 4.1.2 white-box 新增两个 activity

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 新增,基于 blackbox 版本(`packages/blackbox/src/shannon_blackbox/pipeline/activities.py:257` 的 `assemble_report` 与 `:291` 的 `run_report_agent`),适配 white-box 的 `ActivityInput`:

- `assemble_report(input: ActivityInput) -> None`:调 `ReportAssembler.assemble(deliverables, vuln_classes, report_path)`,产出拼接版综合报告。
- `run_report_agent(input: ActivityInput) -> dict`:以 `AgentExecutor` + `PromptManager` 跑 `AgentName.REPORT`,读拼接报告、加执行摘要 + 清理。该机制与 white-box 现有 `run_vuln_agent` **完全同款**(`AgentExecutor` + `PromptManager`),无需新引擎。

> 注:`AgentName.REPORT` 已定义(`packages/core/src/shannon_core/models/agents.py:134`,`prompt_template="report-executive"`,`deliverable_filename="comprehensive_security_assessment_report.md"`)。但其 `prerequisites` 当前指向 `*_EXPLOIT` agent——white-box 编排时不走 prerequisite 图,而是显式编排 reporting 两个 step,故不受影响(实现时确认编排路径)。

#### 4.1.3 white-box reporting phase 改造

`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` 的 reporting phase(当前 `:354-363` 仅调 `render_findings`),改为顺序执行:

1. `render_findings`(保留——为兼容未来 queue 机制,无害)
2. `assemble_report`(新增——确定性拼接)
3. `run_report_agent`(新增——LLM 执行摘要 + 清理)

#### 4.1.4 step_intents 补充中文 step

`packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py` 的 `reporting` phase,从单 step 扩展为:

```python
"reporting": (
    StepSpec("render-findings",   "渲染漏洞条目(若存在队列)"),
    StepSpec("assemble-report",   "拼接各分项报告"),
    StepSpec("run-report-agent",  "撰写执行摘要并清理报告"),
),
```

---

### 4.2 轴 2 — prompts 中文化(分层策略)

#### 4.2.1 关键现实:prompt 体量

逐文件中文化的真实体量:

| 文件 | 行数 | 说明 |
|---|---|---|
| `vuln-auth.txt` | 262 | 含 methodology、JSON queue 格式、置信度规则、7 个 shared include |
| `vuln-authz.txt` | 368 | 同上 |
| `vuln-injection.txt` | 378 | 同上 |
| `vuln-ssrf.txt` | 310 | 同上 |
| `vuln-xss.txt` | 295 | 同上 |
| `report-executive.txt` | 112 | 综合报告执行摘要 + 清理规则 |
| `shared/*.txt`(12 个) | 349 | 被 vuln/exploit prompt 共享的分析指令片段 |

vuln prompt 的内容分两类:
- **分析指令**(role / objective / methodology / rules / shared 片段 / JSON 格式 / 置信度):决定"**怎么分析**",与输出语言无关。GLM 读英文分析指令、写中文叙述完全可行。
- **输出指令**(`deliverable_instructions` 内的输出标题模板):决定"**输出什么语言/结构**"。

#### 4.2.2 分层:Layer 0(必交付)与 Layer 1(可选增量)

**Layer 0 — 输出层中文化(必交付,彻底产出中文报告)**

只改"决定输出语言"的部分,即可让 GLM 产出全中文报告:

1. 各 vuln prompt 的 `<deliverable_instructions>` 块:
   - 输出标题模板**中文化**(按 §4.2.3 词表);
   - 新增一段**输出语言约束**(见 §4.2.4):"全部叙述性文字用简体中文撰写;下列技术标识保留英文原文"。
2. `report-executive.txt`:执行摘要标题、`Summary by Vulnerability Type`、清理规则相关文案中文化(与 vuln 标题配套,见 §4.2.5)。
3. `shared/_target.txt` 等含"输出约定"的片段:核查并中文化其中影响输出的文案。
4. `pipeline-testing/` 下对应的 `vuln-*.txt`、`report-executive.txt`:同步中文化(保持 CI 与生产 prompt 一致)。

**Layer 1 — 分析指令中文化(可选增量,视 Layer 0 效果再定)**

把 methodology / shared 分析指令 / 规则也译为中文。

- **理由**:对 GLM 这类中文原生模型,中文分析指令更一致;但分析质量主要由模型能力决定,英文方法论 GLM 同样执行良好。
- **代价**:~1500 行翻译;回归风险(措辞改动可能微妙影响分析行为);边际收益低。
- **决策**:**本次不做(spec review 已确认 Layer 0 only)**。Layer 0 上线后人工冒烟评估中文报告质量;若叙述中混入明显英文腔,再作为后续增量单独启动 Layer 1。

> 说明:用户在 brainstorming 中认可"全中文 prompt"方向。本设计将其落为 Layer 0(彻底达成"全中文报告"实质目标)+ Layer 1(字面意义的"全中文 prompt",留作后续增量)。

#### 4.2.3 中文标题词表(Layer 0 落地依据)

vuln 分项报告(`*_analysis_deliverable.md`)输出标题统一约定:

| 英文(现) | 中文(Layer 0) |
|---|---|
| `# Authentication Analysis Report` | `# 认证分析报告` |
| `# Authorization Analysis Report` | `# 授权分析报告` |
| `# Injection Analysis Report` | `# 注入分析报告` |
| `# XSS Analysis Report` | `# XSS 分析报告` |
| `# SSRF Analysis Report` | `# SSRF 分析报告` |
| `## 1. Executive Summary` | `## 一、执行摘要` |
| `## 2. Dominant Vulnerability Patterns` | `## 二、主要漏洞模式` |
| `## 3. Strategic Intelligence for Exploitation` | `## 三、利用情报` |
| `## 4. Secure by Design: Validated Components` | `## 四、安全设计:已验证组件` |

综合报告(`comprehensive_security_assessment_report.md`)顶部:

| 英文(现) | 中文(Layer 0) |
|---|---|
| `# Security Assessment Report` | `# 安全评估报告` |
| `## Executive Summary` | `## 执行摘要` |
| `## Summary by Vulnerability Type` | `## 按漏洞类型汇总` |
| `Target:` / `Assessment Date:` / `Scope:` / `Exploitation:` | `目标:` / `评估日期:` / `范围:` / `利用情况:` |

漏洞条目标题:**ID 保留英文格式,描述部分中文化**,例如 `### AUTH-VULN-01: Session Fixation` → `### AUTH-VULN-01: 会话固定`。

#### 4.2.4 输出语言约束块(加入各 vuln prompt 的 deliverable_instructions)

```
<output_language>
全部叙述性、描述性文字用简体中文撰写。以下技术标识必须保留英文原文,不得翻译:
- 漏洞编号(如 AUTH-VULN-01、INJ-VULN-02)
- 代码、命令、文件路径、行号(如 server/app/controller/image.js:102)
- HTTP 方法与状态码(如 GET /api/fileProxyGet、HTTP 302)
- URL、请求头名、JSON 字段名、cookie 名
- 技术缩写(SSRF、SSTI、XSS、CSRF、RBAC、HSTS、IDOR、OAuth、JWT、PKCE)
</output_language>
```

#### 4.2.5 三大约束(实现时强制遵守)

1. **逻辑骨架不动**:prompt 里的模板变量(`{{WEB_URL}}`、`{{DELIVERABLES_PATH}}` 等)、漏洞 ID 正则(`### [TYPE]-VULN-NN`)、JSON queue 字段名、代码示例——**原样保留**,只翻译自然语言指令与标题。
2. **vuln 与 report-executive 成组改、配套测**:这是强耦合。`report-executive.txt` 的清理逻辑靠**匹配标题**决定保留/删除哪些 section;vuln 标题中文化后,report-executive 里对应的 `REPORT_VULN_HEADING` / `REPORT_VULN_SUBHEADING` / `REPORT_FILTER_RULES`(清理规则)必须同步中文化,否则 report agent 清理失配(保留垃圾 section 或误删漏洞列表)。两个 prompt **必须作为一组改、一起测**。
3. **模板变量来源核查**:`REPORT_FILTER_RULES`、`VULN_SUMMARY_SUBSECTIONS`、`REPORT_FILTERS_BLOCK`、`VULN_CLASSES_TESTED` 由 `packages/core/src/shannon_core/prompts/manager.py:111-160` **代码注入**。但 `REPORT_VULN_HEADING` / `REPORT_VULN_SUBHEADING` 在 manager.py 未见替换逻辑——**实现时须先查清其来源**(可能来自 `config.report`,或为未注入的死变量)。若是代码常量注入,中文化要同步改代码;若是死变量,需评估 report agent 清理逻辑当前是否本就部分失效。

---

## 5. 即时补救已完成的扫描(可选,待定)

触发本次设计的扫描(`workspaces/honor_shannon-1782117257489/`)已跑完,但分项 `*_analysis_deliverable.md` 为**英文**。仅重跑 reporting(轴 1)会得到「中文执行摘要 + 英文分项」的混合体,不满足"全中文"。全中文补救两条路:

| 路径 | 做法 | 成本 | 质量 |
|---|---|---|---|
| **(a) 翻译分项** | 对现有英文分项一次性翻译为中文,再跑轴 1 的 reporting | 低(几毛) | 翻译质量 |
| **(b) 重跑 vuln agent** | 用新中文 prompt 重跑 5 个 vuln agent,再跑 reporting | 高(再数十刀) | 原生中文,最高 |

**本次决策**:**纳入范围,走路径 (a)(翻译分项)**。轴 1 改造后,reporting 是独立 phase,可通过 white-box 现有的 resume 机制(`packages/whitebox/src/shannon_whitebox/pipeline/whitebox_resume.py`)从 reporting 步重跑。补救流程:一个小脚本对 `workspaces/honor_shannon-1782117257489/deliverables/` 下的英文分项 `*_analysis_deliverable.md` 做一次性翻译为中文(保留技术标识),再重跑 reporting(assemble + report-agent),即得到该次扫描的全中文综合报告。(resume 是否已支持从任意 phase 重跑、补救脚本的最终形态,在实现计划中确认。)

> spec review 时请确认:是否需要即时补救,以及走 (a) 还是 (b)。

---

## 6. 测试策略

1. **ReportAssembler 提升回归**:把 blackbox 现有相关测试(`packages/blackbox/tests/test_finalize_report.py` 等)迁移/镜像到 `packages/core/tests/`,确认 assemble + inject_model_info 行为不变。
2. **white-box reporting 集成测试**:构造含 `*_analysis_deliverable.md` 的临时 deliverables 目录,跑 reporting phase,断言生成 `comprehensive_security_assessment_report.md` 且内容为各分项拼接。
3. **blackbox 不回归**:运行 blackbox 报告相关测试,确认 import 改动后行为一致。
4. **prompt 中文化冒烟(人工)**:跑一次小规模 white-box 扫描(单 vuln class、小仓库),人工确认 `*_analysis_deliverable.md` 与综合报告为中文、技术标识保留英文、漏洞 ID 未被翻译。LLM 输出语言无法单元测试,以冒烟为准。
5. **已知测试陷阱**:广跑 pytest 有预存挂起/失败(见 [[feat-fork-py-test-gotchas]]),本次测试聚焦 `packages/core/tests/`、`packages/whitebox/tests/`、`packages/blackbox/tests/` 相关用例,按需 `--ignore` 预存问题。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| vuln 标题中文化与 report-executive 清理规则不同步 → 报告残缺 | §4.2.5 约束 2:两 prompt 成组改、配套测;实现时加一个"标题词表"常量,两侧引用同一来源 |
| `REPORT_VULN_HEADING` 来源不明 → 中文化遗漏 | §4.2.5 约束 3:实现首步即查清来源(manager.py / config.report / 死变量) |
| Layer 0 后叙述仍混入英文腔 | §4.2.2:Layer 1 作为兜底,冒烟评估后决定是否启动 |
| ReportAssembler 提升破坏 blackbox | §4.1.1:仅改 import 路径,逻辑零改动;§6 测试 3 兜底 |
| report agent 多一次 LLM 调用增加成本/耗时 | 可接受(单次报告 agent,相对 5 个 vuln agent 成本很小);后续可加 config 开关跳过执行摘要 |

---

## 8. 已定决策(spec review 结果)

1. **轴 2 范围 = Layer 0 only**。Layer 1(分析指令中文化)本次不做,留作冒烟评估后的增量。
2. **即时补救 = 纳入范围,走路径 (a) 翻译分项**。提供补救脚本,对 `honor_shannon-1782117257489` 的英文分项翻译后再重跑 reporting。
3. **report agent 执行摘要开关 = 不加**(YAGNI)。固定为"拼接 + 执行摘要"。

---

## 9. 实现交付物清单(供 writing-plans 参考)

- `packages/core/src/shannon_core/services/report_assembler.py`(从 blackbox 提升,新增)
- `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`(删除或改为 re-export)
- `packages/blackbox/.../activities.py`(import 改 core)
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`(新增 `assemble_report`、`run_report_agent`)
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(reporting phase 改造)
- `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`(reporting step 扩展)
- `prompts/vuln-{auth,authz,injection,ssrf,xss}.txt`(Layer 0 输出层中文化)
- `prompts/report-executive.txt`(中文化 + 与 vuln 标题配套)
- `prompts/pipeline-testing/vuln-*.txt`、`report-executive.txt`(同步中文化)
- 相关测试迁移/新增
- 即时补救脚本(翻译 `honor_shannon-1782117257489` 英文分项 → 重跑 reporting)
