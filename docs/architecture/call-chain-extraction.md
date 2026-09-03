# 调用链提取

调用链提取把入口、source、sink、函数内传播和 GitNexus process trace 合成 `ParameterPropagationGraph.taint_flows`。它追求“锚点真实”：source 和 sink 都必须落在具体代码位置，而不是从函数名猜测。

## 输入

```text
CodeIndex
  blocks            tree-sitter 函数块
  entry_points      入口/路由/process entry
  chains            GitNexus entry→terminal 调用链
  source_points     外部输入/存储读取锚点
  sink_call_sites   精确危险调用点
  storage_write_points
IntraResult
  每个含 sink 函数的函数内 taint 摘要
```

核心模型：

- `CallChain.path`：有序 `FuncBlock.id`，`entry_point_id=path[0]`。
- `PropagationStep`：函数间参数映射，可带 transformation、sanitizer hint、中间变量与位置。
- `TaintFlow.flow_id` 默认为 `{entry_point_id}->{sink_call_site_id}`，还携带 source param/type、精确 sink id、槽位、置信度和复核标记。

## GitNexus process trace

`gitnexus_call_graph.py` 不再导出全量 CALLS 边再 Python BFS。当前流程：

1. MCP `cypher` 探测 `Process` 节点，确认仓库已索引。
2. 读取全部 process label 对应的 `gitnexus://repo/<repo>/process/<label>` resource。
3. 解析 `N: function (file)` 行为有序步骤。
4. 将每一步匹配回 tree-sitter `FuncBlock`：
   - `(file_path, function_name)` 精确匹配；
   - 文件路径尾匹配；
   - 函数名全仓唯一；
   - 都失败则生成占位 id 并置 `has_unresolved=True`。
5. 每条 trace 转成 `CallChain`；链头去重为 process entry。

`edges` 保留字段但当前为空；下游不再依赖全量边重建。单条 trace 读取失败会跳过并记录，不中断整个索引。

## 函数内分析

只有含 sink 的函数进入 LLM taint 分析，避免全函数集调用放大。`analyze_taint_llm` 输出：

- 哪些参数/局部变量污点可达某 sink
- sink hit 与置信度
- 函数内 `local_steps`
- sanitizer/transformation 线索

LLM 不可用或失败时走确定性 intra fallback，至少利用入口参数名、sink 实参和 `is_entry_hint` 保守生成候选；这类候选会被标记复核，由 chain verdict 终审。

## 双路径传播

当前 propagation 同时跑两条路径，再按 `(entry_point_id, source_param, sink_call_site_id)` 去重。

### intra-first

`produce_intra_first_taint_flows` 不依赖 handler 是否在 GitNexus chain 上：

- 对每个含 sink 函数直接找匹配的 `SourcePoint`；
- 用函数内 `local_steps` 生成单函数 flow；
- 覆盖“handler 不在 process trace 但同函数内确实 source→sink”的漏报；
- 当 intra 缺失或空判定时，可用 sink 实参表达式与 source 表达式匹配生成低置信度 `intra-first-expr-fallback`；
- 当 intra 有信息但否定某 sink 时，生成有上限的 `presumed-safe` 候选送终审，防止函数内初判误杀安全分支。

presumed-safe 每 sink 上限由 `SUPERNOVA_PRESUMED_SAFE_MAX_PER_SINK` 控制，避免 source×sink 笛卡尔积爆炸。

### backward sink→source

`propagate_backward_across_chains` 从 chain 中含 sink 的节点反向回溯：

1. 从 sink 函数的 tainted 参数/危险实参取 seed。
2. 对 `path[sink_step -> 0]` 逐跳反向映射调用实参到 caller 参数。
3. 到达 entry 后必须匹配真实 `SourcePoint`；无法锚定的链丢弃。
4. 步骤反转成 source→sink 语义。
5. 合并 sink 函数的 `local_steps`，保留 sanitizer/transformation 信息。
6. 透传 sink 的主槽位和 tainted argument index。

这解决了“只有函数调用图没有参数映射”的空心链问题：两端都有具体代码锚点。

## source 补召回

除入口 source 规则外，索引阶段还会：

- 根据 sink 函数调用反推可能的外部输入；
- 用 LLM source hunter 分析候选函数；
- 识别 storage read 为 `ParameterSource.STORAGE` 的软 `SourcePoint`；
- 去重合并 entry 主路径、规则补召回、LLM 补召回与 storage read。

## 二阶存储链

存储写/读被拆成两个锚点：

- `StorageWritePoint`：用户输入写入 DB/cache/queue/文件的位置，记录 medium 与可解析 token。
- storage read `SourcePoint`：后续从同一 medium 读出的位置。

`second_order_join.py` 用 `(medium, token)` 做二部图匹配：

- 只连接可字面解析的 token；
- `""`、`unresolvable`、含 `+`/`${}` 的动态 token 保守跳过，交给纯 LLM 轨创意补召回；
- 可从 raw SQL `FROM/INTO`、尾随字面量、ORM 实体注解（`@Table`、`@TableName`、`@Document`）和命名约定解析表/集合名；
- join 后由 second-order builder 生成 XSS/SQLi 候选，source type 为 `STORAGE`。

这不是通用数据流证明；它是保守的确定性锚点连接，有效性仍由 chain verdict 验证。

## vuln builder

`vuln_chain_builders` 把 `TaintFlow` 转成对应漏洞卡：

- `injection_builder`：SQL/command/file/template/deserialization 槽位。
- `xss_builder`：XSS sink；同时尝试用户输入写 flow + 内部读/render flow 合成 stored XSS。
- `ssrf_builder`：URL 槽位；redirect sink 归 Open Redirect 子型。
- `second_order_builder`：storage write × read join。
- builder 会 join adjudicated entry route，生成 `METHOD /path` endpoint。
- builder 不终审；它输出候选卡与证据链给 verdict agent。

## 产物与下游

```text
whitebox/intermediate/code_index.json
whitebox/intermediate/parameter_graph.json
whitebox/intermediate/<vuln>_chain_verdicts.json
whitebox/intermediate/<vuln>_gitnexus_queue.json
```

下游消费：

- chain verdict：逐链深判；
- dual-track merger：与纯 LLM queue 合并；
- dataflow view：按 flow id 展示安全/漏洞枝；
- MR scope：按 flow 中 source/sink/propagation 行号过滤增量；
- 跨仓关联：读取最终 exploitation queue，不直接消费内部 pgraph 作为 prompt hints。

## 已知边界

- 多危险槽 sink 当前在 flow 上主要携带主槽位；verdict agent 仍能看见全部实参表达式，但 per-slot fan-out 尚未实现。
- GitNexus trace 匹配失败会保留占位 id 并标记 unresolved，不会虚构函数体。
- 二阶 join 只做保守字面/实体名匹配，动态 ORM alias 和运行时表名留给 LLM 轨。
- intra-first 的表达式回退与 presumed-safe 都是召回候选，不能绕过终审。

## 验证入口

- `packages/core/tests/code_index/test_chain_propagator.py`
- `packages/core/tests/code_index/test_chain_propagator_backward.py`
- `packages/core/tests/code_index/test_storage_chain_propagator.py`
- `packages/core/tests/code_index/test_second_order_join.py`
- `packages/core/tests/code_index/test_dual_track_chain_integration.py`
