# Spec A：参数传播 / 污点图（落地 plan-b，填补空跑的 ParameterPropagationGraph）

> 本 spec 依赖 **Spec B**（`2026-06-09-spec-b-sink-detector-design.md`）产出的 `SinkCallSite` 作为传播终点，并消费 **Spec B §3.4** 定义的 `TaintFlow` 升级契约。产出被 **Spec C**（`2026-06-09-spec-c-llm-consumption-design.md`）喂给 LLM。
>
> 依赖顺序：**B → A → C**。本 spec 不启动，除非 Spec B 的 `SinkCallSite` 已可用。

---

## 1. 背景与现状

### 1.1 "参数传播图"目前是脚手架，从未运行

证据链（grep 全仓库）：

- `parameter_models.py` 定义了 `TaintFlow` / `PropagationStep` / `ParameterPropagationGraph`（50 行，纯数据模型）。
- 它们被 `risk_scorer.py:62`、`tiered_audit.py:61`、`audit_input_builder.py:24` **消费**（作为函数入参）。
- 但 `TaintFlow(...)` / `ParameterPropagationGraph(...)` 的构造**只出现在测试代码**里（`test_risk_scorer.py`、`test_tiered_audit.py`、`test_audit_input_builder.py`）。
- `activities.py:240` 读取 `param_graph.json` 文件：`if param_graph_path.exists(): ...`——**没有任何代码写入这个文件** → 该分支永远不执行 → `taint_flows_by_chain` 永远是空 dict。

后果：
- `risk_scorer` 的 `taint_completeness` 维度（`risk_scorer.py:77-79`）永远为 0。
- `tiered_audit` 的 `taint_flows_by_chain` 永远空，分层审计缺少污点信息。
- 对应 git log 的 `plan-b-parameter-propagation-graph` 文档——**计划存在，实现缺失**。本 spec 就是把它落地。

### 1.2 已有可复用的资产

| 资产 | 位置 | 复用方式 |
|---|---|---|
| `CallChain`（entry→...→sink 的 FuncBlock 路径） | `models.py:55`，`call_graph.py` BFS 已构建 | 跨函数传播沿此路径走 |
| `CallEdge`（A 调用 B + 行号） | `models.py:33` | call site 参数映射定位 |
| `TypedParameter`（含类型/source） | `models.py:109`，`enhanced_parameters.extract_typed_parameters` | Python/TS 已实现，作为参数槽位类型 |
| `SinkCallSite` + `dangerous_slots` | Spec B §3.1 | 传播终点 + 危险槽位约束 |
| `mark_http_parameter_sources` | `enhanced_parameters.py:214` | 入口参数的 `ParameterSource` 标注 |

---

## 2. 目标

1. 实现**真正的**参数传播生产者 `propagation_builder.py`，产出非空的 `ParameterPropagationGraph`，写入 `param_graph.json`，激活 `activities.py:240` 的分支。
2. **过程内**污点传播：在单个函数体内，追踪赋值/拼接/传递，确定 tainted 变量集合。
3. **沿 CallChain 有限跨函数**传播：entry 的 tainted 参数 → 沿 call site 参数映射 → 到达 `SinkCallSite` 的 `dangerous_slot`。
4. **语言范围**：Python + TypeScript 先做（`enhanced_parameters` 已支持这两门的 typed param 提取）。Go/Java/PHP 的 typed param 提取（现为空 `_extract_generic`）作为本 spec 的后续任务。
5. 标注 `transformation`（concat/encode/format/sanitize-hint）与 `confidence`；明确标注**不完备**，作为 LLM 的线索而非结论（呼应 Spec C"作为补充上下文"的决策）。

---

## 3. 数据契约（与 Spec B/C 的接口）

### 3.1 消费 Spec B 的产物

- `SinkCallSite`（§3.1）：传播终点。`dangerous_slots` 约束"污染必须到达哪个 arg_index + slot"才算命中。
- `SlotContext`（§3.1）：`TaintFlow.sink_slot` 取值范围。
- `SinkCallSite.id` 格式（`§7`）：`TaintFlow.sink_call_site_id` 必须严格匹配。

### 3.2 升级 `TaintFlow`（本 spec 实现 Spec B §3.4 预留的升级）

```python
class PropagationStep(BaseModel):
    step_id: str
    from_func_id: str
    from_param: str               # 调用方侧的 tainted 变量/参数
    to_func_id: str
    to_param: str                 # 被调用方侧接收的参数
    transformation: str | None    # "concat" / "encode" / "format" / "sanitize_hint:<name>" / None
    code_location: str            # "{file}:{line}"
    confidence: float             # 本步映射的可信度

class TaintFlow(BaseModel):
    flow_id: str                  # "{entry_point_id}->{sink_call_site_id}"
    entry_point_id: str
    source_param: str             # entry 处的污染源参数
    source_type: ParameterSource
    propagation_steps: list[PropagationStep] = []
    sink_call_site_id: str        # ← 替换原 sink_func_id，指向 SinkCallSite.id
    sink_slot: SlotContext        # ← 新：到达的槽位上下文
    tainted_arg_index: int        # ← 新：污染到达第几个实参（须 ∈ SinkCallSite.dangerous_slots）
    confidence: float             # ← 新：整条链的可信度（取最弱步）
    has_sanitizer_hint: bool      # ← 新：路径上出现疑似 sanitizer（不判有效性，留给 LLM）
    notes: str = ""               # 不完备说明（如"未追踪容器字段"）

class ParameterPropagationGraph(BaseModel):
    taint_flows: list[TaintFlow] = []
    language_coverage: list[str] = []   # 实际传播覆盖的语言（如 ["python","typescript"]）
    skipped_languages: list[str] = []   # 跳过的语言（Go/Java/PHP 暂未支持）
```

### 3.3 `param_graph.json`（本 spec 写入）

`write_index_files` 额外产出 `param_graph.json`（与 `code_index.json` 同目录）。`activities.py:240` 的读取分支随之激活。

---

## 4. 详细设计

### 4.1 新模块：`packages/core/src/shannon_core/code_index/propagation_builder.py`

#### 4.1.1 三阶段算法

```
build_propagation_graph(index: CodeIndex, entry_points, typed_params) -> ParameterPropagationGraph:
  sinks_by_func = group index.sink_call_sites by caller_id
  chains_by_entry = group index.chains by entry_point_id

  flows = []
  for each entry_point:
    tainted_entries = seed_taints(entry_point, typed_params)   # 入口参数 source_type ≠ INTERNAL 的为 tainted
    for each chain starting at entry_point:
      flow = trace_chain(chain, tainted_entries, sinks_by_func, ...)
      if flow reaches a SinkCallSite.dangerous_slot:
        flows.append(flow)
  return ParameterPropagationGraph(flows, ...)
```

#### 4.1.2 过程内传播（`_intra_procedural`）

对单个 FuncBlock，给定入口 tainted 变量集合，返回 `{SinkCallSite.id → (tainted_arg_index, steps)}`。

简化 dataflow（顺序语句分析，不做迭代不动点——函数体通常无循环引入的复杂别名）：

```
tainted: set[str] = seed（入口参数名 + 解构出的 request.x 等已知入口变量）
for stmt in AST statements (顺序):
  if assignment (x = expr):
     if expr_references_tainted(expr, tainted):
        tainted.add(x); record transformation(assignment_node)   # concat/format 检测
  if call:
     callee = destructure(call)
     if callee is a SinkCallSite (查 sinks_by_func):
        for (arg_idx, arg_expr) in enumerate(call.args):
           if references_tainted(arg_expr, tainted) and (arg_idx,_) ∈ sink.dangerous_slots:
              record intra hit: sink.id ← (arg_idx, local_steps)
     elif callee is a known function (有 CallEdge):
        record call-site param map: {callee_param_i: tainted? }   # 供跨函数用
```

`expr_references_tainted`：递归检查表达式是否含 tainted 集合中的标识符。容器字段粗略处理——`d[k]` 若 `d` tainted 则视 `d[k]` tainted（过近似，倾向 recall）。

#### 4.1.3 跨函数传播（`_trace_chain`，沿 CallChain）

```
trace_chain(chain, seed_taints, sinks_by_func):
  current_tainted_by_func = {chain.path[0]: seed_taints}   # entry 的 tainted 参数
  steps = []
  for i, func_id in enumerate(chain.path):
     intra = _intra_procedural(func_id, current_tainted_by_func[func_id], ...)
     # 命中 sink？
     for (sink_id, arg_idx, local_steps) in intra.hits:
        return TaintFlow(entry, steps+local_steps, sink_id, slot, arg_idx, confidence, ...)
     # 否则向下一函数传递：用本函数 call-site param map
     if i+1 < len(chain.path):
        callee_id = chain.path[i+1]
        edge = find CallEdge(caller=func_id, callee=callee_id)
        callee_tainted = map_params(edge.args, current_tainted_by_func[func_id], callee_params)
        current_tainted_by_func[callee_id] = callee_tainted
        steps.append(PropagationStep(func_id → callee_id, transformation, confidence))
  return None   # 链未到达 sink
```

#### 4.1.4 关键简化（明确的不完备边界）

| 简化 | 行为 | 影响 | 缓解 |
|---|---|---|---|
| 无迭代不动点 | 单趟顺序分析，不处理循环内定义-使用 | 循环引入的 taint 可能漏 | 标 `confidence` 降低；LLM 复核 |
| 容器字段过近似 | `d[k]` 跟随 `d` 的 taint | 可能 false positive（recall 偏向） | 安全分析宁可多报 |
| 分支保守 | 任一分支可能污染即视为 tainted | if/else 合并可能过近似 | 同上 |
| 跨函数靠 CallEdge 参数位置映射 | 不做 alias / 指针分析 | 间接传递（通过容器/对象字段）可能漏 | 深度受限（CallChain.max_depth=15）；标不完备 |
| sanitizer 不判有效性 | 路径出现疑似 sanitizer 名记 `sanitize_hint`，不停 taint | 不会因"看到 escape()"就判定安全 | 有效性判定（含 concat-after-sanitize）留给 LLM |

#### 4.1.5 sanitizer 提示集（best-effort，非判定）

```python
SANITIZER_HINTS = {
    "escape", "escapeHtml", "encodeURIComponent", "htmlentities", "htmlspecialchars",
    "sanitize", "validator.", "bleach.clean", "markupsafe",
    "shlex.quote", "quote", "parameterize",  # 参数绑定提示
}
```

匹配到则在 `PropagationStep.transformation` 标 `"sanitize_hint:<name>"`，`TaintFlow.has_sanitizer_hint=True`。**不停 taint**——是否真正阻断由 LLM 研判（Spec C）。

### 4.2 集成进 pipeline

`run_code_index` activity（`activities.py:158`）在 `build_code_index` 后新增一步：

```python
from shannon_core.code_index.propagation_builder import build_propagation_graph
pgraph = build_propagation_graph(index, adjudicated_entry_points, typed_params)
write_param_graph(pgraph, deliverables)   # 写 param_graph.json
```

`run_risk_scoring`（`activities.py:147`，已在主流程）随之读到非空 `taint_flows_by_chain`，`taint_completeness` 与 tiered audit 污点维度激活。

### 4.3 Go/Java/PHP 的处理

`build_propagation_graph` 对这三门语言（`enhanced_parameters._extract_generic` 返回空 typed param）：
- **跳过过程内/跨函数传播**，不产出 TaintFlow。
- 在 `ParameterPropagationGraph.skipped_languages` 记录，供 Spec C 提示 LLM"这些语言无静态污点线索，请自行追链"。
- 后续任务（非本 spec）：为这三门补 typed param 提取，传播算法本体可复用。

---

## 5. 边界与局限

| 局限 | 说明 |
|---|---|
| 仅 Python/TS | Go/Java/PHP 无 typed param 提取，跳过并显式记录 |
| 非完备 dataflow | 无不动点迭代、无 alias 分析、容器过近似——是"线索"非"结论" |
| sanitizer 只提示不判定 | 有效性（含 concat-after-sanitize）仍由 LLM |
| 依赖 CallChain 质量 | 跨函数传播沿 CallChain 走；CallChain 未建/截断的路径不传播 |
| recall 偏向 | 容器/分支过近似 → 可能多报，由 LLM 去伪 |

---

## 6. 测试策略

### 6.1 单元测试（`test_propagation_builder.py`）

- **过程内**：函数体内赋值链、拼接、到达 sink → 正确产出 intra hit。
- **跨函数**：entry → A → B(sink)，参数槽位映射正确，产出完整 TaintFlow。
- **槽位约束**：taint 到达非 dangerous_slot 的参数 → 不算命中 sink。
- **transformation**：concat/format/sanitize_hint 正确标注。
- **不完备标注**：循环/容器场景 confidence 降低、notes 非空。

### 6.2 端到端激活测试

- 构造样例仓库，`run_code_index` 后 `param_graph.json` **存在且非空**。
- `risk_scorer` 的 `taint_completeness` 对含 TaintFlow 的 chain > 0。
- `tiered_audit` 的 `taint_flows_by_chain` 非空。

### 6.3 回归

现有 `test_risk_scorer` / `test_tiered_audit` / `test_audit_input_builder`（它们手工构造 TaintFlow）不受影响——契约升级后，这些测试改为用新字段（`sink_call_site_id` 等），保持行为一致。

---

## 7. 与其他 spec 的接口约定

| 接口 | 方向 | 约定 |
|---|---|---|
| `SinkCallSite.id` / `dangerous_slots` | B → A | 传播终点；`tainted_arg_index` 必须 ∈ `dangerous_slots` 的 arg_index |
| `SlotContext` | B → A | `TaintFlow.sink_slot` 取值 |
| `TaintFlow` 升级字段 | A 定义 → C 引用 | `sink_call_site_id`/`sink_slot`/`tainted_arg_index`/`confidence`/`has_sanitizer_hint` |
| `param_graph.json` | A 写 → risk_scorer/tiered_audit/C 读 | 激活 `activities.py:240` |
| `skipped_languages` | A 产出 → C 提示 | C 据此告诉 LLM 哪些语言无静态线索 |
| `has_sanitizer_hint` | A 标 → C | C 提示 LLM "路径有疑似 sanitizer，请复核有效性（含 concat-after-sanitize）" |

---

## 附录：与原始项目的关系

原始 Shannon 的调用链数据流追踪**完全靠 LLM**（Task 子 agent 用 Read/Grep 跳转）。本 spec 引入**确定性污点传播作为 LLM 的前置线索**，但**不替代** LLM 研判——研判（slot 上下文匹配、sanitizer 有效性）仍由 LLM（Spec C 保持）。这是"结构确定性 + 语义 LLM"分工的延续：能静态算的过程内/沿链传播交给代码，需要语义的 sanitizer/slot 判定交给 LLM。
