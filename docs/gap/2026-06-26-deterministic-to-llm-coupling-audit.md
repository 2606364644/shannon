# 确定性 → LLM 轨耦合审计报告

- **日期**：2026-06-26
- **范围**：whitebox pipeline 中所有「确定性层产物 → LLM 轨 prompt」的注入点（`{{占位符}}` / `prompt_variables` 注入）
- **判定基准**：CLAUDE.md §1 铁律——"不要把确定性层产物喂进 LLM 轨 prompt。LLM 轨靠自身方法论 + 双轨 OR 由 GitNexus 轨独立补召回；把确定性结果喂 LLM 轨会让它依赖确定性层（而确定性层 / GitNexus 经常超时 / 不可用），破坏独立性。"
- **方法**：grep 全部 prompt 的 `{{占位符}}` → 追 `prompts/manager.py` 的填充逻辑 + 各 `*_gitnexus_track.py` renderer → 定位每个注入点的来源、性质、软/硬依赖、是否命中铁律。
- **严重度**：高 = 确定性「分析结果」注入 + 铁律命中；中 = 设计违规但未接线 / 审计边界待判定；低 = GitNexus 轨内部（非违规）/ 死占位符。

---

## TL;DR

- 发现 **7 类注入点**：**3 个高**（真实确定性分析结果喂 LLM 轨）、**2 个中**（死占位符 + audit 边界）、**2 个低/不适用**（GitNexus 轨内部 + 死元数据占位符）。
- **真实发生数据流的违规共 3 处**：`PRE_RECON_GITNEXUS_TRACK`、`RECON_GITNEXUS_TRACK`、`FRAMEWORK_ENDPOINTS_SUMMARY`，全部喂 **recon / pre-recon** 这两个 LLM 轨 agent，且都是**软依赖**（GitNexus 不可用时降级，LLM 轨仍能跑）——违反铁律方向，但不构成硬瘫痪。
- **2 个设计意图违规但接线已断**（死占位符）：`TAINT_FLOW_SUMMARY`、pre-recon Phase 0 的 10 个元数据占位符——`manager.py` 仅 warning，占位符原样残留给 LLM。
- **解耦测试有盲区**：`test_static_dataflow_hints_decoupling.py` 只锁 `_static-dataflow-hints.txt` 一条 `@include`，覆盖不到上述任何正文占位符 / `prompt_variables` 注入。
- **反方向**（LLM → 确定性，不违规）：`run_entry_point_fusion`、`run_merge_sink_reports`——列出仅供图景完整。

---

## 主表：确定性 → LLM 轨注入点

| # | 注入点 | 喂给的 LLM 轨 | 确定性产物 | renderer 来源（.py） | 性质 | 硬/软依赖 | 铁律命中 | 严重度 | 处置建议 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `PRE_RECON_GITNEXUS_TRACK` | pre-recon agent（`pre-recon-code.txt:120-127`） | 确定性 entry points(≤80) / sinks(≤150) / 模板转义 unescaped(≤150) 清单 | `pre_recon_gitnexus_track.py::build_pre_recon_gitnexus_track`（读 `code_index.json` + 模板） | 分析结果 | 软（`code_index.json` 缺→降级提示语，`:72-73`） | **是** | **高** | 拆：最核心违规，确定性 entry/sink 直接喂 LLM 轨 |
| 2 | `RECON_GITNEXUS_TRACK` | recon agent（`recon.txt`） | §4.1 共享路由组 + §4.2 端点安全上下文（确定性 auth/middleware/ownership 推断） | `recon_gitnexus_track.py::build_recon_gitnexus_track`（读 `code_index.json`） | 分析结果 | 软（缺失/解析失败/空→空表降级，`:308-321`） | **是** | **高** | 拆 |
| 3 | `FRAMEWORK_ENDPOINTS_SUMMARY` | recon agent（`recon.txt`） | 确定性 framework analysis 推断的 endpoint 清单 | `framework_endpoint_renderer.py::render_framework_endpoints`（读 `framework_analysis.json`） | 分析结果（确定性 framework 推断，非 GitNexus 但属确定性层） | 软（文件不存在→跳过） | **是** | **高** | 拆或保留待定（framework analysis 不像 GitNexus 易超时，但仍属确定性→LLM） |
| 4 | `TAINT_FLOW_SUMMARY` | recon agent（`recon.txt:117-128`） | （设计意图）确定性参数传播图 source→path→sink | **未接线**——recon `prompt_variables`（`activities.py:108-124`）无此 key | 分析结果（设计） | N/A（死占位符） | 设计上是，实际无数据流 | **中** | 拆占位符 + `<parameter_propagation_data>` 段落（补接线=加重违规） |
| 5 | 元数据占位符群（10 个） | pre-recon agent（`pre-recon-code.txt:440-454` Phase 0） | （设计意图）`TOTAL_CHAINS` / `AVG_CHAIN_DEPTH` / `MAX_CHAIN_DEPTH` / `UNRESOLVED_COUNT` / `TOTAL_FILES` / `INDEXED_SOURCE_FILES` / `TEMPLATE_FILE_COUNT` / `SCHEMA_FILE_COUNT` / `CONFIG_FILE_COUNT` / `DEGRADATION_WARNING_OR_NONE` | **全部未接线**——pre-recon `prompt_variables`（`activities.py:126-133`）只设 `pre_recon_gitnexus_track` | 元数据 | N/A（死占位符） | 设计上轻（元数据），实际无数据流 | **低** | 清理死占位符（接 coverage 降级提示 或 删） |
| 6 | `AUTHZ_GITNEXUS_CANDIDATES` | `authz_gitnexus_judge` prompt（`activities.py:242-247`） | 确定性 IDOR 候选（dom + fw） | `authz_gitnexus_track.py::build_authz_gitnexus_track` | 分析结果 | 轨内 | **否** | **低 / 不适用** | **保留**——这是 GitNexus 轨内部 LLM 判定（`run_claude_prompt` 单次结构化输出），等同 `chain_verdict` 模式；CLAUDE.md §1 明确 authz 的 GitNexus 风格轨 |
| 7 | `CHAIN_AUDIT_INPUT` | audit-tier prompt（`audit-tier1` agent） | 确定性 chain + taint flow summary | `audit_input_builder.py::build_chain_audit_input` | 分析结果 | 待确认 | **是**（audit-tier 是 LLM agent 吃确定性 chain） | **中** | 待判定——需确认 audit-tier 定位是 LLM 轨 agent 还是混合审计轨 |

---

## 反方向：LLM → 确定性（不违反铁律，仅列供图景完整）

| 注入点 | 方向 | 机制 | 位置 |
|---|---|---|---|
| `run_entry_point_fusion` | LLM pre-recon 入口点 → `code_index.json` | 解析 `pre_recon_deliverable.md` 的 LLM 入口点（conf=0.60），append 到确定性 `entry_points`，写回 `code_index.json` | `code_index/__init__.py:279-341` |
| `run_merge_sink_reports` | LLM sink → 确定性 sink 报告 | 合并 LLM 发现的 sink 进确定性 sink 报告 | `workflows.py:167-171` |

> 这两个方向**不违反铁律**（铁律保护 LLM 轨独立性，方向相反）。它们让 GitNexus 轨**消费** LLM 产物提升召回，符合 CLAUDE.md §1 "GitNexus 轨 = 确定性兜底 + 可选 LLM 增强" 定位，且 LLM 轨关闭时安全降级到纯确定性。

---

## 解耦测试盲区

`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 只锁定一行：
```
@include(shared/_static-dataflow-hints.txt)
```
**未覆盖**（即下列注入即使存在/重建，测试仍全绿）：
- `PRE_RECON_GITNEXUS_TRACK`、`RECON_GITNEXUS_TRACK`、`FRAMEWORK_ENDPOINTS_SUMMARY`（正文占位符 / `prompt_variables` 注入）
- `TAINT_FLOW_SUMMARY`、`CHAIN_AUDIT_INPUT`
- pre-recon Phase 0 元数据占位符群

> 若决定清理，应同步扩解耦测试：禁止 LLM 轨 prompt 出现确定性 track 占位符 + 禁止 `prompt_variables` 注入确定性产物。

---

## 附带发现：死代码 / 死占位符

1. **`merge_entry_points`（4 源融合）是死代码**：`entry_point_fusion.py:20-108` 的多源融合函数（gitnexus/schema/convention/llm）**只有测试在调**，无生产调用方。生产实际用的是 `run_entry_point_fusion`（`__init__.py:279-341`）的简单 dedup（确定性 base + append LLM-only）。
2. **5 个死占位符**（#4 `TAINT_FLOW_SUMMARY` + #5 的 10 个元数据）：`manager.py:163-169` 检测到未解析占位符仅 `logger.warning`，占位符原样残留进 LLM prompt，既无数据流又污染 prompt（LLM 看到字面 `{{TAINT_FLOW_SUMMARY}}`）。

---

## 附录：核心证据（file:line）

- 占位符填充逻辑：`packages/core/src/shannon_core/prompts/manager.py:154-169`（`variables` dict 动态填充 + 未解析 warning）
- pre-recon / recon 的 `prompt_variables` 构造：`packages/whitebox/src/shannon_whitebox/pipeline/activities.py:108-133`
- authz GitNexus 判定 prompt：`activities.py:242-247`
- `PRE_RECON_GITNEXUS_TRACK` renderer：`packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py:18-89`
- `RECON_GITNEXUS_TRACK` renderer：`packages/core/src/shannon_core/code_index/recon_gitnexus_track.py:232-326`
- prompt 正文引用：`prompts/pre-recon-code.txt:120-127,440-454`、`prompts/recon.txt:117-128`
- 反方向 fusion：`packages/core/src/shannon_core/code_index/__init__.py:279-341`
- 解耦测试：`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`

---

## 下一步（供决策，本审计不包含修复设计）

按严重度建议处置优先级：
1. **拆 #1/#2/#3**（高，真实违规）——恢复铁律，让 recon/pre-recon 回到纯 LLM 自给。
2. **清理 #4/#5 死占位符**（中/低）——无数据流但污染 prompt，顺手清。
3. **判定 #7 audit-tier**（中）——需先确认其轨归属。
4. **保留 #6**（不适用）。
5. **扩解耦测试**——任何清理都应同步锁死，避免回退。

具体拆法（prompt / renderer / `prompt_variables` 怎么改）留待"决定拆哪些"后的下一轮设计。
