# GitNexus 轨关轨兜底加固设计（NodeGoat 四断链修复）

> 日期：2026-08-21
> 分支：feat/pre-scan-local-agent
> 状态：设计待 review
> 触发：NodeGoat 靶场扫描（`NodeGoat-20260820-174548`，web `__legacy__` 组合扫描）在 `SUPERNOVA_LLM_TRACK_ENABLED=0` 关轨状态下 XSS/SSRF 0 检出、eval 注入 0 检出——GitNexus 轨"独立兜底"承诺在 JS 生态上不成立。
> 关联：CLAUDE.md §1 双轨铁律与关轨语义（2026-07-14 收窄：只关 inj/xss/ssrf）；intra-first 引入背景见 source-recall-intra-first 系列工作（memory 索引同名条目）

---

## 0. 一句话结论

关轨（`SUPERNOVA_LLM_TRACK_ENABLED=0`）把 inj/xss/ssrf 全押 GitNexus 确定性轨，而该轨管道在 NodeGoat 形态上有**四个独立断点**：①参数提取不含嵌套 arrow 的 `req`（intra 从源头问错问题，SSRF/eval 的 taint flow 全断）；②intra-first 双重门无回退（与 backward 的表达式回退不对称）；③`ts-eval` 规则 slot 配错 + 无服务端模板渲染 XSS 规则（eval 路由不进、XSS 整类为零）；④REDIRECT 有意过滤在关轨时变成静默丢弃（开轨假设破产）。本设计六个修复点逐洞补齐，目标是**关轨状态下 NodeGoat 四类真漏洞（SSJS eval 注入/SSRF/模板 XSS/Open Redirect）从 0 到 ≥1 检出，开轨双轨 dedup 不重复，双轨铁律零触碰**。

---

## 1. 背景：漏报因果链

### 1.1 事件

2026-08-20 NodeGoat 组合扫描（白盒+黑盒 run-1，deepseek-v4-flash，总成本 ¥0.81）：检出 inj 1 + auth 9 + authz 4，**XSS/SSRF/SSJS 注入/Open Redirect 全部 0**。authz/auth 走各自轨道（IDOR 候选+深度判定 / 纯 LLM 且关轨语义保留）未受影响。

### 1.2 GitNexus 轨检出管道（四关）

```
第①关 sink：tree-sitter 切函数块 → sink 规则匹配（+LLM sink-hunter 补软 sink）
第②关 flow：taint flow 两条路——
    backward：沿 GitNexus call graph chain 反向（intra 空时有表达式回退）
    intra-first：函数内直接 source→sink（双重门：intra 非空 + sink 命中，无回退）
第③关 路由：extract_candidate_chains 按 slot/类别路由进 builder
    （injection 按 slot∈_INJECTION_SLOTS；ssrf 按 slot=url；xss 按 category==XSS）
第④关 判定：chain_verdict 轻量 LLM → <vuln>_gitnexus_queue.json → merger
```

### 1.3 四个断点（均有当次产物证据）

| # | 断点 | 证据 |
|---|---|---|
| 1 | **参数提取粒度错位**：parser 只提顶层签名参数。NodeGoat 构造函数形态 `function Handler(db) { this.display = (req,res) => {...} }` 下 `block.parameters=['db']`，req/res 缺失 → intra LLM 被问"db 能否到 `eval(req.body.preTax)`"→ 空判定是**对错误问题的正确回答**（46s 耗时证明 LLM 真被调用；非模型失灵） | `code_index.json` blocks: `ContributionsHandler:7 params=['db']` lines 7-78；`taint-analysis done 5/5 → 0 taint_flows` |
| 2 | **intra-first 无回退**：`_tainted_params_reaching_sink`（backward 用）intra 空时回退用 `dangerous_slots[].expression` 当 seed（substring 命中 SourcePoint）→ 救回恰好是 chain entry 的 2 函数（3 条 flows）；`produce_intra_first_taint_flows`（专治 handler 不在 chain，research/eval 属此类）无此回退，双重门全断 | pgraph 3 flows 全 backward-anchored，零 intra-first；research/contributions 不在 8 chains/2 entries 上 |
| 3 | **规则层两洞**：`ts-eval` slot=generic 不在 `_INJECTION_SLOTS`（同类 `ts-child-process-exec` 配 cmd_argument）；XSS 规则仅 2 条 DOM 型（innerHTML/document.write），**零服务端模板渲染规则**——NodeGoat 15 处 `res.render` 的 Swig autoescape:false XSS 整类不可见 | sink_rules.yml 116 条清点；12 个 sink_call_sites 零 XSS 类 |
| 4 | **REDIRECT 过滤的开轨假设**：`ssrf_builder` 有意滤掉 REDIRECT 类 sink（当年理由：LLM 轨会以 `Open_Redirect` 子型报，GitNexus 轨若报会 dedup key 不一致重复）。关轨后两轨同时静默。而原始 shannon `vuln-ssrf.txt` §8 本就是 Open Redirect 方法论、`vulnerability_type` 枚举含 `Open_Redirect`——归入 SSRF 大类是对齐原始设计 | `prompts/vuln-ssrf.txt:107,152,197-207`；ssrf_builder.py 过滤段注释 |

### 1.4 明确不修的更深因素（架构决策内工作）

- **GitNexus call graph 稀疏**（8 chains/2 entries）：JS 动态调用图建不全是外部 CLI 能力边界，`intra-first` 的存在就是项目"不依赖 chain 全覆盖"的架构回答——修复点 A 把这条路线修通，即在该决策内工作。
- **`.env` 关轨本身**：是 token 策略决策非代码缺陷；本设计让"关轨兜底"承诺成立，而非反对关轨。

---

## 2. 目标 / 非目标

**目标**：关轨状态下 NodeGoat 四类真漏洞（SSJS eval 注入 / SSRF needle / 模板 XSS / Open Redirect）各 ≥1 检出；开轨状态双轨 dedup 无重复；全部改动限于 GitNexus 轨内部。

**非目标**：不动 LLM 轨任何 prompt（铁律）；不新增 vuln 类（Open Redirect 归入 ssrf 大类的 URL 子型，非第 6 类）；不动块切分粒度（block id / sink caller_id / chain 对齐保持）；不动 Java/Go/PHP/Python parser；不做 misconfig 类回补。

---

## 3. 设计：六个修复点

两组：机制组（A/B）治 flow 产生，规则组（C/D/E）治 sink 与路由，观测组（F）留第一现场。

### A. `produce_intra_first_taint_flows` 补表达式回退（治断点 2）

位置：`packages/core/src/supernova_core/code_index/chain_propagator.py`。

sink 未被 intra 命中时（`intra is None` 或 `sink.id not in intra.hits`）增加回退分支：直接用 `dangerous_slots[].expression` 对该函数的 `source_points` 做 `_source_points_matching` 同款 substring 匹配，命中即产 flow：

- `needs_review=True`（与 intra-first 对 `llm-discovered-source` 的处理一致），confidence 取 `_INDIRECT_HIT_CONFIDENCE=0.5` 档——LLM 复核语义由 chain_verdict 轻判承担
- 字面量表达式不产 flow（复用 `llm_taint_analyzer._is_literal_expression`）——session.js 6 个 `redirect("/login")` 常量 sink 不进候选，零噪音
- flow 仍经 `merge_taint_flows` 与 backward 去重（key: entry/source/sink）
- `sink_call_sites` 缺失时保持现状（返回空）

命中可行性已用当次产物手工验证：research `url`→SourcePoint(`ResearchHandler:7::url`) ✓；`req.body.preTax`→SourcePoint(`preTax`) substring ✓；`req.query.url`→SourcePoint ✓。

### B. 嵌套 arrow/function 参数并入 `block.parameters`（治断点 1）

位置：`packages/core/src/supernova_core/code_index/parsers/typescript_parser.py`。

`_extract_func_block` 产 block 后，用 tree-sitter 在 block 源码范围内扫描嵌套 `arrow_function`/`function_expression` 的形参，**并入** parameters（去重、追加在末尾，外层参数优先）。只做参数并入**不动块切分**。

消费方影响：intra prompt 参数列表含 req/res（问对问题 + fast-path 判空不再误触发）；`_deterministic_intra_fallback` 的 tainted_params=全部参数变大 → 保守方向正确（超集保召回，误报由 chain_verdict 过滤）；backward `_map_call_site_params_reverse` 参数名匹配面变宽（substring 近似本就过近似，风险低，回归覆盖）。范围限定 typescript_parser。

### C. `ts-eval` slot 纠正（治断点 3 前半）

`sink_rules.yml`：`ts-eval` 的 `slot: generic` → `slot: cmd_argument`。一行，eval flow 可路由进 injection builder。回归面：既有断言 slot 值的测试同步。

### D. 新增 `ts-res-render` XSS 规则（治断点 3 后半）

```yaml
- rule_id: ts-res-render
  languages: [typescript]          # JS 文件经 typescript 语言标识覆盖（NodeGoat .js 已由 ts-res-redirect 证实命中）
  callee: render
  receiver_pattern: "^(res|response|ctx)$"   # receiver 必配（ts-res-redirect 死规则教训）
  category: xss
  sink_subtype: xss_server_render
  needs_review_default: true       # autoescape 开启时安全，交 chain_verdict 结合上下文判定
  dangerous_slots: [{arg_index: 1, slot: generic}]   # 污点在 locals 对象（arg 0 是模板名）
```

配套：`chain_verdict._render_context_for` 认识 `xss_server_render`（映射 server template render 上下文描述）。xss 路由只看 category==XSS，slot=generic 不影响路由。

**噪音退路**：Express 项目 res.render 高频（NodeGoat 15 处）全进候选交轻判是 GitNexus 轨设计形态（宽召回+轻判过滤）；真机噪音过大则降级走 `sink_candidates.yml` 候选表 LLM 精筛——不在本设计实施，留判定依据。

### E. REDIRECT → `Open_Redirect` 子型（治断点 4）

位置：`packages/core/src/supernova_core/code_index/vuln_chain_builders/ssrf_builder.py`。

1. 删 REDIRECT 过滤段，替换为：sink 类别==REDIRECT 的候选 → `vulnerability_type="Open_Redirect"`（对齐 vuln-ssrf.txt:107 枚举），其余保持 `"URL_Manipulation"`
2. title 生成让 verdict LLM 区分两形态（SSRF-fetch vs 3xx redirect）
3. **merger dedup 对齐**（待验证点见 §6）：dedup key 含 vulnerability_type 则两轨 `Open_Redirect` 天然对齐；不含则无需改

回归：当年防污染用例改断言"REDIRECT 产 Open_Redirect 子型、不标 URL_Manipulation"。

### F. intra 全空观测性告警（防御深度）

位置：`packages/core/src/supernova_core/code_index/__init__.py` taint 分析段。

所有含 sink 函数的 intra 结果 tainted_params 全空 **且** 存在任一 sink 的 dangerous_slot `is_entry_hint=True` 时，经 dispatcher 通道发 warning（不裸 logger——worker logger 必走 dispatcher 的既有坑）：

> `GitNexus intra taint 全空但存在直达污点 sink（N 个）——疑似参数提取/模型异常，GitNexus 轨召回可能受损`

纯观测不改行为。

---

## 4. 测试与验收

### 4.1 单测（TDD，每修复点先红后绿）

| 点 | 断言 |
|---|---|
| A | fixture（intra 空 + slot expr `req.query.url`）→ intra-first 产 flow；字面量 expr 不产；与 backward 去重 |
| B | NodeGoat 形态片段（constructor + this.arrow）→ parameters 含 req；外层序不变、去重 |
| C/D | 规则加载断言 slot/category/arg_index；NodeGoat 真实片段跑 detect_sinks 断言 res.render 命中 |
| E | REDIRECT 候选产 vulnerability_type=Open_Redirect；不与 URL_Manipulation 混淆 |
| F | 全空 + is_entry_hint → warning 发出 |

### 4.2 既有产物回归（机制级验证，不依赖真机）

用本次扫描 `code_index.json`/`parameter_graph.json` 做 fixture：修复后 `extract_candidate_chains` 出 ssrf≥1、injection 新增 eval 候选、xss≥1。

### 4.3 真机验收（关轨重扫）

`SUPERNOVA_LLM_TRACK_ENABLED=0` 重扫 NodeGoat：SSRF（needle）≥1、SSJS 注入（eval）≥1、XSS（res.render 链）≥1、Open Redirect（/learn）≥1；原 INJ-GN-01 / authz 4 / auth 9 不回归。⚠️ 改 core/code_index 须 rebuild worker。

### 4.4 防过拟合

既有 JS 语料（juice-shop 等）规则回归，新规则不炸旧用例。开轨状态跑一次 NodeGoat（或至少 merger 单测）验证两轨 Open_Redirect dedup 不重复。

### 4.5 测试陷阱（既有约定）

只跑 code_index / vuln_chain_builders / chain_verdict 相关子集，不跑全套 pytest。

---

## 5. 风险与守恒

- **双轨铁律零触碰**：全部改动在 GitNexus 轨内部，零 LLM 轨 prompt 变更；`test_static_dataflow_hints_decoupling.py` 继续锁定。
- **开轨无重复**：E 点 dedup 对齐是关键验收点。
- **召回/噪音平衡**：A/D 都是宽召回设计（候选多→轻判过滤）；needs_review + 0.5 confidence 控制下游可信度；字面量过滤防常量 sink 噪音。
- **参数并入的传播面**（B）：backward 参数映射匹配面变宽，用既有 chain_propagator 测试回归。

---

## 6. 待验证点（plan 阶段读码确认）

1. `dual_track_merger` dedup key 是否含 `vulnerability_type`（决定 E-3 是否需要改 merger）。
2. `_render_context_for` 现有 sink_subtype 映射表结构（D 点配套改动的落点）。
3. B 点并入参数后，`entry_points.py` / `authz_gitnexus_track.py` 等其它 `block.parameters` 消费方是否需要同步断言更新。
