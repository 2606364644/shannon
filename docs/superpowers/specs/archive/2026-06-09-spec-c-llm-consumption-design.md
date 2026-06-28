# Spec C：vuln agent 直接消费确定性数据流产物（作为补充上下文）

> 本 spec 是三件套的出口，消费 **Spec B**（`SinkCallSite`）与 **Spec A**（`TaintFlow`/`ParameterPropagationGraph`）的产物，把它们喂给 LLM。
>
> 依赖顺序：**B → A → C**。本 spec 不启动，除非 A 的 `param_graph.json` 已产出。

---

## 1. 背景与现状

### 1.1 确定性产物当前没真正进 LLM 的视野

- `vuln-injection.txt` 的 `starting_context` 明确："single source of truth is `recon_deliverable.md`"。vuln agent 不直接读 `code_index.json` / `param_graph.json`。
- `code_index` 对 vuln 阶段的影响是**间接**的：经 recon agent 汇总、经 risk_scoring/tiered audit 排序。但精确的 `SinkCallSite`（B 产出）和 `TaintFlow`（A 产出）**没有结构化地喂给 vuln agent**。
- 结果：vuln agent 仍像原始项目一样，主要靠 Task 子 agent 用 Read/Grep 从零追链，确定性产物浪费。

### 1.2 已有但未充分使用的消费设施

- `audit_input_builder.py` 已存在 `build_audit_input(...)` 和 `format_taint_flow_summary(...)`（`:109`），能把 `TaintFlow` 格式化成 markdown——但目前**喂给的 `audit-tier1` agent 是否在主流程、是否覆盖 5 个 vuln agents，未完全接线**。
- `tiered_audit.py` 的 `TieredAuditPlanner` 已按 risk score 把 chain 分到 tier3/2/1（`:48`），有明确的审计优先级——这是"哪些链的静态线索最该喂给 LLM"的天然排序。
- prompts 目录已有 `audit-tier1.txt`。

### 1.3 决策约束（来自澄清）

**作为补充上下文**（低风险）：把确定性摘要作为"静态线索"喂给 LLM，明确标注需验证；prompt **保留** Task 子 agent 自主追链能力。静态不全不会导致漏报（LLM 仍会自己查）。本 spec 不激进替代 grep。

---

## 2. 目标

1. 产出一份结构化的**确定性数据流摘要** deliverable，把 B 的 `SinkCallSite` + A 的 `TaintFlow` + 现有 `CallChain` 格式化成 LLM 友好的 markdown。
2. vuln agents（injection/xss/ssrf/auth/authz）与 recon agent 在 prompt 中被告知去读这份摘要，作为追链**起点与交叉验证**，而非结论。
3. 摘要按 tiered audit 优先级精简（tier3 优先），控制 token 成本。
4. 诚实传递静态边界：`needs_review`、`has_sanitizer_hint`、`skipped_languages`、`confidence` 都有对应的 LLM 指引语，避免误用（"静态没报 ≠ 安全"、"sanitizer hint 不代表有效"）。

---

## 3. 数据契约（消费 Spec B/A 的产物）

### 3.1 消费 Spec B

- `SinkCallSite`（§3.1）：`category`/`sink_subtype`/`file:line:col`/`dangerous_slots`/`needs_review`/`rule_id`。
- `SlotContext`：摘要中标注槽位上下文词汇，与 vuln prompt 的 slot 系统一致。

### 3.2 消费 Spec A

- `TaintFlow`（§3.2）：`entry_point_id → propagation_steps → sink_call_site_id`，`sink_slot`/`tainted_arg_index`/`confidence`/`has_sanitizer_hint`/`notes`。
- `ParameterPropagationGraph.skipped_languages`：摘要据此声明"哪些语言无静态线索"。

### 3.3 消费现有

- `CallChain`：作为 sink/flow 的可达路径骨架。
- `tiered_audit.AuditPlan`：决定摘要里链的排列优先级。

### 3.4 产出（本 spec 定义）

新增 deliverable：`.shannon/deliverables/static_dataflow_hints.md`（独立文件，不污染 `recon_deliverable.md` 既有契约）。

---

## 4. 详细设计

### 4.1 扩展 `audit_input_builder.py`

新增/增强格式化函数（消费 B/A 模型，产出 markdown）：

```python
def build_static_dataflow_hints(
    index: CodeIndex,
    pgraph: ParameterPropagationGraph,
    audit_plan: AuditPlan,
) -> str:
    """产出 static_dataflow_hints.md 全文。"""
    parts = [
        _header(pgraph.language_coverage, pgraph.skipped_languages),   # §4.1.1
        _sink_inventory(index.sink_call_sites, audit_plan),            # §4.1.2 按 tier 排序
        _taint_flows(pgraph.taint_flows, index),                       # §4.1.3
        _coverage_disclaimer(pgraph),                                  # §4.1.4
    ]
    return "\n\n".join(parts)
```

#### 4.1.1 Header —— 覆盖范围声明（防误用）

```markdown
# Static Dataflow Hints（确定性静态线索，需 LLM 验证）

## 覆盖范围
- 已静态分析语言：python, typescript
- 未覆盖语言（无静态污点线索，请自行追链）：go, java, php
- ⚠️ 本文件是【线索】非【结论】。静态未列出的 sink/路径不代表安全。
```

#### 4.1.2 Sink Inventory —— 按 tier 排序的 sink 清单

每条：`file:line:col` · `category/sink_subtype` · 危险槽 `(arg_index, slot)` · `needs_review` 标记 · `rule_id`。

```markdown
## Sink 调用点（按审计优先级）
### Tier 3（高风险链）
- `src/db/user.py:42:18` SQL/sql_raw @ `cursor.execute` · 危险槽: (0, sql_value) · rule=py-db-cursor-execute
- `src/render/page.ts:88:9` XSS/xss_dom @ `innerHTML=` · ⚠️needs_review · rule=ts-innerhtml
```

#### 4.1.3 Taint Flows —— source→sink 链

每条 flow：entry → steps（含 transformation/sanitizer_hint）→ sink_slot + tainted_arg_index + confidence + notes。

```markdown
## 污点流（entry → sink）
- entry `src/routes/user.py:getUser:10` (param `uid`, source=query)
  → src/db/user.py:42 `execute` slot=sql_value arg=0
  · steps: concat@:40 · ⚠️sanitize_hint:escape@:38（不代表有效，请复核 concat-after-sanitize）
  · confidence=0.6 · notes: 容器字段过近似
```

#### 4.1.4 Coverage Disclaimer —— 边界提醒

明确：动态调用、模板 XSS、Go/Java/PHP 不在静态覆盖内；sanitizer_hint 非有效性判定；confidence 仅反映静态映射可信度。

### 4.2 产出时机与 activity

新增轻量 activity `run_render_dataflow_hints`（或在 `run_risk_scoring` 末尾追加），在 risk_scoring 产出 `AuditPlan` 后调用 `build_static_dataflow_hints` 写入 `static_dataflow_hints.md`。

> 排在 `run_risk_scoring` 之后、`run_vuln_agent` 之前（workflows.py:147→159 之间），确保 vuln agents 启动时摘要已就绪。

### 4.3 Prompt 改动（最小侵入）

对 `vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt` / `vuln-auth.txt` / `vuln-authz.txt` 与 `recon.txt`，在 `<starting_context>` 后新增一段（共享 partial `shared/_static-dataflow-hints.txt`）：

```markdown
<static_dataflow_hints>
可选的确定性静态线索位于 `.shannon/deliverables/static_dataflow_hints.md`：
- 把它作为追链【起点】与【交叉验证】：静态已定位的 sink 调用点、source→sink 流，优先据此展开验证。
- ⚠️ 它是线索，不是结论：
  - 静态【未列出】的 sink/路径 ≠ 安全——仍须用 Task agent 自主覆盖（动态调用、模板、未覆盖语言）。
  - `needs_review` 的 sink 请重点复核转义/上下文。
  - `sanitize_hint` 仅表示路径出现疑似 sanitizer，【不代表有效】——仍须按 slot 上下文判定，并检查 concat-after-sanitize。
  - `confidence` 低或 notes 提示过近似时，以你自己的数据流追踪为准。
- 你的最终判定（slot 匹配、verdict）始终以代码事实为准，静态线索仅供参考与加速。
</static_dataflow_hints>
```

**保留** prompt 现有"禁止主 agent 直接 Read 源码、委派 Task agent"的两层模型（`vuln-injection.txt:82-87`）——摘要由主 agent 读取并派发给 Task agent 作为追链目标，不改变两层结构。

### 4.4 tiered audit 衔接

- `static_dataflow_hints.md` 按 `AuditPlan` 的 tier 排序 sink/flow，tier3 链的静态线索置顶。
- `audit-tier1.txt`（tier1 轻量扫描）同样消费摘要，对低风险链做"静态已覆盖 → 快速确认"。

---

## 5. 边界与局限

| 局限 | 说明 | 缓解 |
|---|---|---|
| 静态不全 | 动态调用/模板/Go·Java·PHP 未覆盖 | disclaimer 明示；prompt 要求 LLM 仍自主覆盖 |
| 摘要 token 成本 | 大仓库 sink/flow 多 | 按 tier 取 top-N；tier1 仅摘要；可配置上限 |
| 不替代 LLM 研判 | slot/sanitizer 有效性仍由 LLM | prompt 显式声明 |
| 依赖 B/A 产出质量 | 摘要价值随 B/A 精度 | B/A 的 needs_review/confidence 传递到摘要 |

---

## 6. 测试策略

### 6.1 单元测试（`test_static_dataflow_hints.py`）

- `build_static_dataflow_hints` 对 fixture 的 CodeIndex + ParameterPropagationGraph + AuditPlan 产出正确 markdown 段落。
- tier 排序：tier3 sink 在 tier2 前。
- `needs_review` / `has_sanitizer_hint` / `skipped_languages` 正确渲染为对应标记/声明。

### 6.2 Prompt 渲染测试

- `PromptManager.load` 渲染 vuln-injection 时，`shared/_static-dataflow-hints.txt` 正确 include。
- 摘要文件不存在时（降级/早期阶段），prompt 仍可正常运行（摘要引用标注"可选"）。

### 6.3 端到端

- 样例仓库跑完 pipeline，`static_dataflow_hints.md` 在 vuln agents 启动前生成于 deliverables。
- 抓取 vuln agent 的归档 prompt（`workspaces/.../prompts/`），确认含静态线索段。

### 6.4 回归

- 现有 vuln agent 行为不破坏（摘要为补充，两层模型不变）。
- `--pipeline-testing` 模式下摘要可关闭（避免拖慢 CI）。

---

## 7. 与其他 spec 的接口约定

| 接口 | 方向 | 约定 |
|---|---|---|
| `SinkCallSite` 字段 | B → C | 摘要渲染 category/sink_subtype/slots/needs_review/rule_id |
| `TaintFlow` 字段 | A → C | 摘要渲染 steps/sink_slot/tainted_arg_index/confidence/has_sanitizer_hint/notes |
| `skipped_languages` | A → C | 摘要 header 声明未覆盖语言 |
| `SlotContext` 词汇 | B → C → prompt | 摘要与 prompt 的 slot 术语统一 |
| `static_dataflow_hints.md` | C 写 → vuln/recon agents 读 | 独立 deliverable，可选消费 |
| `shared/_static-dataflow-hints.txt` | C 定义 → 各 vuln prompt include | 单一来源，改一处全生效 |

---

## 附录：与原始项目的对比

原始 Shannon 的 vuln agent 完全靠 Task 子 agent 从零追链（读 `recon_deliverable.md`）。本 spec 在不改变两层 agent 模型的前提下，把 Spec B/A 的确定性事实以"补充上下文"形式前置给 LLM，加速追链起点、提供交叉验证，但**不改变研判归 LLM**的分工。这是"结构确定性 + 语义 LLM"在消费侧的落地。
