# W5 · handler 不入链致整类全空：source 补召回 + intra-first taint

> 能力点 W5 的场景复盘：**发现了什么问题 -> 怎么排查出根因 -> 设计了什么方案 -> 实测效果如何**。
> 与主文档 [`scan-effectiveness-gains-vs-ts.md`](./scan-effectiveness-gains-vs-ts.md) §4.2 同主题，本文是单机制深挖，补上完整因果链与方案取舍。叙事版 [`refactor-scan-optimization-vs-ts.md`](./refactor-scan-optimization-vs-ts.md) §2.2 是精简引用。
> 口径：测试绿 ≠ 真机已验。本文如实区分「测试层面已验」与「真机待验」。

---

## 1. 场景：NodeGoat 三类 GitNexus 轨全空

拿 NodeGoat（Express / JS 教学靶机）跑白盒扫描，关掉 LLM 轨只跑确定性轨（`SUPERNOVA_LLM_TRACK_ENABLED=0`），结果 injection / xss / ssrf **三类整段全空**：

- `parameter_graph.taint_flows = []`，一条污点链都没有；
- `attack_chains_gitnexus_queue.json` 里 `{"chains": []}`；
- 没有任何 `injection_*` / `xss_*` / `ssrf_*` queue 文件落盘；
- 只有 auth / authz 有产物。

NodeGoat 里明明有 `eval(req.body.preTax)` 这种教科书级的命令注入、`req.query.url` 拼请求的 SSRF，确定性轨却一条都没召回。这是「整类归零」级别的漏报，不是少几条的问题。

第一反应容易怀疑是 GitNexus 上游 JS parser 没解析出来。**排除掉这条**：code_index 实际建了 24 个 block / 8 条 chain / 23 个 entry_point / 8 个 sink，有实质内容。问题不在「没解析到代码」，在「解析到了但没串成链」。

---

## 2. 根因：一条四步因果链

顺着 `taint_flows=0` 往上追，挖出一条四步因果链，环环相扣，任何一环断掉都够让整类归零。

**第一步：entry_point 把路由归到了注册处。**
NodeGoat 在 `index.js` 里用 `app.get('/contributions', handler)` 注册路由。`detect_entry_points` 识别出了路由语句，但把 `func_block_id` 归到了包含 `app.get` 语句的那个函数（`index.js:index:11`），真正干活的 handler 函数（`ContributionsHandler` / `ResearchHandler` 这些）**没被标成 entry_point**。23 个 entry_point 里 22 个都是 `index.js:index:11`。

**第二步：source_detector 只扫 entry_point。**
`source_detector` 的主路径是 `if block.id not in entry_point_ids: continue`——只扫入口函数。handler 不在 entry_point_ids 里，handler 内部的 source（`eval(req.body.preTax)`、`req.query.url`）**全漏**。扫完 `source_points=1`，只有 `index.js` 的 `req.query.url` 这一个。

**第三步：intra 算对了，但产出 TaintFlow 要看 chain。**
这里有个反直觉的点：`analyze_taint_llm` 其实对含 sink 的函数都跑了（按 `sinks_by_func`，不依赖 chain），`IntraResult.local_steps` 里已经记好了同函数 source_param -> sink 的路径。**intra 分析没漏**。但 TaintFlow 的实际产出发生在 `propagate_backward_across_chains`，这个函数只遍历 chain。handler 不在任何 chain 上 -> intra 算出来的结果被丢掉 -> `taint_flows=0`。

**第四步：chain 本身就漏了 handler。**
8 条 chain 全从 `index.js:index` 出发，只连到了 4 个 handler（allocations / benefits / profile / session），`ContributionsHandler`（eval sink 所在）、`ResearchHandler`（ssrf sink 所在）根本没进 chain。

完整因果：**handler 不在 entry_point -> source_detector 漏 source -> source_points=1；handler 不在 chain -> propagate_backward 丢 intra 结果 -> taint_flows=0 -> builders 拿 0 候选 -> 三类全空。** 这是一条结构性死链，不是偶发漏报。

---

## 3. 方案：两个不依赖调用图的组件

根因里真正卡脖子的是两件事：handler 的 source 没被扫到（缺 SourcePoint），intra 算好的结果没路子落地成 TaintFlow（产 TaintFlow 绑死了 chain）。

核心洞察是：**intra 已经把同函数的 source -> sink 算好存进了 `local_steps`，source 也能由规则 / LLM 识别，差的只是①给这个函数补一个 SourcePoint，②一条不经 chain 直接产 TaintFlow 的路径。** 这两件事都不需要调用图。于是方案分两个组件，都绕开 chain / edge，专门覆盖同函数的 source -> sink（eval / ssrf 这类）。

### 3.1 source 补召回（`source_discovery_llm.py`）

照着已有的 `sink_discovery_llm.py` 依葫芦画瓢，给含 sink 的函数补 source：

- **规则路径**：对含 sink 函数的源码跑 source_rules（不再限 entry_point），命中就产正常 SourcePoint（`rule_id=ts-express-body` 之类）。
- **LLM 补召回**：规则没覆盖的非常规写法（比如解构 `const {a, b} = req.body`）丢给轻量 LLM 判一次，产「软 SourcePoint」（`rule_id="llm-discovered-source"`、`needs_review=True`、`entry_point_id` 锚到该函数 id），再写一份 `source_gap_report.json` 反哺规则库。
- **降级**：LLM 不可用就只走规则，解构写法漏掉但不报错、不阻塞。

这里有个关键设计取舍：**`source_detector` 主路径不动**，仍然只扫 entry_point。原因是要守住「source 不被 sink 驱动」这条不变量（source 识别本该独立于 sink 存在）。补召回是独立兜底模块，只对含 sink 函数补，被 sink 驱动但只做兜底，不污染主路径。

### 3.2 intra-first TaintFlow（`produce_intra_first_taint_flows`）

在 `chain_propagator.py` 里加一条不经 chain 的产 flow 路径。对每个含 sink 的函数：取 `intra_results` 的 `local_steps` + `tainted_params`，把原本只在 chain 入口（`i==0`）调的 `_source_points_matching` 推广到 sink 所在函数——匹配到 SourcePoint 就直接产一条单步 intra TaintFlow。intra 已经算好的 `local_steps` 直接当 `propagation_steps`，不用重新追。

几个细节：
- **合并去重**：`merge_taint_flows` 按 `(entry_point_id, source_param, sink.id)` 去重，intra-first 优先（同函数的是超集），`propagate_backward` 补跨函数的；同函数场景两者重叠，chain 产的那条去重掉。
- **needs_review**：source 是 `llm-discovered-source` 的 flow 标 `needs_review=True`，跟软 SinkCallSite 一致，交给下游 `chain_verdict` 复核。
- **sink_slot 透传**：`dangerous_slots[0]` 的 slot / arg_index 透传到 TaintFlow，防 `_route_for` 把 inj / ssrf 路由拒掉（跟 backward 同契约）。

### 3.3 铁律与边界

- **双轨铁律 hold**：source 补召回是 GitNexus 轨内部（确定性层）的事，产物只进 `parameter_graph`，**不喂 LLM 轨 prompt**（CLAUDE.md §1）。
- **只做 A，不做 B**：A（source 补召回 + intra-first）覆盖同函数 source -> sink。B（handler 进调用图 / 重建调用图边）单独立项——深查发现 `edges=0`，调用图根本没建跨函数边，chains 是 GitNexus MCP 从路由推导的，B 要重建调用图边，是独立大工程。A 不依赖 B。
- **跨函数漏洞的残留盲区**：handler -> dao 这类 source 在 handler、sink 在 dao 的跨函数链，若 handler 不在 chain 仍会漏（要等 B）。但 NodeGoat 的 SQL 链 chain 已带 dao + source 补到了 allocations 解构，A 能覆盖。

---

## 4. 实测效果

### 测试层面：已验

`test_source_discovery_llm.py` + `test_intra_first_taint_flow.py` 共 **43 个用例全绿**（本会话实跑确认）。覆盖：规则点号匹配、LLM 补解构、LLM 不可用降级、`source_gap_report` 产出、文件级聚合、IDOR flavor（`req.getParameter`）等。

最有分量的是端到端锚点 **`test_pipeline_intra_first_rescues_handler_not_in_entry_point`**：它复刻了 NodeGoat 场景——写一个 `app.js`，handler 里 `eval(req.body.preTax)`，patch 掉 `detect_entry_points` 让它返回 `[]`（模拟 handler 不入 entry_point），再传 `llm_client=None`（模拟关 LLM 轨，走确定性 fallback）。断言两件事：source 补召回补回了 `req.body.preTax`，intra-first 产出了 TaintFlow（从 0 到 >0）。这条锚点直接对住了 §2 的整条根因链。

双轨铁律锚点 `test_static_dataflow_hints_decoupling` 不破。

### 真机层面：待验

⏳ pipeline 测试用的是最小 fixture，不是真实 NodeGoat 全量扫描。**真机 NodeGoat 重扫验 `parameter_graph.taint_flows` 实际 0->N、inject/xss/ssrf queue 非空**，是下一步。这跟主文档的口径一致——不把「单测绿」当「真机已验」。

---

## 5. 小结

W5 修的是一类结构性全空：框架路由注册写法（`app.get('/path', handler)`）下 handler 不被识别为入口，连带 source 漏扫、intra 结果丢弃，整类 GitNexus 轨归零。方案没有去硬啃调用图重建（那是 B），而是绕开调用图——补 source + 直接产同函数 intra TaintFlow，把「靠调用图串链」的依赖降级成「有最好、没有也能兜」。测试层面 43 绿、端到端锚点验证了 0->N；真机全量验证待跑。

> 排查过程与 spec 见 [`../../superpowers/specs/2026-07-10-source-recall-intra-first-taint-design.md`](../superpowers/specs/2026-07-10-source-recall-intra-first-taint-design.md)。
