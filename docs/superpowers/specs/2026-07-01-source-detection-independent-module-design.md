# Source 独立识别模块 + 双向对称轨设计

> 日期:2026-07-01 | 分支:`feat/fork-py` | 状态:design(brainstorming 产出,待 plan)
> 主题:把 source 识别提升为与 sink 并列的一等端点,inject/xss/ssrf 改 Sink→Source(backward),authz 升级到 SourcePoint 级

---

## 1. 背景与动机

### 1.1 现状:source 识别不独立、被 sink 驱动

shannon-py 的 GitNexus 轨(确定性轨)里,sink 识别与 source 识别**不对称**:

- **sink 识别独立干净**:`sink_detector.py`(规则)+ `sink_discovery_llm.py`(LLM soft 补召回)→ `SinkCallSite`,有专门模块、独立运行。
- **source 识别没有独立模块**,散落两处:
  - 入口点(函数级):`entry_points.py` 的 `detect_entry_points` + gitnexus_process entry。
  - 参数 taint:`llm_taint_analyzer.py` 的 `analyze_taint_llm`,**只对有 sink 的函数跑**(`code_index/__init__.py:172-196` 的 `only for functions with sinks`)——**source 识别范围被 sink 决定**。
- **`ParameterSource` enum 几乎没被消费**:定义了 10 类来源(query/path/body/form/header/cookie/file/session/internal/unknown,`models.py:106-117`),但 `enhanced_parameters.py:222` 自认 "Individual parameter source inference (QUERY/FORM/BODY) is not implemented",`chain_propagator.py:204` 直接把 `source_type` **硬编码成 `QUERY_PARAM`**。
- **authz 的 source 是函数级 `EntryPoint`**(`authz_gitnexus_track.py:118-122`),判"入口→sink 路径无 ownership guard",**过报**(入口可能根本没有用户可控 id 参数流到 sink)。auth 轨则完全没有 source 概念(纯配置 regex 扫描)。

### 1.2 主张:source/sink 对称、并列

- source 识别应**独立成模块**(`source_detector`,平行 `sink_detector`),产 `SourcePoint`(平行 `SinkCallSite`),**不被 sink 驱动**(对所有 entry handler 跑)。
- 两类漏洞从不同端点入手(方向,印证原版):
  - **source 识别服务 authz**:Source→Sink / Endpoint→Guard 正向(从用户可控参数出发,查控制缺失)。
  - **sink 识别服务 inject/xss/ssrf**:Sink→Source backward(从危险 sink 反向回溯到 source)。

### 1.3 原版 shannon 方法论佐证

原版 TS shannon(`/Users/mango/project/shannon-refactor/shannon`)是纯 LLM 自给自足,其方向分类与本主张**完全一致**:

- **注入类 = Sink→Source**:XSS/SSRF prompt 明确声明 "Sink-to-Source / Backward Taint Analysis";injection 混合但 sink 自发现。
- **Authz = Endpoint→Guard 正向**:从 endpoint 的 **object ID parameter(userId/order_id)** 出发追 ownership guard。
- 原版 `recon_deliverable.md` Section 5 "Potential Input Vectors" 是 source 的结构化产物,按 source_type 5 类分表(POST Body / URL Query / URL Path / Cookie / Header),每表 4 列:`Input Field` / `Source`(取用表达式+位置)/ `Validation` / `Flow Path`。

⚠️ **现状偏离**:原版注入类是 backward,但 shannon-py `chain_propagator` 现状是 forward(Source→Sink 正向)。`chain_verdict.py:46` 的 `_DIRECTION={injection:forward, xss:backward, ssrf:backward}` 只是给 LLM 的 `direction_hint` 提示,**xss/ssrf 标着 backward 但实际消费 forward 产出的 taint_flows**——backward 目前只是标签。

---

## 2. 设计目标

1. **`source_detector` 独立识别 `SourcePoint`**(平行 `sink_detector`/`SinkCallSite`),对所有 entry handler 跑,不被 sink 驱动。
2. **inject/xss/ssrf 改 backward**(Sink→Source),`chain_propagator` 新增反向传播,**双向锚定**:起点 `SinkCallSite` + 终点 `SourcePoint`,只有追到真实 SourcePoint 的链才成立。
3. **authz 从 entry 级升级到 SourcePoint 级**(Endpoint→Guard 正向),三重过滤降低过报。
4. **守双轨铁律**:`source_points` 是 GitNexus 轨产物,**不喂 LLM 轨 prompt**;LLM 轨自产 source。

---

## 3. 范围(A+B+C,一个 spec 全量、plan 分 3 阶段)

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **Phase 1 (A)** | `source_detector` + `SourcePoint`(基础设施) | — |
| **Phase 2 (B)** | `propagate_backward` + inject/xss/ssrf 切换到 backward | A(SourcePoint 终点锚定) |
| **Phase 3 (C)** | authz 接入 SourcePoint(三重过滤) | A(SourcePoint 起点锚定) |

A 是 B/C 共同依赖;B 验证双向锚定(inject 轨),C 验证 missing-control 模型(authz 轨)。

---

## 4. 架构总览 + 组件边界

### 4.1 pipeline 位置(source_detector 作为新步骤 ⑧b,与 sink 检测③对称)

```
① parse → blocks
② GitNexus call graph(edges/chains/entry_points)
③ sink detect(rule + LLM soft)→ sink_call_sites
⑤ LLM taint analysis(有 sink 的函数)→ intra_results
⑦ entry 组装(detect ∪ gitnexus_process)
⑧b source detect(rule + LLM soft)→ source_points          【新增,在 ⑦ 后,独立不依赖 sink】
⑥' propagation:
     inject/xss/ssrf → propagate_backward(                  【改:backward 主流程】
         sink_call_sites, source_points, intra_results) → taint_flows
     (forward propagate_across_chains 保留,不进主流程:供 authz _source_reaches_sink
      复用底层 _map_call_site_params + 过渡期测试)
⑧ assemble CodeIndex(+ source_points)
```

**顺序依赖**:backward 起点 `SinkCallSite`(③)+ 终点 `SourcePoint`(⑧b)+ 局部 taint `intra_results`(⑤),故 ⑥' 在三者之后;⑧b 需 entry_points(⑦)故在其后。

### 4.2 组件边界(避免 scope creep)

| 模块 | 职责 | 本次 |
|------|------|------|
| `source_detector.py`(新) | 识别 entry handler 函数体的用户可控字段 → `SourcePoint` | **新增** |
| `sink_detector.py` | 危险调用点 → `SinkCallSite` | 不动(对称参照) |
| `entry_points.py` | 入口函数发现 | 不动(source_detector 消费其产物) |
| `enhanced_parameters.py` | 签名参数类型提取 | 不动(source_detector 填其 QUERY/BODY/PATH 缺口,各管各) |
| `chain_propagator.py` | taint 传播 | **改造**:新增 `propagate_backward_across_chains` + `_map_call_site_params_reverse`;forward `propagate_across_chains` 保留过渡 |
| `authz_gitnexus_track.py` | IDOR 候选 | **改造**:从 entry 级升级到 SourcePoint 级(三重过滤) |
| `chain_verdict.py` / `vuln_chain_builders/*` | 候选提取 + verdict | **不动**(TaintFlow 结构不变,方向对提取无影响) |
| `dual_track_merger.py` | finding 级 OR | 不动 |
| `parameter_models.py` | `TaintFlow` / `SinkCallSite` | **加** `SourcePoint`(结构);`TaintFlow` 不变 |

**最重要的边界声明**:`source_detector` 只产"入口原始 source"(handler 可控参数),**不接管 chain_propagator 的传播职责**。`SourcePoint` 本次服务的消费方:Phase B(backward 终点锚定)+ Phase C(authz 起点锚定)。LLM 轨完全不动。

---

## 5. SourcePoint 数据模型 + source_detector

### 5.1 SourcePoint 数据结构(平行 `SinkCallSite`,放 `parameter_models.py`)

字段对齐原版 Input Vector 表(第 8 节详述):

```python
class SourcePoint(BaseModel):
    id: str                       # "{entry_point_id}::{param_name}::{line}"
    entry_point_id: str           # handler 的 func_block_id(反查 route/http_method)
    param_name: str               # 字段名,如 "userName" / "userId" / "threshold"
    source_type: ParameterSource  # body/query/path/header/cookie —— 复用现有 enum(对齐原版 5 类)
    expression: str               # 取用表达式,如 "req.body.userName"(对齐原版 Source 列)
    file_path: str
    line: int                     # 取用点行号(对齐原版 "at session.js:55")
    column: int = 0
    validation: str = "NONE"      # 取用点验证(NONE/parseInt/regex/escape...),对齐原版 Validation 列
    confidence: float             # 保留:source 识别不确定性高于 sink,authz judge 需加权
    rule_id: str                  # 规则 id 或 "llm-discovered"
    needs_review: bool = False
```

**字段对称性自检(对比 `SinkCallSite`)**:`id`/`entry_point_id`(↔ caller_id)/`param_name`(↔ callee_name)/`source_type`(↔ category)/`expression`(↔ dangerous_slots.expression)/`file_path`/`line`/`column`/`rule_id`/`needs_review` 全对称。两点不对称(均有理由):
- **多 `confidence`**:sink 无此字段(用 rule_id+needs_review 隐含),source 保留——LLM soft + 框架多样性不确定性更高,judge 需加权。
- **多 `validation`**:对齐原版 Input Vector 表 Validation 列(原版有,sink 无对应概念)。
- **无 `dangerous_slots` 列表**:source 是单字段,`expression` 已够(sink 需 slots 因一次调用有多个危险参数位)。

### 5.2 识别算法(两层,对称 `sink_detector`)

**① 规则层 `detect_sources(blocks, parser)`** —— 对每个 entry handler,分析其**签名 + 函数体**,按多语言框架解构模式匹配:
- 看签名注解参数:FastAPI `Query()/Path()/Body()`、Spring `@RequestParam/@PathVariable/@RequestBody`。
- 看函数体取用:Express `req.params.id`/`req.query.x`/`req.body.x`、Django `request.GET['x']`、Flask `request.args`、Gin `c.Query("x")`、PHP `$_GET['x']`。
- 每个模式 → 精确 `source_type`,产 rule-based `SourcePoint`(`rule_id` 追溯规则定义)。顺带识别同取用点的简单 validation(parseInt/Number/已知 regex/escape);复杂的标 `needs_review`。
- 两路(签名注解 / 函数体取用)识别结果按 `(entry_point_id, param_name, source_type)` 去重——同一可控字段只保留一个 SourcePoint(优先 `rule_id` 更具体的);两路通常互斥(FastAPI 用签名注解就不直接 `req.query`,Express 用 `req.query` 就无签名注解),去重仅处理边缘共存。

**② LLM soft 补召回 `discover_sources_llm`** —— 规则未命中的 handler(非常规框架/解构),轻量 LLM 判"有哪些可控字段 + source_type",产 `rule_id="llm-discovered"` 的 soft `SourcePoint`。**复用 `sink_discovery_llm.py` 并发骨架(`map_llm_with_bounds`)+ 兜底**(LLM 不可用 → 退回规则 + `is_entry_hint`)。参考原版 "upstream list + own grep" 混合模式(`vuln-injection.txt:135-138`):规则层产确定性起点,LLM 补找漏掉的 dynamic route/hidden param/indirect source。

**独立运行**:对所有 entry handler 跑(**不看该 handler 有没有 sink**),这是和现状 `only-for-functions-with-sinks` 的根本区别。

**与 `is_entry_hint` 关系**:`sink_detector.py:247-274` 已有 AST 浅判"实参是否来自外部输入"——`source_detector` 反向用同一思路("handler 哪些数据项是外部输入"),可抽共享 AST helper,但不强耦合。

### 5.3 规则层必须自写(GitNexus 不够)

调查确认:GitNexus MCP 只提供**函数级** entry points(函数名+文件+行号,**不含参数**),以及调用链/可达性。它**完全不能**提供:
- 函数参数列表(来自 tree-sitter,`enhanced_parameters.py:3` 明确注释:"GitNexus provides function definitions and call relationships but **not full parameter types**")。
- **参数的 HTTP source_type(query/body/path)**——GitNexus 不知道 `user_id` 来自 `req.query` 还是 `req.body`。

仓库从未向 GitNexus 查过参数/source_type。**规则层必须自己写**(tree-sitter AST + 多语言框架解构模式)。GitNexus 给"入口是哪个函数",`source_detector` 给"这个入口的哪些参数可控、来自 HTTP 哪个部位"——两层职责分明。复用现有 parser/框架模式基础设施(与 `entry_points.py` decorator 正则同层思路)。

---

## 6. inject/xss/ssrf backward 改造(Phase B)

### 6.1 核心算法:`chain_propagator.py` 新增 `propagate_backward_across_chains`,双向锚定

```
for sink in sink_call_sites:                          # ① 起点:SinkCallSite(规则+LLM soft)
    sink_func = blocks[sink.caller_id]
    seed_params = _tainted_params_reaching_sink(       # ② seed:sink 的 dangerous_slots
        sink, intra_results[sink_func])                   #   → sink_func 哪些参数 tainted
    for chain in _chains_containing(sink_func.id):     #    (或 trace_from_sink 产反向链)
        current_tainted = seed_params
        steps_rev = []
        for (callee, caller) in _reverse_hops(chain.path, sink_func.id):  # ③ 反向沿 path
            caller_tainted = _map_call_site_params_reverse(              #   反向参数映射
                callee_block=callee, callee_tainted=current_tainted, caller_block=caller)
            steps_rev.append(PropagationStep(callee→caller, ...))
            current_tainted = caller_tainted
            if caller 是 entry:                         # ④ 终点锚定:SourcePoint
                for sp in _source_points_matching(caller, current_tainted):
                    emit TaintFlow(                      #   仍是 source→sink 语义
                        source_param=sp.param_name, source_type=sp.source_type,  # 精确,非硬编码
                        sink_call_site_id=sink.id, propagation_steps=正序化(steps_rev))
```

### 6.2 关键设计点

1. **双向锚定**:起点 `SinkCallSite`(sink 端真实)+ 终点 `SourcePoint`(source 端真实)。**只有反向追到真实 SourcePoint 的链才成立**——过滤"到达 sink 但 source 不是用户可控"的假链。`source_type` 用 SourcePoint 精确值,替掉 `chain_propagator.py:204` 硬编码 `QUERY_PARAM`。
2. **反向参数映射 `_map_call_site_params_reverse`**:已知 callee 的 tainted params,反推 caller 调用时传的哪些实参 tainted。**复用 `_find_call_args_for_callee`**(找 caller 里的调用实参),位置映射反推。唯一新增的核心工具函数。
3. **TaintFlow 产出不变**:仍是 `source→sink` 语义,`propagation_steps` 正序化,下游 `chain_verdict` / builder / merger **零改动**。`TaintFlow` 结构(`parameter_models.py:52-81`)不变。
4. **复用现有基础设施**:`gitnexus_call_graph.py:118` 的 `trace_from_sink()` 已能从 sink 追 upstream callers 产反向 CallChain;`_find_call_args_for_callee` / `_references_tainted` 可复用。
5. **`only-for-sinks` 不矛盾**:`SourcePoint` 独立识别所有 entry(source 端独立,Phase A);backward trace 从 sink 出发是 backward 的本质。两者不矛盾。

### 6.3 pipeline 分流 + forward 保留过渡

- `__init__.py` pipeline:inject/xss/ssrf 切到 `propagate_backward`(主流程产 taint_flows)。
- forward `propagate_across_chains` **保留不进主流程**(过渡期):供 authz `_source_reaches_sink` 复用底层 `_map_call_site_params` + 过渡期回归测试。删除留 follow-up。

### 6.4 风险

反向参数映射精度(*args/**kwargs/keyword args/默认参数)——与 forward `_map_call_site_params` 同级风险,保守回退(无法映射时把 callee tainted 全传 caller params,`chain_propagator.py:243-245` 同款)。

---

## 7. authz 接入 SourcePoint(Phase C)

### 7.1 核心算法:`find_unguarded_sink_paths` 改造(三重过滤)

```
for ep in entry_eps (http_route/rpc/gitnexus_process):
    handler = blocks[ep.func_block_id]
    if _handler_has_ownership_guard(handler): continue        # dominance 短路(保留)
    ep_sources = source_points_for(ep.func_block_id)          # ① 新:SourcePoint(Phase A 产)
    if not ep_sources: continue                                 #   无用户可控 source → 跳过(降过报)
    for chain in chains_of(ep):
        for step_idx in 1..len(chain.path):
            sid = chain.path[step_idx]
            if not _is_side_effect_sink(blocks[sid]): continue # ② authz 专属 sink(DB/ORM/file/state)
            if not _source_reaches_sink(                       # ③ 正向可达(复用 forward 工具)
                    ep_sources, chain.path[:step_idx+1], blocks): continue
            if _segment_has_ownership_guard(...):  continue    # ④ ownership guard(保留)
            emit IDORCandidateChain(..., source_point_ids=命中)  # 附 source 证据给 judge
```

### 7.2 关键设计点

1. **三重过滤降过报**:现状只有"路径存在 + 无 guard";改造后 = **有 SourcePoint** + **参数实际流到 sink** + **无 guard**。真正命中 IDOR 语义:用户可控 id 流到 side-effect sink 且路径无 ownership。
2. **authz 的 sink 是专属概念**:`_SIDE_EFFECT_SINK_RE`(DB write / ORM mutation / file / state,`authz_gitnexus_track.py:38-47`),**不是**注入的 `SinkCallSite`(execute/eval/innerHTML)。两类漏洞 sink 语义不同,各自识别,不混。
3. **复用 forward 工具做正向可达**:新增 `_source_reaches_sink(ep_sources, segment, blocks)`——从 SourcePoint 参数沿 segment 正向传播,复用 `_map_call_site_params`(forward)。**这正是 forward `propagate_across_chains` 保留过渡的用途之一**。
4. **`IDORCandidateChain` 加 `source_point_ids` 字段**:记录命中的 SourcePoint(source 端证据),供 judge LLM 和 queue 输出。

### 7.3 两方向并存(对称架构成型)

- **forward**(`_map_call_site_params`)→ 服务 authz(Endpoint→Guard 正向)
- **backward**(`_map_call_site_params_reverse`)→ 服务 inject/xss/ssrf(Sink→Source)
- **SourcePoint** 是两端共享的 source 锚;sink 端 authz 用 side-effect sink、inject 用 SinkCallSite

---

## 8. 产物落盘 + pipeline 集成

### 8.1 CodeIndex 加字段(`models.py`)

```python
class CodeIndex(BaseModel):
    ...
    sink_call_sites: list["SinkCallSite"] = []
    source_points: list["SourcePoint"] = []      # 新增,平行 sink_call_sites
    parameter_graph: "ParameterPropagationGraph | None" = None
```

`SourcePoint` 定义放 `parameter_models.py`(和 `SinkCallSite` 同文件)。`CodeIndex.source_points` 用 forward ref,需在 `models.py:_resolve_forward_refs()` 注册导入(对齐 `sink_call_sites`,`models.py:209-215`)。`write_index_files` 无需改——`code_index.json` 是 `model_dump_json`,新字段自动序列化。

### 8.2 产物文件(变/不变)

| 文件 | 变化 |
|------|------|
| `code_index.json` | **加 `source_points` 字段** |
| `parameter_graph.json` | 结构不变;`taint_flows` 改由 backward 产,`source_type` 精确(来自 SourcePoint,非硬编码) |
| `*_gitnexus_queue.json`(inj/xss/ssrf) | 不变(builder/verdict 零改动) |
| `authz_gitnexus_queue.json` | `IDORCandidateChain` 加 `source_point_ids` |

authz 轨(`build_authz_gitnexus_track`)读 `code_index.json`,用 `source_points` + side-effect sink + forward 工具做 `find_unguarded_sink_paths` 改造,**不产 TaintFlow**(独立路径)。

### 8.3 字段对齐原版 Input Vector 表

| 原版列 | 实例 | shannon-py 对应 |
|--------|------|-----------------|
| `Input Field` | `userName` / `userId` / `threshold` | `SourcePoint.param_name` |
| `Source` | `req.body.userName at session.js:55` | `SourcePoint.expression` + `file_path:line` |
| `Validation` | `NONE` / `/^.{1,20}$/` / `parseInt()` | `SourcePoint.validation` |
| `Flow Path` | `→ userDAO.validateLogin() → user-dao.js:91 MongoDB findOne` | `TaintFlow`(backward 产,非 SourcePoint 字段) |
| 分类(POST Body/Query/Path/Cookie/Header) | — | `SourcePoint.source_type` |

原版把 source + flow 放一个表;shannon-py 拆成 `SourcePoint`(source 端)+ `TaintFlow`(flow,backward 产)——更结构化,信息完全覆盖。原版 `injection_queue.json` 的 `source` 字段(`"req.query.threshold at allocations.js:21"`)+ `combined_sources` 同此格式。

### 8.4 双轨铁律边界(最重要的对齐澄清)

- 原版 Input Vector 表是 **LLM 轨产物**(recon agent 产,喂 vuln agent)。
- shannon-py 的 `source_points` 是 **GitNexus 轨产物**(确定性 source_detector 产,喂 backward/authz)。
- **两轨 source 各自独立产出,不互通**:GitNexus 轨 `source_points` **不喂 LLM 轨 prompt**(双轨铁律,呼应 CLAUDE.md §1 + `test_static_dataflow_hints_decoupling.py` 精神);LLM 轨(authz agent / vuln-*.txt)自己识别 source。merger 只在 **finding 级**做 OR,不在 source 级。
- 所以"参考原版"= 参考**字段表达/分类**,不是参考"把 source 喂 LLM 轨"。

---

## 9. 降级 / 双轨铁律 / 可观测性 / 测试 / 成功标准

### 9.1 降级(对齐现有兜底模式)

- source_detector 规则层:纯确定性,无外部依赖,不降级。
- LLM soft 补召回:LLM 不可用(stub/超时)→ 退回纯规则 + `is_entry_hint`(对齐 `sink_discovery_llm` 兜底 + 呼应 CLAUDE.md "GitNexus 轨确定性兜底")。
- GitNexus 不可用:code_index 降级 → `source_points` 空 → backward 无起点 → GitNexus 轨空产出,**LLM 轨独立兜底**(双轨 OR)。
- backward 反向映射失败:保守回退(callee tainted 全传 caller params,对齐 `chain_propagator.py:243-245`)。

### 9.2 双轨铁律(贯穿,锁定)

- source_detector 属 GitNexus 轨,LLM soft 是轨内补召回(对称 `sink_discovery_llm`)。
- **`source_points` 不喂 LLM 轨 prompt**;LLM 轨(authz agent / vuln-*.txt)完全不动;merger finding 级 OR。
- **防回退锁**:加守卫测试断言 `source_points` 不进 `prompts/shared/_*.txt`(对齐 `test_static_dataflow_hints_decoupling.py` 精神)。

### 9.3 可观测性(对齐最近 injection/authz-gitnexus-observability 方向)

- source_detector:`log_info_activity` 报规则 N + LLM soft M 个 SourcePoint。
- backward:报起点 sink 数 / 锚定成功(追到 SourcePoint)数 / 终点未命中丢弃数。
- authz:三重过滤各阶段计数(有 SourcePoint 的 entry / 参数流到 sink / 无 guard)。

### 9.4 测试策略

- **source_detector**:规则层多语言框架解构(每语言一例)+ LLM soft stub + 兜底。
- **backward**:`propagate_backward_across_chains` 单元(双向锚定:追到 SourcePoint 成立 / 未追到丢弃)+ 反向参数映射。
- **authz**:`find_unguarded_sink_paths` 三重过滤 + `source_point_ids` 字段。
- **防回退**:`source_points` 不进 LLM 轨 prompt 守卫测试。
- **回归**:现有 inject/xss/ssrf builder 测试(TaintFlow 结构不变应通过)+ forward 保留测试。

### 9.5 成功标准(NodeGoat 对照原版)

- source_detector 识别原版 Input Vector 表的关键 source:`req.query.threshold`、`req.body.preTax/afterTax/roth`、`req.params.userId`、`req.body.userName`、`req.query.url/symbol`。
- backward 双向锚定成立:INJ-VULN-01(threshold→`$where`)、INJ-VULN-02/03/04(`eval`)链 source 端有 SourcePoint + sink 端有 SinkCallSite。
- authz:`GET /allocations/:userId` 的 `userId` SourcePoint 流到 side-effect sink 且无 ownership guard → IDOR 候选。
- 前置:GitNexus 轨 chains > 0(呼应最近 MCP 调用层修复)。

---

## 10. follow-up(不在本 spec)

- forward `propagate_across_chains` 删除(过渡期验证后)。
- `combined_sources`(backward 多 source 到同 sink)的结构化表达。
- inject 轨 source_type 已由 SourcePoint 精确化(本 spec Phase B 完成);`enhanced_parameters.mark_http_parameter_sources` 的容器级标记是否可由 source_detector 完全替代,后续评估。
- injection 的 direction_hint(forward)与 backward 主流程的一致性(本次 inject 改 backward,direction_hint 可统一)。

---

## 11. 关键决策记录(brainstorming 过程)

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| 1 | 方向语义 | source/sink 对等端点(视角起点) | 两类漏洞从不同端点入手;trace 算法改不改归范围决策(#2),本 spec 最终 inject 真改 backward |
| 2 | 范围切片 | A 基础设施 + B authz(后扩 + C inject backward) | 用户选择"本次也改 inject",扩为 A+B+C 全量,plan 分 3 阶段 |
| 3 | SourcePoint 粒度 | 入口 handler 参数级 | source 本质是程序边界点,在 entry handler 参数;内部函数 tainted 归 chain_propagator |
| 4 | authz 接入深度 | 方案 B 深接入(参数流到 sink) | 真正兑现参数级 source 的 IDOR 精确语义;复用 chain_propagator 工具 |
| 5 | 规则层 | 自写(GitNexus 不够) | GitNexus 不提供参数 source_type;复用 parser/框架模式基础设施 |
| 6 | LLM soft 归属 | GitNexus 轨内补召回(非 LLM 轨) | 对称 sink_discovery_llm;守双轨铁律 |
| 7 | confidence 字段 | 保留 | source 识别不确定性高于 sink,judge 需加权 |
| 8 | validation 字段 | 加(对齐原版) | 原版 Input Vector 表 Validation 列;source 点记验证有意义 |
| 9 | backward 终点 | SourcePoint 锚定(双向锚定) | 过滤假链;source_type 精确;source 端真实 |
| 10 | forward 处理 | 保留过渡(不进主流程) | authz 复用底层工具 + 过渡回归 |
| 11 | authz sink | 专属 side-effect sink(非 SinkCallSite) | 两类漏洞 sink 语义不同 |
| 12 | IDORCandidateChain | 加 source_point_ids | 附 source 证据给 judge |
| 13 | 产物落盘 | source_points 嵌 code_index.json(不独立 Markdown) | 对称 sink_call_sites;不喂 LLM 轨(铁律) |
