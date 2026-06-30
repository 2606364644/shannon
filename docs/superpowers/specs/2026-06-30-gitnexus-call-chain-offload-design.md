# GitNexus 轨调用链下沉 + entry 重构 + authz 适配（process trace 全包）

- **日期**：2026-06-30
- **状态**：设计获批，待出实现计划
- **分支**：`feat/fork-py`
- **触发**：`/superpowers:brainstorming`「把 GitNexus 轨的调用链跟踪下沉到 GitNexus，不用 Python BFS（不通用）」→ 探针坐实后扩到 entry 重构 + authz 适配（全包）
- **关联**：`2026-06-30-gitnexus-mcp-call-layer-fix-design.md`（已落地）、`2026-06-30-discover-sinks-llm-concurrency-design.md`（已落地）、`docs/gap/2026-06-28-gitnexus-track-lifecycle-analysis.md`、memory `gitnexus-1.6.7-real-machine-behavior`

---

## 0. 一句话结论

GitNexus 轨的三个相互纠缠的问题，用 GitNexus 原生 `process trace` 一次性解：
1. **调用链来源**：`build_call_graph_from_gitnexus` 的 chains 从「全量 cypher + Python BFS」改成「process trace 预计算路径」—— injection/xss/ssrf 直接吃，**判定不变**。
2. **entry 体系**：现有 `detect_entry_points`（pattern）在 Go SRPC 仓失效（3 cli / 0 http_route）；改用 process trace 的 entry（step1）作新来源（并集），拿回真正的 RPC 业务入口。
3. **authz 判定**：换 process trace 后 authz 双重失效（entry http_route=0 + 看 terminal side-effect=0）；改「扫链任意步找 side-effect sink + entry 过滤含 process entry + ownership guard 扫描」→ authz 从 **0→21** 召回（statement_template_svr 实测）。

Python 不再重建图、拼路径、做 BFS。

---

## 0.5 GitNexus 轨全生命周期 + 产物流转

> 本节是 spec 改动落地后的 GitNexus 轨端到端视图：串联每一步的产物与消费关系，并附**产物连通性审计结论**（哪些字段对齐通、哪些有断点需配套改）。目的是让 writing-plans 阶段不会出现"上一步产了、下一步没读到"的断裂。

### 生命周期总览

```
层0 基础设施 ──▶ A. pre-recon code-index ──▶ B. injection/xss/ssrf 判定 ──▶ C. authz 判定 ──▶ D. 双轨合并
   (G4)           run_code_index              run_gitnexus_chain_verdict     run_authz_gitnexus_judge    run_merge_dual_track_queues
                  ↓ 产物                        ↓ 产物                          ↓ 产物                       ↓ 产物
                  code_index.json               *_gitnexus_queue.json          authz_gitnexus_queue.json    *_exploitation_queue.json
                  parameter_graph.json
```

### 阶段 A：pre-recon code-index（活动 `run_code_index`）

| # | 步骤 | 模块/函数 | 产物（内存 / 落盘 `deliverables/`） |
|---|---|---|---|
| A1 | tree-sitter parse 全仓 | `parser.parse_file` | `FuncBlock[]`（all_blocks）+ `file_sources` |
| A2 ★G1 | **调用链来源**（process trace） | `build_call_graph_from_gitnexus` 重写 + `process_trace_reader` + `impact_supplement` | `CallGraphResult{chains: CallChain[], entry_points: FuncBlock[](=path[0]), edges:[]}` |
| A3 | sink 检测（规则） | `detect_sinks` | `SinkCallSite[]` |
| A4 | LLM sink 补召回 | `discover_sinks_llm` | soft `SinkCallSite[]` + `RuleGap[]` |
| A5 | LLM 参数级 taint（**仅含 sink 函数**，并发） | `analyze_taint_llm` | `intra_results: dict[func_id→IntraResult]` |
| A6 | 跨函数 taint 传播 | `propagate_across_chains`（读 `chain.path`） | `TaintFlow[]` → `ParameterPropagationGraph` |
| A7 ★G2 | entry 组装（detect ∪ process） | `__init__.py:213-231` 重写 | `CodeIndex.entry_points: EntryPoint[]` |
| A8 | 落盘 | `write_index_files` | `code_index.json` / `code_index_summary.md` / `parameter_graph.json` / `rule_gap_report.json` |

### 阶段 B：injection / xss / ssrf 判定（活动 `run_gitnexus_chain_verdict`）

**输入**：`parameter_graph.json` + `code_index.json`（XSS 路由要 `sink_call_sites`）

| 步骤 | 模块/函数 | 产物 |
|---|---|---|
| B1 | `build_injection_findings(pgraph, llm)` | injection 候选 + 轻量 LLM 判定（`run_claude_prompt` 单次结构化） |
| B2 | `build_xss_findings(pgraph, llm, sink_call_sites)` | xss 候选 + 判定 |
| B3 | `build_ssrf_findings(pgraph, llm)` | ssrf 候选 + 判定 |

**落盘**：`injection_gitnexus_queue.json` / `xss_gitnexus_queue.json` / `ssrf_gitnexus_queue.json`

### 阶段 C：authz 判定（活动 `run_authz_gitnexus_judge`）★G3

**输入**：`code_index.json` + `framework_analysis.json`

| 步骤 | 模块/函数 | 产物 | 改动 |
|---|---|---|---|
| C1 | `build_authz_gitnexus_track` → `find_unguarded_sink_paths` | IDOR 候选（dom + fw） | ★ 三处改（§4.8） |
| C2 | `run_claude_prompt`（`authz_gitnexus_judge` prompt） | LLM verdict（`source_track=gitnexus`） | 不变 |

**落盘**：`authz_gitnexus_queue.json`

### 阶段 D：双轨合并（活动 `run_merge_dual_track_queues`）

对每个 `vuln_class ∈ {injection, xss, ssrf, authz, auth}`：读 `<vuln>_gitnexus_queue.json` + `<vuln>_llm_queue.json` → `merge_dual_track_queues(verdict OR)` + 去重 + 标 `merge_source`(both/llm-only/gitnexus-only) → 落盘回 `<vuln>_exploitation_queue.json`。

### 产物连通性审计

**3 条主链路字段对齐（已核实通）：**

1. **injection/xss/ssrf taint 链（三类共用）**：A2 `CallChain.path`(=`list[FuncBlock.id]`) → A6 `propagate_across_chains`（用 id 查 `blocks_by_id`，walk path） → `TaintFlow{entry_point_id=path[0], sink_call_site_id, propagation_steps}` → `parameter_graph.json` → B 三个 `build_*_findings(pgraph)`（都经 `extract_candidate_chains` 吃 `taint_flows`）。✅ 字段通（**但有断点②，见下**）。
2. **entry 链**：A2 `CallGraphResult.entry_points`(`FuncBlock[]`) → A7 转 `EntryPoint(func_block_id=block.id)` → `code_index.json` → C `find_unguarded_sink_paths` 按 `chain.entry_point_id == ep.func_block_id` 匹配。✅ **对齐契约**：`CallChain.entry_point_id`、`EntryPoint.func_block_id`、`CallGraphResult.entry_points[].id` 三者必须都 = `path[0]` 的 FuncBlock.id（spec §4.2/§4.7 已对齐）。
3. **sink 链**：A3 `SinkCallSite[]` → `code_index.json` → B2 xss(`sink_call_sites` 按 `category==XSS` 路由) + C `_is_side_effect_sink`(扫 `chain.path` block 的 `source_code`)。✅ 两套 sink 视角独立但都从 `code_index.json` 取，通。

**断点（审计发现，需配套改 / 标注）：**

- 🔴 **断点①（必修，G3 闭环）**：`find_unguarded_sink_paths`（`authz_gitnexus_track.py:102-103`）当前 entry 过滤是 `entry_type=="http_route" and ep.route is not None` —— 含 `route is not None` **硬守卫**。process entry（决策 5：`route=None`）会被这个守卫挡掉 → authz 仍 0。**spec §4.8 改 1 必须**：扩 entry_type 白名单的**同时**对 `gitnexus_process` 放宽 `route is not None`（route 为 None 也放行），否则决策 4/5 与 §4.8 改 1 不闭环。
- 🟡 **断点②（injection/xss/ssrf 三类隐藏假设，须真机验证）**：`propagate_across_chains`（`chain_propagator.py:163-165`）**硬要求** `intra_results[path[0]]`（即 entry）存在且 `tainted_params` 非空，否则 `continue` 跳过整条链；而 A5 只对**含 sink 的函数**跑 `analyze_taint_llm`；process trace 的 entry（`path[0]`，transport/endpoint 层如 `init`/`NewEndpoint`）通常**不含 sink** → 无 intra → **chains 非空但 `taint_flows` 仍可能 0**。三类 builder 同源（都经 `extract_candidate_chains` 吃 `pgraph.taint_flows`，`chain_verdict.py:177`）→ **`taint_flows=0` ⇒ 三类候选全空 ⇒ 三类 GitNexus 轨都产 0**。即 **G1（chains 来源）是三类 taint 漏洞有产出的必要非充分条件**。spec §0/§4.6「判定不变直接吃新 chains」措辞过乐观，已在 §4.6/§8 回填为风险。

  **根因（精确，2026-06-30 复审）**：source（种子）识别被 sink 绑架——`analyze_taint_llm` 的 prompt 是 sink-driven（`llm_taint_analyzer.py:142-169`，`tainted_params` 语义="能 reach sink 的参数"），且只对含 sink 函数跑；source 未独立成层 → 无 sink 的 entry 拿不到种子。**不是**「规则 sink 识别没带 source」（LLM sink 识别 `discover_sinks_llm` 也只标 sink，与规则同结构）。**解法定向 follow-up B′**：独立 source 识别层（对 entry 识别外部可控输入参数，不绑 sink；候选 B′-a 扩 `extract_typed_parameters` 支持 Go / B′-b 轻量 LLM source prompt）。**本 spec 决策 A：不动 source 层，B′ 单开 spec。**
- 🟢 **断点③（可观测性，小）**：`build_authz_gitnexus_track` 的 `http_route_count`（`:342-345`）只数 `http_route`；process entry 进来后，候选源已是 `gitnexus_process`，log 文案「http_route=0…全靠 LLM 兜底」会误导。须同步统计 `gitnexus_process` entry 数。

---

## 1. 背景：当前三块的问题

| 块 | 现状 | 实测问题 |
|---|---|---|
| 调用链来源 | 全量 cypher `LIMIT 5000` + Python BFS（`_build_chains_from_edges`） | readline 64KB 崩；空壳（生产 3 仓 `chains=0`）；不通用 |
| entry 检测 | `detect_entry_points`（pattern：http_route/rpc/cli）+ `query("entry point")` + tree-sitter 对齐 + `detect_entry_points` 交集 | statement_template_svr 只识别 **3 个 cli**（**0 http_route/rpc**）；与 process entry **0 重合** |
| authz 判定 | `find_unguarded_sink_paths`：`entry_type=="http_route"` 过滤 + 看 `chain.path[-1]` 是 side-effect | 双重 0：无 http_route entry + terminal 多是 ErrCode（**0/140** side-effect）；扫全链才能到 21/140 |

---

## 2. 真机发现（设计依据）

完整记录见 memory `gitnexus-1.6.7-real-machine-behavior`（5 轮探针 + 5 个姿势纠正）。此处列三块改造的关键依据。

### 2.1 process trace = 预计算调用链路径（核心）
- 接口：`resources/read gitnexus://repo/{name}/process/{label}`（MCP **resource**，非 tool）。
- 返 YAML `trace:` 段 = `N: <func> (<filePath>)` 有序步骤，entry→terminal。
- 全量 label：`cypher MATCH (p:Process) RETURN p.label`（实测 140）。⚠️ `processes` resource 截断只给 20；URI 用 **label 不是 id**。
- 单条 ~668B，永不 readline 崩。

### 2.2 impact = 定向可达性（补充，不产 path）
- 双向原生（upstream/downstream）+ `file_path`/`target_uid`/`kind` 消歧 + `maxDepth`/`crossDepth`/`relationTypes`/`timeoutMs`。
- 返 `byDepth`（分层可达闭包，**不带符号间边**）→ **不产出 `chain.path`**，只做可达性确认/消歧/risk 标注。
- Go 仓纯 name ambiguous 率极高，**必带 file_path**。

### 2.3 authz terminal vs 全链 side-effect（探针 `probe_authz_entry_compat.py`，用 `_is_side_effect_sink` 真规则）
- `terminal(path[-1])` 是 side-effect：**0/140**。
- 全链任一步是 side-effect：**21/140**（`UploadTemplateFile`/`AddGreyUser`/`downloadWorkerStatementFile` 等在链中间）。
- → authz 看 terminal 召回 0；扫全链召回 21。

### 2.4 entry 体系失效（同探针）
- `detect_entry_points`：**3 个 cli / 0 http_route**。
- process entry（step1）与 detect_entry_points **重合 0/140**。
- process entry 是真业务入口：`init / NewTemplateTemplateSvrEndpoint / SearchTemplateVersion(impl) / downloadWorkerStatementFile`（transport/endpoints/service 层）。

### 2.5 MCP resource 坑 + cypher 裁剪版
- `resources/list` 只列静态 2 个（repos/setup）；per-repo resource（schema/processes/process）**直接 read 才能读**。
- resources content 是 `{uri,mimeType,text}`（**无 `type` 字段**），不同于 tools/call 的 `{type:"text"}`。
- LadybugDB cypher：支持单跳/变长/IN/count/ORDER BY/UNWIND；**不支持** `type()/startNode/elementId/list comprehension`。路径查询别用 cypher，用 process trace。

### 2.6 召回边界（statement_template_svr）
- 140 process / `detect_sinks` 只 1 个 sink（`TextFileDiff→Command`，工具函数，不在任何 process）→ injection 在本仓 GitNexus 轨召回 0（**sink_detector Go 召回低是独立瓶颈，本 spec 不解**）。
- process trace 覆盖"业务执行流"；边缘/工具 sink 漏 → 靠 LLM 轨兜底（双轨）。

---

## 3. 目标 / 非目标

### 目标
- **G1（调用链来源）**：`build_call_graph_from_gitnexus` 的 chains 来源改 process trace（+ impact 补充），删 Python BFS。**injection/xss/ssrf 判定不变**，直接吃新 chains。
- **G2（entry 重构）**：process entry 作新 entry 来源（与 `detect_entry_points` 并集），`CodeIndex.entry_points` 覆盖 RPC 业务入口（statement_template_svr 从 3→含 140 process 的 entry 集合）。
- **G3（authz 适配）**：`find_unguarded_sink_paths` 改「扫链任意步找 side-effect sink + entry 过滤含 process entry + ownership guard 扫描 entry→sink 段」→ authz 在 statement_template_svr **0→21**。
- **G4（基础设施）**：修 `GitNexusMCPClient` readline 64KB bug + 新增 `read_resource(uri)`。

### 非目标（排除）
- **injection 判定语义**不改（`chain_verdict`/`propagate_across_chains` 沿 chain 找危险 sink，天然适配 process trace）。
- **`sink_detector` Go 召回**（独立 spec；本 spec 不改 sink 规则）。
- **Go PDG taint**（上游 roadmap，TS/JS only；参数级 taint 仍 `analyze_taint_llm`）。
- **LLM 轨**（守 CLAUDE.md §1 双轨铁律）。
- **`dual_track_merger` / verdict OR / `externally_exploitable`**。
- **auth/config 扫描器**（auth 不在本 spec，只 injection+authz）。
- **source 识别独立化 / head-seed**（follow-up spec B′；本 spec 不动 `analyze_taint_llm` / `extract_typed_parameters` / `propagate` 的 seed 逻辑——见 §0.5 断点②根因）。

---

## 4. 设计

### 4.1 数据源角色

| 源 | 角色 | 产出 |
|---|---|---|
| `process trace` resource | **主路径源 + entry 来源** | entry→…→terminal 有序链（带 filePath）；entry=step1 |
| `impact`（upstream/downstream） | **定向可达补充** | byDepth 闭包 + risk + 消歧；**不产 path** |
| `cypher` 全量 CALLS 边 | **废弃** | readline 崩 + BFS，删 |
| `cypher MATCH (p:Process) RETURN p.label` | 拿 process 全量 label | 140 个 |

### 4.2 `build_call_graph_from_gitnexus` 重写（G1）

替代 `gitnexus_call_graph.py:165` 的 query+cypher+BFS：
```
1. cypher MATCH (p:Process) RETURN p.label        → 全部 process label
2. 每 label：resources/read process/{label}        → trace YAML
3. 解析 trace 步骤 [(idx,name,filePath)] 按 idx 排序
4. (name,filePath)→FuncBlock.id 对齐（§4.4）→ CallChain.path
5. entry_point = path[0]；chains.append(CallChain(...))
6. impact（可选）：entry/sink 跑 upstream/downstream 拿可达性/risk（不产 path）
```
返 `CallGraphResult(edges=[], chains=[...], entry_points=[path[0] blocks...], degradation_report)`。`edges` 返空（`_build_chains_from_edges` 废弃删；`propagate_across_chains` 只读 `chain.path` 不读 edges，`authz_gitnexus_track` 也只读 chains）。

### 4.3 `process_trace_reader.py`（新模块）
- `read_all_process_traces(mcp_client, repo_name) -> list[ProcessTrace]`：cypher 拿全 label → 每 label `resources/read` → 解析 YAML `trace:` → `ProcessTrace(label, steps=[(idx,name,filePath)], process_type, step_count)`。
- 读 resource 取 `contents[*].text`（**不查 `type`**，§2.5）。
- 单 trace 失败 → log + 跳过。

### 4.4 trace → CallChain 转换 + FuncBlock 对齐
- 四级匹配：① `(filePath,name)` 精确 ② `file_path` 尾匹配 ③ name 唯一退化 ④ 失败→占位 `<file>:<name>` + `has_unresolved=True`。
- 对齐失败真因（实测 48/总）多为 name 格式差（method receiver 前缀等），四级匹配 + log 覆盖；对齐率进可观测性 log。
- 占位 step：`has_unresolved=True`；下游 `chain_verdict` 据 `has_unresolved` 降置信（既有字段语义）。

### 4.5 `impact_supplement.py`（新模块，决策 2）
- `impact_upstream(mcp, sink_name, file_path)` / `impact_downstream(mcp, entry_name, file_path)`：拿 byDepth + risk + affected_processes，**必带 file_path** 消歧。
- **不产 path**（path 只来自 process trace）；用途：sink/source 消歧、可达性确认、risk/affected_processes 标注。
- 失败/超时 → log + 返空。

### 4.6 injection / xss / ssrf —— 判定不变
- `detect_sinks` 产 `SinkCallSite`（`callee_name`+`file_path`+`caller_id`）→ 在 process trace 链上 match `caller_id`（含 sink 的函数）→ 命中即 source(entry)→…→sink。
- `chain_verdict`/`propagate_across_chains` 沿 `chain.path` 做 taint（逻辑完全不变）。
- ⚠️ **隐藏假设（§0.5 断点②，须真机验证；影响 injection/xss/ssrf 三类，非 injection 独有）**：`propagate_across_chains`（`chain_propagator.py:163-165`）**硬要求** entry(`path[0]`) 在 `intra_results` 且有 `tainted_params`，否则 `continue` 跳过整条链；而 A5 只对**含 sink 的函数**跑 `analyze_taint_llm`。process trace 的 entry（transport/endpoint 层）通常不含 sink → 无 intra → **chains 非空但 `taint_flows` 仍可能 0**。三类 builder（injection/xss/ssrf）同源——都经 `extract_candidate_chains(pgraph)`（`chain_verdict.py:177` `for flow in pgraph.taint_flows`）提候选，xss 的 `sink_call_sites` 仅做 slot 路由非独立候选源 → **`taint_flows=0` ⇒ 三类候选全空 ⇒ 三类 GitNexus 轨都产 0**。即 **G1 是三类 taint 漏洞有产出的必要非充分条件**（chains 非空 ≠ 三类有产出）。本 spec 守非目标「不改三类 taint 判定」，不动此处；若真机普遍出现「chains 非空但 taint_flows=0」，列为 follow-up（候选解法：A5 对 entry 函数也识别 source 入参，或 propagate 从链中首个有 intra 的节点起 seed）。statement_template_svr 因 §2.6 sink_detector 只 1 sink 且不在 process，本仓无法验证此链。**根因 + 解法定向见 §0.5 断点②（source 识别被 sink 绑架，应独立成层；本 spec 决策 A，B′ 单开 follow-up spec）。**

### 4.7 entry 体系重构（G2，新）
- **新来源**：process trace 每条的 step1 → `EntryPoint(func_block_id=chain.path[0], entry_type="gitnexus_process", route=None, http_method=None, confidence=0.9, evidence="GitNexus process entry: <label>", source="gitnexus", needs_llm_review=False)`（决策 5）。
- **并集**（决策 4）：`CodeIndex.entry_points = detect_entry_points(blocks) ∪ {process entries}`，按 `func_block_id` 去重；同一 id 若 detect 已识别（带 route/http_method，更具体），**优先 detect 的 entry_type**，process entry 仅补 detect 漏的（如本仓的 SRPC 端点）。
- **`__init__.py:213-231` 的 entry 组装逻辑改写**：不再"GitNexus entry ∩ detect_entry_points"（旧 query("entry point") 那套废弃，process entry 直接进），改"detect ∪ process"。
- entry_type 分布进可观测性 log（区分 detect 来源 vs gitnexus 来源）。

### 4.8 authz 判定适配（G3，新）—— 改 `find_unguarded_sink_paths`
四处改（`authz_gitnexus_track.py:84-145`；改 1 含 §0.5 断点①、改 4 为 §0.5 断点③）：
1. **entry 过滤扩 + route 守卫放宽**（决策 4 接入；⚠️ §0.5 断点①）：从 `entry_type=="http_route" and ep.route is not None` 扩到 `entry_type in ("http_route","rpc","gitnexus_process")`；**对 `gitnexus_process` 放宽 `route is not None`**（process entry 决策 5 `route=None`，必须放行，否则被原硬守卫挡掉 → authz 仍 0）。`http_route`/`rpc` 保留 `route is not None`（它们带路由信息）。
2. **sink 判定改扫全链**（决策 7）：从 `chain.path[-1]` 改为遍历 `chain.path`，**任意一步** `_is_side_effect_sink` 命中即记 `(entry, sink_step_idx, path)` 候选（一条链可能产多个候选）。
3. **ownership guard 扫描段**（决策 6）：检查 entry→sink_step 段（含两端）的 FuncBlock 源码有无 `OWNERSHIP_PREDICATE_RE`；全无 → IDOR 候选。
4. **可观测性统计同步**（§0.5 断点③）：`build_authz_gitnexus_track` 候选诊断 log 的 `http_route_count` 扩成同时统计 `gitnexus_process` entry 数，避免 process entry 进来后「http_route=0…全靠 LLM 兜底」误导。
- `IDORCandidateChain` 增 `sink_step_idx` 字段（定位链中 sink 位置）。
- 候选 render / LLM 判定 / queue 逻辑不变。

### 4.9 错误处理 & 边界
- **sink 不在任何 process**（§2.6）：该 sink 无 GitNexus 链 → injection 静默漏，靠 LLM 轨兜底（决策 1，**不**标 needs_review——没 chain 就没东西标，措辞改"静默漏 + LLM 兜底"）。
- **step→FuncBlock 对齐失败**：§4.4 四级匹配 + has_unresolved + log。
- **readline 64KB 修**（G4）：`start()` 的 `create_subprocess_exec` 传 `limit=4*1024*1024`（StreamReader 4MB）；或 `loop.set_read_buffer_limit`。
- **process trace 读全失败**：空 chains → injection 跳过 + authz `candidate_count=0` → LLM 轨兜底（不引入 A3 硬失败）。

### 4.10 决策记录

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | sink 不在 process | 静默漏 + LLM 轨兜底 | 双轨设计；不做小 cypher 补边（违反不 BFS） |
| 2 | impact 角色 | 纯补充（消歧/确认/risk），**不产 path** | path 只来自 process trace，纯粹 |
| 3 | spec 范围 | **全包**（调用链来源 + entry 重构 + authz 适配） | 三者纠缠于 process trace；用户选 C |
| 4 | entry 来源 | **detect_entry_points ∪ process entry**（并集） | 保留 detect 有效识别（cli 等）+ 补 process entry |
| 5 | process entry 类型 | `entry_type="gitnexus_process"`，route/http_method=None，source="gitnexus" | SRPC 非 HTTP；method 走 protobuf 不在 code_index 层填 |
| 6 | authz ownership 扫描 | 扫 entry→sink_step 段 | 符合"中间有无鉴权"语义；非只 entry、非全链 |
| 7 | authz sink 判定 | 扫链任意步 side-effect（替 terminal） | 探针 0/140→21/140 |

---

## 5. 影响范围

### 必改

| 文件 | 改动 |
|---|---|
| `gitnexus_call_graph.py` | `build_call_graph_from_gitnexus` 重写（§4.2）；`_build_chains_from_edges` 废弃删；接入 impact 补充（§4.5） |
| **新** `process_trace_reader.py` | §4.3 |
| **新** `impact_supplement.py` | §4.5 |
| `gitnexus_mcp.py` | readline 修（§4.9）+ 新增 `read_resource(uri)`（取 text 不查 type） |
| `code_index/__init__.py` | entry 组装改"detect ∪ process"（§4.7，:213-231 重写） |
| `authz_gitnexus_track.py` | `find_unguarded_sink_paths` 三处改（§4.8）；`IDORCandidateChain` 加 `sink_step_idx` |

### 不变（吃新 chains/entry，判定不变）
- `chain_propagator.py`（只读 chain.path）、`chain_verdict.py`、`vuln_chain_builders/*`（injection 判定）
- `dual_track_merger.py`、`activities.py`（run_code_index/run_gitnexus_chain_verdict/run_authz_gitnexus_judge）

### 测试必改
- `test_gitnexus_call_graph.py`（FakeMCP 加 resources/read；process trace fixture → 非空 chains）
- 新 `test_process_trace_reader.py` / `test_impact_supplement.py`
- `test_gitnexus_mcp.py`（readline 4MB 不崩 + read_resource 格式）
- `test_authz_gitnexus_track.py`（find_unguarded_sink_paths 扫全链 + process entry 候选）
- entry 相关测试（detect ∪ process 并集）

---

## 6. 测试策略（TDD）

| 测试 | 锁住的不变量 |
|---|---|
| `process_trace_reader`：cypher 全 label（140）+ resources/read + YAML 解析 | 全量召回（不依赖 processes resource 的 20 截断） |
| `read_resource` 取 `contents[*].text` 不查 `type` | MCP resource 格式（§2.5） |
| trace→CallChain 四级对齐 + has_unresolved | §4.4 |
| `build_call_graph_from_gitnexus`（process trace fixture）→ **chains 非空** | 核心回归锚点（生产一直空壳） |
| injection：trace 链上命中 detect_sinks 的 sink caller → 截取 entry→…→sink | sink→source 反查（判定不变） |
| **entry：CodeIndex.entry_points 含 process entry（gitnexus_process）+ detect 并集** | §4.7 |
| **authz：新 chains + process entry → find_unguarded_sink_paths 扫全链 side-effect → 候选数 > 0** | **0→21 回归锚点**（statement_template_svr fixture） |
| authz：process entry(`entry_type=gitnexus_process`, `route=None`) 进候选（§0.5 断点①） | route 守卫放宽不被回退（防漏改 `ep.route is not None`） |
| authz：ownership guard 在 entry→sink 段出现 → 不产候选 | 决策 6 |
| impact upstream/downstream + file_path 消歧 + 超时容错 | 决策 2 |
| `_send_request` 读 4MB 返回不崩 | readline 修复（G4） |

**关键回归锚点**：① `build_call_graph` 喂 process trace → chains 非空；② authz 喂 statement_template_svr fixture → 候选 > 0（当前 0）。**注意区分**：「chains 非空」≠「GitNexus 轨有发现」（statement_template_svr chains 非空但 injection sink 命中 0，因 sink_detector 独立瓶颈）。

---

## 7. 不变量（必须守住）

- **CLAUDE.md §1 双轨铁律**：只动 GitNexus 轨（调用链来源 + entry + authz 判定），不碰 LLM 轨；**不向 LLM 轨喂确定性产物**。
- **降级契约**：process trace 拿不到 → 空 chains → injection 跳过 + authz 0 候选 → LLM 轨兜底（不引入 A3 硬失败）。
- **injection 判定语义不变**（chain_verdict / propagate_across_chains）；**authz 判定语义改**（§4.8，决策 7，明确）。
- **`CallChain` 结构不变**（entry_point_id/path/depth/has_unresolved）。
- **`EntryPoint` 模型不增字段**（用既有 source/entry_type；`gitnexus_process` 是新 entry_type 值，非新字段）。
- **`CODE_INDEX_RETRY(max 3)` 不动**；**`externally_exploitable` 不被覆写**。

---

## 8. 风险

| 风险 | 缓解 |
|---|---|
| authz 改判定（扫全链 + ownership 段）→ 候选数变、噪声变 | TDD + statement_template_svr 0→21 锚点；LLM 判定层兜底误报 |
| entry 重构（process entry 进）→ authz 候选源变 | entry_type 可观测性 log；detect 并集保留旧识别 |
| process detection 召回限制（工具 sink 漏，§2.6） | 靠 LLM 轨兜底；召回边界文档化 |
| step→FuncBlock 对齐失败（48/总） | 四级匹配 + has_unresolved + log |
| GitNexus 版本绑定 1.6.7 | memory 记录；升级要重验（resource 格式、process 结构） |
| LadybugDB binary 反复丢失（--ignore-scripts） | memory 记修复；生产用正常 npm install |
| ownership guard 扫描段定义（entry→sink_step）可能漏报（守卫在 sink 之后） | 决策 6 接受；follow-up 可扩到全链 |
| spec 范围大（三块） | §9 分阶段实现 + 每阶段独立验证 |
| 🔴 authz route 守卫（§0.5 断点①） | §4.8 改 1 已含放宽；writing-plans 须单测覆盖「process entry `route=None` 也进候选」防漏改 |
| 🟡 injection/xss/ssrf head-seed（§0.5 断点②；根因=source 识别被 sink 绑架）：chains 非空但 entry 不含 sink → 无 source 种子 → `taint_flows` 仍 0 → 三类 GitNexus 轨全空 | **决策 A**：本 spec 不动 source 层；follow-up spec B′（独立 source 识别层，不绑 sink：B′-a 扩 `extract_typed_parameters` 支持 Go / B′-b 轻量 LLM source prompt）；真机验证触发频度 |

---

## 9. 实现顺序（供 writing-plans，分三阶段）

**阶段 1：基础设施 + 调用链来源（G1+G4）**
1. readline 修复（G4）+ 4MB fixture 测试。
2. `read_resource` + resource 格式测试（§2.5）。
3. `process_trace_reader.py` + 测试（§4.3）。
4. trace→CallChain 转换 + 对齐测试（§4.4）。
5. `build_call_graph_from_gitnexus` 重写（§4.2）+ chains 非空回归锚点。
6. `impact_supplement.py`（§4.5）。
7. injection 回归（trace 链上命中 sink caller）。

**阶段 2：entry 重构（G2）**
8. entry 组装改 detect ∪ process（§4.7）+ `__init__.py:213-231` 重写。
9. entry 可观测性 log + 测试。

**阶段 3：authz 适配（G3）**
10. `find_unguarded_sink_paths` 三处改（§4.8）+ `IDORCandidateChain.sink_step_idx`。
11. authz 0→21 回归锚点（statement_template_svr fixture）。
12. ownership 段扫描测试。

**收尾**
13. 全量测试绿（test_gitnexus_call_graph/mcp + 新 reader/impact + authz + entry + injection 集成）。
14. 真机冒烟（statement_template_svr：process trace 端到端、readline 不崩、authz 候选 > 0；injection 受 §2.6 sink_detector 限制已接受）。

---

## 10. 参考

- **代码**：`gitnexus_call_graph.py:165`（build_call_graph）、`gitnexus_mcp.py:105-160`、`code_index/__init__.py:146,213-231`（call_graph 调用 + entry 组装）、`authz_gitnexus_track.py:84-145`（find_unguarded_sink_paths）、`entry_points.py:13`（detect_entry_points）、`chain_propagator.py:133`（只读 chain.path）
- **探针**（10 个，scripts/）：`probe_gitnexus_impact.py` / `probe_gitnexus_cypher_chain.py` / `probe_gitnexus_process.py` / `probe_process_steps.py` / `probe_gitnexus_resources.py` / `probe_gitnexus_resources2.py` / `probe_gitnexus_schema_process.py` / `probe_process_sink_match.py` / `probe_impact_bydepth.py` / `probe_authz_entry_compat.py`
- **spec/gap**：`2026-06-30-gitnexus-mcp-call-layer-fix-design.md`、`2026-06-30-discover-sinks-llm-concurrency-design.md`、`docs/gap/2026-06-28-gitnexus-track-lifecycle-analysis.md`
- **memory**：`gitnexus-1.6.7-real-machine-behavior`（真机能力 + 5 姿势纠正）、`dual-track-consumption-model`、`pre-recon-gitnexus-blockage`
- **CLAUDE.md**：§1 双轨概念 + 双轨铁律
- **follow-up spec（待开）**：`2026-07-XX-taint-source-identification-design.md`（B′：source 识别独立成层，解 §0.5 断点② head-seed——让 entry 无 sink 也能产 source 种子，injection/xss/ssrf 三类 GitNexus 轨才能真正产出；候选 B′-a 扩 `extract_typed_parameters` 支持 Go / B′-b 轻量 LLM source prompt；本 spec 决策 A 不动）
