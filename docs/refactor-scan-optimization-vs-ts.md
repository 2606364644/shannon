# supernova-py 重构版相比原始 TS 版的安全扫描效果优化

> 对比对象：`/root/shannon`（TypeScript 原始版，下称 **TS 版**） vs `/root/shannon-py`（Python 重构版，下称 **PY 版**，分支 `feat/fork-py`）。
> 范围：**白盒 / 黑盒安全扫描的检测效果**（召回、准确率、覆盖面、产物可用性），不含纯工程重构。
> 写法：每条优化按「踩到什么坑 → 怎么改的 → 验证有没有用」组织，不灌水。

---

## 0. 一句话背景：TS 版是单轨纯 LLM

TS 版的检测链路从头到尾**只有一条腿**：每个漏洞类（injection / xss / ssrf / auth / authz）各跑一个 LLM agent，agent 自己读 recon、自己 grep、自己派 Task 子代理追 source→sink 链，自己下 verdict。没有代码索引、没有 sink/source 规则、没有 taint 图、没有 AST、没有任何确定性兜底（全仓 grep `taint|sink_rule|parameter_graph|code_index` 在 `apps/worker/src` 零命中确定性引擎代码）。召回全押在「这一个 agent 这一轮有没有盯到」上。

这意味着两个先天弱点：**单点召回**（agent 漏了就彻底漏了，没有第二来源交叉验证）和**单仓视角**（微服务架构下跨服务数据流、跨仓信任边界完全盲区）。

PY 版的优化主线就是围绕这两点展开的。

---

## 1. 双轨架构：给纯 LLM 单轨加一条确定性兜底腿（verdict OR）

### 踩到的坑
TS 版召回 100% 押在一个 vuln agent 上。agent 受上下文窗口、注意力、单轮 token 上限（CLI 64K output、单次 Write 32K）和超时影响，漏报是常态而非例外——而且漏了你不知道漏了，因为没有第二个独立来源对账。LLM 一旦在某一类上整段翻车（超时、spending cap 触发、模型走神），这一类就直接归零。

### 怎么改的
引入 **GitNexus 确定性轨**，和原 LLM 轨**各自独立、只在合并器交汇**：

- **LLM 轨**：保持 TS 版原样，`vuln-*.txt` agent 纯 LLM 自给自足（读 recon + 自己 grep + 自己追链），产 `<vuln>_exploitation_queue.json`。
- **GitNexus 轨**：代码索引（`build_code_index_with_gitnexus`）→ sink/source 规则 + taint 传播图（`parameter_graph.json`）→ `vuln_chain_builders/*_builder.py` 提候选链 → `chain_verdict.py` 跑**轻量 LLM 单次判定**（`run_claude_prompt` 结构化输出，非 agent）→ 产 `<vuln>_gitnexus_queue.json`。
- **合并器** `dual_track_merger.py::merge_dual_track_queues`：按 `(vuln_type, location, sink)` 去重，**verdict 取并集 OR**——任一轨判 vulnerable 即最终 vulnerable。两轨都报 = `merge_source="both"` / `confidence="high"`；单轨报 = `needs_review`。

铁律：两条腿**链来源不同**（LLM 轨自主探索 vs GitNexus 确定性产链），互不喂数据，所以 OR 才成立——确定性轨挂了 LLM 轨照跑，反之亦然。

### 验证有没有用
- `chain_verdict.py` 头部明确定位 GitNexus 轨是 **cross-validation / blind-spot fill，不约束 LLM 轨自由分析**。
- `merge_dual_track_queues` 的 OR 语义经单测锁定（`_is_vulnerable(llm) or _is_vulnerable(gitnexus)`，`dual_track_merger.py:100`）。
- 真机收益见下文各子项（authz 0→21、NodeGoat 三类 0→N）——这些本来 LLM 轨要么漏要么整段超时归零，是确定性轨兜回来的。
- **诚实标注**：双轨的战略价值是「漏报对账 + 任一轨挂了不归零」，代价是 token 翻倍（初期明确不省 token 换少漏）。可配置开关 `SUPERNOVA_LLM_TRACK_ENABLED` 支持关 LLM 轨走纯确定性兜底（token 紧张档）。

---

## 2. 确定性轨的召回增强：让新加的这条腿真正能下脚

双轨架构落地后立刻暴露一个事实：**GitNexus 轨不是装上就灵**。确定性层自己的规则不全、taint 追链会断、大仓会超时——每一类都让这条新腿瘸掉。下面是踩过的坑和补法，每条都带验证。

### 2.1 sink 硬规则补齐 + LLM 补召回（rule_id="llm-discovered"）

**坑**：`data/sink_rules.yml` 早期规则覆盖稀疏。典型翻车——Java 的 `receiver_pattern` 写成 `null`（精确匹配 receiver 为空），导致 `em.createNativeQuery(...)`、`httpClient.execute(request)` 这类带 receiver 的调用**全部漏检**；fastjson `parseObject`、Jackson `readValue`、Spring `resttemplate` 等 sink 压根没规则。确定性轨 sink 抓不到 → taint 链建不起来 → 整类归零。

**改法**：
- 硬规则补齐（`sink_rules.yml`，现 74 条）：Java SQL/deser/SSRF/Cmd/Redirect 各类 `receiver_pattern` 从 `null` 放宽到 `.+` 并补 `needs_review_default=true`；新增 fastjson `parseObject`/`parseArray`、Jackson `readValue`、`httpclient-send`/`postforentity`/`url-openconnection` 等。多语言侧 PHP `$` 前缀 lstrip、Go `db-query`、TS `child_process` 一并修。
- **LLM 补召回**（`sink_discovery_llm.py` + `data/sink_candidates.yml`）：规则没命中的可疑 call（候选模式表按语言+receiver 精确筛选，替掉旧版 flat 子串正则）丢给轻量 LLM 判一次是不是 sink，产 `rule_id="llm-discovered"` 的**软 SinkCallSite**（与规则 sink 同流、可区分、`needs_review=True`）+ `rule_gap_report.json` 反哺规则库。LLM 不可用就退回纯规则 + `is_entry_hint`，不浪费。

**验证**：`test_sink_detector.py` + `test_rule_loader.py` 74 测试绿；`ts-orm-model-query` 规则对 `dbConfig.trip.query` 整链 receiver 命中实测通过。真机重扫 sentinel_dashboard 验 `code_index.json` 的 `sink_call_sites` 从空壳变硬规则 sink 待跑。

### 2.2 source 补召回 + intra-first taint（修 NodeGoat 三类全空）

**坑**：NodeGoat（Express/JS）扫描 injection/xss/ssrf **三类 GitNexus 轨全空**（`taint_flows=0`）。根因是 `detect_entry_points` 把路由归到注册处 `index.js`，handler 不在 entry_point 里 → `source_detector` 漏扫 handler 内的 source（`eval(req.body.preTax)` 这类）→ `source_points=1` → handler 不进调用链 → `propagate_backward` 丢弃 intra 结果 → 0 flow。

**改法**（spec 2026-07-10 A 部分）：
- `source_discovery_llm.py`：`discover_sources_by_rules` 对**含 sink 的函数**跑 source 规则（不再限 entry_point），`discover_sources_llm` 产软 `SourcePoint`（`rule_id="llm-discovered-source"`）；source_detector 主路径不动（守「source 不被 sink 驱动」不变量）。
- `chain_propagator.py::produce_intra_first_taint_flows`：对含 sink 函数直接产**单步 intra TaintFlow**（不经 chain），`merge_taint_flows` 按 `(entry, param, sink)` 去重、intra-first 优先。

**验证**：`test_source_discovery_llm`(13) + `test_intra_first_taint_flow`(7) = 20 绿；pipeline 锚点 `test_pipeline_intra_first_rescues_handler_not_in_entry_point` 绿。真机 NodeGoat 冒烟验 `parameter_graph.taint_flows` 0→N 待跑。

### 2.3 调用链下沉到 GitNexus 原生 process trace（authz 0→21）

**坑**：GitNexus 轨调用链来源原来是「全量 cypher 查询 + Python BFS 拼边」（`_build_chains_from_edges`）。这条路上 cypher `LIMIT 5000` 撞 readline 64KB 崩、Python BFS 拼出的链经常空壳。authz 轨尤其惨——`statement_template_svr` 实测 **authz 候选 0 条**，链空导致 IDOR 路径完全建不起来。

**改法**（spec 2026-06-30）：调用链来源从自造 BFS 下沉到 GitNexus 原生 **process trace resource**：
- `gitnexus_call_graph.py::build_call_graph_from_gitnexus` 重写，新 `process_trace_reader.py` + `impact_supplement.py` 读 process trace，删 Python BFS。
- entry 组装 `detect ∪ process`（新 `entry_type="gitnexus_process"`）。
- authz 的 `find_unguarded_sink_paths` 四处改：entry 过滤含 process entry、放宽 route 守卫、sink 扫全链替 terminal、ownership 扫 entry→sink 段。

**验证**：89 测试绿。**真机硬收益：authz 候选 0→21**（statement_template_svr，process trace 下沉后实测），chains 不再空壳，readline 不崩。这是确定性轨兜底价值最硬的一笔证据。

### 2.4 SSRF taint 断链 + 超时跳过 fallback（修 sentinel_dashboard SSRF=0）

**坑**：sentinel_dashboard 扫描 **SSRF 一条 flow 都建不起来**（inj 仅 4 条 fastjson deser）。source_points=269、sink_call_sites=51（含 11 个 ssrf）、调用图 chain 也连上了，但 flow 没建。深查两层根因：
1. `sink_detector` 提取 `dangerous_slots[0].expression` 是**浅提取**——对 `httpClient.execute(request)` 这种「sink 参数是局部变量对象」写法，提取出 `"request"`（HttpGet 局部对象）而非回溯到背后的 `ip`/`pprofPort`。backward 反向传播时 `"request"` 不是 callee 参数、映射不出，到 entry 锚定 source 失败 → flow 丢弃。
2. `proxyPprofRequest` 等函数 >60s 超时被 `map_llm_with_bounds` 跳过（events.ndjson 实证 `timed out, skipped`），**直接不进 intra_results**——不是断链，是整段丢。

**改法**：
- P0 `code_index/__init__.py::backfill_skipped_taint_fallback`：超时/异常跳过的 sink 函数调 `_deterministic_intra_fallback` 产兜底 IntraResult（全参 tainted + hits=0.5）填回 `intra_results`，不丢弃。兑现「LLM 不可用档不浪费」。
- P1 `llm_taint_analyzer.py`：`parse_llm_response` 失败返回 None（区分合法空响应 `{}`）+ WARNING，`analyze_taint_llm` 检测 None 走兜底。

**验证**：`test_taint_timeout_fallback.py`(6) + 回归 208 绿。真机重扫验 ssrf>0 待跑。**影响面**：「sink 参数是局部变量对象」的跨函数 taint 链此前全丢——SSRF（httpClient.execute / OkHttpClient）、部分 SQL/命令注入（参数经 String.format 构造后传入）都受影响。

### 2.5 二阶存储中转双轨（stored XSS / 二阶 SQLi 系统性漏报）

**坑**：同服务二阶漏洞——write 端做了净化但 read 端信任存储数据直接进 sink——双轨都系统性漏。GitNexus 轨的规则层和 `chain_propagator` **没有「存储中转」概念**（source_rules 无 storage-read / sink_rules 无 storage-write / propagator 只连单跳）；LLM 轨 `vuln-*.txt` 有 cross-service sink 规则但无系统「存储 write/read 二阶追链」方法论。

**改法**（spec 2026-07-21，code-index 子项⑤）：
- 三抽象：`StorageWritePoint`（**非危险 sink**，不进 `sink_call_sites` 避免单跳轨误报 DB 写入）/ `StorageReadPoint`（新 source flavor=storage，进 `source_points`）/ `StorageNode`（`(medium, token)` join 枢纽）。
- `second_order_join.py` + `second_order_builder.py`：write 端 tainted ∧ read 端单跳 vulnerable = 二阶 verdict。read 端复用单跳 `chain_verdict`（`chain_propagator` backward 已支持按 source 风味锚定）。
- 双轨：GitNexus ⑤ 字面量 token（db/config/cache/file，关轨兜底）+ LLM 轨二阶方法论（开轨增强），verdict OR。

**验证**：8 task TDD 全完成，24/24 测试绿；集成 gap 修了（`extract_candidate_chains` 按 sink 路由不看 source_type 致 STORAGE 链被单 hop + 二阶 builder 双重 emit，commit `2bbd2947` 加 `source_type != STORAGE` 过滤）。真机 sentinel_dashboard 关轨重扫验 2ND-GN 非空待跑。

---

## 3. authz 确定性深度 agent 轨（IDOR 候选 + 多轮深判）

### 踩到的坑
authz 不是 source→sink taint，属 missing-control，确定性 sink 规则不覆盖。TS 版 authz 纯靠 LLM agent 一个脑袋，horizontal IDOR 全靠 agent 自己从 recon §8 预候选一个一个追代码。漏了就漏了，没有兜底。

### 怎么改的
authz 加了自己的「GitNexus 风格」轨（`authz_gitnexus_track.py` + `authz_gitnexus_judge.txt` / `authz_gitnexus_explore.txt`）：
- **候选>0 分支**：吃 GitNexus IDOR 候选（endpoint + Object ID 参数），`run_gitnexus_verdict_agent` **多轮深度 agent 判定**（owner 检查、guard 分析）——注意这是**深度 agent**不是轻量单次判定，因为 authz 要追 owner 逻辑、guard 顺序，比 taint 链判定重。
- **候选=0 分支**：agent 自主探索 IDOR（从 process trace entry 出发找未守卫的 sink 路径）。
- 候选来源扩展到 OpenAPI / 框架注解（spec-1b）。

### 验证有没有用
- process trace 下沉后 **authz 0→21**（见 2.3），就是这条轨跑出来的。
- 修了一个致命的静默丢数据 bug：authz 探索分支 agent 产了 4 个 IDOR 候选（LLM turn 明说「34 条路由发现 4 个 IDOR 候选」），但 `authz_gitnexus_queue.json` 落地 **0 条**——根因是探索 prompt schema 没 ID 字段，`parse_lenient` 把缺必填 ID 的条目全丢了且不报。修法：`_parse_gitnexus_verdict_output` parse 前补序列化 ID + 读 `warnings` 打日志。`test_run_authz_gitnexus_judge.py` 6 passed 2 xfailed。**真机 hr_20260713 实测 4→0 → 修复后不再丢**。

---

## 4. 跨仓微服务关联（多仓拓扑 + 信任边界 + 跨服务数据流）

### 踩到的坑
TS 版**单仓扫描**——`PipelineInput.repoPath` 是单一绝对路径，全仓 grep `cross.repo|multi.repo` 零命中。微服务架构下，A 服务把用户输入写消息队列、B 服务消费后直接拼 SQL，这种跨服务数据流和跨仓信任边界对 TS 版完全盲区。`vuln-authz.txt` 里甚至写了已知局限："Untraced Microservice Calls... could not be analyzed without their source code"。

### 怎么改的
`packages/multi/` 包：声明式 multi-repo 编排（`multi-repo.yaml`）+ **cross-repo-correlation Agent**（`cross-repo-correlation.txt`）推断服务拓扑 / 信任边界 / 跨服务候选数据流。关联 Agent 在编排器进程内跑（非 Temporal activity，规避 child-workflow 负担），per-edge 推断用 `asyncio.Semaphore(3)` 并发 + 单边 try/except 隔离。黑盒侧 `--correlated-workspace` flag 穿透 4 层，加载 topology 做 gateway 层验证。

### 验证有没有用
cross-repo 全套 34 测试绿（orchestrator / correlation / blackbox_reuse / blackbox_flag），final review Ready-to-merge。真机 multi-repo 冒烟待跑。**这是覆盖面的硬扩展**——TS 版连多仓输入都不支持，谈不上跨服务召回。

---

## 5. PoC 产物化（curl + Burp raw，结构化交付）

### 踩到的坑
TS 版的 PoC 散在 exploit 阶段的自由格式 `*_exploitation_evidence.md` 里（exploit prompt 里给 curl 示例），**没有结构化 Burp 产物**，`externally_exploitable` 是 LLM 自填的可达性标签但下游没专门消费它产可复用 PoC。交付给甲方的「拿来就能打」的 PoC 要人工从 markdown 里抠。

### 怎么改的
`services/poc_generator.py`：报告生成后，针对 `externally_exploitable==True` 的漏洞**纯后处理**产 curl + Burp raw PoC md（黑白盒各一份），不动判定链路：
- 混合生成：inj/xss/ssrf 有 `witness_payload` → 纯模板；auth/authz + body/path 缺口 → 富信息 LLM 单次结构化生成（优先发挥模型分析能力，不脱敏）。
- 置信度三档：`verdict=="vulnerable"` 或黑盒 ∈ accepted_ids → ✓已确认（优先于 confidence）；`confidence=="high"` → ●高置信；其余 → ⚠疑似。
- 真实 token 不持久化（PoC 用 `<AUTH_TOKEN>` 占位符），host 优先 `web_url` 退 `TARGET[:PORT]`。

### 验证有没有用
`scripts/generate_poc.py` 对 invite_code_center 历史 session **实测产出 11 个 curl PoC**（XSS×2 / AUTHZ×4 / SSRF×1 + 变体）。`test_poc_generator.py` 测试套件落地。白盒/黑盒 workflow 端到端冒烟待跑。**产物质量直接拉满**——从「markdown 里抠 curl」到「一键产出可复用 curl + Burp raw 包」。

---

## 6. 稳定性优化：让确定性轨在大仓上能跑完（间接保召回）

这一组不直接提召回，但解决一个致命问题：**确定性轨在大仓上整段超时失败 = 这条腿直接归零 = 双轨退化成 TS 单轨**。所以是召回的前提。

### 6.1 chunk token 阈值按模型 context 自适应

**坑**：`CHUNK_TOKEN_THRESHOLD=12_000` 硬编码对 glm-5.2（1M context）只占 1.2%，文件级聚合（569 函数→259 chunk）只砍 55% 没榨干；且 `_estimate_tokens=len//4` 对中文注释严重低估 4-8x，违背「防 context 爆」初衷。真机 kol_mapping_service sink-discovery 259 chunk 撞 20min activity timeout 反复重试失败。

**改法**：`model_caps.py` 配模型真实 context（glm-5.2=1M / 默认 128K），threshold 自动 = context × 0.75；token 估算改 CJK 加权（`cjk×1.5 + 其余/4`）；discovery 经 `resolve_tier_model(config, "medium")` 传参，不裸读 `SHANNON_MODEL`。

**验证**：7 task TDD 全实现，97 测试绿。预期 kol 259 chunk → 个位数~十几个，调用次数降 ~30x 不再撞 timeout。真机冒烟待跑。

### 6.2 sink/source 补召回 per-function → 文件级聚合

**坑**：sink-discovery 对 569 个含可疑 call 的函数 per-function 粒度 × 并发 3 → ~95min 累加，撞 activity `start_to_close_timeout` 10min → cancel → 重试 gRPC error → 扫描失败。

**改法**：`llm_concurrency.py::chunk_items_by_file` 按 file_path 分组 + token 贪心装箱（同 block 不拆散），sink/source 共用；`per_call_timeout` 默认 120s；`run_code_index` activity timeout 10→20min（后因 Koa 治本又调 45min）。

**验证**：`test_llm_chunking.py`(9) + sink/source discovery 文件级新测试全绿。真机 kol 冒烟待跑。

> **Koa + Sequelize 后端治本**（trip_1784270506）：同类稳定性问题的延伸——source_rules 对 TS 只有 Express `req.*` 无 Koa `ctx.*`（141 controller 里 104 用 ctx.*、0 用 req.*），sink_rules 漏 `Trip.query`/`dbConfig.trip.query`，discovery 各 120s timeout 串行累加撞 20min 墙。治本：source 加 5 条 Koa 规则、sink 加 `ts-orm-model-query`、候选层扩 Koa 解构、timeout 推荐 360s。source Koa 3 + sink 3 + discovery 5 新用例全绿，真机端到端待跑。

---

## 7. 黑盒：endpoint_verify agent + exploitation-only（对齐 TS 基线 + 加端点活体验证）

### 踩到的坑 / 怎么改的
TS 版**没有独立黑盒**——只有白盒 + exploitation（白盒发现后的利用验证）。PY 版黑盒做成 exploitation-only（不独立发现漏洞，强制复用白盒 queue，对齐 TS 黑盒基线），并在 exploitation 前插一个 **`endpoint_verify_executor.py` + `blackbox-endpoint-verify.txt`** agent：LLM 验证端点可达性 + 路由前缀探测，产 `blackbox/endpoint_verify.json`，失败降级零回归，结果经 `_endpoint-verify-hint` 衔接 exploit。

### 验证有没有用
`test_endpoint_verify.py` 13 测试 + 全量 55 绿。**诚实说**：黑盒这块两边实际安全检测效果基本相当（都 exploitation-only），PY 唯一比 TS 多的是 TS 黑盒那个 `checkAuthzCoverage` 确定性对账兜底（recon 端点全集 − queue − safe_vectors = 漏判端点，纯数据减法写进报告标「authz 未测请人工复核」），PY 黑盒只有 queue 内部 coverage_renderer（universe=queue 非真实攻击面）。这道兜底是 advisory（提漏检可见性/合规可审计性，不提召回），优先级中低。

---

## 8. 总结：哪些是真能打的优化，哪些是诚实边界

**真能打的（有真机数据 / 架构级新增）：**
| 优化 | 类型 | 硬证据 |
|------|------|--------|
| 双轨 verdict OR（确定性兜底腿） | 召回对账 + 鲁棒性 | 架构新增，TS 无第二条腿 |
| process trace 下沉 | 召回 | **authz 0→21**（真机 statement_template_svr） |
| source 补召回 + intra-first taint | 召回 | NodeGoat 三类 0→N（pipeline 测试锚点，真机待跑） |
| authz 深度 agent 轨 + ID 丢弃修复 | 召回 | **hr_20260713 4→0 修复**（真机） |
| 跨仓微服务关联 | 覆盖面 | TS 单仓，PY 多仓拓扑+信任边界 |
| PoC 产物化（curl+Burp） | 产物质量 | **11 个 curl PoC 真机产出** |
| SSRF taint 断链 fallback | 召回 | sentinel_dashboard SSRF=0 根因定位+修（真机待重扫） |

**诚实边界：**
- sink 规则补齐、二阶存储双轨、chunk threshold、文件级聚合、Koa/SSRF 治本——**测试全绿但真机冒烟多数待人工跑**。文档不把「测试绿」当「真机已验」。
- 黑盒安全效果两边基本相当，PY 黑盒 authz 对账兜底反而比 TS 少一道（advisory，非召回下降）。
- 双轨代价是 token 翻倍（初期明确不省 token 换少漏）；可配关 LLM 轨走纯确定性兜底。
- 双引擎（claude-agent-sdk / openai-agents）是工程能力对齐，**不算扫描效果优化**，本文不展开。

> 一句话：PY 版相比 TS 版的核心增量是**给单轨纯 LLM 加了一条确定性兜底腿并把它真正跑通**（authz 0→21、NodeGoat 0→N、SSRF 断链修复是硬证据），外加**跨仓视角**和**结构化 PoC 产物**两个 TS 版完全没有的能力。确定性轨的多数召回增强目前是「测试绿 + 真机冒烟待跑」状态，真机全量验证是下一步。
