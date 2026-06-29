# GitNexus MCP 调用层修复 设计

> 日期：2026-06-30　分支：`feat/fork-py`
>
> **背景**：双轨模式下 GitNexus 轨（确定性轨）"基本没结果"。`2026-06-29-authz-gitnexus-track-observability-design.md` 与 `2026-06-29-injection-gitnexus-track-observability-design.md` 已给判定函数加了可观测性 log（让"空壳"可见），但都明确写明"**不解空壳根因——taint_flows=0 / http_route=0 的根因在更深的 GitNexus 调用图**"。本 spec **修那个根因**：实测坐实根因不在调用图算法、不在语言、不在 sink，而在 **MCP 调用层与 GitNexus 1.6.7 失配**。
>
> **真机核查证据**（3 个生产 workspace 的 `code_index.json`）：
>
> | workspace | lang | blocks | entry | chains | edges | sinks | taint_flows | degradation |
> |---|---|---|---|---|---|---|---|---|
> | NodeGoat_28 | typescript | 24 | **0** | **0** | **0** | 68 | **0** | full |
> | 188 靶场 | typescript | 24 | **0** | **0** | **0** | 87 | **0** | full |
> | crAPI | go | 51 | **0** | **0** | **0** | 2 | **0** | full |
>
> `entry/chains/edges` 三连零、`degradation=full`，但 **sinks 检到了**（NodeGoat LLM sink-hunter 找 65 个）→ 瓶颈在调用图，与语言无关（crAPI 是 go、parser 完整，照样空），sink 不是瓶颈。

---

## 1. 目标 / 非目标

### 目标

- **G1**：消除 MCP 调用层失配——GitNexus 轨在**多 repo 索引环境**（生产常态：NodeGoat/crAPI/juice-shop 都在全局 registry）下，能把 GitNexus 已产出的调用图数据正常消费出来：`entry_points`/`edges`/`chains` 不再因漏传 repo / 解析失败被置空，`parameter_graph.taint_flows` 不再空壳。（前提：GitNexus 对目标项目能分析出调用图；上游能力稀疏是另一个问题，见 §7。）
- **G2**：MCP 返回解析失败时**可见**（`workflow.log` 留 warning + 诊断字段），不再静默吞成空。
- **G3**：测试锁住三条失配路径（多 repo 漏参、JSON+trailing、cypher markdown），防回退。

### 非目标（follow-up）

- **不修 impact/context 的 ambiguous 重名**（`trace_from_sink` / `get_function_context` 次级 bug，留 F1）。
- **不加 list_repos 启动校验**（留 F2）。
- **不动 detect_language**（已证非根因：crAPI lang=go、parser 完整仍空；.js→ts 是刻意设计无 js parser）。
- **不动 LLM 轨**（双轨独立性，CLAUDE.md §1）；不改 GitNexus 轨判定/写 queue/merge 逻辑。
- **不解决"GitNexus 对某些项目的调用图本身稀疏"**——本 spec 只修"shannon-py 这边把 GitNexus 已产出的数据搞丢了"，GitNexus 上游对 JS 调用图的能力是另一个问题。

---

## 2. 根因（逐环实测坐实）

GitNexus 轨空壳 = 3 个叠加 bug，全在 MCP 调用层：

### 2.1 `call_tool` 漏传 `repo` 参数（致命）

`gitnexus mcp` 从全局 registry（`~/.gitnexus/registry.json`）发现**多个**已索引 repo 时，`query`/`cypher`/`impact`/`context` 必须传 `repo` 参数指定目标。实测不传时返回**字符串错误**：

```
'Error: Multiple repositories indexed. Specify which one with the "repo" parameter. Available: NodeGoat, crAPI, juice-shop'
```

但 `build_call_graph_from_gitnexus`（`gitnexus_call_graph.py:183/235`）、`trace_from_sink`（`:330`）、`find_sinks_by_patterns`（`:409`）、`get_function_context`（`:435`）共 **6 处 `call_tool` 从不传 `repo`**。生产环境 registry 里常年多个 repo → 每次 白盒 run 都触发此错误。

### 2.2 `_parse_tool_result` 用 `json.loads`（静默吞错）

GitNexus 1.6.7 的工具返回是 **「合法 JSON 对象 + trailing 人类提示文本」** 拼在一个 text 里：

```
'{\n  "processes": [],\n  ...合法 JSON...\n}\nUse context({...}) to see categorized refs...'  ← 尾巴
```

`json.loads`（严格模式）遇到 JSON 后的额外文本报 `Extra data: line X column 1` 失败 → `_parse_tool_result`（`gitnexus_mcp.py:172-173`）`except json.JSONDecodeError: return text` **静默返回原始 str**。

这个 str：
- 不是 `None` → 绕过 `if query_result is None: raise GitNexusNotIndexedError`（`gitnexus_call_graph.py:188`）
- 不是 `dict`/`list` → 被 `isinstance(query_result, dict) else []`（`:195`）静默吃成空 → `entry_points=[]`
- `find_sinks_by_patterns:409` **连 isinstance 守卫都没有** → `for entry in result:` 在 str 上**逐字符迭代**，产出垃圾（比静默空更阴险）

### 2.3 cypher 返回 markdown 表格（非 list[dict]）

GitNexus 1.6.7 的 cypher 返回 `{markdown: "| caller_file | caller_name | ... |", row_count}`，**没有 raw records**。老代码（`:239-263`）期望 `list[dict]` 直接 `record.get("caller_name")`。即使 2.1/2.2 修好，cypher 仍拿不到 edges。

### 失败链路（一行）

```
漏传 repo + JSON+trailing 解析失败 + cypher markdown
 → query/cypher 拿到 str（错误文本或 markdown 包裹）
 → _parse_tool_result json.loads 失败 → 返 str
 → isinstance(dict/list) 全 False → symbols=[] / edges=[]
 → chains=[] → taint_flows=[] → parameter_graph 空壳
 → 3 类 builder 全空 → 不写 *_gitnexus_queue.json
 → merger 读不到 → GitNexus 轨 0 贡献（non-fatal，静默靠 LLM 轨兜底）
```

---

## 3. 实测证据（已逐项验证）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 瓶颈在调用图非 sink/语言 | 读 3 个生产 `code_index.json` | entry/chains/edges 三连零、sinks 有值、degradation=full |
| 工具支持 `repo` 参数 | MCP `tools/list` | query/cypher/impact/context 等全有 `repo`，描述 `"name or path. Omit if only one repo"` |
| 无 `format`/`raw` 开关 | MCP `tools/list` schema | cypher 仅 query/params/repo → 返回格式固定，"用 raw 模式"路堵死 |
| `raw_decode` 可解 JSON+trailing | 探针四工具 | query/cypher/impact/list_repos 全部 `raw_decode` 成功，trailing 126-222 字符提示 |
| query 字段兼容 | `raw_decode` 后看结构 | `{processes, process_symbols, definitions[N], timing}`，filePath **相对路径**与 tree-sitter block 匹配 ✅ |
| markdown 表格可解析 | `_parse_md_table` 试 cypher | 8 行 edges 全对、字段齐全（caller_file/caller_name/callee_file/callee_name）、相对路径 ✅ |
| repo 参数 name/path 双形式可用 | 探针 `repo=str(repo_root)`(绝对路径) 与 `repo=目录名` | 都 `raw_decode` 成功、definitions=5 ✅ → 4.1 可直接用 path 形式（`setdefault("repo", str(self.repo_root))`） |

---

## 4. 设计

### 4.1 `call_tool` 自动注入 `repo`（共识，必改）

`gitnexus_mcp.py:85-99`：在 `call_tool` 发请求前 `arguments.setdefault("repo", str(self.repo_root))`。

- **一处改，6 处 `call_tool` 全受益**（build_call_graph 的 query/cypher、trace_from_sink 的 impact、find_sinks_by_patterns 的 query、get_function_context 的 context）。
- `self.repo_root` 现是**死字段**（构造存了没用），正好激活。
- 用 **path 形式**（工具 schema 明确接受 "name or path"），免去推导目录名；单 repo 时传了也无害（"Omit if only one repo" 是许可而非禁止）。
- 调用方显式传了 `repo` 时不覆盖（`setdefault`）。

### 4.2 `_parse_tool_result` 健壮化（共识，必改）

`gitnexus_mcp.py:157-175`：

1. `json.loads(text)` → `json.JSONDecoder().raw_decode(text.lstrip())`（解析首个 JSON 对象，容忍 trailing 提示文本）。
2. `raw_decode` 失败 / text 以 `"Error:"` 开头 / 解析出 `{status: "ambiguous"}` → **`logger.warning(tool_name + 原因 + text 前 120 字符)` + 返回 `None`**（不再静默返 str）。返回 `None` 让下游 isinstance 守卫正确走空分支。
3. 保留现有的 `content[].type=="text"` 提取逻辑。

### 4.3 cypher markdown 表格解析（决策 A，必改）

两处：

- **`gitnexus_mcp.py`**：`_parse_tool_result` 检测 `isinstance(obj, dict) and "markdown" in obj and "row_count" in obj` → 调 `_parse_md_table(obj["markdown"])` → 塞回 `obj["rows"] = [...]`。新增模块级 `_parse_md_table(s)`：按 `|` 切分、跳表头行 + `|---|` 分隔行、`zip(headers, cells)` 成 `list[dict]`；列数不齐的行跳过；空表返 `[]`。
- **`gitnexus_call_graph.py:235-263`**：cypher 消费从"遍历 list"改为 `for rec in (cypher_result or {}).get("rows", [])`，字段名不变（caller_file/caller_name/callee_file/callee_name）。

只对 `{markdown, row_count}` 双字段命中时触发 rows 解析（避免误伤别的 markdown 返回）；非 cypher 工具不受影响。

### 4.4 消费层类型对齐（仅一处真改）

实测：`find_sinks_by_patterns` / `trace_from_sink` / `get_function_context` **零调用点**（死代码，仅测试用）——本 spec 不动（YAGNI），其同型失配留独立 follow-up。

主流程只走 `build_call_graph_from_gitnexus`（`__init__.py:146` 唯一调用），其两个 call_tool 消费点：

- **query（`:195`）**：已是三元守卫 `query_result.get(...) if isinstance(query_result, dict) else []`——str/None 安全走 else。`_parse_tool_result` 修复后 query 返 dict，三元守卫取 `process_symbols`+`definitions` 自动工作 → entry_points 能从 definitions 匹配 blocks（filePath 双边都是相对路径）→ **不用改**。
- **cypher（`:239`）**：现为 `if isinstance(cypher_result, list):` 遍历 record，但 GitNexus 1.6.7 返 `{markdown, row_count}` dict（绝非 list）→ 永不进分支 → edges=[]。改为读 `rows`：`for rec in (cypher_result or {}).get("rows", [])`，字段名不变。**必改（仅此一处）**。

### 4.5 可观测性（决策 scope，必改）

- **`run_code_index` activity**（`activities.py:442`，`write_index_files` 后、return 前）：经 `get_audit_session().log_info` 发一条汇总——`blocks/entry_points/chains` 数量 + `degradation_level` + 是否空壳的判断。**落点在 activity 层而非 core 的 `build_call_graph_from_gitnexus`**，因为 core 包不持有 audit session；`index.total_chains/total_entry_points/total_blocks/degradation_level` 在 activity 里已可读（`:520-522`）。对齐 06-29 注入轨可观测性 spec 的 InfoEvent 风格（best-effort `try/except`，不阻塞）。
- **`_parse_tool_result` 解析失败**：`logger.warning`（4.2 已含）。
- 这样修完后，06-29 spec 加的 `taint_flows=N` log 与本 spec 的 `chains=N` log 串成完整诊断链：`chains=0 → taint_flows=0 → 0 findings`，任一环空都能在 `workflow.log` 定位。

### 4.6 错误处理（软降级但可见）

保持 GitNexus 轨 **non-fatal**（`workflows.py:362-405` 的 try/except 不动）——符合双轨设计（GitNexus 轨失败靠 LLM 轨兜底）。修复后失败路径改为：解析失败 → log warning + 返空 → GitNexus 轨空壳但**可见**，不再静默。**不引入硬失败**（避免 GitNexus 一抖动整个白盒挂）。

---

## 5. 影响范围

### 必改

| 文件 | 改动 |
|---|---|
| `packages/core/src/shannon_core/code_index/gitnexus_mcp.py` | `call_tool` 注入 repo（4.1）；`_parse_tool_result` raw_decode + markdown 解析 + 失败 log/返 None（4.2/4.3）；新增 `_parse_md_table` |
| `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` | **仅** `build_call_graph_from_gitnexus` cypher 消费改读 `rows`（4.3/4.4） |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_code_index` build 后加 `log_info` 汇总（4.5） |

### 测试必改

| 文件 | 改动 |
|---|---|
| `packages/core/tests/code_index/test_gitnexus_mcp.py` | 新增：raw_decode 容 trailing、markdown 表格 → rows、Error 文本 → None+warning、ambiguous → None、空 content；`call_tool` 断言 repo 注入 |
| `packages/core/tests/code_index/test_gitnexus_call_graph.py` | `FakeMCPClient` 改喂**真实格式 str**（走 `_parse_tool_result`，不再绕过）；断言 `call_tool` 收到 `repo` 参数；str/None 输入下 4 个消费函数不崩、不逐字符迭代 |

### 别动

- `gitnexus_engine.py` — CLI 通道已正确传 `--repo`（`:123`），独立通道。
- `sink_discovery_llm.py` / `llm_taint_analyzer.py` — 不调 `call_tool`，仅用 LLM client。
- `__init__.py` — 仅传递 `mcp_client`/`repo_path`（`:146` 调 build_call_graph_from_gitnexus）。
- `_parse_process_response` / `_build_chains_from_edges` — 纯数据处理，不涉 MCP。
- `find_sinks_by_patterns` / `trace_from_sink` / `get_function_context`（gitnexus_call_graph.py）— **零调用点（死代码，仅测试用）**，本 spec 不动；其 call_tool 也有同型失配，留独立 follow-up。

---

## 6. 测试策略

**TDD**：每个改点先写失败测试。

| 测试 | 锁住的不变量 |
|---|---|
| `_parse_tool_result("JSON + trailing 提示")` → dict | raw_decode 容 trailing |
| `_parse_tool_result("{markdown, row_count}")` → dict 含 `rows: list[dict]` | cypher markdown 解析 |
| `_parse_tool_result("Error: Multiple repos...")` → None + warning | 错误文本不静默 |
| `_parse_tool_result({status:"ambiguous"})` → None + warning | ambiguous 不静默 |
| `_parse_md_table` 常规/空/缺分隔行/列不齐 | 解析器健壮性 |
| `call_tool` 任何工具都带 `repo=str(repo_root)` | repo 注入（除非调用方显式传） |
| `build_call_graph_from_gitnexus`（FakeMCP 喂真实 str）→ entry_points/edges/chains **非空** | 端到端：修完后调用图非空（**核心回归锚点**） |
| `find_sinks_by_patterns` 收到 str/None → 不逐字符迭代、返 [] | 防垃圾数据 |

**回归锚点**：`build_call_graph_from_gitnexus` 喂真实格式 fixture 必须产非空 chains——这正是生产里一直为 0 的量，修完必须非空。

---

## 7. 风险

| 风险 | 缓解 |
|---|---|
| markdown 表格格式依赖（GitNexus 升级改格式 → `_parse_md_table` 坏） | 解析失败 log + 返空 rows（软降级）；只对 `{markdown,row_count}` 双字段命中触发，误伤面小 |
| `repo=path` 某版本只认 name | 已验证 1.6.7 接受 path；若未来坏，F2 的 list_repos 可拿 path→name 映射兜底 |
| 修完后 GitNexus 对 JS 调用图本身稀疏（上游能力） | 本 spec 只修"shannon-py 搞丢数据"；上游能力是另一个问题，由 chains 数 log 暴露真实情况 |

---

## 8. follow-up

- **F1**：`impact`/`context` ambiguous 重名消歧（`trace_from_sink` / `get_function_context` 用 `file_path`/`kind`/`target_uid` 消歧）。
- **F2**：`list_repos` 启动校验——`run_code_index` 前确认目标 repo 已索引、拿 path↔name 映射。
- **F3**：**真机冒烟**（NodeGoat/crAPI 跑完整白盒，确认 GitNexus 轨 `<vuln>_gitnexus_queue.json` 非空、`chains>0`）—— 与 AU-1 / INJ-1 / RE-6 同批（memory 里多处在等这次冒烟）。
- **F4**：精化/更新相关 memory（`prerecon-recon-effect-gap-analysis` 的"detect_language 误判 JS→ts 是根因"已被证伪；`authz/injection-gitnexus-observability` 的"chains=0 玄学根因"已精化到 MCP 调用层）。

---

## 9. 与现有工作的关系

- **承接** `2026-06-29-authz/injection-gitnexus-track-observability-design.md`：那两个 spec 让"空壳可见"（加 log），本 spec 修"空壳根因"（让 chains 非空）。修完后，那两个 spec 的 log 将显示 `taint_flows>0`，形成验证闭环。
- **不冲突** `2026-06-26-gitnexus-llm-sink-discovery-design.md`（GitNexus 轨接 LLM 补召回）：那个改 `sink_detector`/`sink_discovery_llm`，本 spec 改 `gitnexus_mcp`/`gitnexus_call_graph`，无交集。
- **守** CLAUDE.md §1 双轨铁律：本 spec 只动 GitNexus 轨自己的 MCP 调用层，不碰 LLM 轨 prompt、不喂确定性产物给 LLM 轨。
