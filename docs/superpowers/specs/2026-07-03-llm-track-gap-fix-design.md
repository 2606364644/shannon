# LLM 轨弱项修复（recon 对账 + vuln 字段 + 双轨 attack chain + SharedKnowledge 接通）

> 日期：2026-07-03　分支：`feat/fork-py`
>
> **背景**：对 shannon-py LLM 轨相对原始 TS 项目（`/root/shannon`）做弱项 gap 分析后发现——清单层（sink/source/slot/参数枚举、reconciliation 对账、shared partials）PY 已全面对齐甚至超越 TS（行数普遍 +40~+300、reconciliation 接线两边一致、shared partials 14 个 vs TS 13 个）。真正的弱项在三类更隐蔽处：① 白盒纯静态路径 `recon-static.txt` 缺 TS 的枚举完整性强制对账机制（`_enumeration-completeness.txt` 只接到黑盒/动态的 `recon.txt:484`，白盒主路径 `recon-static` 没接）；② vuln inj+xss 的 `starting_context` 删了 TS 的 Section 4.2 Endpoint Security Context 字段清单 + framework auto-gen 提示；③ attack_chains/route_chains 是 dead-end（依赖 frontend_mapper，白盒纯静态恒空 `[]`，零下游消费）。
>
> **关键核实修正**（推翻若干记忆）：
> - 记忆 `llm-track-reconciliation-port-status` 说"reconciliation 已对齐"——只对黑盒/动态路径成立，**白盒路径仍未落地**（用户主要白盒纯静态扫描）。
> - 记忆 `shared-knowledge-injection-lost-in-refactor` 说"PromptManager 不支持知识占位符"——**已过时**，`manager.py:175-178` 有通用 `{{KEY}}` fallback 循环，加占位符零改动。
> - TS SharedKnowledge 不是"半死"是"半接通"：写/store/builder 读通了（`activities.ts` 3 处 `updateSharedKnowledge`、`knowledge-store.ts` 完整、`attack-chain-builder.ts:27` 真读），但 prompt 注入那条断了（`buildSharedKnowledgeContext` 零调用、`{{SHARED_KNOWLEDGE}}` 恒填兜底串、`_shared-knowledge.txt` 孤儿）。
> - TS attack-chain-builder 整套是 **dead-end**（纯确定性 XSS 4 步 + IDOR 模板拼装、写无人读 JSON、`vulnerabilityContext` 置信度升级是死分支、零 reader）——**PY 无可移植对象，B'1 是新建**。
>
> **落地决策**：
> - 白盒 = 纯静态模式（用户确认：白盒黑盒分开扫，白盒不起服务）。所有历史白盒 deliverables 的 `attack_chains.json`/`route_chains.json` 恒为 `[]` 印证。
> - attack chain 在 vuln **后**产（吃 vuln 双轨 queue 的 confirmed 单步链组装）→ 喂报告。黑盒按链验证（B'3）留 follow-up。
> - 不移植 TS SharedKnowledge store 抽象（用 PY 通用 `{{KEY}}` 占位符 + activities 注入）。
> - 守 CLAUDE.md §1 双轨铁律：LLM 轨数据源只能是 LLM 轨自产（recon_deliverable / exploitation_queue），不引 GitNexus 确定性层产物喂 LLM 轨 prompt。GitNexus 轨 attack chain 是确定性层自己的合法产出（与 `gitnexus_queue.json` 同性质），合并走 `dual_track_merger` OR。

---

## 1. 目标 / 非目标

### 目标

- **G1（A1 — recon-static 对账）**：`recon-static.txt` 补 TS 的枚举完整性强制对账机制（5 角度并行枚举 + Step 3.5 强制 reconciliation + §4.3 表 + anchor count + parameter completeness + shared route group + pre-auth），堵白盒纯静态"整条路由家族被默默跳过"。delta 分类加 `not-applicable`（白盒纯静态适配，target 无此类代码时标 N/A 而非编造）。
- **G2（A2/A3 — vuln 字段）**：vuln inj+xss 的 `starting_context` 补回 Section 4.2 Endpoint Security Context 字段清单 + framework auto-gen 端点提示；inj source-list 警示恢复 TS 强信号。
- **G3（B'1 — 双轨 attack chain）**：新建双轨多步组合链（stored XSS / IDOR 链 / 跨服务链），vuln 后产，喂报告。LLM 轨 Agent（创意驱动）+ GitNexus 轨确定性组装（证据驱动）差异化，`dual_track_merger` OR 合并。删当前 dead-end 的 `run_attack_chain_assembly`。
- **G4（B'2 — SharedKnowledge 注入）**：把 recon 前序知识（frameworkAnalysis + endpoint inventory + authz candidates）经通用 `{{KEY}}` 占位符注入 vuln prompt，让 vuln agent 拿到结构化前序知识（不依赖 agent 自读 md 的彻底性）。**不注入 attack_chains**（vuln 后产）。

### 非目标（明确排除）

- **B'3 黑盒按链验证**：留 follow-up。当前黑盒不读白盒 attack_chains。
- **GitNexus 轨 attack chain 不喂 LLM 轨 prompt**：它是确定性层自己的产出，只经合并器 OR 进 attack_chains.json，不反向注入 vuln（守铁律）。
- **不移植 TS SharedKnowledge store 抽象**（`knowledge-store.py` / `SharedKnowledge` 类型 / `_shared-knowledge.txt` / `buildSharedKnowledgeContext`）——PY 用通用占位符替代。
- **不删 frontend_mapper / attack_chain_builder.py 现有确定性 builder**：黑盒/动态路径仍用，B'1 只加"白盒静态分支"或新建独立 assembler。
- **不动已对齐项**：SSRF 的 Open Redirect §8（PY 已强于 TS）、slot 体系、recon.txt 黑盒路径的对账（已接 `_enumeration-completeness.txt`）。
- **跳过功能等价低项**：MCP collector→Write/Edit、playwright 具名 skill→通用占位符（纪律性微降，不值得动）。

---

## 2. 现状证据

### 2.1 行数对比（PY 清单层普遍 ≥ TS，唯一缩水是 recon-static 但为重组）

| prompt | TS 行数 | PY 行数 | 说明 |
|---|---|---|---|
| vuln-injection | 344 | 388 | PY +44，但 starting_context 删了 §4.2 字段（A2） |
| vuln-xss | 254 | 296 | PY +42，同上 |
| vuln-ssrf | 282 | 334 | PY +52，且多 §8 Open Redirect（PY 更强） |
| vuln-authz | 368 | 408 | PY +40 |
| recon（黑盒/动态） | 192 | 499 | PY 大幅增强，已接 `_enumeration-completeness.txt:484` |
| **recon-static（白盒主路径）** | 478 | 212 | **TS 的 §0-§9 产出契约 + 5 角度 + Step 3.5 对账在 PY 缺失** |

### 2.2 白盒路径走 recon-static（已坐实）

- TS `apps/worker/src/temporal/workflows.ts:757` 白盒 workflow 硬编码 `promptOverride: 'recon-static'`
- PY `executor.py:39-40` `agent==RECON and not web_url → "recon-static"`

### 2.3 attack_chains/route_chains 白盒恒空 + dead-end

- 所有历史白盒 deliverables 的 `attack_chains.json`/`route_chains.json` 均 `[]`（2 bytes）
- `run_route_chain_building`（`activities.py:1472`）+ `run_attack_chain_assembly`（`:1534`）各自独立读 `framework_analysis.json` + `frontend_mapping.json` 调 builder，**互不相读**（重复两次 builder 调用）
- `frontend_mapper` 需 live web target → 白盒纯静态无输入 → 输出空
- 零下游消费：reporting 不读 attack_chains

### 2.4 PromptManager 已支持任意占位符

`manager.py:175-178` 通用 fallback：`for key, value in variables.items(): result.replace("{{"+key.upper()+"}}", value)`。加 `{{RECON_CONTEXT}}` 等占位符零改动，只需 activities 塞 `prompt_variables`。

### 2.5 双轨合并基础设施已就绪

- `dual_track_merger.py`：verdict OR + location/sink 去重 + `merge_source` 三态（gitnexus-only/llm-only/both），`externally_exploitable` 是可达性标签不被覆写
- `chain_verdict.py`：GitNexus 轨轻量判定（`CandidateChain` 含 `propagation_steps`）
- `vuln_chain_builders/{injection,xss,ssrf}_builder.py`：提单步候选链

---

## 3. Track A 设计

### 3.1 A1 — recon-static.txt 补对账机制

**改动文件**：`prompts/recon-static.txt` + `prompts/shared/_enumeration-completeness.txt`（加 not-applicable）

**(a) 产出契约对齐 recon.txt 的 §0-§9 结构**（让下游 vuln agent 无论 recon 走动态/静态，deliverable 结构一致）：
```
§0 HOW TO READ / §1 Executive Summary / §2 Technology & Service Map
§3 Auth & Session Flow
§4 API Endpoint Inventory
  §4.1 Shared Controller Route Groups      ← 新增（共享 handler 组 + pre-auth 标记，@include _cross-route-enumeration.txt）
  §4.2 Endpoint Security Context           ← 保留 PY 现有 framework endpoint 检测（finale-rest/epilogue）
  §4.3 Enumeration Reconciliation          ← 新增对账表
§5 Parameter Completeness Verification     ← 强化（每 endpoint 全参数 + 隐藏参数 cross-check）
§6 Network & Interaction Map / §7 Role & Privilege / §8 Authz Candidates（保留 PY 现有 8.1/8.2/8.3）/ §9 Injection Sources
```
PY 现有 §2.1 Endpoint Security Context / §6.4 Guards / §6.5 Privilege Lattice / §8 Authz Candidates 好内容保留，重新编号归位。

**(b) 方法论改造** — Phase 1 改为 5 角度并行枚举（参照 TS `recon-static.txt:120-129`），每角度 Task Agent 返回 `endpoints + source anchor count`（grep pattern + file + count）：
1. Route definitions（router.get/post、framework decorators、config-driven route tables）
2. Controller methods（handler methods referenced by routes）
3. Interface contracts（proto http annotations、OpenAPI/swagger、graphql schema）
4. Frontend calls（axios/fetch/rpc，reverse-inferred backend endpoints）
5. Gateway config（nginx location/proxy_pass、ingress routes）

**(c) Step 3.5 强制 reconciliation**（MANDATORY，Phase 2 之前）— 汇总 anchor count、对比 §4 dedup set、产 §4.3 表（每角度：anchor count / reported count / delta / classification），delta 非 0 必须分类，`true-miss` 必须加回 §4 才能终止。

**(d) @include 两个已有 partial**（零新建）：`shared/_enumeration-completeness.txt` + `shared/_cross-route-enumeration.txt`。

**(e) 白盒纯静态适配** — `_enumeration-completeness.txt` 的 delta 分类从 3 类（dedup/out-of-scope/true-miss）扩到 4 类，加 **`not-applicable`**（target 无此类代码，如纯后端服务无 frontend-call 层、无 gateway 配置）。**改共享 partial**（recon.txt 黑盒也受益、单一来源；recon.txt 行为变化仅为多一个合法 delta 分类，向后兼容）。

**(f) Parameter Completeness Verification** — §5 强化（参照 TS `recon-static.txt:299-309`）：每个 §4 endpoint 列所有 client-controllable 参数；template-rendering endpoint 做隐藏参数 cross-check。

### 3.2 A2 — vuln inj+xss starting_context 补 Section 4.2 字段

**改动文件**：`prompts/vuln-injection.txt` + `prompts/vuln-xss.txt`

补回（参照 TS `vuln-injection.txt:43-50` / `vuln-xss.txt:42-49`）：
```
recon_deliverable.md 的 Section 4.2 Endpoint Security Context 为每个 endpoint 提供：
- HTTP methods / auth 等级（anon/user/admin）/ ownership validation（none detected / yes file:line）
- framework auto-generated（finale-rest / epilogue）/ middleware chain
据此判断每个 endpoint 的可达性，并对 middleware chain 后的 sink 做验证。
```
framework auto-gen 重点提示：
- injection："pay special attention to endpoints marked as framework auto-generated that accept user input, trace where that input reaches a sink"
- xss："...trace where that input is rendered in frontend components"

> 与 A1 形成呼应：A1 让 recon-static 产出 §4.2，A2 让 vuln agent 消费它。

### 3.3 A3 — inj source-list 警示强化

**改动文件**：`prompts/vuln-injection.txt`

恢复 TS（L137-138）强信号：
```
recon 与 pre-recon 的 deliverable 只是起点，不是穷尽的 sink 清单。
仅分析它们列出的 endpoint 会漏掉未枚举的 sink —— 你必须自己用 grep
扩展 sink 搜索（按语言的 sink 清单），覆盖 recon 未列出的路径。
```

---

## 4. Track B'1 设计 — 双轨 attack chain（新建）

### 4.1 定位

attack chain = **多步组合链**（stored XSS: input→storage→render / IDOR 链 A→B→C / 跨服务链），独立于单 vuln 类 source→sink 链（后者双轨已有：`{vt}_gitnexus_queue.json` + `{vt}_exploitation_queue.json`）。

### 4.2 时序

vuln **之后**产（吃 vuln 双轨 queue 的 confirmed 单步链作组装输入）→ `attack_chains.json` → 报告多步场景展示。

**删掉当前 `run_attack_chain_assembly`**（`activities.py:1534`，dead-end 重复，与 `run_route_chain_building` 互不相读），用 B'1 双轨替代。

### 4.3 LLM 轨（Agent，创意驱动 — 广覆盖）

**新增**：`prompts/attack-chain.txt`（Agent 模式：Task 委派、多轮、自己 grep，参照 `vuln-*.txt` 风格）

- 数据源：`recon_deliverable.md` + vuln 双轨 queue（`{vt}_gitnexus_queue.json` + `{vt}_exploitation_queue.json`，含 confirmed 单步链）+ 代码 grep
- 推断：业务逻辑链（reset/checkout 流程滥用）/ 跨服务链 / 隐式数据流（无严格 propagation 但语义流转）
- 产出：`attack_chains_llm_queue.json`（AttackChain[]，confidence 多为 theoretical/probable，需 verdict）
- 守铁律：数据源全是 LLM 轨自产 + 代码 grep，不引确定性层产物喂 prompt

### 4.4 GitNexus 轨（确定性组装，证据驱动 — 精确可追溯）

**新增**：`packages/core/src/shannon_core/code_index/attack_chain_assembler.py`

- 数据源：现有 `vuln_chain_builders` 的 `CandidateChain.propagation_steps`（单步链已产，含 taint flow 证据）
- 确定性组装：跨 endpoint 关联多个单步链（A 的 sink=storage → B 的 source=storage query = stored XSS；或 IDOR 链 A→B→C 按数据依赖串联）
- 产出：`attack_chains_gitnexus_queue.json`（每步有 file:line + propagation 证据，低召回高精度）
- 降级：GitNexus 超时/不可用（CLAUDE.md §3 预存问题）则此轨空，LLM 轨独立兜底——双轨意义所在

### 4.5 合并

attack-chain schema（`AttackChain` 含 `steps` 列表）与 `Vulnerability`（单 finding）不同，**不能直接复用 `run_merge_dual_track_queues`**（其 `_finding_key` 按 location+sink 去重 Vulnerability）。**新增 attack-chain 专用合并函数**（同模块 `dual_track_merger.py`，复用 `merge_source` 三态 + verdict OR 逻辑，去重键改为 chain 的 endpoint-sequence + vuln_type）→ `attack_chains.json`（`merge_source`: gitnexus-only/llm-only/both）。→ 报告。

### 4.6 两轨差异

| | LLM 轨 | GitNexus 轨 |
|---|---|---|
| 驱动 | 创意（recon 启发 + grep 推断） | 证据（propagation_steps 严格组装） |
| 召回/精度 | 高召回、可能误报 | 低召回、高精度 |
| 链类型 | 业务逻辑链、跨服务链、隐式数据流 | 有 taint 证据的 stored XSS、IDOR 串联 |
| 可追溯 | 语义级 | file:line + propagation |

合并取并集（GitNexus 高置信 + LLM 广覆盖）。

---

## 5. Track B'2 设计 — SharedKnowledge 注入

### 5.1 机制

用 PY 通用 `{{KEY}}` 占位符（`manager.py:175-178`）+ activities 塞 `prompt_variables`，**不移植 TS store 抽象**。

### 5.2 注入内容（vuln 前可获得的前序知识）

- `{{FRAMEWORK_ANALYSIS}}`：`framework_analysis.json` 的 detectedFrameworks + inferredEndpoints 摘要
- `{{RECON_CONTEXT}}`：`recon_deliverable.md` 的 §4 endpoint inventory + §8 authz candidates 结构化摘要（activities 解析或 LLM 摘要）
- **不注入 attack_chains**（vuln 后产）

### 5.3 注入点

vuln prompts（`vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt` / `vuln-authz.txt` / `vuln-auth.txt`）的 `starting_context` 段加占位符块。

### 5.4 实现位置

`packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 vuln activity 调用前，读 `framework_analysis.json` + `recon_deliverable.md`，构造摘要塞 `prompt_variables`。

### 5.5 守铁律

注入的是 LLM 轨自己 recon 阶段产物（`recon_deliverable.md` 是 LLM 轨产、`framework_analysis.json` 是 pre-recon 代码层推断），**不是 GitNexus 确定性层产物**（`parameter_graph.json`/`SinkCallSite`）。`framework_analysis.json` 边界要核实——它是 pre-recon 的 framework_analyzer 代码层推断（非 GitNexus），合规。

---

## 6. 数据流（升级后）

```
pre-recon（代码层 framework 推断）
  → framework_analysis.json ──────────────────────────┐
                                                      │ B'2 注入
recon-static（白盒纯静态 + A1 对账）                    │  {{FRAMEWORK_ANALYSIS}}
  → recon_deliverable.md（§4/§5/§8）                   │  {{RECON_CONTEXT}}
  │                                                     │     ↓
  │                                                      │  vuln agent（A2 字段 + A3 警示）
  │   ┌─ B'2 注入 ─────────────────────────────────────┤  → {vt}_exploitation_queue.json
  │   ↓                                                  │
  │  vuln prompts                                        │
  ↓                                                      ↓
vuln 双轨 queue ──────────────────────────┐  GitNexus 轨：{vt}_gitnexus_queue.json
（confirmed 单步链）                        │  （CandidateChain.propagation_steps）
        ↓                                  ↓
        B'1 双轨 attack chain（vuln 后）:
          ├─ LLM 轨 Agent（attack-chain.txt）→ attack_chains_llm_queue.json
          └─ GitNexus 轨 assembler          → attack_chains_gitnexus_queue.json
                       ↓
        dual_track_merger OR → attack_chains.json → 报告多步场景
```

---

## 7. 文件改动清单

| Track | 文件 | 类型 | 改动 |
|---|---|---|---|
| A1 | `prompts/recon-static.txt` | 纯 prompt | 章节重构 §0-§9 + 5 角度方法论 + Step 3.5 + @include |
| A1 | `prompts/shared/_enumeration-completeness.txt` | 纯 prompt | delta 分类加 `not-applicable` |
| A2/A3 | `prompts/vuln-injection.txt`、`prompts/vuln-xss.txt` | 纯 prompt | starting_context 补 §4.2 字段 + framework auto-gen + source-list 警示 |
| B'1 | `prompts/attack-chain.txt` | 纯 prompt（新） | LLM 轨 Agent 方法论 |
| B'1 | `packages/core/src/shannon_core/code_index/attack_chain_assembler.py` | 代码（新） | GitNexus 轨确定性组装 |
| B'1 | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 代码 | 新增双轨 attack chain activity（替代 run_attack_chain_assembly） |
| B'1 | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 代码 | 编排：vuln 后调双轨 attack chain |
| B'1 | `packages/core/src/shannon_core/code_index/dual_track_merger.py` | 代码 | 新增 attack-chain 专用合并函数（endpoint-sequence 去重，复用 merge_source 三态） |
| B'2 | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 代码 | vuln 调用前塞 `{{FRAMEWORK_ANALYSIS}}`/`{{RECON_CONTEXT}}` 到 prompt_variables |
| B'2 | `prompts/vuln-{injection,xss,ssrf,authz,auth}.txt` | 纯 prompt | starting_context 加占位符块 |

---

## 8. 测试策略

### 8.1 prompt 解耦测试（守铁律）

- `test_static_dataflow_hints_decoupling.py` rglob 自动覆盖新 partial（`attack-chain.txt`、改后的 `_enumeration-completeness.txt`）——只要不写 FORBIDDEN token（`parameter_graph`/`SinkCallSite`/`static_dataflow_hints`）即过。
- 新增断言：`attack-chain.txt` 不 `@include` 确定性产物；B'2 注入的 `{{RECON_CONTEXT}}` 内容来自 `recon_deliverable.md`（LLM 轨产）而非 GitNexus。

### 8.2 A1 对账测试

- 给定 mock recon-static 产出，验证 §4.3 表每角度行存在、delta 分类含 `not-applicable`。
- `not-applicable` 在 `_enumeration-completeness.txt` 的单元断言。

### 8.3 B'1 双轨测试

- LLM 轨：mock recon + vuln queue，验证 `attack_chains_llm_queue.json` 产出多步链（stored XSS / IDOR 链）。
- GitNexus 轨：mock CandidateChain + propagation_steps，验证 assembler 产跨端点关联链；GitNexus 不可用时降级空。
- 合并：`dual_track_merger` OR + 去重，`merge_source` 三态。
- 时序：workflow 测试确认 attack chain 在 vuln 后、reporting 前产。

### 8.4 B'2 注入测试

- vuln activity 调用时 `prompt_variables` 含 `FRAMEWORK_ANALYSIS`/`RECON_CONTEXT`。
- 渲染后 vuln prompt 含占位符块内容（非空，当 recon 存在时）。
- 白盒纯静态下 `FRAMEWORK_ANALYSIS` 有内容（代码层推断）、`RECON_CONTEXT` 有内容（recon-static 产出）。

### 8.5 回归

- 黑盒/动态路径：`recon.txt` 对账行为不变（`_enumeration-completeness.txt` 加 `not-applicable` 是新增合法分类，向后兼容）。
- attack_chain_builder.py 现有确定性 builder 不删（黑盒/动态仍用）。

---

## 9. 风险与降级

| 风险 | 缓解 |
|---|---|
| B'1 LLM 轨 Agent token 成本（多轮 grep） | 单次 attack chain agent，限制 max_turns；白盒纯静态下 recon 已是主要输入，grep 范围可控 |
| B'1 GitNexus 轨不可用（超时） | 降级空，LLM 轨独立兜底（双轨意义） |
| B'2 `{{RECON_CONTEXT}}` 摘要质量 | activities 解析 md §4/§8 或轻量 LLM 摘要；先解析、不行再 LLM |
| A1 `not-applicable` 被滥用（LLM 偷懒标 N/A 跳过角度） | `_enumeration-completeness.txt` 要求 N/A 必须附"target 无此类代码"证据（grep 过零结果），不允许无据标 N/A |
| recon-static 章节重构影响下游 vuln agent 自读 md | 产出契约对齐 recon.txt §0-§9（下游已适配 recon.txt 结构），静态/动态 deliverable 结构统一反而降低下游分支 |
| attack-chain.txt 误报率高 | verdict 阶段（合并器）+ 报告标 theoretical/probable/confirmed 分级，不硬塞 confirmed |

---

## 10. 不做（follow-up）

- **B'3 黑盒按链验证**：黑盒 exploit 读 `attack_chains.json` 按 chain step 验证，升级 confidence。当前黑盒不读白盒 attack_chains。基础设施已就绪（同 workspace 文件级共享 + `detect_whitebox_results` 机制），后续接 prompt 段即可。
- **attack_chains 注入 vuln prompt**：attack chain 在 vuln 后产，无法注入。若未来想要"vuln 前 attack chain 提示"，需另设计两阶段（候选/最终）。
- **GitNexus 轨 attack chain 注入 LLM 轨 prompt**：永远不做（守铁律）。
- **功能等价低项**（MCP collector、playwright 具名 skill）。

---

## 11. 与 CLAUDE.md §1 双轨铁律的合规性

| 改动 | 数据源 | 合规性 |
|---|---|---|
| A1 recon-static 对账 | LLM 自给 grep + anchor count | ✓ LLM 轨自给，不吃确定性层 |
| A2/A3 vuln 字段 | recon_deliverable.md（LLM 轨产） | ✓ |
| B'1 LLM 轨 attack chain | recon + exploitation_queue + grep | ✓ LLM 轨自产 |
| B'1 GitNexus 轨 attack chain | CandidateChain.propagation_steps（确定性层自己的产出） | ✓ 确定性层合法产出，与 `gitnexus_queue.json` 同性质，不反向喂 LLM 轨 prompt |
| B'2 注入 vuln prompt | framework_analysis.json + recon_deliverable.md | ✓ 需核实 framework_analysis 非 GitNexus 产物（pre-recon 代码层推断） |

**关键不变量**：GitNexus 轨 attack chain 产物（`attack_chains_gitnexus_queue.json`）只经 `dual_track_merger` OR 进 `attack_chains.json`（去报告），**绝不注入 vuln prompt**。`test_static_dataflow_hints_decoupling.py` rglob 守护。
