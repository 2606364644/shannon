# GitNexus 轨分析（确定性层）

> 本文档深入分析 shannon-py 双轨白盒架构中 **GitNexus 轨（确定性层）** 的内部机制：
> Source/Sink 识别、Call Graph 构建、参数污点分析、可达性判定，以及入口点识别的两套机制融合。
>
> 所有引用基于 `feat/fork-py` 分支实际代码核实（函数名稳定，行号随代码演进）。
> 双轨铁律与宏观流程见 [`CLAUDE.md`](../CLAUDE.md) §1 与 [`architecture.md`](architecture.md)。
>
> **注意**：`architecture.md` 的白盒数据流段（`build_code_index` / `rebuildCallChains`）仍是
> 重构前纯 AST 流程的描述，未反映当前 GitNexus 双轨——以本文为准。

---

## 0. 定位：GitNexus 轨在双轨中是什么

双轨模型（CLAUDE.md §1 铁律）：

| 轨 | 产物来源 | 分析方式 | 输出 |
|---|---|---|---|
| **GitNexus 轨** | 确定性层（AST + GitNexus 图） | 产物**由 LLM 分析**（轻量判定） | `<vuln>_gitnexus_queue.json` |
| **LLM 轨** | 纯 LLM agent（prompt + grep + Task 委派） | 100% 自给自足，**不吃确定性产物** | `<vuln>_exploitation_queue.json` |

两条轨**各自独立**，只在合并器（`dual_track_merger.py`）做 **verdict OR** 汇聚。GitNexus 轨的核心价值是**确定性、可批量、不漏 sink 函数名**；核心脆弱性是**强依赖 GitNexus 索引可用**（大仓常 >10min 超时）。

**铁律（反复强调）**：不要把确定性层产物喂进 LLM 轨 prompt（`static_dataflow_hints` 桥梁已拆除，`test_static_dataflow_hints_decoupling.py` 锁定）。

---

## 1. 总数据流：`build_code_index_with_gitnexus` 七步管线

入口：`packages/core/src/shannon_core/code_index/__init__.py` → `build_code_index_with_gitnexus(repo_path, *, mcp_client, llm_client, auto_index=False)`。

```
⓪ auto_index（可选）  ensure GitNexus 已索引仓库，失败 hard-fail（无 fallback）
   │
① tree-sitter parse   discover_source_files → get_parser → parser.parse_file
   │                  产出 FuncBlock[]（全仓所有函数块）
   ▼
② GitNexus call graph build_call_graph_from_gitnexus(repo, mcp_client, blocks)
   │                  → CallGraphResult{edges, chains, entry_points}
   ▼
③ sink 检测（规则）   detect_sinks(all_blocks, parser) → SinkCallSite[]
   ├ ③b LLM 软 sink    collect_suspicious_calls → discover_sinks_llm → 补 soft sinks
   ▼
④ 分组               按 caller_id 把 sinks 分到各函数
   ▼
⑤ 单函数 LLM taint   analyze_taint_llm(block, sinks_in_func) —— 仅对有 sink 的函数
   │                  失败降级 → _deterministic_intra_fallback
   ▼
⑥ 跨函数传播         propagate_across_chains(chains, blocks, intra_results)
   │                  → TaintFlow[] → ParameterPropagationGraph
   ▼
⑦ 入口点融合         GitNexus entry_points ∩ AST detect_entry_points + synthetic 补全
   ▼
⑧ 组装 CodeIndex     → write_index_files → code_index.json / parameter_graph.json
```

下游：`vuln_chain_builders/*_builder.py` 读 `parameter_graph.json` 提候选链 → `chain_verdict.py` 轻量 LLM 判定 → `<vuln>_gitnexus_queue.json` → 合并器。

---

## 2. Source 识别（入口点）

> 详见 §5 入口点识别专题。这里给概要。

GitNexus 轨的 source = **EntryPoint**（网络可达的入口函数）。识别走两套机制融合：

- **机制 A（GitNexus 判定，真值来源）**：`build_call_graph_from_gitnexus` 向 MCP 发 `query({query: "entry point"})`，GitNexus 用自身语义判哪些符号是入口，返回 `process_symbols / definitions`，shannon-py 把它们**匹配回** tree-sitter 的 FuncBlock。
- **机制 B（shannon-py AST 判定，元信息增强）**：`detect_entry_points`（`entry_points.py:13`）按语言匹配装饰器/签名（Python `@app.route`、Go `gin.Context`、TS NestJS `@Get`/Express `app.get()`、Java Spring `@GetMapping`、PHP `#[]Route()`），**提取 route / http_method / entry_type**。

**融合结果（关键）**：最终入口集合 = GitNexus 判定集合（GitNexus 是守门人），机制 B 只负责给被认可的入口**补充 route/http_method 元信息**。详见 §5。

`ParameterSource` 枚举（`models.py`）区分 source 类型：`query / path / body / form / header / cookie / file / session / internal / unknown`。

---

## 3. Sink 检测

### 3.1 规则检测（主）—— `sink_detector.py`

- **规则库** `DEFAULT_RULES`（硬编码元组，**无独立清单文件**）：每条 `SinkRule` = `callee`（函数名）+ `receiver_pattern`（接收者正则）+ `category` + `dangerous_slots`（危险参数位 `[(arg_index, SlotContext)]`）。
- 覆盖：SQL 注入 11 条、命令注入 13 条、反序列化 5 条、SSRF 10 条、模板/XSS/文件/重定向若干（约 48 条）。
- **机制**：tree-sitter AST 调用点匹配，`(language, callee) → 规则列表` O(1) 索引 + `receiver_pattern.fullmatch()`。
- **增强**：
  - String-built SQL：参数是 f-string/format/拼接 时 `SQL_VALUE` → `SQL_IDENTIFIER`（参数绑定无法保护，暗示标识符注入）。
  - `is_entry_hint`（`sink_detector.py:247`）：浅层判断参数是否直接来自外部输入（函数参数 / `request.*` / `$_GET`），供确定性降级分层用。
- **产物**：`SinkCallSite`（`parameter_models.py`），id 严格格式 `"{file}:{caller_func}:{callee}:{line}:{col}"`。

### 3.2 LLM 软 sink 补召回（③b）—— `sink_discovery_llm.py`

规则未命中的可疑 call（`collect_suspicious_calls`）→ `discover_sinks_llm` 让 LLM 判是否是漏网的 sink → 产出"软 `SinkCallSite`"并入主列表，同时记录 `RuleGap`（驱动规则库迭代，写 `rule_gap_report.json`，**不参与 taint/verdict**）。

---

## 4. Call Graph 构建 —— `gitnexus_call_graph.py`

这是 GitNexus 轨**最强也最脆**的一步。

### 4.1 MCP 三类请求

`build_call_graph_from_gitnexus(repo_path, mcp_client, blocks)`：

1. **`query({query: "entry point"})`** —— 拿入口候选符号（`process_symbols / definitions`），匹配回 FuncBlock 成 `entry_point_blocks`。返回 `None` → `raise GitNexusNotIndexedError`。
2. **`cypher`** —— 查 CALLS 边：
   ```cypher
   MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(callee)
   RETURN caller.filePath, caller.name, caller.startLine,
          callee.filePath, callee.name, r.confidence
   LIMIT 5000
   ```
   解析成 `CallEdge[]`。失败仅 warning，edges 为空。
3. **`context({name})`**（per-function）—— 360° 视图，`incoming.calls` / `outgoing.calls`。

### 4.2 数据结构（`models.py`）

```
CallGraphResult
├─ edges:    list[CallEdge]      # caller_id / callee_name / callee_file / resolved / line
├─ chains:   list[CallChain]     # entry_point_id / path[] / depth / has_unresolved
├─ entry_points: list[FuncBlock] # GitNexus 认可的入口函数块
└─ degradation_report            # resolved/unresolved 边统计
```

### 4.3 建链算法（`_build_chains_from_edges`）

resolved edges 建邻接表 → **BFS 从每个 entry_point 展开** → 环检测（callee 已在 path 则截断）→ **max depth 20**。leaf 节点 / 深度超限 / 环都各自收尾成 `CallChain`，并标记 `has_unresolved`。

### 4.4 ⚠️ 脆弱性（CLAUDE.md §3）

- **同步阻塞**：`build_code_index_with_gitnexus` 里 `await build_call_graph_from_gitnexus(...)`，MCP 响应超时 `MCP_READ_TIMEOUT`（`gitnexus_mcp.py`）。
- **hard-fail 无 fallback**：GitNexus 未索引 → `GitNexusNotIndexedError` → `ErrorCode.CODE_INDEX_FAILED`，**不降级到纯 AST minimal mode**。
- **级联空转**：GitNexus 索引了但 `query "entry point"` 返回空 → `entry_points=∅` → `entry_point_ids=∅` → BFS 无起点 → **chains=∅** → `propagate_across_chains` 无输入 → **TaintFlow=∅ → 确定性轨召回归零**。届时全部召回压力转移到 LLM 轨——这正是 LLM 轨必须独立的根因。

---

## 5. 参数污点分析

### 5.1 单函数内（LLM）—— `llm_taint_analyzer.py` → `analyze_taint_llm`

`build_taint_prompt` 把以下内容喂 LLM **单次结构化输出**：
- 函数元数据 + 参数类型信息
- **sink-aware 截断源码**（max 1200 行，sink 行前后留窗口）
- 该函数检测到的 `SinkCallSite` + dangerous_slots
- 期望 JSON schema

**为什么非 LLM 不可**：复杂数据流、条件传播、字符串变换（concat/format/encode）语义、对象属性访问——纯 AST 做不到。

**降级** `_deterministic_intra_fallback`（LLM 失败时）：按 `is_entry_hint` 分层——直接命中外部输入 0.9 / 全 literal 常量跳过 / 间接变量引用 0.5。

### 5.2 跨函数传播（确定性）—— `chain_propagator.py` → `propagate_across_chains`

沿 `CallChain.path` 把单函数结果串成完整 `TaintFlow`：
- `_map_call_site_params`：按位置映射 `arg[i] → callee_params[i]`（若 arg 引用了 caller 的 tainted 参数，则 callee 的对应参数被种下污点）。
- 每跳记录 `PropagationStep`（`from_func/from_param → to_func/to_param` + `transformation`：`concat`/`encode`/`sanitize_hint:<name>` + `code_location`）。
- 函数命中 sink 时产出 `TaintFlow`，`flow_id="{entry_point_id}->{sink_call_site_id}"`，**精确锚到 `SinkCallSite.id`**。

### 5.3 Sanitizer 标注 —— `sanitizer_library.py`

`DEFAULT_SANITIZER_RULES`（sql bind / `shlex.quote` / `html.escape` …）+ `annotate_sanitizers` 扫 `transformation` 字段。**只标注，不判有效性**——有效性留给候选链的轻量 LLM 判（§6）。

### 5.4 数据结构（`parameter_models.py`）

```
ParameterPropagationGraph
└─ taint_flows: list[TaintFlow]
   ├─ entry_point_id, source_param, source_type: ParameterSource
   ├─ propagation_steps: list[PropagationStep]
   ├─ sink_call_site_id, sink_slot: SlotContext, tainted_arg_index
   ├─ confidence, has_sanitizer_hint
   └─ flow_id = "{entry_point_id}->{sink_call_site_id}"
```

---

## 6. 候选链提取 + 轻量 LLM 判定 —— `chain_verdict.py`

### 6.1 提取（`extract_candidate_chains`）

从 `ParameterPropagationGraph` 按 vuln 类过滤 `TaintFlow`：
- **Injection**：按 `sink_slot`（`sql_value` / `sql_identifier` / `cmd_argument` / `file_path` / `template_expr` / `deserialize`）
- **SSRF**：按 `sink_slot`（`url`）
- **XSS**：按 `sink_category == XSS`（需 `sink_call_sites` 参数）

同时跑 `sanitizer_library.annotate_sanitizers` + `_detect_post_sanitize_concat`（sanitize 后还有 concat → 视为无效）。

各 builder 特化方向：**injection forward**（source→sink）、**xss/ssrf backward**（sink→source）；xss 还有 **Stored XSS 合成**（read flow + write flow 按共享字段名配对）。

### 6.2 判定（`judge_chain_verdict`）

把**单条候选链**喂 `_VERDICT_PROMPT` 跑 `run_claude_prompt` **单次结构化输出（非 agent）**，要 JSON `{verdict, witness_payload, evidence_chain, mismatch_reason, confidence}`。

**铁律（宁过报）**：LLM 调用失败 / JSON 解析失败 → 一律返回 `verdict="vulnerable"`，保证 OR 合并时不漏报。

### 6.3 builders 设定 `externally_exploitable`

`injection_builder` / `xss_builder` / `ssrf_builder` 均 `externally_exploitable=(verdict.verdict == "vulnerable")`。auth config 扫描保守设 `True`，authz 由 LLM judge 设。

---

## 7. 可达性分析

### 7.1 核心区分：`verdict` ≠ `externally_exploitable`

- `verdict`（漏洞存在性）：source→sink 无有效防御？
- `externally_exploitable`（可达性）：公网可达（true）vs 内部/跨服务（false）。

两者**独立，互不推导**（`BaseVulnerability`，`queue_schemas.py`）。

### 7.2 GitNexus 轨的可达性 = entry_point 可达

**第一道闸在污点传播**：只有从 `entry_point`（网络可达入口）传播到 sink 的 flow 才生成 `TaintFlow`，才会被提取为候选链。**不可达的 sink 根本不进候选链。**

### 7.3 合并器铁律：`externally_exploitable` 不被 verdict 覆写

`dual_track_merger.py`：
- **去重** `(vulnerability_type, location, sink)` 三元组
- **verdict OR**：任一轨 `vulnerable` → 合并 `vulnerable`
- **🛡️ 保护** `_clone_with_merge_fields`：**只改 `verdict/merge_source/confidence/evidence_chain`，`externally_exploitable` 原样保留 base**（默认取 LLM 轨，因 LLM 轨更懂路由认证）。注释原文："do NOT overwrite externally_exploitable — it is a reachability tag, NOT part of the verdict"。
- 测试锁定（`test_dual_track_merger.py`）：两轨冲突（LLM=false, GN=true）→ 合并后保留 **false**，不 OR。

**跨服务 sink**（vuln prompt 关键场景）：用户控制的 SQL/command 片段转发给下游服务执行 = `verdict: vulnerable` 但 `externally_exploitable: false`（**不是 safe**），仍入队。这保证了"本地不执行但下游执行"的漏洞不被可达性过滤误删。

---

## 8. 入口点识别专题：机制 A vs 机制 B 融合

### 8.1 两套机制

| | 机制 A（GitNexus 判定） | 机制 B（shannon-py AST 判定） |
|---|---|---|
| 实现 | `build_call_graph_from_gitnexus` 的 `query "entry point"` | `detect_entry_points`（`entry_points.py:13`） |
| 谁判 | **GitNexus 外部服务**（逻辑不暴露） | shannon-py 自己（装饰器/签名正则） |
| 产出 | FuncBlock id 集合 G | EntryPoint[]（带 route/http_method/entry_type） |
| 元信息 | ❌ 不提供 route/http_method | ✅ 唯一提供者 |

### 8.2 融合逻辑（`__init__.py` 第 ⑦ 步）

```python
gitnexus_ep_ids  = {ep.id for ep in call_graph.entry_points}          # G
all_entry_points = detect_entry_points(all_blocks, language, ...)     # A

# ① A 中被 GitNexus 也认可的 → 保留 AST 的丰富元信息
gitnexus_entry_points = [ep for ep in all_entry_points if ep.func_block_id in gitnexus_ep_ids]

# ② GitNexus 认了但 AST 没判出的 → 补 synthetic
for ep_block in call_graph.entry_points:
    if ep_block.id not in detected_ids:
        gitnexus_entry_points.append(EntryPoint(entry_type="gitnexus",
            route=None, http_method=None, confidence=0.9, ...))
```

最终集合 = `(A∩G) ∪ (G−A) = G`。**GitNexus 是入口守门人**；机制 B 只给被认可的入口补 route/http_method/entry_type，**不贡献召回**（AST 判出但 GitNexus 没认的全被丢）。

### 8.3 机制 B 的真实价值 = 元信息，不是召回

机制 B 产出的 `entry_type="http_route"` + `route` 是两条下游轨的**入口筛选依据**：

- **authz IDOR 候选**（`authz_gitnexus_track.py:92`）：
  ```python
  http_eps = [ep for ep in index.entry_points
              if ep.entry_type == "http_route" and ep.route is not None]
  ```
- **recon §4.1 共享路由组**（`recon_gitnexus_track.py:55`）：`if ep.route is None: continue`。

### 8.4 删除机制 B 的后果评估

若删 `detect_entry_points` + 全部降级 synthetic（`entry_type="gitnexus"`, `route=None`）：

| 影响 | 范围 |
|---|---|
| ❌ **authz IDOR GitNexus 轨失效** | `entry_type=="http_route"` 永远 False → `http_eps=∅` → IDOR 候选归零 |
| ❌ **recon §4.1 共享路由组归零** | `route is None` 全部 continue |
| ⚠️ summary/recon 报告入口显示 `—` | 纯显示，`summary.py` 已 `route or "—"` null 安全 |
| ✅ injection/xss/ssrf 核心链**不受影响** | taint 追链 / `_VERDICT_PROMPT` / `externally_exploitable` 只用 `entry_point_id`，不读 route |

**结论**：机制 A 与 B 职责互补（A 给召回，B 给元信息），不可互相替代。机制 B 唯一被"浪费"的是**召回能力**（被 GitNexus 守门），而非元信息。若要优化，方向是改融合逻辑（`A∪G` 让 B 补 GitNexus 漏认的入口），而非删除 B。

---

## 9. 设计原则与已知脆弱性

1. **宁过报不漏报**：chain_verdict LLM 失败 → `vulnerable`；string-built SQL → 强制 `needs_review`；auth config 扫描命中 → `externally_exploitable=True`。
2. **确定性 + LLM 混合**：规则匹配 / 污点传播 / sanitize 标注 = 确定性；单函数 taint / 候选链判定 / render context 推断 = LLM。
3. **可达性第一公民**：`externally_exploitable` 贯穿全流程不被 verdict OR 污染。
4. **GitNexus 单点依赖**：入口识别（守门人）+ call graph 都强依赖 GitNexus。不可用时整条轨 hard-fail / 空转，召回全压 LLM 轨——这是双轨独立性铁律的工程根因。

---

## 10. 关键文件速查

| 职责 | 文件 |
|---|---|
| 管线编排 | `code_index/__init__.py` → `build_code_index_with_gitnexus` |
| Sink 规则检测 | `code_index/sink_detector.py` → `detect_sinks` / `DEFAULT_RULES` / `is_entry_hint` |
| Sink LLM 补召回 | `code_index/sink_discovery_llm.py` |
| Call graph（GitNexus） | `code_index/gitnexus_call_graph.py` → `build_call_graph_from_gitnexus` / `_build_chains_from_edges` |
| 入口点（AST） | `code_index/entry_points.py` → `detect_entry_points` |
| 单函数 taint | `code_index/llm_taint_analyzer.py` → `analyze_taint_llm` / `_deterministic_intra_fallback` |
| 跨函数传播 | `code_index/chain_propagator.py` → `propagate_across_chains` |
| Sanitizer 标注 | `code_index/sanitizer_library.py` → `annotate_sanitizers` |
| 候选链 + 判定 | `code_index/chain_verdict.py` → `extract_candidate_chains` / `judge_chain_verdict` |
| 各 vuln builder | `code_index/vuln_chain_builders/{injection,xss,ssrf}_builder.py` |
| authz GitNexus 轨 | `code_index/authz_gitnexus_track.py` → `find_unguarded_sink_paths` |
| recon GitNexus 轨 | `code_index/recon_gitnexus_track.py` → `detect_shared_route_groups` |
| 双轨合并 | `code_index/dual_track_merger.py` → `merge_dual_track_queues` |
| 数据模型 | `code_index/models.py`（EntryPoint / CallGraphResult / ParameterSource）、`code_index/parameter_models.py`（TaintFlow / PropagationStep / SinkCallSite / ParameterPropagationGraph） |
