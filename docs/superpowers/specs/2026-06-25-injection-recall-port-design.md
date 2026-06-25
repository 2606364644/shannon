# Injection Recall 移植设计（shannon-py）

**Date:** 2026-06-25
**Status:** Pending Review
**上游 spec（权威方案）：** `/root/shannon/docs/superpowers/specs/2026-06-25-injection-recall-design.md`（原始 TS 项目的注入召回优化设计）
**本文件性质：** 把上游 spec 移植到重构项目 shannon-py，并按 shannon-py 双轨实情扩出两块上游没有的工作。可行性已对代码逐条核验（§1）。

**双轨消费模型（canonical，详见 memory `dual-track-consumption-model`）：**
- **GitNexus 轨** = 确定性结果（`parameter_graph`/sink）→ `chain_verdict` 轻量 LLM 判定 → finding。
- **LLM 轨** = `vuln-injection.txt` agent，**纯 LLM 独立分析**（参照原始 `/root/shannon`，TS 无确定性层、100% 自给自足），**不吃任何 GitNexus/确定性结果**。
- 两条轨各自独立，只在合并器（verdict OR）交汇。

本 spec 的改动严格按轨划分：LLM 轨的改动（1.1/2/3/4）不引入确定性依赖；GitNexus 轨的改动（1.2）只作用于确定性层。

---

## 0. 一句话结论

上游 spec 三条 prompt 改动在 shannon-py 基本可 1:1 移植（落 LLM 轨）；但要让 SQLi/CMDi 真正被扫出来，须额外做三件 shannon-py 特有事：① **解封并解耦 LLM 轨**（glm-openai 下被 Task 死指令锁死 + 现有 hints include 违规耦合确定性层）；② **扩充 GitNexus 轨的确定性 sink 规则**（ORM Raw/动态标识符零规则）；③ 改动 3 落地必需的 **合并器 `externally_exploitable` 解耦**。

---

## 1. 可行性核实（逐条映射，已对代码核验）

| 改动 | 轨 | 上游 TS 位置 | shannon-py 落点 | 判定 |
|---|---|---|---|---|
| **1.1 prompt sink 清单** | LLM | `vuln-injection.txt:153-155` | `prompts/vuln-injection.txt:145`（逐字同款粗清单） | 直接改 |
| **1.2 代码规则扩充** | GitNexus | —（TS 无代码层） | `sink_detector.py:67-198`（ORM Raw/动态标识符零规则） | 新增规则 + arg-shape 增强 |
| **2 契约** | LLM | `vuln-injection.txt:140-141` | `prompts/vuln-injection.txt:134-135`（同款坏契约 §1.2） | 直接改（TS 式源） |
| **3a queue criteria** | LLM | `vuln-injection.txt:167-170` | `prompts/vuln-injection.txt:161`（逐字同款闸门） | 直接改 |
| **3b step 5 强化** | LLM | `vuln-injection.txt:162-165` | `prompts/vuln-injection.txt:153-156` | 直接改 |
| **3c exploit 分诊** | exploit | `exploit-injection.txt`（新增） | `prompts/injection-exploit.txt`（尚无） | 新增 |
| **3′ 合并器解耦** | 合并点 | —（TS 无合并器） | `dual_track_merger.py:52` | 删覆写行 |
| **4 LLM 轨解封+解耦** | LLM | —（TS CLI 原生有 Task） | `tools_openai`（加 Task tool）+ `vuln-injection.txt:45`（移除 hints include） | 给 openai-agents 引擎补 Task（approach ①，agent 集成层对齐能力）+ 移除 include；Task 块 prompt 不改 |

### 1.1 已就绪、不用动的下游（改动 3 受益）
- `findings_renderer.py:181 _passes_filter` 只按 confidence/severity 过滤，**不过滤 `externally_exploitable`** → 跨服务 finding 入队即渲染。
- `report-executive.txt:92-108` 已保留 `### [TYPE]-VULN-NN` 带 ID section → 跨服务 finding 不被报告丢弃。

### 1.2 Section 7 契约坏在哪（核实）
`vuln-injection.txt:134-135` 指示从 `pre_recon_deliverable.md` 的 "7. Injection Sources" 建 todo，但该 section 不存在：`pre-recon-code.txt:276` 的 Section 7 是 "Overall Codebase Indexing"；样例 deliverable 实际是 "External Entry Points / Data Flow Security / Input Validation Patterns"，无 "Injection Sources"。

### 1.3 LLM 轨两处故障核实（改动 4 根因）
- **瘫痪：** active=`glm-openai` → 引擎 `openai_compatible` → `build_tools()` 返回 8 工具**无 Task**（`tools_openai/__init__.py:27`）；`vuln-injection.txt:86/91` "NEVER Read / delegate to Task Agent / MANDATORY" → 死指令 → agent 瘫痪。同款死指令在 5 个 vuln prompt 全部存在。
- **耦合（违反双轨消费模型）：** `vuln-injection.txt:45 @include(shared/_static-dataflow-hints.txt)` 把确定性层产物 `static_dataflow_hints.md` 喂进 LLM 轨 prompt → LLM 轨依赖确定性层（确定性层/GitNexus 经常超时/不可用）。应移除，LLM 轨自给自足。

---

## 2. 设计

### 改动 1：扩充 sink 覆盖（prompt 喂 LLM 轨 + 代码规则喂 GitNexus 轨）

#### 1.1 prompt 清单（LLM 轨；对应上游根因 #1）
**落点：** `prompts/vuln-injection.txt:145`。粗清单扩为 per-language（保留 File/SSTI/Deserialize 行）：ORM Raw / string-built SQL（Go `db.Raw`/`gorm.Expr`/`Where(fmt.Sprintf)`/`db.Exec`、Node `knex.raw`/`sequelize.query`/typeorm `.query`、Python `cursor.execute(f"...")`/`%`/`.raw(`/SQLAlchemy `text()`、Java MyBatis `${}`/JPA `createNativeQuery`、PHP `whereRaw`/`DB::raw`）；动态标识符（slot=ident，bind 不保护）；间接命令执行（`sh -c`/`shell=True`/SSH/job-scheduler 拼接）。

#### 1.2 代码规则扩充（GitNexus 轨；shannon-py 特有）
**落点：** `sink_detector.py:67-198`。当前 ORM Raw 与动态标识符零规则（`SlotContext.SQL_IDENTIFIER` 枚举存在但无规则用它）。新规则**只喂 GitNexus 轨**（经 `parameter_graph` → `injection_builder` 候选链），**不经 hints 喂 LLM 轨**（改动 4 已解耦）。

- **A. ORM Raw sink 规则**（clean callee+receiver，加 `SinkRule`）：`py-django-raw`(`raw`@`objects`)、`py-sqlalchemy-text`(`text`)、`ts-knex-raw`(`raw`@`knex`)、`ts-sequelize-query`(`query`@`sequelize`)、`go-gorm-raw`(`Raw`@`db|gorm|DB`)、`go-gorm-exec`(`Exec`@同)、`java-jpa-createnativequery`、`php-laravel-whereraw`、`php-db-raw`(`raw`@`DB`)，slot 多为 `SQL_VALUE`。
- **B. 动态标识符 arg-shape 增强**：`_build_dangerous_slots`（`sink_detector.py:339-362`）对 **SQL 类** sink，当危险槽 arg 形如 f-string/`fmt.Sprintf`/`%`/拼接（`+`/`.format(`）时，改标 `SQL_IDENTIFIER` + `needs_review=True`（绑定值不会是 f-string；字符串构建进 SQL callee 强烈暗示标识符/DDL 注入，覆盖 `tableName=uid%N`→`db.Exec(CREATE TABLE…)`）。新增 `_looks_string_built(expr)`。只影响 SQL 类、只在 string-built 时改标。
- **C. 命令侧降假阳性**（纳入、不推迟）：subprocess sink 已触发，`shell=True` 不匹配 `sanitizer_library.py:161` 的 `shell=False` defense → 判 vulnerable（**已正确**）。改进是反向：让 `sanitizer_library.py` 在 **AST 级**识别 `shell=False`+数组实参 → 标 `subprocess_array` defense，降 `shell=False` 安全用例的假阳性。
- **D. 下游可达性：** 新规则 → `SinkCallSite` → Spec A `TaintFlow` → `parameter_graph.json` → `injection_builder` GitNexus 轨候选链。须确认新 subtype 不被 `finding_models.py:28 VALID_INJECTION_CATEGORIES` 白名单误拒（SQL/COMMAND 核心 category，plan 加断言）。

> Out of Scope：NoSQL 规则（MongoDB `$ne` 等）——上游与本任务聚焦 SQLi/CMDi，另做。

### 改动 2：修 Section 7 契约（LLM 轨；对应上游根因 #3）
**落点：** `prompts/vuln-injection.txt:134-135`。候选 source **两路派生（TS 式，不引确定性结果）**：
1. 从 `pre_recon_deliverable.md` 真实攻击面 section（"External Entry Points" / "Data Flow Security" / "Input Validation Patterns"）派生输入向量；
2. 用改动 1.1 的 per-language sink 清单**主动 grep 全仓**找 sink。

> **不引 `static_dataflow_hints.md`**（双轨消费模型：LLM 轨不吃确定性结果，见改动 4 解耦）。`pre-recon` 不改。

### 改动 3：跨服务 / 二阶注入——入队，不丢（LLM 轨 + exploit；对应上游根因 #2）
- **3a. `vuln-injection.txt:161`**：QUEUE INCLUSION CRITERIA 改为——EVERY `vulnerable` finding 入队；`externally_exploitable` 转可达性标签（true=公网 / false=内部或跨服务），不再挡入队。
- **3b. `vuln-injection.txt:153-156`** step 5 新增：用户可控 SQL/command fragment 被本服务转发给下游执行 = `vulnerable` 跨服务 sink（`externally_exploitable=false`），不是 safe。
- **3c. `injection-exploit.txt`（新增段）**：`externally_exploitable=false` 条目走短证据分诊（不 live exploit，本二进制无本地 sink），豁免 "no skipping"/"minimum 3 payload" 规则。
- **3′. `dual_track_merger.py:52` 解耦（合并点·必须）⚠️**：当前 `data["externally_exploitable"] = vulnerable` 把可达性覆写成 verdict-OR → 跨服务 finding（false, vulnerable）被翻 true。**最小修复：删该覆写行，保留 base finding 的 `externally_exploitable`**（both/llm-only 分支 base 是 LLM 轨、可达性权威；gitnexus-only 分支 base 是 GitNexus 轨）。`merge_dual_track_queues` 的 `vulnerable`(verdict-OR) 计算不变。不用"可达性 OR"——跨服务 finding 是 LLM-only，GitNexus 轨产不出同 key 跨服务条目。相关不阻塞：`injection_builder.py:51` GitNexus 轨 `externally_exploitable=verdict`（单仓合理保守，不改）。

### 改动 4：LLM 轨解封 + 解耦（LLM 轨；shannon-py 特有·前置）★

> **双引擎约束（用户 2026-06-25 拍板）：** glm-openai 与 glm-anthropic 是双 Agent 引擎，**两个都要支持，且流程设计要一致（可互换）**。故"切到 glm-anthropic 了事"（=丢 openai 引擎）不可行；"engine-adaptive prompt 让 openai 退化单 agent"（approach ②）也违背流程一致性，**作废**。原始 TS 无此问题——它单引擎跑 Claude Code CLI，Task tool 原生在（`apps/worker/src/ai/output-formatters.ts:205` "Task tool calls"、`workflows.ts:95` "sub-agent execution"），`vuln-injection.txt` 的 "delegate to Task Agent" 直接生效。shannon-py **双引擎（claude-agent-sdk / openai-agents）代码流程一致、只核心智能体能力不同**（CLAUDE.md §2）：openai-agents 经 `tools_openai/build_tools()` 暴露工具子集（8 个），**未含子代理委派**——与 claude-agent-sdk（CLI 原生有委派）能力不同；又照搬 TS 的 Task 强制 prompt，故 openai 侧 vuln agent 跑不出。**委派能力对齐是 agent 集成层（`packages/core/src/shannon_core/agents/`）的职责。选定方案 = approach ①：agent 集成层为 openai-agents 提供委派 tool（`tools_openai/task.py`），两引擎能力对齐、流程一致、prompt 不改。**

**4a. 解封（approach ①：给 openai 引擎补 Task tool，prompt 不改）：**
- **vuln prompt 不动**——`vuln-injection.txt:85-96` 的 "delegate to Task Agent, MANDATORY" 原样保留（TS-faithful），两引擎都走这套 Task 委派。
- **给 openai 引擎实现 Task tool**：在 `packages/core/src/shannon_core/agents/tools_openai/` 新增 Task 工具，语义对齐 Claude Code CLI 的 Task（给定 prompt → spawn 子代理读码 → 返回结果）。底层用 openai-agents 的 handoff/子代理（memory `env-config-profiles-local-state` 确认 openai-agents 已装），子代理配 Read/Glob/Grep（代码阅读）。接入 `build_tools()`（`:27`，从 8 工具扩到含 Task）。
- **结果**：openai 引擎与 CLI 引擎流程一致（同一 Task-delegation prompt、同一子代理读码模式），glm-openai 与 glm-anthropic 可互换。
- **plan 须核实**：openai-agents handoff 的具体 API（`Agent.handoffs` vs 自定义 Tool 包装）；CLI Task 子代理的工具集契约（确保 openai Task 子代理工具对齐，子代理能 Read/Glob/Grep）。
- **运行时关键 checkpoint（已实测通过 ✅）**：GLM 能否驱动子代理委派 tool。**实测**（`scripts/validate_glm_task_probe.py`，glm-anthropic，**2/2 可复现**）：GLM 正确发起子代理委派——CLI v2.1.x 该 tool 名为 **`Agent`**（由 `Task` 改名），GLM 把 prompt 的 "delegate to Task Agent" 正确映射到 `Agent`，构造完整委派（`description`+`subagent_type`+`prompt`），子代理读码后 GLM 产出正确 SQLi 判定。**结论：glm-anthropic 不瘫（与原始 TS 一致），问题仅在 glm-openai 缺该 tool。** 残留：GLM 同时也用 Read（不只委派），但委派可靠发生，对 vuln agent 可接受。openai 侧实现 `Agent` tool 后须复用该探针在 glm-openai 上同样验证。

**4b. 解耦（贯彻双轨消费模型）：**
- **移除 `vuln-injection.txt:45 @include(shared/_static-dataflow-hints.txt)`**——LLM 轨不再消费确定性 hints，自给自足（TS 式）。LLM 轨召回靠自身方法论（改动 1.1/2/3）；确定性召回由 GitNexus 轨（改动 1.2）独立提供，双轨 OR 合并互补。
- 移除后 `static_dataflow_hints.md` 对 LLM 轨失效；是否停止生产该文件属可选清理（plan 定，harmless 保留亦可）。

**范围：** 4a 的 Task tool 加在 openai 引擎一次，**所有 agent 自动受益**（injection/authz/auth/xss/ssrf 都跑 Task-delegation prompt），无需 per-prompt 改动。4b 移除 `_static-dataflow-hints.txt` 的 `@include` 是 per-prompt 编辑——本 spec 至少移除 `vuln-injection` 的；其余引了该 include 的 vuln prompt 建议同批移除（贯彻双轨消费模型，成本为零）。

---

## 3. 数据流（端到端，双轨独立）

```
[改动 4a 解封] openai 引擎补 Task tool → 两引擎都能跑 Task 委派，vuln agent 与 TS 一致地跑
[改动 4b 解耦] LLM 轨 prompt 不含确定性 hints → 自给自足
        │
        ├──── LLM 轨（独立）──────────────────────────────┐
        │  [改动 2] recon 攻击面 + grep repo 派生源        │
        │  [改动 1.1] 按 per-language 清单识别 sink + 标 slot│
        │  [改动 3b] 跨服务转发 = vulnerable(false)         │
        │  [改动 3a] 入队，不被闸门挡                       │
        │  → injection_llm_queue.json                       │
        │                                                   │
        └──── GitNexus 轨（独立）──────────────────────────┤
           [改动 1.2] detect_sinks 扩 ORM Raw + 动态标识符   │
              → parameter_graph.json → injection_builder    │
              → chain_verdict 轻量 LLM 判定                 │
           → injection_gitnexus_queue.json                  │
                                                           ▼
                    run_merge_dual_track_queues（verdict OR）
                    [改动 3′] externally_exploitable 不被 verdict 覆写
                                                           ▼
                    injection_exploitation_queue.json
                                                           ▼
              findings_renderer（§1.1 不过滤）→ injection_findings.md（带 ID）
                                                           ▼
              report-executive（§1.1 保留带 ID section）→ 最终报告
```
exploit 阶段若跑：改动 3c 对 `externally_exploitable=false` 走短证据分诊。**两条轨独立产 finding、OR 合并，任一挂另一仍跑。**

---

## 4. Impact

- **Prompt（Task 块不改）：** `vuln-injection.txt`（1.1 `:145`、2 `:134-135`、3a `:161`、3b `:153-156`、**4b 移除 `:45` hints include**；Task 块 `:85-96` 保留 TS 原样）；`injection-exploit.txt`（3c 新增 triage 段）。**改动 4a 不改 prompt。**
- **代码（4 处）：** `sink_detector.py`（1.2 A/B）；`sanitizer_library.py`（1.2 C，AST 级 shell=False 标注）；`dual_track_merger.py`（3′ 删 `:52` 覆写）；**`tools_openai/`（4a 新增 Task tool，接入 `build_tools()`）**。
- **Scan 成本：** 无新增 agent/phase。1.2 多出的 detect_sinks 规则匹配是 O(1) 索引 + 轻量 arg-shape 正则，可忽略。
- **Queue schema / session.json / workflow：** 无改动。
- **其他 vuln 类：** 4a 的 Task tool 加在 openai 引擎一次，**所有 agent 自动受益**（auth/authz/ssrf/xss 同样跑 Task-delegation prompt）；4b 移除 include 可同批作用于引了该 include 的 prompt。

---

## 5. Out of Scope

- **NoSQL 注入规则**（MongoDB `$ne` 等）：聚焦 SQLi/CMDi，另做。
- **`injection_builder.py:51` `externally_exploitable=verdict` 解耦**：GitNexus 轨单仓判定合理保守，不阻塞改动 3′。
- **pre-recon 不改**：与上游一致；其 Section 9 XSS-centric 由改动 2 的 grep 补偿。
- **`VALID_INJECTION_CATEGORIES` deserialization 错配（INJ-4）**：正交小修，plan 加断言防御即可。
- **是否停止生产 `static_dataflow_hints.md`**（4b 移除 include 后）：可选清理，plan 定。

---

## 6. Validation

1. **1.2 A — 新 sink 规则单测：** 扩 `test_sink_detector.py`，每条 ORM Raw 规则用内联源码断言命中（如 `db.Raw("SELECT "+x)` 命中 `go-gorm-raw`）。
2. **1.2 B — 动态标识符 arg-shape：** `db.Exec(f"CREATE TABLE {tn}")` 危险槽标 `SQL_IDENTIFIER`+`needs_review=True`；`cursor.execute("SELECT %s",(x,))` 仍标 `SQL_VALUE`。
3. **1.2 C — 命令侧 FP：** `subprocess.run(["ls",x],shell=False)` 标 `subprocess_array` defense；`subprocess.run("ls "+x,shell=True)` 不标（判 vulnerable）。
4. **1.2 D — 白名单不误拒：** 新 SQL/COMMAND subtype finding 不被 `VALID_INJECTION_CATEGORIES` 丢弃。
5. **3′ — 合并器保可达性：** `test_dual_track_merger_preserves_reachability`：`externally_exploitable=False,verdict="vulnerable"` 的 LLM finding 过合并 → 仍 `False`、`verdict="vulnerable"`；回归现有 `test_dual_track_merger` 全绿。
6. **4a — openai Agent tool + GLM 驱动：** glm-anthropic 侧**已实测通过**（`scripts/validate_glm_task_probe.py`，GLM 正确驱动 `Agent` 子代理委派，2/2 可复现）。openai 侧实现 `Agent`/`Task` tool 接入 `build_tools()` 后，复用该探针在 glm-openai 上验证 GLM 同样驱动。
7. **4b — 解耦渲染：** vuln-injection prompt **不含** `static_dataflow_hints` include 块（防回归）。
8. **prompt 闭环（1.1/2/3）：** 复用 `test_static_dataflow_hints_e2e` 模式，验证含新清单 / 新契约 / 新 queue criteria 文本。
9. **端到端冒烟（解封后·人工）：** 跑一个有 `Where(fmt.Sprintf(...))` 或 RPC 转发 SQL 字段的 Go 服务白盒，确认：(a) SQLi/CMDi 召升；(b) 跨服务 finding 以 `externally_exploitable=false` 进 queue 与报告；(c) exploit agent 走短证据不空耗；(d) **LLM 轨不再产空**（顺带闭合 INJ-1 e2e 空白）。

---

## 7. 与上游 spec 的差异索引

| 维度 | 上游 TS spec | 本移植 spec（shannon-py） |
|---|---|---|
| 改动 1 | prompt only | **prompt（LLM 轨）+ 代码规则（GitNexus 轨）** |
| 改动 2 source 派生 | recon Section 5 + grep repo | 同（pre-recon 攻击面 + grep），**不引确定性 hints** |
| 改动 3a/3b/3c | prompt | prompt（1:1） |
| 改动 3 落地前提 | 无 | **额外 `dual_track_merger.py` 解耦** |
| LLM 轨能否跑 | 隐含能（CLI 原生 Task） | **glm-openai 无 Task → 改动 4a 给 openai 引擎补 Task（approach ①），两引擎流程一致** |
| LLM 轨独立性 | 自给自足（无确定性层） | **现有 hints include 耦合 → 需改动 4b 解耦** |
| 代码改动 | `Code: none` | 4 处（sink_detector / sanitizer_library / dual_track_merger / **tools_openai Task tool**） |
