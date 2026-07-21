# code_index 确定性产物层重构设计:全风味 + 解耦 + 多轨复用

> 状态:design(待 plan)。分支 `feat/fork-py`。
> 起源:2026-07-21 会话,关轨模式扫 `sentinel_dashboard` 只出 auth/authz、inj/xss/ssrf 全空的根因深挖。

---

## 1. 背景与根因

### 1.1 现象

关轨模式(`SHANNON_LLM_TRACK_ENABLED=0`,见 `.env:30`)扫 `repos/frontend/sentinel_dashboard`(Spring Boot Java),结果只有 auth(8)+ authz(17),**inj/xss/ssrf 三类全空**。原始 TS 版同仓扫出 10 inj + 10 ssrf(+ 8 auth + 6 authz)。

关轨的设计意图(CLAUDE.md §1):inj/xss/ssrf 的 LLM vuln agent 被跳过,**由 GitNexus chain_verdict 主干兜底**。本 spec 回答:兜底为什么落空,以及怎么治本。

### 1.2 根因链(sentinel_dashboard 实测 `code_index.json`)

| 字段 | 值 | 含义 |
|---|---|---|
| `language` / `language_coverage` | `java` / `["java"]` | 项目正确识别,Java 未被跳过 |
| `source_points` | **195** | 全硬规则命中(`@RequestParam` 122 / `@PathVariable` 32 / `@RequestBody` 41) |
| `sink_call_sites` | **3** | 全是 `llm-sink-hunter`,且 `callee_name`/`callee_receiver`/`dangerous_slots` **全空**(2 个落在 `application.properties` 配置文件) |
| `edges` | **0** | 无 source→sink 连边 |
| `parameter_graph.taint_flows` | `[]` | taint 流为空 |

→ `vuln_chain_builders/{injection,xss,ssrf}_builder` 基于空 taint_flows 提不出候选链 → `run_gitnexus_chain_verdict` 的 `per_class` 为空 → 不写 `<vuln>_gitnexus_queue.json` → `gitnexus_track_status.json` 里这三类无条目(既不 ok 也不 failed,直接跳过)→ merger 读不到 → 关轨无 LLM 兜底 → 三类全空。

硬规则 sink 命中 **0 个**(8 条 Java 规则 `executeQuery`/`createNativeQuery`/`Runtime.exec`/`readObject`/`resttemplate` 对本仓非典型持久层——pom.xml 无 mybatis/jpa/jdbc,代码 0 个 `JdbcTemplate`/`QueryWrapper`/`@Select`)。

### 1.3 三层根因

**R1 — sink 补召回被实现成「判定器」,而非对称 source 侧的「探测器」**

- source 侧(`source_discovery_llm.py:147` prompt):「Given a FILE with entry handler functions, **identify ALL** user-controllable input fields」——给 LLM **函数源码**,自由识别。**探测器**。
- sink 侧(`sink_discovery_llm.py:179` prompt):「judge whether **each suspicious call** is a real security sink」——给 LLM **候选 call 列表**,逐个判 yes/no。**判定器**。候选来自 `collect_suspicious_calls:153` 的 `_matches_candidate`(候选表 receiver/callee 精确匹配)。

后果:`JSON.parseObject`(fastjson)/ `ClassPathResource.createRelative`(Spring)/ `SentinelApiClient.executeCommand`(自研)不在 `sink_candidates.yml` → 进不了判定 LLM → 漏。**source/sink 不对称,sink 侧退化成判定器是 inj/xss/ssrf 全空的直接根因。**

**R2 — source/sink 串行耦合**

`__init__.py` 编排:`detect_sinks(104) → discover_sinks_llm(199) → detect_sources(296) → discover_sources_llm(316)`。`collect_source_candidates:132` 用 `target_ids = sink_func_ids | entry_point_ids`——`sink_func_ids` 是个**边际剪枝**(让 source 探测器多看含 sink 的函数,省 LLM 调用),非正确性依赖。代价:**sink 失明连累 source 补召回范围**。

**R3 — source 注入风味偏科**

`SourcePoint` 只识别「注入风味」源(`@RequestBody`/`@RequestParam`,流到 SQL/SSRF 的)。IDOR 风味源(`req.params.userId`、对象级 id 取用)**识别不出来**,`authz_gitnexus_track.py:295-296` 注释明文:「IDOR 风味源不被注入风味的 SourcePoint 检测识别,这里补认」。补认靠 re-anchor + grep,本场没命中。

后果:`authz_gitnexus_track.py:208` 门控 `if not ep_sources: ... return`——entry 没有 SourcePoint 就不产 IDOR 候选 → `authz_gitnexus_queue.json` 这场 = `{"vulnerabilities": []}`(27B)→ **source 喂了 authz GitNexus 轨但没 work**(这场 17 个 authz 全来自 LLM 轨 `authz_llm_queue.json`,GitNexus 轨空)。source 这场几乎没产生任何价值。

### 1.4 决定性反例

原始版 **INJ-01** 的 sink = `ClusterConfigController.java:76` 的 `JSON.parseObject(payload)`(fastjson autotype,RCE 级)。重构版 `llm-sink-hunter` 这场产出的第 3 个空壳 sink **正是** `ClusterConfigController.java:76`(`category=ssrf`,但 `callee_name=""`)。

LLM 摸到了正确的文件+行号+甚至类别,但判定器管道(parser 没切出 `JSON.parseObject` fastjson 静态调用 / 候选表没匹配)让它只能产出空壳。空壳连不出边 → 进不了 `taint_flows` → 链断在这里。**这是 R1 最直观的标本。**

---

## 2. 目标

让 code_index 确定性层的 source/sink 产物成为「**全风味 + 解耦 + 多轨复用**」的资产,使关轨模式下 GitNexus 兜底不再灾难性归零:

1. **解耦**:sink 识别失明不再连累 source(R2)。
2. **全风味**:source 覆盖 IDOR 风味,不只注入(R3)。
3. **sink 探测器化**:对称 source,能自由发现框架/自研 sink(R1)。
4. **多轨复用**:source/sink 产物被 taint(连边)+ authz GitNexus 轨(IDOR)消费,不白识别。

**非目标**:不追求「关轨追平原始 TS 版全覆盖」。业务逻辑缺陷(SSRF-01 registry poisoning、`app=null` 绕过)与跨服务二阶(INJ-09)确定性层结构性覆盖不了,仍需开轨。关轨模式定位仍是「用漏报换 token」,本 spec 只把漏报从「灾难性归零」降到「可控」。

---

## 3. 架构:四子项(可独立交付)

### 3.1 子项① — source/sink 并行化(解耦 R2)

- 去掉 `collect_source_candidates` 的 `sink_func_ids` 剪枝,source 探测器只基于 `entry_point_ids`(主路径 `detect_sources` 本就如此,非破坏性)。
- `__init__.py` 编排改为两段并行 + 最后连边:
  ```
  detect_sinks  ∥  detect_sources       # 硬规则,各自基于 entry/blocks
  discover_sinks_llm ∥ discover_sources_llm   # LLM,各自基于 entry
  → chain_propagator 连边(parameter_graph)   # 最后一步,不变
  ```
- 不变量:链边连接仍是 `chain_propagator` 的职责,语义不变;只是 source 不再等 sink。

### 3.2 子项② — source IDOR 风味扩展(治 R3)

- `source_discovery_llm._PROMPT_TMPL` + `collect_source_candidates` 的 `_SOURCE_CANDIDATE_HINT` 增补「对象级 id 取用」信号:`req.params.x` / `@PathVariable` 用作实体 id / 对象直传入 service。
- 产出的 `SourcePoint` 增 `flavor` 字段(或复用 `source_type` 加 `idor`),与注入风味源可区分。
- 收益:authz GitNexus 轨的 `ep_sources` 门控(line 208)不再因「无注入风味 SourcePoint」而空 → 直接缓解子项④。

### 3.3 子项③ — sink 探测器化(治 R1,对称 source)

- 新增 `discover_sinks_by_entry`,基于 `entry_point_ids`,给 LLM **entry handler 函数源码**,让它自由找 sink(对称 `source_discovery_llm` 的「identify ALL」模式)。
- prompt 骨架(对称 source):
  ```
  You are a security sink detector for the GitNexus track. Given entry handler
  functions, identify ALL security sinks WITHIN each function (deserialization /
  ssrf / sql / path-traversal / command / template). Rule-based detection already
  covered common sinks; you handle the unconventional ones (framework-specific
  static utility calls, reflection).
  Return JSON per sink: {"sink":"<call expr>","category":"...","line":<int>,
    "dangerous_arg":"<expr>","is_sink":true,"rationale":"..."}
  ```
- 产软 `SinkCallSite`(`rule_id="llm-discovered-sink"`,`needs_review=True`)→ 进 `sink_call_sites` → 走 `taint_flows` → `chain_verdict` 复核兜底误报。
- **与现有判定器路径并存**(不删 `collect_suspicious_calls` + `_matches_candidate` 那条),两条产出合并去重。
- 改动面:仅 `sink_discovery_llm.py`(加探测器)+ `__init__.py`(编排插入)。
- 守铁律:仍是「entry 约束 + needs_review 复核」的 GitNexus 轨补召回,**非全仓自由 LLM agent**(source 侧已是此模式,sink 侧对称只是补齐)。

### 3.4 子项④ — authz GitNexus 轨空产出排查(关联②)

- 高度可疑路径:`authz_gitnexus_track.py:208` `if not ep_sources` 门控 × R3 source 风味偏科。子项② 落地后 `ep_sources` 不再空,应直接缓解。
- 待深入(plan 阶段做):`build_authz_gitnexus_track` 主入口全门控审计;re-anchor 补认(`:295-296`)本场为何没命中 sentinel_dashboard 的 handler;候选阈值(`:116` `route_count >= 2 or hr_count_for_fb >= 3`)是否过严。
- 验收基线:子项②+④ 落地后,sentinel_dashboard 重扫 `authz_gitnexus_queue.json` 非空(GitNexus 轨在关轨模式产出 IDOR 兜底)。

---

## 4. 不变量 / 铁律边界

- **铁律(CLAUDE.md §1):确定性产物不喂 LLM 轨 prompt。** 本 spec 所有改动都在 GitNexus 轨(确定性层 + authz GitNexus 深度 agent),**不碰 LLM 轨 `vuln-*.txt` prompt**。source/sink 喂 authz GitNexus 轨 ✓(它本就吃 code_index);喂 auth LLM 轨 ✗(铁律禁止,曾有的 `auth_config_scanner` 即因此被删)。
- sink 探测器仍是「entry 约束 + needs_review + chain_verdict 复核」,守 GitNexus 轨「确定性兜底」定位,不变质为自由 LLM agent。
- `externally_exploitable`(可达性标签)不被 verdict 覆写(CLAUDE.md §1)。
- 守铁律测试 `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 必须保持绿。

---

## 5. 测试策略(TDD)

- **子项①**:sink 空时 source 仍全量识别(并行正确性);`chain_propagator` 连边语义不变(用现有 taint 测试集回归)。
- **子项②**:`req.params.userId` / `@PathVariable` id 风味被识别为 `SourcePoint`(新 fixture,NodeGoat A4 IDOR 场景参考)。
- **子项③**:`discover_sinks_by_entry` 在含 `JSON.parseObject` 的 entry handler 上产出软 sink;**`ClusterConfigController.java:76` 复现为验收标志**(反例闭环)。
- **子项④**:source 含 IDOR 风味后,`authz_gitnexus_track` 的 `ep_sources` 门控通过;`authz_gitnexus_queue.json` 非空。
- 守铁律:`test_static_dataflow_hints_decoupling.py` 绿;`test_workflow_gitnexus_failfast.py` 关轨 fail-fast 语义不破。

---

## 6. 不做(YAGNI)

- **宽版 sink 探测器**(跨函数调用链 BFS):复杂度逼近 `chain_propagator`+builder,token/行为逼近 LLM 轨,投入产出比不如直接开轨。跨函数追链是 chain_verdict 的活,不是 sink 探测器的活。
- **source 喂 auth LLM 轨**:铁律禁止。
- **重写整个 code_index**:只改产物层维度 + 编排,不动 parser/规则匹配内核。
- **硬规则大扩充**(补 fastjson/Spring sink 规则):规则枚举永远追不上 LLM 语义覆盖,且每换框架要补;子项③ 探测器化是更对的路径(硬规则只作为探测器的「已覆盖」快路径)。

---

## 7. 覆盖评估(对原始版 20 漏洞,诚实)

| 漏洞 | sink 位置 | 子项③ 窄版 | 宽版(不做) | 开轨 |
|---|---|:--:|:--:|:--:|
| INJ-01/02/03 fastjson | source+sink 同 entry 函数 | ✅ | ✅ | ✅ |
| INJ-10 PathTraversal | resource handler 内 | ⚠️ 可能 | ⚠️ | ✅ |
| INJ-04~08 / SSRF-02~10 SSRF | 跨 controller→service→client | ❌ | ✅ | ✅ |
| INJ-09 跨服务二阶 fastjson | 需先追通 SSRF 链 | ❌ | ❌ | ✅ |
| SSRF-01 registry poisoning / SSRF-05 `app=null` | **无 sink,业务逻辑缺陷** | ❌ | ❌ | ✅ |

- 子项③ 窄版:补 **3~4/20**(fastjson RCE 级,价值最高)。
- 子项②+④:让关轨模式 authz GitNexus 轨不再空(GitNexus 兜底生效)。
- **全 20 覆盖仍需开轨 `SHANNON_LLM_TRACK_ENABLED=1`**(业务逻辑缺陷 + 跨服务二阶是确定性层结构外)。

**结论**:本 spec 提升确定性层健壮性,**不替代开轨**。若要保原始版全覆盖,开轨是唯一解;本 spec 让关轨模式的漏报从「inj/xss/ssrf 灾难性归零 + authz GitNexus 轨空转」降到「单点 fastjson 能兜 + authz 兜底生效,仅跨链/业务逻辑类漏报」。

---

## 8. 关键文件清单

### 必改
- `packages/core/src/shannon_core/code_index/sink_discovery_llm.py` — 子项③ 探测器(`discover_sinks_by_entry` + entry-driven prompt)
- `packages/core/src/shannon_core/code_index/source_discovery_llm.py` — 子项② IDOR 风味(prompt + hint)
- `packages/core/src/shannon_core/code_index/source_detector.py` — 子项② hint 正则
- `packages/core/src/shannon_core/code_index/__init__.py` — 子项① 并行编排(line 104/199/296/316 重排)

### 必读(契约)
- `authz_gitnexus_track.py:208`(ep_sources 门控)/ `:262`(遍历 source_points)/ `:295-296`(IDOR 风味补认) — 子项④
- `data/sink_rules.yml` / `sink_candidates.yml` / `source_rules.yml` — 规则覆盖现状
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:417-425` — chain_verdict 编排(产出 `<vuln>_gitnexus_queue.json`)

### 对照参考(对称模板)
- `source_discovery_llm.py` 整体 — 子项③ 探测器照此对称实现
- `sink_merger.py:106-143` — 软 sink 合并 + `rule_id="llm-sink-hunter"` 标记逻辑

---

## 9. 后续

- 本 spec 为 design,完成后写 plan(拆 4 子项的 TDD task 序列,子项①/②/③ 可并行,④ 依赖②)。
- 真机验收:sentinel_dashboard 关轨重扫,对比 inj/xss/ssrf queue 与 authz_gitnexus_queue 非空情况;理想再跑 NodeGoat 回归(子项② 的 IDOR fixture 来源)。

---

## 10. 落地验收记录(2026-07-21,plan 全 8 task 实施完成)

### 10.1 单测回归(SDD 每 task TDD + Task 8 汇总)

| 测试套 | 结果 | 说明 |
|---|---|---|
| `test_sink_hunter_llm.py` | 5/5 ✓ | 子项③(收集 + 探测器 + fastjson INJ-01 复现) |
| `test_source_discovery_llm.py` | 36/36 ✓ | 子项② hint/prompt + 子项① 解耦(含 4 新测试) |
| `test_build_code_index_orchestration.py` | 3/3 ✓ | 子项③ 接入 + ① entry 解耦(顺序 + 数据流不变量) |
| `test_authz_gitnexus_track.py` | 7/7 ✓(新建) | 子项④ 回归锚点 + ownership 正则修复锁 |
| `test_sink_discovery_llm.py::test_soft_sink_does_not_break_injection_whitelist` | 1/1 ✓ | 子项③ 软 sink 不破 injection 白名单 |
| `test_workflow_gitnexus_failfast.py` | 14/14 ✓ | 关轨 fail-fast 语义不破 |
| `test_static_dataflow_hints_decoupling.py` | 6/7(1 预存失败) | 见 §10.2 |

### 10.2 预存失败(非本 plan 引入)

`test_static_dataflow_hints_decoupling.py::test_fusion_guarded_by_enable_llm_track` 在本 plan base `be8935b8` 即已失败(worktree 实测同一 `assert False` @ :139)。该测试是 workflows.py entry-point fusion 调用须在 `if input.enable_llm_track:` 守卫内的 AST 锁,被 in-flight 的 `146cf98f`/`fc6aa1de`(LLM 轨 authz/auth 重构 + fusion AST 收窄对齐"entry_point_fusion 不守卫设计")打破。

- 本 plan `git diff be8935b8 HEAD` 对 `workflows.py` 与该测试文件 = **空**(本 plan 8 文件全在 `code_index/`,未碰 fusion 门控)。
- 同文件其余 6 测试全绿——真正的铁律(CLAUDE.md §1:确定性产物不喂 LLM 轨 prompt)仍持守;红的是 fusion-gating 这条不同不变量。
- **超本 plan 范围,不修**:修需碰 workflows.py fusion gating,会撞在途重构(memory `recon-llm-track-gating-status.md` 注该区正重构 + .env 第 27 行注释矛盾待改)。留给那条在途工作流收尾。

### 10.3 子项④ 真 bug 修复(超出 plan 预设,调查落地)

plan Task 7 原假设 R1(sink 失明致 `_idor_reaches_sink` 断)。调查(controller 侦察 + opus implementer + sonnet reviewer 三方核验)推翻:`find_unguarded_sink_paths` 的 sink 识别走 `_SIDE_EFFECT_SINK_RE` 正则 on `index.chains` 路径 block,**完全不消费 `sink_call_sites`**——Task 2 hunter sinks(injection 轨)不直接喂 authz 轨。真根因是 `patterns.py::OWNERSHIP_PREDICATE_RE` alt5 过匹配 IDOR 风味源赋值(`const userId = req.params.userId`)误判 ownership,短路 `authz_gitnexus_track.py:300` 门。最小修复:RHS 收窄 `(req\.user|ctx|currentUser)`(要求 auth context),10 用例验证(3 FP 消除 + 6 TP 保留 + 1 negative)。

### 10.4 真机冒烟(待人工,Step 3)

关轨重扫 sentinel_dashboard 验收(需 GitNexus 已索引 + GLM 真机,subagent 不可靠代跑,留给用户):
```bash
# 改前基线(若留档):inj/xss/ssrf_gitnexus_queue.json 不存在/空;authz_gitnexus_queue.json {"vulnerabilities":[]}
SHANNON_LLM_TRACK_ENABLED=0 uv run shannon-whitebox start --repo /root/shannon-py/repos/frontend/sentinel_dashboard
# 验收点:改后 injection_gitnexus_queue.json 应含 ClusterConfigController(fastjson soft sink 进 taint);
#         authz_gitnexus_queue.json 应非空(ownership 正则修复后 IDOR 候选不再被误短路)
ls -la workspaces/sentinel_dashboard_*/deliverables/whitebox/*gitnexus_queue.json
```
预期:子项③ 补 3~4/20(fastjson RCE 级,spec §7);子项④ 让 authz GitNexus 轨产出 IDOR 兜底。**全 20 覆盖仍需开轨**(业务逻辑缺陷 + 跨服务二阶是确定性层结构外)。残余:patterns alt5 RHS 仍含裸 `ctx`,若真机现 Koa `ctx.request.body.userId` FP 再收窄。

### 10.5 最终 review 补丁:hunter sink slot 路由修复(C1,2026-07-21)

最终全分支 review(opus)发现 per-task review 全漏的 **Critical C1**:hunter prompt `_SINK_HUNTER_PROMPT_TMPL` 不向 LLM 索取 `slot` 字段 → `_to_hunter_sink` 恒 `SlotContext.GENERIC` → `extract_candidate_chains` 因 `generic∉_INJECTION_SLOTS`(chain_verdict.py:42-43)滤掉 → hunter sinks 进了 taint_flows 但**到不了 `<vuln>_gitnexus_queue.json`**,击穿子项③ 核心承诺。端到端复现:fastjson slot=GENERIC→0 chains,slot=deserialize→1 chain。

修复(commit 22140602):(A) hunter prompt 加 `slot` 字段(对称 judge prompt `_DISCOVERY_PROMPT_TMPL` 的 8 值枚举)+(B) `_CATEGORY_TO_SLOT` 模块映射 + 优先级(LLM 非 generic slot 优先;否则按 category 派生;否则 GENERIC)——LLM 漏 slot 时 deserialization→DESERIALIZE_OBJ("deserialize"∈_INJECTION_SLOTS)过路由。加 e2e 回归测试 `test_hunter_sink_routes_to_injection_queue`(`discover_sinks_by_entry→pgraph→extract_candidate_chains` 穿过不丢)。教训:Task 3 编排测试只断言"hunter 在 taint 前跑且喂 taint",未覆盖"hunter sink 经路由到 queue"全路径——故 C1 漏网。e2e 测试现已锁该不变量。
