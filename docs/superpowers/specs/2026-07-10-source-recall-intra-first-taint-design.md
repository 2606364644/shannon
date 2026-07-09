# Source 补召回 + intra-first TaintFlow 设计

> 2026-07-10。修复 GitNexus 确定性层对 NodeGoat(Express/JS)inject/xss/ssrf 全空。
> 分支 feat/fork-py。只做 A(source 补召回 + intra-first);B(调用图根治)单独立项(见 §7)。

## 1. 背景

2026-07-10 NodeGoat whitebox 扫描(`SHANNON_LLM_TRACK_ENABLED=0`,只跑 GitNexus 轨)inject/xss/ssrf 全空:`parameter_graph.taint_flows=[]`、`attack_chains_gitnexus_queue {"chains": []}`、无任何 `injection_*/xss_*/ssrf_*` queue 文件。仅 auth/authz 有产物。

## 2. 根因(深查结论)

不是 GitNexus 上游 JS parser 问题——code_index 建了 24 blocks / 8 chains / 23 entry_points / 8 sinks,有实质内容。是 GitNexus 确定性层对 Express 路由模式的识别局限:

1. **entry_point 识别把路由归注册处**:NodeGoat 在 `index.js` 用 `app.get('/contributions', handler)` 注册路由。`detect_entry_points`(entry_points.py)识别了路由语句,但 `func_block_id` 归到含 `app.get` 语句的函数(`index.js:index:11`),**handler 函数(ContributionsHandler / ResearchHandler 等)没被识别为 entry_point**。23 个 entry_point 里 22 个是 `index.js:index:11`。

2. **source_detector 只扫 entry_point block**(source_detector.py:79-84 `if block.id not in entry_point_ids: continue`)。NodeGoat handler 不在 entry_point_ids → handler 里的 source(`eval(req.body.preTax)`、`req.query.url`)全漏。`source_points=1`(只 index.js:req.query.url)。

3. **intra 分析对了,但 TaintFlow 产出依赖 chain**:`analyze_taint_llm` 对含 sink 函数跑(按 `sinks_by_func`,不依赖 chain,`__init__.py:200`),`IntraResult.local_steps` 已记录同函数 source_param→sink 路径(llm_taint_analyzer.py:232-241)。但 TaintFlow 产出在 `propagate_backward_across_chains`(chain_propagator.py:385,只遍历 chain),handler 不在任何 chain → intra 结果丢弃 → `taint_flows=0`。

4. **chain 漏 handler**:8 条 chain 从 `index.js:index` 出发,只连 4 个 handler(allocations/benefits/profile/session),漏 ContributionsHandler / ResearchHandler(eval/ssrf sink 所在)。

完整因果:handler 不在 entry_point → source_detector 漏 source → source_points=1;handler 不在 chain → propagate_backward 丢弃 intra 结果 → taint_flows=0 → builders 0 候选 → inject/xss/ssrf 全空。

## 3. 设计

两个组件,不依赖调用图(chain/edge),覆盖同函数 source→sink 漏洞。核心洞察:intra 已把同函数 source→sink 算好存进 `local_steps`,source 也由 LLM 识别(source_param),只差①该函数的 SourcePoint(source_detector 漏扫)②一条直接产 TaintFlow 的路径(不经 chain)。

### 3.1 source 补召回(新模块 `source_discovery_llm.py`)

类比 `sink_discovery_llm.py`。

- **输入**:`sinks_by_func`(含 sink 函数)+ all_blocks + source_rules
- **规则路径**:对含 sink 函数的 `source_code` 跑 source_rules(扩范围;当前 source_detector 只扫 entry_point)→ 命中产 SourcePoint(`rule_id=ts-express-body` 等正常 rule_id)
- **LLM 补召回**:规则没命中的非常规写法(解构 `const {a,b} = req.body`),对该函数 source_code 调 LLM 找 source → 软 SourcePoint(`rule_id=llm-discovered-source`, `needs_review=True`, `entry_point_id=该函数 id`)
- **产出**:`source_gap_report.json`(类比 `rule_gap_report`,记规则没覆盖的写法,反哺 source_rules.yml)
- **降级**:LLM 不可用 → 只规则(解构漏,不报错,不阻塞)
- **跟 source_detector 关系**:source_detector 主路径**不变**(仍扫 entry_point,守"source 不被 sink 驱动"设计,memory `source-detection-independent-module-status`);source 补召回是独立兜底模块,只对含 sink 函数补。合并去重(按 entry_point_id + param_name + source_type)

### 3.2 intra-first TaintFlow(改 `code_index/__init__.py:287` 附近)

新函数 `produce_intra_first_taint_flows(sink_call_sites, intra_results, source_points, blocks)`:

- 对每个含 sink 函数(block.id):
  - 取 `intra_results[block.id]` 的 `local_steps`(同函数 source_param→sink)+ `tainted_params`
  - `_source_points_matching(block.id, intra.tainted_params, source_points)`(chain_propagator.py:367)—— 当前只在 chain entry(i==0)调,推广到 sink 所在函数
  - 匹配到 SourcePoint → 产 TaintFlow(source_point → sink, `propagation_steps=local_steps[该 sink]`,单步 intra)
- **合并去重**:`intra_first_flows + propagate_backward_flows`,按 `(source_point.id, sink.id)` 去重。intra-first 产同函数的(超集),propagate_backward 补跨函数的;同函数的 intra-first 优先,chain 产的去重掉
- **needs_review**:source 是 `llm-discovered-source` 的 TaintFlow 标 `needs_review=True`(跟软 SinkCallSite 一致,下游 chain_verdict 复核)

### 3.3 数据流

```
sink_detector → sinks_by_func(含 sink 函数)
  → [source 补召回] 对含 sink 函数:规则 + LLM 补 → 软 SourcePoint(entry_point_id=该函数)
  → intra 分析(已有,不改)→ IntraResult.local_steps(同函数 source→sink)+ tainted_params
  → [intra-first] _source_points_matching(block.id, ...) → 匹配则产 TaintFlow(单步,不经 chain)
  → 合并 propagate_backward chain TaintFlow(按 source.id+sink.id 去重)→ parameter_graph.taint_flows
```

## 4. 错误处理 / 降级

- source 补召回 LLM 不可用 → 只规则(降级,不报错,记 source_gap)
- intra-first 无 SourcePoint 匹配 → 不产(跳过)
- **不影响现有 propagate_backward**(intra-first 是增量路径,propagate_backward 不改)
- intra-first 产的 TaintFlow 若 source 是 llm-discovered → `needs_review=True`,下游 chain_verdict 复核

## 5. 测试

- `test_source_discovery_llm`:规则匹配(点号 `req.body.x`)+ LLM 补解构 + LLM 不可用降级 + `source_gap_report` 产出
- `test_intra_first_taint_flow`:同函数 source→sink 产 TaintFlow(handler 不在 chain 也能产)+ 去重(跟 propagate_backward 不重复)+ `needs_review` 标记
- 回归锚点:NodeGoat `contributions.js eval(req.body.preTax)` 产 TaintFlow(当前 0,修复后 >0)
- 守双轨铁律:source 补召回是 GitNexus 轨内部(确定性层),不喂 LLM 轨 prompt;不破坏 `test_static_dataflow_hints_decoupling` 类锚点

## 6. 设计决策(已定)

1. **source_detector 主路径不变**(扫 entry_point),source 补召回独立兜底(对含 sink 函数,被 sink 驱动但只兜底)——不破坏"source 不被 sink 驱动"主路径设计
2. **解构写法用 LLM 补**(类比 sink,通用),不加正则(解构多变量跨行,正则复杂易错)
3. **intra-first 产的 TaintFlow**,source 是 `llm-discovered-source` 的标 `needs_review=True`(跟软 SinkCallSite 一样,下游复核)

## 7. 范围边界

- **本 spec 只做 A**(source 补召回 + intra-first),覆盖同函数 source→sink 漏洞(eval/ssrf 型)
- **B(handler 入调用图 / 调用图边构建)单独立项**:深查发现 `edges=0`(调用图根本没建跨函数边,chains 来自 GitNexus MCP 从路由推导),B 不只是补 handler entry_point,要重建调用图边,是独立大工程。A 不依赖 B
- **跨函数漏洞(handler→dao,source 在 handler、sink 在 dao)**:A 的 intra-first 覆盖同函数;跨函数的若 handler 不在 chain 仍漏(B 解决)。但 NodeGoat 的 sql 链 chain 已有 dao + source 补到 allocations 解构,A 可覆盖

## 8. 双轨铁律

source 补召回是 GitNexus 轨内部(确定性层),产物(SourcePoint)只进 `parameter_graph`,**不喂 LLM 轨 prompt**。守 CLAUDE.md §1 铁律(LLM 轨纯 LLM 自给自足,不吃确定性产物)。

## 9. 涉及文件(预估)

- 新:`packages/core/src/shannon_core/code_index/source_discovery_llm.py`(source 补召回,类比 sink_discovery_llm.py)
- 改:`packages/core/src/shannon_core/code_index/__init__.py`(调 source 补召回 + `produce_intra_first_taint_flows`,287 附近)
- 新:`packages/core/src/shannon_core/code_index/data/source_candidates.yml`(可选,source 候选模式表,类比 sink_candidates.yml;初版可只走 source_rules + LLM)
- 测试:`packages/core/tests/code_index/test_source_discovery_llm.py`、`test_intra_first_taint_flow.py`
