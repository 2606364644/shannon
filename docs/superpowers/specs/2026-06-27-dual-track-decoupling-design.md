# 双轨解耦设计（Dual-Track Decoupling）

- **日期**：2026-06-27
- **状态**：设计已与用户确认，待写实现计划（writing-plans）
- **关联文档**：
  - `docs/gap/2026-06-26-deterministic-to-llm-coupling-audit.md`（耦合审计，本设计依据）
  - `CLAUDE.md` §1（双轨铁律）
  - `docs/superpowers/specs/2026-06-25-injection-recall-port-design.md`（injection 轨已拆 `_static-dataflow-hints` 的先例）

---

## 1. 背景

CLAUDE.md §1 规定 shannon-py 的注入 / xss / ssrf 白盒检测是**双轨**：GitNexus 轨（确定性）与 LLM 轨（纯 LLM 自给），**各自独立、只在合并器（verdict OR）交汇**。铁律：**确定性层产物不得喂进 LLM 轨 prompt**——否则 LLM 轨依赖确定性层（而确定性层 / GitNexus 经常超时 / 不可用），破坏独立性。

历史上一条确定性→LLM 桥梁（`static_dataflow_hints.md` + `prompts/shared/_static-dataflow-hints.txt` partial + `@include`）已在 injection-recall port 中拆除，并由 `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定。

`docs/gap/2026-06-26-deterministic-to-llm-coupling-audit.md` 发现：除已拆的 `@include` 桥梁外，还存在**另一条未被测试覆盖的耦合路径——LLM 轨 prompt 正文占位符 + `prompt_variables` 注入**，共 7 个注入点。本设计处置这 7 个点。

## 2. 用户原则（判定标尺）

> 双轨可以在某些环节合并，但至少要解耦——**各自输出产物之后合并产物才是可接受的**；过程层把一轨中间产物塞进另一轨输入的，不可接受。

据此分类：
- **产物层合并（可接受）**：`dual_track_merger`（verdict OR）、反方向 fusion（读 LLM 已产出的 deliverable 做 OR 合并）。
- **过程层注入（不可接受）**：把确定性摘要塞进 LLM 轨 prompt 的 `{{占位符}}` 或 `prompt_variables`。

## 3. 现状证据（核查结论）

### 3.1 七个注入点当前接线状态

| # | 注入点 | (a) 占位符在 prompt 正文 | (b) renderer 产确定性产物 | (c) prompt_variables 注入 | 性质 |
|---|---|---|---|---|---|
| 1 | `PRE_RECON_GITNEXUS_TRACK` | 是 | 是 | 是 | **活跃违规** |
| 2 | `RECON_GITNEXUS_TRACK` | 是 | 是 | 是 | **活跃违规** |
| 3 | `FRAMEWORK_ENDPOINTS_SUMMARY` | 是 | 是 | 是 | **活跃违规** |
| 4 | `TAINT_FLOW_SUMMARY` | 是（残留） | N/A | 否 | 死占位符 |
| 5 | 元数据占位符群（10 个） | 是（残留） | N/A | 否 | 死占位符 |
| 6 | `AUTHZ_GITNEXUS_CANDIDATES` | 是 | 是 | 是 | **轨内合法**（GitNexus 轨内部 LLM 判定） |
| 7 | `CHAIN_AUDIT_INPUT` | 是 | 是（零生产调用方） | 否 | 死占位符 + 死注册 agent |

### 3.2 renderer 消费关系（关键区分）

**底层确定性 JSON —— GitNexus 轨独立直读，删 renderer 不碰：**
- `code_index.json`（entry_points / sink_call_sites）← `chain_verdict.py` / `vuln_chain_builders/*` / `authz_gitnexus_track.py:301-311`
- `parameter_graph.json`（taint chain）← `chain_verdict.py`（收 `ParameterPropagationGraph` 对象）
- `framework_analysis.json`（inferred_endpoints）← `authz_gitnexus_track.py:149-165` / `route_chain_builder` / `attack_chain_builder`（收 `FrameworkAnalysisResult`）

**renderer 函数 —— 唯一生产消费者都是 `activities.py` 的 `prompt_variables` 注入（喂 LLM 轨 prompt）：**
- `build_pre_recon_gitnexus_track`（`code_index/pre_recon_gitnexus_track.py:69`）← `activities.py:127,130`
- `build_recon_gitnexus_track`（`code_index/recon_gitnexus_track.py:302`）← `activities.py:122,124`
- `render_framework_endpoints`（`services/framework_endpoint_renderer.py:6`）← `activities.py:112,120`

GitNexus 轨**不消费** renderer 产物（它用结构化模型，不用 LLM-oriented markdown 摘要）。→ 断开 LLM 注入后即为死代码。

### 3.3 manager.py 占位符填充

`packages/core/src/shannon_core/prompts/manager.py:154-169`：填充后扫描残余 `{{UPPER_CASE}}`，**仅 `logger.warning`，不 error、不 strip**，占位符原样残留进发给 LLM 的 prompt。

### 3.4 反方向 fusion

| fusion | 位置 | 读什么 | 写回什么 | 来源标记 | 性质 |
|---|---|---|---|---|---|
| `run_entry_point_fusion` | `code_index/__init__.py:279-341` | `pre_recon_deliverable.md`（LLM 入口点，conf=0.60） | append LLM entry_points 写回 `code_index.json` | `source="llm_pre_recon"` | 产物层 OR，软依赖 |
| `run_merge_sink_reports` | `activities.py:491-534` | `pre_recon_deliverable.md`（LLM sinks） | append LLM sinks 写回 `code_index.json` | `rule_id="llm-sink-hunter"`, `needs_review=True` | 产物层 OR，软依赖 |

两者方向相反（确定性消费 LLM 产物），**不违反铁律**。时序：`workflows.py:148-160` 确定性 code_index 与 LLM PRE_RECON 并行 → `:166-178` 顺序跑 merge_sink → entry_point_fusion。

`SHANNON_LLM_TRACK_ENABLED`（`config/concurrency.py:40` → `pipeline/shared.py:20` → `workflows.py:296`）**当前只守卫 vuln agents，不守卫 fusion**——靠 "PRE_RECON 不产出文件" 间接降级（瑕疵②）。

### 3.5 解耦测试盲区

`test_static_dataflow_hints_decoupling.py` 只断言"任何 `*.txt` prompt 不得含 `@include(shared/_static-dataflow-hints.txt)`"。正文占位符 / `prompt_variables` 注入 / 反方向 fusion 全是盲区。

## 4. 设计决策

- **D1 Scope = 全面治理**：拆活跃注入 + 清死代码 + 修 fusion 瑕疵② + 扩解耦测试。
- **D2 产物处置 = renderer 全删**（底层 JSON 保留）。理由：renderer 是"给 LLM 看的文本视图"，GitNexus 轨用结构化模型不消费它，断开 LLM 注入即死代码；YAGNI。即便按 CLAUDE.md §1 演进方向（GitNexus 轨接 LLM 做 sink discovery），它消费的也是结构化 `SinkCallSite`，不会回头用这种 markdown 摘要。
- **D3 fusion 治理 = 加显式守卫 + 不折腾写回**（靠已有来源标记）。理由：fusion 本是合规产物层合并，瑕疵①（物理分离）只是可审计性锦上添花，代价（下游消费者连锁）不值。

## 5. 改动清单

### 5.1 拆除 #1/#2/#3 + 死占位符 #4/#5（三层全拆）

| 项 | 删 prompt 正文段 | 删 `prompt_variables` key | 删 renderer | 删连带测试 |
|---|---|---|---|---|
| #1 `PRE_RECON_GITNEXUS_TRACK` | `prompts/pre-recon-code.txt:121` | `activities.py:130` `pre_recon_gitnexus_track` | `code_index/pre_recon_gitnexus_track.py`（整文件） | `test_pre_recon_gitnexus_track.py`、`test_pre_recon_track_integration.py` |
| #2 `RECON_GITNEXUS_TRACK` | `prompts/recon.txt:253` | `activities.py:124` `recon_gitnexus_track` | `code_index/recon_gitnexus_track.py` 的 renderer 函数 | `test_recon_track_integration.py`、`test_recon_build_track.py` |
| #3 `FRAMEWORK_ENDPOINTS_SUMMARY` | `prompts/recon.txt:298` | `activities.py:120` `framework_endpoints_summary` | `services/framework_endpoint_renderer.py`（整文件） | `test_framework_endpoint_renderer.py` |
| #4 `TAINT_FLOW_SUMMARY`（死） | `prompts/recon.txt:117-128` 整个 `<parameter_propagation_data>` 段（含引导文字） | — | — | — |
| #5 元数据占位符群（死） | `prompts/pre-recon-code.txt:443-456` 的 10 个占位符 | — | — | — |

**注意点：**
1. **#2 常量迁移**：`recon_gitnexus_track.py` 内的 `_OWNERSHIP_PREDICATE_RE` 被 `authz_gitnexus_track.py:70` 复用。删整个 renderer 文件前，先把该正则迁到共享位置（`code_index/models.py` 或新建 `patterns.py`），再删文件；或仅删 renderer 函数、保留常量。
2. **#4 整段删**：`<parameter_propagation_data>` 段含"教 recon 用 taint flow data 追链"的引导文字——这是设计意图耦合，即便数据未接，框架仍教 LLM 依赖确定性层。整段删除才彻底。
3. **#5 Phase 0 元数据**：`TOTAL_CHAINS` / `AVG_CHAIN_DEPTH` / `MAX_CHAIN_DEPTH` / `UNRESOLVED_COUNT` / `TOTAL_FILES` / `INDEXED_SOURCE_FILES` / `TEMPLATE_FILE_COUNT` / `SCHEMA_FILE_COUNT` / `CONFIG_FILE_COUNT` / `DEGRADATION_WARNING_OR_NONE`。占位符所在段一并清理。

### 5.2 清死代码 #7（audit-tier1）

- 删 `packages/core/src/shannon_core/code_index/audit_input_builder.py`（零生产调用方的死 renderer，仅测试调用）。
- 删 `prompts/audit-tier1.txt` 中的 `{{CHAIN_AUDIT_INPUT}}` / `{{VULN_CLASSES_TESTED}}` 占位符。
- `audit-tier1` agent 注册（`models/agents.py:146-153` AGENTS 字典 + `AgentName` 枚举 + `:190` phase 映射 `vulnerability-analysis`）：**实现时先验证引用链**（grep 确认无 workflow 调用 `run_agent(audit-tier1)` / 无 phase 依赖 / 无其它 import），安全则删 agent 注册 + `prompts/audit-tier1.txt`；若连锁过大则降级为"保留注册 + 标 deprecated"。
- 连带删 `test_audit_input_builder.py`。

### 5.3 反方向 fusion 加显式守卫（瑕疵②）

`run_entry_point_fusion`（`code_index/__init__.py`）与 `run_merge_sink_reports`（`activities.py`）入口加：
```python
if not enable_llm_track:
    logger.info("%s skipped: LLM track disabled", name)
    return
```
需把 `enable_llm_track`（来自 `PipelineInput`）透传到这两个函数。**不动写回逻辑**（靠已有 `source` / `rule_id` / `needs_review` 来源标记）。

### 5.4 解耦测试扩法（锁死防回退）

扩 `test_static_dataflow_hints_decoupling.py`（或新建 `test_dual_track_decoupling.py` 并在旧测试中加引用）：
- **黑名单断言**：LLM 轨 prompt 正文（`pre-recon-code.txt` / `recon.txt` / `audit-tier1.txt` 等 LLM 轨 agent prompt）不得出现确定性 track 占位符——`{{PRE_RECON_GITNEXUS_TRACK}}` / `{{RECON_GITNEXUS_TRACK}}` / `{{FRAMEWORK_ENDPOINTS_SUMMARY}}` / `{{TAINT_FLOW_SUMMARY}}` / `{{CHAIN_AUDIT_INPUT}}` / 10 个元数据占位符。
- **注入禁止断言**：`activities.py` 中给跑 LLM 的 agent（PRE_RECON / RECON / vuln-* / audit-tier1）构造的 `prompt_variables` 不得注入确定性产物 key（grep 断言）。注：此处约束的是 prompt 集合（管"能不能吃确定性产物"），与 `enable_llm_track` 守卫范围（只 vuln agents，管"开关时跑不跑"）是两个维度。
- **白名单 #6**：`AUTHZ_GITNEXUS_CANDIDATES`（`authz_gitnexus_judge` prompt，`activities.py:243-246`）是 GitNexus 轨内部合法判定，测试显式允许，不误伤。

## 6. 降级与错误处理

- **`SHANNON_LLM_TRACK_ENABLED=0`（关 LLM 轨）**：vuln agents 不跑（已有 `workflows.py:296`）+ fusion 不跑（新守卫）；pre-recon / recon 仍跑（属 recon 阶段，回归纯 LLM 自给）。
- **GitNexus 不可用 / `code_index.json` 缺**：fusion 跳过（已有降级）；recon / pre-recon 纯 LLM 自给——**这是拆 #1/#2/#3 的核心收益**：recon/pre-recon 不再因确定性摘要缺失而退化。
- **删 renderer + 占位符段后**：`manager.py` 的"未解析占位符 warning"应自然消失（验证项）。

## 7. 测试策略

- 删除 5.1 / 5.2 列出的连带测试文件（它们测的是被删 renderer）。
- 扩解耦测试（5.4）作为防回退锁——任何日后重建确定性→LLM 注入都会触发测试失败。
- 现有 `test_static_dataflow_hints_decoupling.py`（锁 `@include`）保留，与新断言并存。
- 回归：跑 `packages/core/tests/code_index/`、`packages/core/tests/prompts/`、`packages/whitebox/` 改动相关子集（勿跑全套——见 CLAUDE.md §3 测试陷阱）。

## 8. Scope 外 / follow-up

- **反方向 fusion 物理分离**（瑕疵①：overlay / merged 产物）——明确本轮不做，列为 follow-up。若日后可审计性需求升级再评估。
- **audit-tier1 启用**：若将来要 audit 能力，按"产物层消费"重新设计，不重建确定性→LLM 注入。
- **其余 vuln prompt**（injection 之外）：injection-recall port 已拆 injection 的 `_static-dataflow-hints`；其余 vuln 的同类清理不在本轮 scope。

## 9. 风险与回滚

- **风险①**：删 renderer 后若发现某个隐蔽消费者（核查已确认无，但实现时再 grep 一遍三个 renderer 函数名兜底）。
- **风险②**：删 `audit-tier1` 注册的连锁（5.2 已要求验证引用链，过大则降级保留）。
- **风险③**：recon / pre-recon 拆确定性摘要后，deliverable 质量是否受影响——按铁律方向本就该纯 LLM 自给（与原始 TS 一致），属预期行为，非回归。人工冒烟时确认 recon/pre-recon deliverable 仍产出。
- **回滚**：所有改动集中在 prompt 文件 + `activities.py` + 三个 renderer 文件 + fusion 守卫 + 测试，git revert 即可整体回滚。

## 附录：file:line 索引

- 占位符填充：`packages/core/src/shannon_core/prompts/manager.py:154-169`
- pre-recon / recon `prompt_variables` 构造：`packages/whitebox/src/shannon_whitebox/pipeline/activities.py:108-133`
- #1 renderer：`packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py:69`
- #2 renderer：`packages/core/src/shannon_core/code_index/recon_gitnexus_track.py:302`（`_OWNERSHIP_PREDICATE_RE` 被 `authz_gitnexus_track.py:70` 复用）
- #3 renderer：`packages/core/src/shannon_core/services/framework_endpoint_renderer.py:6`
- #7 死代码：`packages/core/src/shannon_core/code_index/audit_input_builder.py:21-113`、`models/agents.py:146-153,190`、`prompts/audit-tier1.txt:15`
- prompt 正文：`prompts/pre-recon-code.txt:121,443-456`、`prompts/recon.txt:117-128,253,298`
- 反方向 fusion：`packages/core/src/shannon_core/code_index/__init__.py:279-341`、`activities.py:491-534`、`workflows.py:148-178`
- `enable_llm_track`：`config/concurrency.py:40`、`pipeline/shared.py:20`、`workflows.py:296`
- 解耦测试：`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`
- 审计依据：`docs/gap/2026-06-26-deterministic-to-llm-coupling-audit.md`
