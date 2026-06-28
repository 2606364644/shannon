# Design: GitNexus 调用图 + LLM taint 分析架构

> **Date:** 2026-06-10
> **Status:** Approved
> **Replaces:** `propagation_builder.py` (Spec A taint engine), `call_graph.py` (BFS call graph)
> **Spec reference:** 三维对比白盒分析 spec §3.2

---

## 1. 动机

当前 taint 传播引擎（`propagation_builder.py`，526 行）和调用图构建（`call_graph.py`，133 行）是纯工程化确定性代码——正则匹配赋值、集合运算跟踪 tainted 变量、BFS 遍历调用图。问题：

1. **无人维护**：正则模式覆盖不完整（`self.x`、`d["k"]`、解构赋值等 6 种模式断裂），修不完
2. **调用图精度差**：函数名匹配导致同名冲突，跨文件 import 解析缺失，diamond path 丢失
3. **扩展性差**：每增加一种赋值模式或语言特性都需要手写正则

**新架构**：GitNexus 提供精确调用图（上游），LLM 做函数内 taint 传播分析（下游），确定性代码做跨函数参数映射（中间层）。

---

## 2. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| taint 精度 | 参数级（与当前 TaintFlow 模型兼容） | 下游 risk_scorer 不需要改 |
| LLM 调用粒度 | 每函数单独调用 | 小上下文、结果稳定、可测试 |
| 回退策略 | 删除当前引擎，不保留 propagation_builder.py | 彻底消除维护负担 |
| sink 检测 | 保留 sink_detector.py（AST 规则匹配） | 378 行，198 条规则，确定性且准确 |
| GitNexus 集成 | 必须可用，不做 AST 降级 | 内部使用场景，环境可保证 |
| pipeline 接口 | 改为 async | 上层 Temporal workflow 已经是 async |

---

## 3. 模块划分

### 3.1 删除清单

| 文件 | 行数 | 处置 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/propagation_builder.py` | 526 | 删除 |
| `packages/core/src/shannon_core/code_index/call_graph.py` | 133 | 删除 |
| `packages/core/src/shannon_core/code_index/taint_propagator.py` | ~100 | 删除（已标记 deprecated） |
| `packages/core/tests/code_index/test_propagation_builder.py` | 495 | 删除 |
| `packages/core/tests/code_index/test_call_graph.py` | ~200 | 删除 |

### 3.2 新增清单

| 文件 | 职责 | 预估行数 |
|---|---|---|
| `gitnexus_call_graph.py` | 通过 GitNexus MCP 构建精确调用图 | ~100 |
| `llm_taint_analyzer.py` | 逐函数调用 LLM 做 intra-procedural taint 分析 | ~150 |
| `chain_propagator.py` | 确定性的跨函数参数映射（沿 GitNexus 调用链） | ~120 |

### 3.3 保留清单

| 文件 | 处置 |
|---|---|
| `sink_detector.py` (373 行) | 保留，AST 规则匹配不变 |
| `models.py` (188 行) | 保留，`FuncBlock`、`CallEdge`、`TaintFlow` 等模型不变 |
| `parameter_models.py` (138 行) | 保留，数据结构不变 |
| `enhanced_parameters.py` (257 行) | 保留，Tree-sitter 参数提取不变 |
| `gitnexus_engine.py` | 保留，CLI wrapper 不变 |
| `gitnexus_mcp.py` | 保留，MCP client 不变 |
| `parsers/*.py` | 保留，语言解析器不变 |

---

## 4. 整体数据流

```
源代码
  │
  ├─ Tree-sitter parsers ──→ FuncBlock[] + 参数信息
  │
  ├─ GitNexus MCP ─────────→ 精确调用图 (edges, chains, entry_points)
  │
  ├─ sink_detector ────────→ SinkCallSite[]
  │
  └─ 过滤: 只对有 sink 的函数
       │
       ▼
  llm_taint_analyzer (逐函数, async)
       │  输入: func_block + sinks_in_func + typed_params
       │  输出: IntraResult (tainted_params + hits + propagation_paths)
       │
       ▼
  chain_propagator (确定性, 同步)
       │  沿 GitNexus 调用链映射: caller.tainted → callee.seed
       │  输出: TaintFlow[]
       │
       ▼
  ParameterPropagationGraph → risk_scorer
```

---

## 5. 模块详细设计

### 5.1 GitNexus 调用图集成（`gitnexus_call_graph.py`）

#### 职责

替代 `call_graph.py` 的两阶段流程（`resolve_edges` 名称匹配 + `build_call_chains` BFS），改为从 GitNexus MCP 获取精确调用关系。

#### 函数签名

```python
def build_call_graph_from_gitnexus(
    repo_path: str,
    mcp_client: GitNexusMCPClient,
) -> CallGraphResult:
    """通过 GitNexus MCP 构建精确调用图。"""
```

#### 返回类型

```python
@dataclass
class CallGraphResult:
    edges: list[CallEdge]          # 复用 models.py
    chains: list[CallChain]        # 复用 models.py
    entry_points: list[FuncBlock]  # 复用 models.py
    degradation_report: DegradationReport | None = None
```

#### GitNexus MCP 工具协作

1. **query** — 获取入口点列表 + confidence score
2. **process** — 从入口点出发获取完整调用链，每条边包含 caller_file、caller_line、callee_file、callee_line、callee_name
3. **cypher** — 补充查询 process 遗漏的间接调用（回调、动态分派），查询模式：`MATCH path=(ep)-[:CALLS*]->(sink) RETURN path`

#### 与当前 call_graph.py 的对比

| 维度 | 当前 (call_graph.py) | 新 (gitnexus_call_graph.py) |
|---|---|---|
| 调用边来源 | Tree-sitter 正则 + 函数名匹配 | GitNexus 知识图谱（符号级精确） |
| 同名函数冲突 | 取第一个候选 | 按文件路径+行号精确解析 |
| 跨文件 import | 不支持 | GitNexus 14 语言 import resolution |
| 继承/多态 | 不支持 | heritage + constructor inference |
| diamond path | BFS 丢失 | GitNexus process 保留 |
| 入口点 | 启发式规则 | GitNexus EP scoring + confidence |

#### 前置条件

- GitNexus 已索引目标仓库
- MCP server 可连接
- 不可用时抛出 `GitNexusNotIndexedError`，终止 pipeline

---

### 5.2 LLM taint 分析器（`llm_taint_analyzer.py`）

#### 职责

替代 `propagation_builder.py` 的 `seed_taints()` + `analyze_intra()`。对每个包含 sink 的函数，调用 LLM 判断参数级 taint 传播。

#### 函数签名

```python
async def analyze_taint_llm(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
    *,
    typed_params: list[TypedParameter] | None = None,
    llm_client: LLMClient,
) -> IntraResult:
    """LLM 驱动的函数内 taint 分析。"""
```

#### 返回类型

```python
@dataclass
class IntraResult:
    tainted_params: set[str]
    hits: dict[str, float]               # sink_id → confidence
    local_steps: list[PropagationStep]   # 传播路径（用于可视化和调试）
```

与 chain_propagator 的消费接口完全兼容。

#### LLM Prompt 策略

```
你是一个安全代码分析器。分析以下函数的 taint 传播。

## 函数信息
函数名: {block.function_name}
文件: {block.file_path}:{block.start_line}
参数: {params_with_types}

## 函数源码
```{language}
{block.source}
```

## 危险调用点
{sinks_with_args}

## 任务
1. 判断哪些入口参数是"不可信数据源"（用户输入、HTTP 参数、外部请求）
2. 追踪每个参数在函数内的赋值和传递过程
3. 判断不可信数据是否到达了上述危险调用点的参数

## 输出格式 (JSON)
{json_schema}
```

#### Structured Output 约束

```python
class TaintPath(BaseModel):
    source_param: str
    sink_id: str
    sink_arg_index: int
    intermediate_vars: list[str] = []
    sanitized: bool
    sanitizer_description: str | None = None
    confidence: float  # 0.0 - 1.0

class TaintAnalysisResult(BaseModel):
    tainted_params: list[str]
    propagation_paths: list[TaintPath]
```

#### 调用过滤

不对所有函数调用 LLM，只分析有 sink 的函数。典型项目约 50-200 个函数（而非全量 500-2000 个）。

#### seed 判断

LLM 从参数名、类型注解和框架上下文推断哪些参数是外部输入源。如果无法判断（参数名模糊），默认标记为 tainted（过近似，不漏报）。

#### 函数体截断

- 上限：1200 行
- 截断策略：前 1000 行 + sink 所在行 ± 30 行
- 避免超出 LLM 上下文窗口

---

### 5.3 确定性跨函数传播（`chain_propagator.py`）

#### 职责

替代 `propagation_builder.py` 的 `_trace_chain()`。沿 GitNexus 调用链做确定性的参数映射，不调用 LLM。

#### 函数签名

```python
def propagate_across_chains(
    chains: list[CallChain],
    blocks: list[FuncBlock],
    intra_results: dict[str, IntraResult],
    *,
    max_depth: int = 20,
) -> list[TaintFlow]:
    """沿调用链做确定性的跨函数 taint 传播。"""
```

#### 传播算法

```
for chain in chains:
    current_tainted = intra_results[chain[0]].tainted_params
    accumulated_steps = []

    for i, caller_id in enumerate(chain):
        if i >= max_depth: break

        callee_id = chain[i + 1]
        call_site = find_call_site(caller_id, callee_id, blocks)

        # 确定性参数映射
        callee_params = blocks[callee_id].params
        callee_seed = set()
        for j, arg_expr in enumerate(call_site.args):
            if _references_tainted(arg_expr, current_tainted):
                callee_seed.add(callee_params[j])

        # 合并 LLM intra 结果
        callee_intra = intra_results.get(callee_id)
        if callee_intra:
            confirmed = callee_seed & callee_intra.tainted_params
            for sink_id, confidence in callee_intra.hits.items():
                accumulated_steps.append(PropagationStep(...))

        current_tainted = callee_seed

    if accumulated_steps:
        taint_flows.append(TaintFlow(...))
```

#### 参数引用检查

```python
def _references_tainted(arg_expr: str, tainted: set[str]) -> bool:
    """判断参数表达式是否引用了 tainted 变量。过近似匹配。"""
    return any(t in arg_expr for t in tainted)
```

#### 输出

`list[TaintFlow]`，直接复用 `parameter_models.py` 的数据结构。下游 `risk_scorer` 无需修改。

---

## 6. Pipeline 集成

### 新 pipeline 主函数

```python
async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client: GitNexusMCPClient,
    llm_client: LLMClient,
) -> CodeIndex:
    # ① Tree-sitter 解析 → FuncBlock[]
    blocks = parser.parse_all(repo_path)

    # ② GitNexus MCP → 精确调用图
    call_graph = build_call_graph_from_gitnexus(repo_path, mcp_client)

    # ③ sink 检测
    sinks = detect_sinks(blocks, parser, source_provider=...)

    # ④ 按函数分组 sink
    sinks_by_func = group_by(sinks, key=lambda s: s.func_id)

    # ⑤ LLM taint 分析（只对有 sink 的函数）
    intra_results = {}
    for func_id, func_sinks in sinks_by_func.items():
        intra_results[func_id] = await analyze_taint_llm(
            block=blocks_by_id[func_id],
            sinks_in_func=func_sinks,
            typed_params=typed_params_by_block.get(func_id),
            llm_client=llm_client,
        )

    # ⑥ 确定性跨函数传播
    taint_flows = propagate_across_chains(
        chains=call_graph.chains,
        blocks=blocks,
        intra_results=intra_results,
    )

    # ⑦ 组装 CodeIndex
    return CodeIndex(
        blocks=blocks,
        edges=call_graph.edges,
        entry_points=call_graph.entry_points,
        chains=call_graph.chains,
        sink_call_sites=sinks,
        parameter_graph=ParameterPropagationGraph(taint_flows=taint_flows),
    )
```

### 调用者变更

```python
# 之前:
from shannon_core.code_index import build_code_index

# 之后:
from shannon_core.code_index import build_code_index_with_gitnexus
```

`build_code_index` 旧函数删除。`build_code_index_with_gitnexus` 从空壳升级为唯一入口。

### LLM Client 注入

通过 pipeline 顶层注入，不硬编码：

```python
# whitebox pipeline
async def run_whitebox_scan(repo_path: str, llm_client: LLMClient):
    index = await build_code_index_with_gitnexus(
        repo_path,
        mcp_client=get_gitnexus_mcp(),
        llm_client=llm_client,
    )
```

### 对下游的影响

| 下游消费者 | 变更 |
|---|---|
| `risk_scorer` | 零变更 — 消费 `TaintFlow[]`，数据结构不变 |
| `rebuild_call_chains` activity | 删除 — GitNexus 已提供精确调用链 |
| `vuln_agent` activity | 零变更 — 消费 `TaintFlow` 做 LLM 漏洞分析 |
| `parameter_graph.json` | 零变更 — 序列化格式不变 |

---

## 7. 错误处理

### LLM 错误

| 错误类型 | 处理策略 |
|---|---|
| 超时 / 限流 | 保守过近似：`IntraResult(tainted_params=all_params, hits={}, local_steps=[])` |
| JSON 解析失败 | 重试 1 次，仍失败则保守过近似 |
| 引用不存在的参数名 | 静默忽略该路径，只保留可验证的路径 |
| 返回空结果 | 视为无 taint 传播 |

核心原则：**宁可过报（false positive），不可漏报（false negative）**。过报由下游 `vuln_agent` 二次过滤，漏报则彻底丢失。

### GitNexus 错误

| 错误类型 | 处理策略 |
|---|---|
| 未索引 | 抛出 `GitNexusNotIndexedError`，终止 pipeline |
| MCP 连接失败 | 抛出 `GitNexusConnectionError`，终止 pipeline |
| 部分调用边缺失 | 记入 `DegradationReport`，继续分析 |

### 边界情况

| 场景 | 处理 |
|---|---|
| 函数无参数 | 不调用 LLM，返回空 `IntraResult` |
| sink 参数全是硬编码 | LLM 应返回空路径；误报由 chain_propagator 过滤 |
| 递归调用 | GitNexus 处理环检测；chain_propagator 用 `max_depth=20` 兜底 |
| 回调/动态分派 | GitNexus heritage + constructor inference；解析不了记入 DegradationReport |
| 函数体过大（>1200 行） | 截断：前 1000 行 + sink 所在行 ± 30 行 |

### 日志与可观测性

每次 LLM 调用记录：

```python
logger.info(
    "llm_taint_analysis",
    extra={
        "func_id": block.id,
        "func_name": block.function_name,
        "file": block.file_path,
        "num_sinks": len(sinks_in_func),
        "tainted_count": len(result.tainted_params),
        "hits_count": len(result.hits),
        "latency_ms": elapsed,
    },
)
```

---

## 8. 可配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `max_depth` | 20 | chain_propagator 最大传播深度 |
| `max_func_lines` | 1200 | 发送给 LLM 的函数体行数上限 |
| `func_prefix_lines` | 1000 | 截断时保留的前 N 行 |
| `sink_context_lines` | 30 | 截断时 sink 所在行 ± N 行 |
| `llm_retry_count` | 1 | LLM 输出格式错误时的重试次数 |
