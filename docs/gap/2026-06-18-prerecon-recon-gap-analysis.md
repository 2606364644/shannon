# shannon-py 重构项目 pre-recon / recon 阶段弱势项分析（vs 原始 Shannon）

> 对比原始 TypeScript Shannon（`/root/shannon`，分支 `feat/fork`）与 Python 重构版（`/root/shannon-py`，分支 `feat/fork-py`）在 **`pre-recon` 与 `recon` 两个流水线阶段**上的弱势项，为后续优化提供阶段级基线。
>
> **术语澄清（重要）**：本文档的「pre-recon / recon」指 Shannon 流水线里**真正的两个阶段**（确定性静态索引 + LLM 侦察 / 动态侦察），**不是** `docs/superpowers/specs/2026-06-17-pre-recon-weakness-analysis.md` 标题里那个"重构开工前的前期侦察"（meta 概念）。两者同名易混，但本文聚焦阶段实现。
>
> **数据来源**：4 个并行审计 agent 的代码级核验（pre-recon 静态层 / recon 动态编排 / 阶段间知识交付 / 阶段可靠性与 prompt 质量），均带 `file:line` 证据；P0 级头条论断由主控亲自复核（LLM taint stub、`write_index_files` 漏写 `parameter_graph.json`、recon 漏传 retry_policy、route_chains/attack_chains 孤儿）。
>
> **日期**：2026-06-18
>
> **核心结论**：重构项目在 pre-recon/recon 阶段呈现**"机制先进、接线残缺"的鲜明反差**——PY 拥有 TS 完全没有的确定性静态分析层（tree-sitter 5 语言 + GitNexus 双通道 call graph），但这条号称"确定性跨函数污点传播"的核心增量能力在生产中**产出丢失 / 客户端是桩 / tier 不驱动调度**，多条 P0 断链使其退化为"sink 调用点清单 + 入口点清单"。同时 recon 阶段漏传 retry_policy、白盒从不强制 recon-static、route_chains/attack_chains 孤儿产物等问题叠加，使 pre-recon/recon 的产出对下游 vuln/exploit agent 的**实际增益远低于设计预期**。本文同时**修正一条项目记忆**：route_chains 孤儿的根因不是"SharedKnowledge 通道丢失"，而是"产物写了不读 + 编排时序晚"（TS 的 SharedKnowledge 通道本身也是半死代码）。

---

## 目录

1. [分析范围与方法](#1-分析范围与方法)
2. [阶段全景：两边的 pre-recon / recon 流水线](#2-阶段全景两边的-pre-recon--recon-流水线)
3. [弱势项总览矩阵](#3-弱势项总览矩阵)
4. [P0 — 阶段核心能力断链](#4-p0--阶段核心能力断链)
5. [P1 — 产出有效性与可靠性硬伤](#5-p1--产出有效性与可靠性硬伤)
6. [P2 — 覆盖深度、可维护性与边角](#6-p2--覆盖深度可维护性与边角)
7. [关键归因修正：route_chains 孤儿的真根因](#7-关键归因修正route_chains-孤儿的真根因)
8. [平衡对比：PY 在 pre-recon/recon 上的优势](#8-平衡对比py-在-pre-reconrecon-上的优势)
9. [修复优先级路线图](#9-修复优先级路线图)
10. [附录](#10-附录)

---

## 1. 分析范围与方法

### 1.1 对比对象（聚焦 pre-recon/recon 两个阶段）

| 项 | 原始 Shannon (TS) | 重构 Shannon-py |
|---|---|---|
| pre-recon 静态层 | **无**——纯 LLM `pre-recon-code` agent（`workflows.ts:468-469` 单 agent 串行） | tree-sitter 5 语言 parser + GitNexus 双通道（CLI+MCP）call graph + AST sink detector（`code_index/`） |
| pre-recon 编排 | 串行单 agent | 并行 `run_code_index ∥ PRE_RECON`（LLM）+ sink merge / entry fusion / adjudication / framework / frontend / route-chain |
| recon 静态模式 | 白盒**强制** `recon-static`（`workflows.ts:752-754` + `runner.ts:189`） | 白盒**恒用动态** `recon`，从不强制 `recon-static` |
| 黑盒 recon | **无**（黑盒要求白盒 deliverables 存在，直接进 exploitation，`workflows.ts:967-972`） | 有 `recon-blackbox` 阶段（PY 独有，102 行 prompt） |
| 阶段间知识交付 | SharedKnowledge JSON 通道（**但半死代码，见 §7**）+ deliverable 自读 | 无 SharedKnowledge；deliverable 自读 + `static_dataflow_hints.md` 自读 |

### 1.2 验证方法

- 4 个独立维度并行审计（每个均要求 TS 侧 + PY 侧 `file:line` 证据，不存在项以「全仓 grep 零命中」佐证）。
- P0 级头条论断由主控**亲自读源码复核**：`activities.py:236-238`（taint stub）、`code_index/__init__.py:299-312`（write_index_files 漏写）、`workflows.py:226-229`（recon 无 retry_policy）、`activities.py:645/713`（孤儿产物）。
- 凡 06-17 文档已覆盖项（W-02/W-05/W-08/W-10）在本文以"细化/证伪/新增证据"形式标注，不重复展开。

### 1.3 本文新增价值（相对 06-17 文档）

06-17 文档是**全局工程化**视角，对 pre-recon/recon 只在 W-10 零散提及。本文是**阶段级深度**视角，新增 06-17 完全没有的 P0 发现：
- **PR-01** chain-wide taint 产出永不落盘（`ParameterPropagationGraph` 孤儿）
- **PR-02** 生产 LLM taint 客户端是 `return "{}"` 桩
- **PR-03** recon agent **完全漏传 retry_policy**（06-17 的 W-08 只笼统说"whitebox 未接 retry profile"，未发现 recon 连 policy 都没传）
- **PR-05** route_chains/attack_chains 孤儿产物 + attack_chains 编排时序错误（vuln 之后才跑）

并**证伪/细化**了 06-17 的 W-10 与一条项目记忆（见 §7）。

---

## 2. 阶段全景：两边的 pre-recon / recon 流水线

### 2.1 PY 白盒（`whitebox/pipeline/workflows.py`）

```
setup(preflight+cred+auth) →
  pre-recon 阶段 (131-211):
    并行: run_code_index(确定性,10min) ∥ run_agent(PRE_RECON, LLM, 2h, PRODUCTION_RETRY)
    → run_merge_sink_reports → run_entry_point_fusion → run_save_adjudication
    → 并行: run_framework_analysis ∥ run_frontend_mapping
    → run_route_chain_building            ★ 写 route_chains.json（孤儿，见 PR-05）
  recon 阶段 (213-237):
    run_agent(RECON, prompt="recon", 2h)   ★ 无 retry_policy（见 PR-03）；恒动态模板（见 PR-10）
  risk-scoring (240-265):
    run_risk_scoring → run_render_dataflow_hints   ★ 此处才生成 static_dataflow_hints.md（见 PR-09 时序）
  vuln 阶段 (267-311): 5 类并行 run_vuln_agent
  attack-chain (313-337): run_attack_chain_assembly  ★ 写 attack_chains.json（孤儿 + 时序晚，见 PR-05）
  reporting
```

### 2.2 TS 白盒（`temporal/workflows.ts`）

```
setup →
  pre-recon: runSequentialPhase('pre-recon', single LLM pre-recon-code agent)   ★ 无确定性层
  recon: runSequentialPhase('recon', promptOverride='recon-static')             ★ 强制静态模板
  vuln → ...
  (无独立 route-chain / attack-chain JSON 产物阶段；attack-chain-builder 在 vuln 后读 SharedKnowledge)
```

### 2.3 黑盒

- **TS**：黑盒 workflow（`workflows.ts:849,967-972`）**不跑 recon**，强制要求白盒 deliverables 存在，直接进 exploitation。
- **PY**：黑盒（`blackbox/pipeline/workflows.py:155-170`）在无白盒结果时跑 `RECON_BLACKBOX`（`recon-blackbox.txt`，102 行）。设计分歧，非 bug（见 PR-21）。

---

## 3. 弱势项总览矩阵

| # | 领域 | 弱势项 | 严重度 | 阶段 | TS 对照 | 一句话 |
|---|---|---|---|---|---|---|
| **PR-01** | 静态层 | chain-wide taint 传播产出永不落盘（`ParameterPropagationGraph` 孤儿） | **P0** | pre-recon | TS 无此能力 | PY 核心增量能力产出丢失，taint_completeness 恒 0 |
| **PR-02** | 静态层 | 生产 LLM taint 客户端是 `return "{}"` 桩 | **P0** | pre-recon | TS 无此子步 | 即便 PR-01 修好，喂入的也是"全参数 tainted"过近似 |
| **PR-03** | 可靠性 | **recon agent 完全漏传 retry_policy** | **P0** | recon | TS 显式 PRODUCTION_RETRY | 回退 SDK 默认无限重试，行为不可控（06-17 未发现） |
| **PR-04** | 可靠性 | resume 死分支：pre-recon/recon 中断后从头重跑 | **P0** | pre-recon/recon | TS 完整链 | 2h 级长跑守卫形同虚设（细化 06-17 W-02） |
| **PR-05** | 知识交付 | route_chains.json / attack_chains.json 孤儿 + attack_chains 编排时序晚 | **P0** | pre-recon→vuln | TS 至少 attack-chain-builder 读 | pre-recon 产出写了下游零读（修正记忆归因） |
| PR-06 | 静态层 | tiered audit tier 分级不驱动 vuln agent 调度 | P1 | pre-recon→vuln | TS 无 tier | 恒每类 1 agent，"tiered audit"退化为等权 |
| PR-07 | 静态层 | entry_points.json adjudication 裁决无下游消费 | P1 | pre-recon | TS 无裁决 | 跑了 adjudication 但产出纯开销 |
| PR-08 | 静态层 | enhanced_parameters typed params 算完即弃 | P1 | pre-recon | TS 无 | LLM taint 拿不到参数 source 类型 |
| PR-09 | 知识交付 | recon hint 时序竞态：recon 早于 render_dataflow_hints | P1 | recon | TS 无 hints | recon agent 永远拿不到静态 hints |
| PR-10 | recon 编排 | 白盒 recon 恒用动态 recon.txt，从不强制 recon-static | P1 | recon | TS 双重强制 | 白盒无 URL 场景 prompt 指令错误（细化 W-10） |
| PR-11 | 知识交付 | framework/frontend analysis JSON 不进下游 prompt | P1 | pre-recon→vuln | TS 进 shared-knowledge.json | vuln 看不到结构化框架/前端线索 |
| PR-12 | 知识交付 | PromptManager 无动态知识注入能力 | P1 | 跨阶段 | TS 有死代码 | 阻塞未来 prompt 时结构化注入 |
| PR-13 | 可靠性 | 长跑 activity 无 heartbeat | P1 | pre-recon/recon | TS 60min+2s 心跳 | 假死要等满 2h（细化 W-05） |
| PR-14 | 可靠性 | retry profile 不按 mode 切（testing/subscription 失效） | P1 | pre-recon/recon | TS 按 mode 切 | get_retry_policy 选择器写好零调用（细化 W-08） |
| PR-15 | prompt | recon-static.txt 孤儿 + 内容陈旧（164 vs 443 行） | P1 | recon | TS 强制使用 | docstring/孤儿双重误导（细化 W-10） |
| PR-16 | 可维护性 | degradation / coverage_report 模块完全孤儿 | P2 | pre-recon | TS 无 | 可观测性卖点无产出 |
| PR-17 | 静态层 | gitnexus sink 反向溯源三函数孤儿 | P2 | pre-recon | TS 无 | sink→caller 倒推能力未接 |
| PR-18 | 静态层 | 多语言仓库只索引主语言 | P2 | pre-recon | TS 无（全交 LLM） | 前后端混合仓前端线索缺失 |
| PR-19 | 可靠性 | code_index activity 无 retry_policy | P2 | pre-recon | TS 有 PRODUCTION_RETRY | 确定性步骤失败行为不可预期 |
| PR-20 | 知识交付 | static_dataflow_hints 弱化为 agent 自读（可跳过） | P2 | recon/vuln | TS 无此层 | 线索送达依赖 agent 自律 |
| PR-21 | 黑盒 | blackbox recon 设计分歧 + recon-blackbox 薄 + BlackboxActivityInput 缺字段 | P2 | recon(blackbox) | TS 黑盒不做 recon | 设计分歧非 bug，但 prompt 偏薄 |

> 严重度图例：**P0** = 阶段核心能力断链 / 虚假能力承诺；**P1** = 产出有效性 / 可靠性硬伤；**P2** = 覆盖深度 / 可维护性 / 边角。

---

## 4. P0 — 阶段核心能力断链

### PR-01 chain-wide taint 传播产出永不落盘（`ParameterPropagationGraph` 孤儿）

这是 pre-recon 静态层**最核心的断链**——PY 相对 TS 的头号增量能力"确定性跨函数污点传播"在生产中**不产出任何可用数据**。

- **产出侧**：`code_index/__init__.py:182` 构造 `pgraph = ParameterPropagationGraph(taint_flows=...)`，`:186` 还打印了 `len(pgraph.taint_flows)` 日志——但 `CodeIndex` 模型**没有 `parameter_graph` 字段**（`models.py` grep 零命中），`:212` 注释自承「CodeIndex does not have a parameter_graph field. The pgraph is stored separately and can be written by write_index_files.」。
- **持久化侧（关键：docstring 说谎）**：`write_index_files`（`:299-312`）的 docstring（`:300`）声称「Write code_index.json, code_index_summary.md, **and parameter_graph.json**」，但函数体（`:304-308`）只写前两个文件，`:310-311` 注释「parameter_graph built in build_code_index_with_gitnexus if available / Try to get it from a side-channel **or skip**」后**直接 return 不写**。docstring 与实现不符，是审查盲点。
- **消费侧**：`run_risk_scoring`（`activities.py:411`）和 `run_render_dataflow_hints`（`:504`）都 `deliverables / "parameter_graph.json"` 读——读一个**永不存在的文件**，恒得空 `ParameterPropagationGraph()`。
- **后果链**：`ChainRiskScore.score()` 的 `taint_completeness` 维（`risk_scorer.py:122-129`）因 `taint_flows` 恒空而**恒为 0**；tier 评分退化为准 3 维；`audit_input_builder._taint_flows()`（`:248-275`）渲染的"污点流"section **在生产 hints 文件里永远为空**——即 `static_dataflow_hints.md` 里 source→sink 流这一关键 section 是空的。
- **TS 对照**：TS 无确定性污点分析能力，无可比基线。PY 是**唯一声称有此能力**的实现，但产出丢失。
- **修复方向**：① 给 `CodeIndex` 加 `parameter_graph` 字段（或让 `write_index_files` 真正写 `parameter_graph.json`）；② 修正 docstring；③ 验证消费侧能读到非空 flows。

### PR-02 生产 LLM taint 客户端是 `return "{}"` 桩

即便 PR-01 修好，喂入传播的 per-function taint 结果也来自一个**桩客户端**。

- **证据**：`activities.py:236-238`
  ```python
  async def _llm_taint_client(prompt: str, **kwargs) -> str:
      # Placeholder: in production, this calls run_claude_prompt
      return "{}"
  ```
  该桩在三条 code path 全部使用：GitNexus MCP 路径（`:262`）、MCP 失败降级（`:272`）、未 indexed 降级（`:280`）。
- **后果**：`analyze_taint_llm` 永远拿到空 JSON → `parse_llm_response` 返回空 `TaintAnalysisResult`（`llm_taint_analyzer.py:188`）→ 走 conservative fallback「**全参数 tainted**」（`:284-288`）。per-function taint 精度归零，且即使 PR-01 修好，传播的也是"全 tainted"噪声。
- **TS 对照**：TS 无 per-function LLM taint 子步。
- **修复方向**：把 `_llm_taint_client` 接到真实的 `run_agent`/provider 调用（`run_claude_prompt` 等价物）。

### PR-03 recon agent 完全漏传 retry_policy（06-17 未发现）

06-17 的 W-08 只笼统说"whitebox 未接 retry profile"，但**没有发现 recon 连 retry_policy 参数都没传**——这是比 W-08 更具体的 P0。

- **证据**：`workflows.py:226-229`
  ```python
  metrics = await workflow.execute_activity(
      activities.run_agent, recon_input,
      start_to_close_timeout=timedelta(hours=2),
  )  # ← 无 retry_policy=
  ```
  对比同文件 pre-recon 的 PRE_RECON 显式传了 `retry_policy=PRODUCTION_RETRY`（`:157`）。
- **后果**：recon 回退到 temporalio Python SDK 默认 RetryPolicy（无限重试 / initial 1s / backoff 2.0 / max_interval 100s），而非项目任何命名 profile。transient 失败时重试节奏与项目意图脱钩，可能风暴重试或与 pre-recon 行为不一致。
- **TS 对照**：TS recon 经 `runSequentialPhase`（`workflows.ts:472`）走 `acts` proxy，显式 `retry: PRODUCTION_RETRY`（`:96`）。
- **修复方向**：`workflows.py:226-229` 补 `retry_policy=PRODUCTION_RETRY`（并见 PR-14 接 mode-aware 选择器）。

### PR-04 resume 死分支：pre-recon/recon 中断后从头重跑（细化 06-17 W-02）

pre-recon / recon 是**全流水线最长跑的阶段**（2h 级），中断重跑成本最高，而 resume 守卫形同虚设。

- **守卫**：`workflows.py:131`（`if AgentName.PRE_RECON.value not in self._state.completed_agents`）、`:213`（recon 同构）。
- **死因**：`completed_agents` 是 `PipelineState` 实例字段（`shared.py:25`），`WhiteboxScanWorkflow.__init__`（`:33-34`）每次新建空 state；全仓 grep `load_resume` / `loadResumeState` / `restore_git` / `shouldSkip` / `saveCheckpoint` **零命中**，无任何代码在 workflow 入口从磁盘重建 `completed_agents`。
- **后果**：守卫恒为 `True not in []` → 永远从 pre-recon code_index + PRE_RECON 2h + RECON 2h 从头重跑。
- **TS 完整链**：`loadResumeState`（`workflows.ts:282-287`）→ `restoreGitCheckpoint`（`:294-299`）→ `recordResumeAttempt`（`:313-319`）→ `shouldSkip` 闭包（`:324-326`，注释「Temporal replay safety by design」）→ 每 phase `saveCheckpoint`（`:340-342`）。PY **每一环都缺**。

### PR-05 route_chains.json / attack_chains.json 孤儿 + attack_chains 编排时序晚

pre-recon 产出的路由级攻击链和组装的多步攻击链，**下游零消费**，且 attack_chains 跑在 vuln 之后（来不及喂 vuln agent）。**这一项修正了项目记忆对 route_chains 孤儿的归因（见 §7）。**

- **route_chains.json**：`run_route_chain_building`（`activities.py:645`）写入，但全仓非测试代码**零读者**（grep `route_chains.json` 仅命中 writer）。
- **attack_chains.json**：`run_attack_chain_assembly`（`activities.py:713`）写入，下游零读者；且编排于 vuln 阶段**之后**（`workflows.py:313-328`，vuln 在 `:267-311`）——即便有读者也来不及喂 vuln/exploit agent，只能服务 reporting。
- **TS 对照**：TS 无独立 route_chains.json；attack_chains 由 `buildAttackChainsActivity`（`activities.ts:1042`）写 shared-knowledge.json 供 attack-chain-builder 读（也在 vuln 后，但 TS 至少有读者）。
- **修复方向**：见 §7 根因——优先把 route_chains 在 vuln 阶段前注入 prompt（或经 PromptManager 占位符），并调整 attack_chains 时序或明确其仅服务报告。

---

## 5. P1 — 产出有效性与可靠性硬伤

### 5.1 静态层产出有效性（PR-06 ~ PR-08）

| # | 项 | PY 现状（证据） | TS 对照 |
|---|---|---|---|
| PR-06 | tiered audit tier 不驱动调度 | `AuditBudget.estimate_calls`（`risk_scorer.py:188-198`）定义 tier3=5/tier2=2/tier1=1 agents 预算，但 `workflows.py:276-294` 对每类恒起 **1 个** vuln agent，无 per-tier 分发；`build_chain_audit_input`/`build_tier1_audit_input`（`audit_input_builder.py:27,90`）整体孤儿 | TS 无 tier，也是每类 1 agent |
| PR-07 | entry_points adjudication 无消费 | `save_adjudication`（`__init__.py:424`）写 CONFIRMED/NEEDS_REVIEW/REJECTED，但下游零逻辑消费（仅 `deliverables.py:35` 枚举映射） | TS 无裁决步骤 |
| PR-08 | typed params 算完即弃 | `_build_typed_params_by_block`（`__init__.py:25-48`）定义后全仓零调用；`analyze_taint_llm` 的 `typed_params` 形参在生产调用点（`:170-174`）从不传入；spec 自承 Go/Java/PHP 抽取返回空（`:29`） | TS 无 |

> 这三项共同构成 pre-recon 静态层的"**设计做完了、调度没接**"模式：tier 评分、入口裁决、参数类型都算出了结构化结果，但没有任何代码据此改变 agent 行为或注入 prompt。

### 5.2 recon 阶段编排与 prompt（PR-09, PR-10, PR-15）

**PR-09 recon hint 时序竞态**：`recon.txt:13` `@include(shared/_static-dataflow-hints.txt)` 指向 `static_dataflow_hints.md`，但该文件由 `run_render_dataflow_hints` 在 **risk-scoring 阶段**（`workflows.py:258`）才写，而 recon 在 `:226` 先于它运行 → **recon agent 运行时 hints 文件必不存在**。好在 `_static-dataflow-hints.txt:2` 有「若文件不存在，跳过本段」兜底，所以不报错但 recon **完全拿不到静态线索**；hints 实际只对 vuln 阶段（晚于 risk-scoring）有效。

**PR-10 白盒 recon 恒用动态模板**：PY `AgentName.RECON` 定义 `prompt_template="recon"`（`agents.py:46-51`），whitebox workflow 构造 `recon_input` 时**从不赋 `prompt_override`**（`workflows.py:225`）→ 恒用动态 `recon.txt`。该 prompt 在 `:155/:160` 明确写「navigate to the target」「in the browser」——白盒无 live target 场景下这是错误指令。TS 在 `workflows.ts:752-754`（workflow）+ `runner.ts:189`（local）**双重强制** `promptOverride: 'recon-static'`。PY 的 `prompt_override` 机制（`executor.py:56 template_name = prompt_override or defn.prompt_template`）本身健全，缺陷纯在调用方未赋值——修复成本极低（`workflows.py:225` 加一行）。

**PR-15 recon-static.txt 孤儿 + 内容陈旧**：`prompts/recon-static.txt` 在 PY 全代码（`packages/*/src`）**零引用**（孤儿 prompt），且仅 164 行 vs TS 443 行（缩水 63%），还是旧版"Phase 1/2/3 + §1-§7"扁平结构，缺 TS 的 §3 角色架构 / §7 权限格 / §8 越权候选 / §9 Injection 等关键章节。运行时影响：白盒 recon 实际走的是动态 `recon.txt`（500 行，结构完整），故**运行时 recon 质量不差**——缺陷是孤儿文件误导维护者 + 缺 TS "静态 recon 模式"入口。

### 5.3 阶段间知识交付（PR-11, PR-12）

**PR-11 framework/frontend analysis 不进下游 prompt**：`framework_analysis.json`（`activities.py:543`）、`frontend_mapping.json`（`:573`）是结构化 JSON，但仅 `attack_chain_assembly` 内部读（`:692-707`），**不进任何 vuln/exploit prompt**。TS 的等价物写入 shared-knowledge.json（虽注入端死代码，但 attack-chain-builder 会读）。

**PR-12 PromptManager 无动态注入能力**：`prompts/manager.py:74-155` 的 `_interpolate()` 仅插值**静态 config 变量**（`{{WEB_URL}}`/`{{REPO_PATH}}`/`{{AUTH_CONTEXT}}`/`{{RULES_*}}` 等），**无** `{{SHARED_KNOWLEDGE}}`/`{{ROUTE_CHAINS}}`/`{{ATTACK_CHAINS}}` 占位符；`@include` 是静态文件原样拼接无运行时数据填充。这是阻塞未来任何"prompt 时结构化注入"优化的能力缺口——即便移植 knowledge-store 也无法把数据塞进 prompt。

### 5.4 阶段可靠性（PR-13, PR-14）

**PR-13 无 heartbeat**（细化 W-05）：pre-recon/recon 全部 `execute_activity` 不传 `heartbeat_timeout`，activity 实现内 `activity.heartbeat()` 全仓零命中（`activities.py:74-125,222-298`）。TS `acts` proxy `heartbeatTimeout: '60 minutes'`（`workflows.ts:95`），activity 内 2s 周期 `setInterval` 心跳且 payload 带 `{agent, elapsedSeconds, attempt}`（`activities.ts:59,165-168`）。后果：PY activity worker 假死 / SDK 阻塞时要等满 2h `start_to_close_timeout` 才失败，TS ~60min 探测 + 诊断信息。

**PR-14 retry profile 不按 mode 切**（细化 W-08）：`retry.py:54-64` 提供了 `get_retry_policy(mode)` 选择器，但 `packages/whitebox/src` **零调用**。pre-recon 写死 `PRODUCTION_RETRY`（`workflows.py:157`），recon 根本无 policy（PR-03），code_index 也无（PR-19）。TS `selectActivityProxy` 按 `pipelineTestingMode`/`subscription` 切 `testActs`/`subscriptionActs`/`acts`（`workflows.ts:214-218,220`）。后果：PY 在 testing 模式无法快速失败（TESTING_RETRY 5 次/10-30s）、subscription 模式无法长退避（SUBSCRIPTION_RETRY 100 次/6h），全部按生产或默认节奏跑。

> **澄清**：06-17 文档曾对 pre-recon gather 的 fail-fast 注释存疑。经核验，`workflows.py:148-159` 未传 `return_exceptions=True`，asyncio.gather 默认语义即首个异常 cancel 其余——**fail-fast 注释准确**，与 vuln 段 `return_exceptions=True`（`:297`）形成有意对比，不构成缺陷。仅 2 并发，无资源放大问题。

---

## 6. P2 — 覆盖深度、可维护性与边角

| # | 项 | PY 证据 | TS 对照 | 影响 |
|---|---|---|---|---|
| PR-16 | degradation / coverage_report 孤儿 | `build_degradation_report`（`__init__.py:14` import 后零调用）；`coverage_report.py` 整模块（`AuditTierReport`/`Phase0Coverage`/`Phase3Coverage`/`CoverageReport`）非测试零调用 | TS 无 | 设计文档里的覆盖率/降级可观测性卖点生产无产出 |
| PR-17 | gitnexus sink 反向溯源孤儿 | `trace_from_sink`/`find_sinks_by_patterns`/`get_function_context`（`gitnexus_call_graph.py:312,396,426`）非测试零调用；仅 `build_call_graph_from_gitnexus` 进生产 | TS 无 | sink→caller 倒推已实现未接，削弱"从危险 sink 倒推可达入口" |
| PR-18 | 多语言只索引主语言 | `detect_language`（`parser.py:40`）`most_common(1)`；`detect_all_languages`（`:64`）存在但生产路径不用 | TS 无（全交 LLM） | Python 后端+TS 前端混合仓前端 XSS/CSRF 静态线索缺失 |
| PR-19 | code_index 无 retry_policy | `workflows.py:150-153`（`run_code_index`，10min，无 retry_policy） | TS pre-recon 共用 acts proxy（含 PRODUCTION_RETRY） | 确定性步骤失败回退 SDK 默认重试（与 PR-03 同源） |
| PR-20 | static_dataflow_hints 弱为自读 | `_static-dataflow-hints.txt:2`「若文件不存在，跳过本段」——agent 可跳过 | TS 无此层（PY 独有创新） | 静态线索送达依赖 agent 自律，非 prompt 强约束 |
| PR-21 | 黑盒 recon 设计分歧 | PY 黑盒无白盒结果时跑 `recon-blackbox`（`workflows.py:155-170`，102 行 prompt）；`BlackboxActivityInput`（`shared.py:33-43`）缺 `prompt_override` 字段 | TS 黑盒不做 recon（强制要白盒 deliverables） | 设计分歧非 bug；但 recon-blackbox.txt 偏薄 + 缺 prompt_override 限制未来灵活性 |

---

## 7. 关键归因修正：route_chains 孤儿的真根因

项目记忆 `shared-knowledge-injection-lost-in-refactor.md` 记录：**"重构未移植原始 SharedKnowledge 动态注入通道，是 route_chains 孤儿产物 / vuln 拿不到结构化链上下文的根因"**。本次阶段级审计**部分证伪、部分细化**该归因——方向对，但定位需修正：

### 7.1 TS 的 SharedKnowledge 通道本身也是半死代码

| 环节 | TS 状态 | 证据 |
|---|---|---|
| 数据结构 | 5 字段定义全 | `shared-knowledge.ts:18-24` |
| 写入 frameworkAnalysis | ✅ 写 | `activities.ts:265-275` |
| 写入 frontendRoutes | ✅ 写 | `activities.ts:293-302` |
| 写入 attackChains | ✅ 写 | `activities.ts:1042-1048` |
| 写入 endpointInventory | ❌ **零写入方** | 字段定义于 `:20,33` 但无 activity 写 |
| 写入 vulnerabilityContext | ❌ **零写入方** | 字段定义于 `:22,57` 但无 activity 写 |
| 注入 `_shared-knowledge.txt` | ❌ **从未被任何 agent prompt @include** | grep `_shared-knowledge` 在 `apps/worker/prompts` 零命中 |
| `buildSharedKnowledgeContext()` | ❌ **零调用方** | 仅定义于 `prompt-manager.ts:296` |
| `{{SHARED_KNOWLEDGE}}` 占位 | ❌ 恒填兜底串 | `prompt-manager.ts:476-481`「No shared knowledge available」 |
| **唯一真正生效的读者** | attack-chain-builder | `attack-chain-builder.ts:27-52` 读 frameworkAnalysis + frontendRoutes |

**即**：TS 的 SharedKnowledge JSON 通道 = **framework→attack-chains 这一条线有效**，其余（endpointInventory/vulnerabilityContext 写入 + prompt 注入）**全是设计稿未接线**。TS 真正生效的跨阶段知识交付其实是**另一条通道**：deliverable markdown 文件 + 下游 agent 自己读（`vuln-authz.txt:41,53` 反复指示读 `recon_deliverable.md` Section 4/8）。

### 7.2 真正的根因（三层）

| 根因 | 描述 | 证据 |
|---|---|---|
| **R1（不存在，但影响小）** | PY 未移植 SharedKnowledge 类型/knowledge-store/`_shared-knowledge.txt`。但 TS 这条通道本身半死，"丢了它"实际损失有限。 | PY grep 零命中；TS 注入端零调用 |
| **R2（真正影响：孤儿 + 时序）** | PY 把 route_chains/attack_chains 写成 JSON 但**下游 prompt 零引用**，且 attack_chains 编排在 vuln **之后**（`workflows.py:326`）。**这是 route_chains 孤儿的直接根因。** | §4 PR-05 |
| **R3（能力缺失：无注入机制）** | PY `PromptManager._interpolate()` 不支持知识占位符。即便移植 knowledge-store 也塞不进 prompt。修复需同时补：knowledge 持久化 + PromptManager 占位符渲染 + prompt 模板 `@include` + 各 activity 预渲染。 | `manager.py:74-155` |

### 7.3 结论

原假设**"方向对、定位偏"**。route_chains 孤儿的根因**不是** SharedKnowledge 未移植（TS 的 SharedKnowledge 也没真喂 vuln），而是 **R2（产物写了不读 + 编排时序晚）**。SharedKnowledge 通道缺失是 R1/R3——只有未来要做"prompt 时结构化注入"才需要，而当前 TS 也没做到。

> **记忆更新**：`shared-knowledge-injection-lost-in-refactor.md` 的归因已按本节修正。

---

## 8. 平衡对比：PY 在 pre-recon/recon 上的优势

为避免"只看弱势"的偏颇，如实记录 PY 在这两个阶段**相对 TS 的超越之处**——这些是后续优化时应当保留的资产：

1. **确定性静态分析层（TS 完全没有）**：tree-sitter 5 语言真实 parser（go/java/php/python/typescript，`parsers/*.py` 均真实 `import tree_sitter_*`，非占位）+ AST sink detector（`sink_detector.py`）+ GitNexus 双通道 call graph（CLI `subprocess` + MCP stdio JSON-RPC，真实集成非桩，`gitnexus_engine.py:49-51,133`/`gitnexus_mcp.py:45-59`）。三级降级路径真实（indexed→MCP / MCP fail→StubMCP+auto_index / 未 indexed→minimal AST）。**sink_danger 维是唯一真实生效的风险维度。**
2. **pre-recon 并行化**：`run_code_index ∥ PRE_RECON`（`workflows.py:149-159`）并行跑确定性层与 LLM，TS 是单 agent 串行。
3. **static_dataflow_hints 静态线索通道**（PY 独有）：`static_dataflow_hints.md` 经 `@include` 进 vuln prompt（`vuln-*.txt`），是静态产出流入 LLM 的真实通道（虽是"指针式"自读，见 PR-20）。
4. **黑盒 recon 能力**（PY 独有）：`recon-blackbox` 让纯黑盒场景也能跑 recon（TS 黑盒强制要求白盒 deliverables）。
5. **recon.txt 动态模板保真良好**：500 行 vs TS 484，结构对齐（§0-§9 + Guards），PY 还多了"Parameter Propagation Graph"段（Spec B/C 注入位）。
6. **pre-recon-code.txt 增强版**：472 行 > TS 433，多出 `<phase0_data>` code_index 统计注入块 + Phase 0 Code Index Review 段（Spec B 接线）。
7. **entry-point fusion + adjudication 机制存在**（虽下游未消费，见 PR-07，但机制本身是 TS 没有的确定性+LLM 入口点融合能力）。

> **核心判断**：PY 在 pre-recon/recon 的**机制完备性**上明显领先 TS，问题集中在**接线深度**（PR-01/02/03/05 几条 P0 断链）和**产出→下游消费**（PR-05/06/07/11 一组"算了不读"孤儿）。修复这些断链的 ROI 极高——机制已就位，缺的是"把产出接下去"的最后一公里。

---

## 9. 修复优先级路线图

### P0 — 立即（恢复阶段核心能力）

| # | 任务 | 工作量 | 说明 |
|---|---|---|---|
| 1 | PR-01 让 ParameterPropagationGraph 落盘 | S | 给 `CodeIndex` 加字段或让 `write_index_files` 真写 `parameter_graph.json`；修正 docstring（`:300`）；验证 `activities.py:411/504` 读到非空 flows |
| 2 | PR-02 接真实 LLM taint 客户端 | S | `activities.py:236-238` 把 `return "{}"` 桩接到 provider/`run_agent` |
| 3 | PR-03 recon 补 retry_policy | XS | `workflows.py:226-229` 加 `retry_policy=PRODUCTION_RETRY`（一行） |
| 4 | PR-05 route_chains/attack_chains 接下游或明确语义 | M | 优先把 route_chains 在 vuln 前注入 prompt；attack_chains 调整时序或明确仅服务报告（见 §7 R2） |

> PR-04（resume）虽是 P0，但属全局工程化问题（06-17 W-2 已立项），本文不重复排期。

### P1 — 短期（产出有效性与可靠性）

| # | 任务 | 工作量 |
|---|---|---|
| 5 | PR-10 白盒强制 recon-static（`workflows.py:225` 加 override）+ PR-15 补 recon-static.txt 至 TS 水平 | M |
| 6 | PR-14 接 mode-aware retry（`get_retry_policy`）覆盖 pre-recon/recon/code_index（含 PR-03/PR-19） | S |
| 7 | PR-13 长跑 activity 加 `heartbeat()` + `heartbeat_timeout` | S |
| 8 | PR-06 tiered audit tier 驱动 vuln agent 调度（接 `AuditBudget`） | M |
| 9 | PR-09 调整 recon/render_dataflow_hints 时序，让 recon 能拿到 hints | S |
| 10 | PR-11 framework/frontend JSON 注入下游 prompt（需先做 PR-12） | M |
| 11 | PR-12 PromptManager 加动态知识占位符 + 渲染能力 | M |
| 12 | PR-07 entry_points adjudication 接 authz 优先级；PR-08 typed params 接 taint | S/M |

### P2 — 中期（深度与可维护性）

| # | 任务 | 工作量 |
|---|---|---|
| 13 | PR-16 接 degradation/coverage_report 产出（或删除死代码） | S |
| 14 | PR-17 接 gitnexus sink 反向溯源三函数 | S |
| 15 | PR-18 多语言仓库索引（用 `detect_all_languages`） | M |
| 16 | PR-20 static_dataflow_hints 改为 prompt 时注入（强约束） | M |
| 17 | PR-21 评估黑盒 recon 契约（对齐 TS 或强化 recon-blackbox.txt）+ 补 `BlackboxActivityInput.prompt_override` | S |

---

## 10. 附录

### 10.1 file:line 证据索引（PY 侧）

| 主题 | 文件:行 |
|---|---|
| taint stub | `whitebox/pipeline/activities.py:236-238`（定义）、`:262/272/280`（使用） |
| ParameterPropagationGraph 孤儿 | `code_index/__init__.py:182`（构造）、`:212`（注释自承无字段）、`:214-227`（return 不带）、`:299-312`（write_index_files 漏写 + docstring 说谎）；`code_index/models.py`（无字段） |
| taint_completeness 恒 0 | `code_index/risk_scorer.py:122-129`；消费侧 `activities.py:411/504` |
| 污点流 section 恒空 | `code_index/audit_input_builder.py:248-275` |
| recon 无 retry_policy | `whitebox/pipeline/workflows.py:226-229` |
| pre-recon PRODUCTION_RETRY | `whitebox/pipeline/workflows.py:154-158` |
| resume 死守卫 | `whitebox/pipeline/workflows.py:131,213`；`pipeline/shared.py:25` |
| route_chains 孤儿 | `whitebox/pipeline/activities.py:645`（仅 writer） |
| attack_chains 孤儿+时序 | `whitebox/pipeline/activities.py:713`（仅 writer）；编排 `workflows.py:313-328`（vuln 后） |
| recon 恒动态模板 | `core/models/agents.py:46-51`；`workflows.py:225`（不赋 override） |
| recon-static 孤儿 | grep `recon-static` 在 `packages/*/src` 零命中；`prompts/recon-static.txt`（164 行） |
| PromptManager 无注入 | `core/prompts/manager.py:74-155` |
| tier 不驱动调度 | `code_index/risk_scorer.py:188-198`；`workflows.py:276-294` |
| heartbeat 零命中 | grep `heartbeat` in `packages/*/src` 零命中 |
| get_retry_policy 零调用 | `core/models/retry.py:54-64`；grep in `packages/whitebox/src` 零命中 |

### 10.2 file:line 证据索引（TS 侧）

| 主题 | 文件:行 |
|---|---|
| 强制 recon-static | `temporal/workflows.ts:752-754`；`local/runner.ts:189` |
| heartbeat（60min + 2s 心跳） | `temporal/workflows.ts:92-97`；`temporal/activities.ts:59,165-168,253` |
| mode-aware retry | `temporal/workflows.ts:214-218,220,665` |
| resume 全链 | `temporal/workflows.ts:282-287,294-299,313-319,324-326,340-342` |
| SharedKnowledge 半死 | `services/prompt-manager.ts:296-378`（buildSharedKnowledgeContext 零调用）、`:476-481`（占位恒兜底）；`prompts/shared/_shared-knowledge.txt`（零 @include） |
| SharedKnowledge 唯一生效读者 | `services/attack-chain-builder.ts:27-52` |
| 黑盒不做 recon | `temporal/workflows.ts:849,967-972` |

### 10.3 prompt 行数矩阵

| prompt | PY 行数 | TS 行数 | 差距 | 评估 |
|---|---|---|---|---|
| `recon.txt`（动态） | 500 | 484 | +16 PY | 保真良好（白盒实际用的模板） |
| `recon-static.txt` | 164 | 443 | **−279 PY（缩水 63%）** | 孤儿 + 旧结构（PR-15） |
| `pre-recon-code.txt` | 472 | 433 | +39 PY | 增强版（Spec B 接线） |
| `recon-blackbox.txt` | 102 | N/A | PY 独有 | 偏薄（PR-21） |

### 10.4 与 06-17 文档的关系

| 本文编号 | 06-17 对应 | 关系 |
|---|---|---|
| PR-01/02/03/05 | — | **本文新增 P0** |
| PR-04 | W-02 | 细化（pre-recon/recon 视角 + TS 全链） |
| PR-13 | W-05 | 细化（含 TS 2s 心跳 payload） |
| PR-14 | W-08 | 细化 + **新增 PR-03**（recon 漏传 policy） |
| PR-10/15 | W-10 | 细化（prompt_override 机制健全性 + recon-static 时序竞态 PR-09） |
| §7 | 记忆条目 | **证伪/修正** SharedKnowledge 归因 |

---

*本报告基于 2026-06-18 两代码库（`/root/shannon` @ `feat/fork`、`/root/shannon-py` @ `feat/fork-py`）pre-recon/recon 阶段的代码级核验生成，由 4 个并行审计 agent + 主控复核（LLM taint stub、write_index_files 漏写、recon 无 retry_policy、孤儿产物）共同产出。建议后续每次大改动后按本结构做「v2 复核更新」。*
