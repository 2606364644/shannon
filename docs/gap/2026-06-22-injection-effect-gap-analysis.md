# 注入审计效果差距分析

> 对比原始 Shannon（TypeScript, `/Users/mango/project/shannon-refactor/shannon`）与重构 Shannon-py（Python）在 **注入（Injection）安全审计效果**上的差距。
>
> **聚焦口径**：效果/效能——注入漏洞的检出广度与深度、确定性分析层的可重复性增益、动态验证（exploit）证据硬度、端到端实测闭环。
>
> **与 auth 的关键反差**：本文是 [`2026-06-21-auth-effect-gap-analysis.md`](2026-06-21-auth-effect-gap-analysis.md) 的姊妹篇，但结论**截然相反**。认证审计两项目「方法论持平 + 重构有概念错配（AU-3）」；注入审计则是「**重构净增强**」——新增了一整套确定性 sink/污点分析层（原始为零），且 `_static-dataflow-hints` 这同一片段对 injection 是**精准匹配**、对 auth 是**概念错配**。同片段、不同漏洞类型、相反定性，是三份 gap 文档的核心分野。
>
> **数据来源**：① 逐文件代码核验（prompt / schema / 注册 / 确定性分析层 / 执行 / 调度）② 共享历史扫描产出（`workspaces/juice-shop_whitebox-1780587584138/`）③ 原始 Shannon 独立 NodeGoat 扫描（`shannon/workspaces/NodeGoat_whitebox-1780678757791/`）。所有数字经脚本核验（`find` / `diff -q` / `python3 -c json.load` / 关键词计数），非 agent 转述。
>
> **日期**：2026-06-22

---

## 0. 与现有 gap 文档的关系定位

本文档**不重复**以下已有结论，仅在必要时交叉引用：

| 现有文档 | 已覆盖维度 | 本文档关系 |
|---|---|---|
| `2026-06-21-vuln-agent-gap-analysis.md` | injection 类型的**结构对齐**（枚举/注册/schema 字段/exploit 数量/queue 契约） | **引用**。结构已证明「逐维对齐」，本文在其之上回答「对齐之后效果如何」 + 「重构新增的确定性层带来什么」 |
| `2026-06-21-auth-effect-gap-analysis.md` | 认证效果（AU-1 无实测 / AU-2 软依赖 / AU-3 static-hints 错配） | **姊妹篇 + 反例**。GAP-INJ-1/INJ-2 与 AU-2/AU-1 同构；但 AU-3 的错配在 injection 维度**反转为增强** |
| `sink-gap-analysis-v2.md` | Sink 点检测的方法论层差距 | **引用**。本文聚焦 sink_detector.py 这层**确定性实现**的实际覆盖与盲区 |
| `2026-06-18-prerecon-recon-gap-analysis.md` | recon 阶段（vuln agent 的输入） | **引用**。injection agent 消费 recon 产物 + static_dataflow_hints |

**核心问题**：现有文档要么从「结构对齐」角度、要么从「prompt 文本」角度对比 injection，缺一份从「**注入漏洞能不能检全、检准、确定性锚点有没有用、动态证死没证**」角度的对比，并**显式回答「重构新增的确定性分析层到底是不是质变」**。本文填补这个空白。

---

## 1. 注入审计的能力框架

两项目的注入审计都是 **白盒静态（vuln-injection）+ 黑盒动态（injection-exploit）** 两阶段。但重构在白盒**之前**额外插入了一层**确定性代码分析（code_index）**，这是原始项目完全没有的架构层。

| 层 | 原始 Shannon (TS) | 重构 Shannon-py | 效果差距定性 |
|---|---|---|---|
| **⓪ 确定性 sink/污点分析**（白盒前） | **无**（100% 依赖 LLM free-text 追链） | **新增完整层**：AST sink 检测（47 规则）+ 跨函数污点传播 + LLM 函数级 taint + sink 合并，产出 `static_dataflow_hints.md` 注入 vuln prompt | 🟢 **重构显著增强**（从零到一，§3） |
| **① 静态分析 vuln-injection**（纯代码白盒） | 387 行 prompt，slot 标签体系 + sanitizer↔context + concat-after-sanitize + confidence + witness payload | 379 行 prompt，**方法论逐节对齐**；额外 include `_static-dataflow-hints`（精准匹配）+ Source Completeness Rule + schema 字段强化 | 🟢 **重构增强 + 3 处轻量退化**（§4） |
| **② 动态利用 injection-exploit**（live HTTP/playwright） | 453 行 prompt，OWASP 四阶段 + Level 1-4（Level 3+ 才 EXPLOITED）+ Bypass Exhaustion | 447 行 prompt，**方法论逐节对齐**；额外显式 `<vulnerability_entries>` 注入 queue | ✅ **基本持平**（§4.2） |

**裁决**：注入审计的**方法论两项目逐节对齐**。真正的差距是结构性的——**重构在白盒前多了一整层确定性分析（原始为零）**，这是 injection 维度相对 auth 维度最大的不同（auth 没有这层增益）。但这份增强目前停留在**代码级证据**：单测全绿（104 passed），却**从未在真实仓库端到端验证过**——而原始 Shannon 在 NodeGoat 上有完整独立的 injection 实测背书。这是「能力增强」与「能力未被实测」并存的不对称。

---

## 2. 真实产出对比 ★

> ⚠️ **口径警示（关键）**：与 auth/authz 文档同样的困境——**重构 shannon-py 名下的 4 份 injection 产出与原始 Shannon 逐字节相同**（`diff -q` 判定 IDENTICAL），是重构前共享的历史数据，**不可作两项目独立对比**，仅作「Shannon 体系在 Juice Shop 上的注入检出水平」参照。真正的独立对比基线只有原始 Shannon 的 NodeGoat 扫描——而**重构没有同靶场的独立 injection 产出，连一次都没有**。

### 2.1 共享数据：Juice Shop / localhost 系列（体系能力参照，非两项目对比）

`workspaces/{juice-shop_whitebox-1780587584138, 192-168-100-106_*, host-docker-internal_*, localhost_*}/deliverables/`（两项目 diff 为空，queue 各 12288 bytes、analysis 各 24306 bytes）：

| 产出 | 内容 | 核验 |
|---|---|---|
| `injection_exploitation_queue.json` | **10 条 INJECTION-VULN**，全 `externally_exploitable=true`、`verdict=vulnerable` | high 8 / medium 2 |
| 类型分布（10 条） | SQLi ×2、LFI ×2、SSTI ×2、InsecureDeserialization ×2、PathTraversal ×2 | 5 子类各 2，广度均衡 |
| `injection_exploitation_evidence.md` | **无**（4 个 workspace 均未产 evidence） | 白盒阶段（exploit=false），未触达 exploit |

**含义**：在 Juice Shop 这类框架驱动靶场上，Shannon 体系的注入审计能覆盖 5 大子类（SQL/LFI/SSTI/Deserialization/PathTraversal），10 条全可外部利用。但这是**重构前同一套 prompt 演化**的产物，不证明重构本身的能力。

### 2.2 原始 Shannon 独立产出：NodeGoat ★

`shannon/workspaces/NodeGoat_whitebox-1780678757791/deliverables/`（**原始独有，重构无对应**）：

| 产出 | 内容 | 核验 |
|---|---|---|
| `injection_exploitation_queue.json` | **7 条 INJECTION-VULN**，全 `externally_exploitable=true` | high 7 / medium 0 |
| 类型分布（7 条） | **CommandInjection ×5、NoSQL Injection ×2** | 全 high confidence |
| `injection_exploitation_evidence.md`（344 行） | **整份仅 `Successfully Exploited` 一节**（计数=1），无 Potential / Validation Blocked | 5 个 `###` 条目，全证死 |

**动态验证证据硬度**（evidence，关键词实测：`HTTP`×41、`200`×7、`500`×1、`payload`×3）——全部为**可复现请求/响应片段**：
- **完整 RCE 链**：`require('child_process').execSync` 经注入点触发
- **经 SSRF 读 `/etc/passwd` 全文**：注入 → SSRF → 文件读，链式利用
- **`process.env` 泄露**：环境变量（含密钥）经注入外带
- **MongoDB `users` 集合 dump**：明文凭据 `admin / Admin_123`
- **`$ne` 操作符认证绕过**：NoSQL 注入 → HTTP 302 重定向到 `/benefits`（无密码登录）

**关键观察**：原始检出的两类——**CommandInjection 与 NoSQL Injection**——恰好命中 NodeGoat 的标志性漏洞，也恰好是**重构确定性 sink 规则库的盲区**（§3.5：COMMAND 规则虽有 14 条但 NoSQL 零规则）。这意味着：如果重构真去跑 NodeGoat，其确定性层**不会**预先定位 NoSQL sink，只能靠 LLM 现场追——能否复现原始的检出，未知（因为没跑过）。

### 2.3 重构 Shannon-py：**无独立 injection 端到端产出** ★★

这是本文最重要的发现之一，与 auth 文档 AU-1 同源但更严重（因为重构新增的整个确定性层也从未在真机上验证）：

| workspace | deliverables 内容 | 阶段（workflow.log 实证） |
|---|---|---|
| `NodeGoat_shannon-1782041072350` ~ `1782059117521`（6 个） | **无 injection queue / evidence** | 最新一个在 `[PHASE] Starting risk-scoring` 处终止，**从未进入 vulnerability-analysis / injection-vuln 阶段** |
| 全项目 | **不存在任何 `injection_exploitation_evidence.md`**（`find` 返回空） | — |

**含义**：重构近期 6 次 NodeGoat 独立扫描全部是 pre-recon / recon / risk-scoring 阶段的冒烟运行（对应 memory 中「白盒 live 显示重设计」「Provider 逐轮日志」「确定性层（code_index）」等近期改动的验证），**不是完整白盒流水线**，因此未跑到 vulnerability-analysis 阶段的 injection-vuln，更别说 injection-exploit。

**定性**：这是**验证缺口，不是能力退化**。重构 injection agent 的代码与 prompt完整（§3/§4 证明），shared juice-shop 数据也证明「Shannon 体系在 Juice Shop 上能检出 5 类 10 条注入漏洞」。但**重构后至今没有一次跑完 injection 阶段的独立实测**来确认：
1. 重构本身没有引入回归（prompt 改写、确定性层接线后，LLM 是否还能正常产出 queue）；
2. **新增的确定性分析层在真实仓库上的实际命中率与假阳率**（这是 §3 增强结论的硬证据缺口）。

### 2.4 对比裁决

- **广度（参照 juice-shop）**：Shannon 体系的注入审计能覆盖 5 大类（SQL/LFI/SSTI/Deserialization/PathTraversal），10 条全可外部利用。原始在 NodeGoat 上额外独立检出 CommandInjection + NoSQL（共 7 条）。
- **深度（NodeGoat 独立）**：原始能产出完整 RCE / SSRF 读文件 / 凭据 dump / NoSQL 认证绕过的动态实证，证据为可复现请求/响应，**整份 evidence 零 Potential / 零 Validation Blocked**——证死率 100%。
- **不对称**：**原始有独立 NodeGoat 完整 injection 实测（7 queue + 344 行 evidence），重构连一次 injection-vuln 都没跑完**。不能据此说「重构注入审计更弱」——重构的确定性层与 prompt 增强是**代码级事实**——但能说「**重构的注入审计能力、尤其新增的确定性分析层，从未被端到端实测验证**」，这是面向交付的实质风险，且比 auth（AU-1）多一层「确定性层未被真机验证」的未知。

---

## 3. 确定性分析层（重构独有，★ 核心增强）

这是 injection 维度相对原始**最大的结构性差异**。原始项目注入审计 **100% 依赖 LLM free-text 追链**（无 AST、无 sink 库、无 taint 模型——检索证据见 §3.6）；重构新增了一整套确定性 sink/污点分析层，在白盒 vuln agent 之前运行，产出 `static_dataflow_hints.md` 注入 prompt。

### 3.1 sink 规则覆盖广度

`sink_detector.py:67-198` 的 `DEFAULT_RULES` 共 **47 条**，按 SinkCategory × 语言：

| category | go | java | php | python | typescript | 合计 |
|---|---|---|---|---|---|---|
| **SQL** | 1 | 2 | 2 | 2 | 1 | **8** |
| **COMMAND** | 1 | 1 | 4 | 6 | 2 | **14** |
| **DESERIALIZATION** | 0 | 1 | 1 | 3 | 0 | **5** |
| **TEMPLATE (SSTI)** | 0 | 0 | 0 | 2 | 0 | **2** |
| **FILE (LFI/path)** | 0 | 0 | 3 | 0 | 0 | **3** |
| SSRF（非 injection） | 2 | 1 | 2 | 4 | 2 | 11 |
| XSS/REDIRECT（非 injection） | 0 | 0 | 0 | 1 | 3 | 4 |
| **语言合计** | 4 | 5 | 12 | 18 | 8 | 47 |

**直接服务 injection 审计的规则 = 32 条**（SQL+COMMAND+DESER+TEMPLATE+FILE），覆盖 5 语言（py/ts/go/java/php）。每条规则带 `dangerous_slots`（`sink_detector.py:45`），声明 `(arg_index, SlotContext)`，例如 SQL 规则统一标 `SlotContext.SQL_VALUE @ arg0`。

### 3.2 污点传播模型

| 模型 | 定义位置 | 能力 |
|---|---|---|
| `SlotContext`（8 值） | `parameter_models.py:28-37` | `SQL_VALUE`/`SQL_IDENTIFIER`/`CMD_ARGUMENT`/`FILE_PATH`/`TEMPLATE_EXPR`/`URL`/`DESERIALIZE_OBJ`/`GENERIC`——与 `vuln-injection.txt:113` 的 `slot_type` **一一对应** |
| `SinkCategory`（9 值） | `parameter_models.py:132-142` | SQL/COMMAND/FILE/TEMPLATE/DESERIALIZATION/SSRF/XSS/LOG/REDIRECT——injection 子类覆盖完整（**无 LDAP/NoSQL/XPath 专属 category**） |
| `TaintFlow` | `parameter_models.py:52-81` | 完整表达「源 → 中间变换 → 精确 sink 槽位 + 链最弱步 confidence + sanitizer 提示」，字段含 `entry_point_id`/`source_param`/`propagation_steps[]`/`sink_call_site_id`/`sink_slot`/`confidence`/`has_sanitizer_hint` |
| 跨函数传播 | `chain_propagator.py` | 沿 GitNexus 调用图逐跳走；用 **regex 在 caller 源码找 `callee(...)`** 提取实参（`:48-75`）；判定污染用**子串过近似**（`:31-45`，`request` 能匹配 `request.user_id`）；找不到调用点时**保守传播**（`:241-245`，所有 tainted 传所有 callee 参数） |
| LLM 函数级 taint | `llm_taint_analyzer.py` | 对每个含 sink 的函数调一次 LLM，prompt 含函数源码（超长按 sink 行 ±30 截断，`truncate_source:34-73`）；LLM 失败时**保守标所有参数 tainted**（`:278-288`） |

### 3.3 确定性 → prompt 注入闭环（无断点）★

闭环完整，四处接缝均已核验：

```
① 确定性产出                 ② 写文件                    ③ prompt include               ④ agent 消费
SinkCallSite[] / TaintFlow[] → static_dataflow_hints.md → _static-dataflow-hints.txt → vuln-injection.txt:45
ParameterPropagationGraph      (deliverables/)             (shared/, 线索非结论)          (Task agent 据此追链)
```

- **①→②**：`audit_input_builder.py:289-307` `build_static_dataflow_hints()` 消费 Spec B（SinkCallSite）+ Spec A（TaintFlow）+ 分层优先级，产出 markdown；`activities.py:483-528` `run_render_dataflow_hints` 调它并 `atomic_write_text(deliverables/"static_dataflow_hints.md")`（`:524`）。
- **②→③**：`prompts/shared/_static-dataflow-hints.txt:1-10` 指向 `.shannon/deliverables/static_dataflow_hints.md`，**明确「线索非结论」「未列出 ≠ 安全」「sanitize_hint 不代表有效」**——片段本身反复要求 agent 仍须按 slot 上下文判定、查 concat-after-sanitize。
- **③→④**：`prompts/vuln-injection.txt:45` `@include(shared/_static-dataflow-hints.txt)`。
- **编排**：`workflows.py:129-159` 确定性 `run_code_index` 与 LLM `PRE_RECON` **并行**（注释明确「原始 Shannon 无确定性层，PRE_RECON 照跑」）；hints 在 risk-scoring 之后、vuln agent 之前写盘（`activities.py:487-489`）。
- **软约束**：`pipeline_testing_mode=True` 时跳过写 hints（`activities.py:493-494`）并剔除 `<static_dataflow_hints>` 块（`test_static_dataflow_hints_e2e.py:89-93`）——CI 模式下 agent 收不到 hints，这是设计意图（避免 CI 喂 LLM），非缺陷。

**闭环判断：无断点。**

### 3.4 finding 校验与去重

- **`VALID_INJECTION_CATEGORIES`（`finding_models.py:28-31`，6 值）**：`sql_injection`/`command_injection`/`path_traversal`/`ssti`/`ldap_injection`/`nosql_injection`。
- **白名单绑定（`finding_models.py:56-62`）**：`AGENT_TYPE_WHITELIST["injection"] = VALID_INJECTION_CATEGORIES`。`parse_and_validate_findings:105-131` 对 agent 产出的每个 finding，`issue_type` 不在白名单 → **静默丢弃**（`:118-121`）。
- **去重 key（`finding_models.py:81-89`）**：5-tuple `(entry_point_id, category, issue_type, vulnerable_function_id, tuple(call_chain_path))`；`deduplicate_findings:134-151` 保留首次出现。**注意：不含 `code_location`（file:line）**，同一逻辑漏洞在不同行复现会合并。

### 3.5 确定性层的盲区（诚实记录）★

增强不等于无盲区。确定性层目前的覆盖缺口：

| 盲区 | 证据 | 影响 |
|---|---|---|
| **LDAP / NoSQL / XPath / ORM 注入零 sink 规则** | `DEFAULT_RULES` 无对应 category；`VALID_INJECTION_CATEGORIES` 声明了 `ldap_injection`/`nosql_injection` 但无规则支撑 | 这两类 100% 靠 LLM 现场追。**反讽**：原始 NodeGoat 独立检出的 NoSQL Injection（×2）正落在此盲区 |
| **`VALID_INJECTION_CATEGORIES` 缺 `deserialization` 独立类** | 白名单 6 值无 deserialization/rfi/xpath；但 `DEFAULT_RULES` 有 5 条 DESER sink | 确定性 DESER sink 的 finding 可能**被白名单误拒**（issue_type 不匹配）——这是确定性层与 finding 校验层之间的小错配 |
| **跨函数传播是 regex + 子串过近似** | `chain_propagator.py:31-45`，`notes` 自承「容器字段过近似」；`source_type` 硬编码 `QUERY_PARAM`（`:206`） | 对动态调度/反射/别名无能为力；不是工业级数据流分析，是启发式 |
| **Go DESER / TS·Go SSTI 缺** | DESER 仅 java/php/python；SSTI 仅 python（Flask/Jinja2） | 这些语言×子类组合仍 100% 靠 LLM |

### 3.6 原始项目对应层 = 无（已确认）★

检索证据（全部空结果）：
- `grep -rniE "sink_rule|SinkRule|tree-sitter|tree_sitter|ast\.walk|detect_sinks|taint_flow|TaintFlow|sink_call_site|SinkCallSite" shannon/apps` → **0 匹配**
- `grep "tree-sitter" shannon/apps/worker/package.json shannon/package.json` → **0 匹配**（无 tree-sitter 依赖）
- 原始 `apps/worker/src/audit/` 仅 8 个文件（session/logger/knowledge-store 等），**无任何分析器模块**
- 原始 `prompts/vuln-injection.txt` **无** `@include(shared/_static-dataflow-hints.txt)`

唯一形似：`queue-schemas.ts:45-46,135-136` 有 `sink_call`/`slot_type` 字段——但那是 **LLM exploitation queue 的输出 schema**（agent 自由文本填的），**不是**确定性分析的产物。`slot_type` 体系（SQL-val/like/num...）在原始项目里是 **prompt 教 LLM 用的标签**，确定性层为零。

**结论**：原始项目注入审计的 sink 定位与污点追踪 100% 交给 LLM 自由发挥；重构新增了确定性锚点，理论上提升可重复性与召回下限。

### 3.7 测试覆盖

`packages/core/tests/code_index/` 共 30 个测试文件，injection 相关 8 个，**全部 104 测试绿**（0.37s）：

| 文件 | 覆盖 |
|---|---|
| `test_sink_detector.py`（580 行） | 用内联源码片段 + 真实 `detect_sinks`，覆盖 py/ts/go/java/php 各语言 sink |
| `test_chain_propagator.py` | 跨函数传播（regex 调用点 + 位置映射 + 保守传播） |
| `test_llm_taint_analyzer.py` | LLM prompt 构建 + 响应解析 + 失败保守回退 |
| `test_sink_merger.py` | 确定性 vs LLM sink 按 (file,line) 去重 |
| `test_finding_models.py` | 白名单过滤 + 5-tuple 去重 |
| `test_static_dataflow_hints_e2e.py`（103 行） | **闭环验证**：fixture → hints.md → vuln-injection prompt 含 `<static_dataflow_hints>` 块 → pipeline-testing 模式剔除 |
| `test_static_dataflow_hints.py` | hints markdown 渲染边界 |
| `test_risk_scorer.py` | 链风险评分 + 分层 |

**真实代码库端到端测试：无。** 所有测试用内联源码片段或 fixture/mocked GitNexus；`fixtures/{python,typescript,go,java,php}/` 有真实风格样本但仅在 parser 单测里用，无「真仓库 → code_index → hints → agent」全链路 smoke。

### 3.8 确定性层增强幅度裁决

**显著增强（非质变）。**

- **从零到一**：原始为零（§3.6 确证），重构新增 32 条 injection sink 规则 × 5 语言 + 完整污点模型 + 跨函数传播 + LLM 函数级 taint，是架构层新增能力。
- **闭环完整**：确定性发现定位为「追链起点 + 交叉验证」，同时诚实标注边界（sanitize_hint 非有效、未覆盖语言仍需 LLM）——这种「确定性锚点 + LLM 验证」的混合模式，理论上比纯 LLM 更可重复、更难漏 sink。
- **未到质变**：① sink 库有盲区（LDAP/NoSQL/ORM 零规则）；② 跨函数传播是启发式（regex + 子串过近似）；③ **从未在真实仓库跑通自动 e2e**——命中率/假阳率无数据；④ schema 有小漏（DESER finding 可能被白名单误拒）。

**对证据硬度的影响**：确定性层的存在让注入审计的**可重复性**显著提升（同仓库重跑能定位相同 sink，不依赖 LLM 随机性）。但「显著增强」目前是**代码级证据（强）+ 效果级证据（弱）**：代码存在且单测全绿，但缺真实仓库的量化命中率。

---

## 4. prompt 方法论与 schema 对齐度

### 4.1 vuln-injection.txt：方法论逐节对齐，净增强 + 3 处轻量退化

| 子维度 | 原始（387 行） | 重构（379 行） | 定性 |
|---|---|---|---|
| role/objective 覆盖子类型（SQLi/Cmd/LFI/RFI/SSTI/PathTraversal/Deserialization） | L1-3 | L1-3 | ✅ 持平 |
| slot_type 标签体系 | L121 完整 | L113 完整 | ✅ 逐字对齐 |
| sanitizer↔slot context 匹配规则 | L157-161 | L149-153 | ✅ 持平 |
| concat-after-sanitize 非有效规则 | L165 | L157 | ✅ 持平 |
| confidence scoring / false_positives_to_avoid | L186-188 / L224-237 | L178-180 / L216-229 | ✅ 持平 |
| witness_inputs_for_later（各子类型 PoC 载荷） | L211-221 | L203-213 | ✅ 持平 |
| evidence_quality_standards / confidence_level_definitions | L247-260 | L239-252 | ✅ 持平 |
| **@include `_static-dataflow-hints.txt`** | **无**（原始无此文件） | L45 | 🟢 **重构增强**（对 injection 精准匹配，§4.3） |
| **Source Completeness Rule**（每 source 独立条目 + 排序） | **无**（仅 `combined_sources` 信息字段） | L124 新增整段 | 🟢 **重构增强** |
| **`authentication_required` / `accessible_routes` 进 schema 一等字段** | **无**（仅 conclusion L380 提及） | L109-110 + 字段表 | 🟢 **重构增强** |
| starting_context「Endpoint Security Context / framework auto-generated 端点」8 行提示 | L43-50 | **删除**（仅 L41 一行） | 🟡 **退化**（V1） |
| Branch Path Exhaustion 规则（if/else 每分支独立追） | L148 | **删除** | 🟡 **退化**（V6） |
| conclusion 第 3 条 Cross-Route Verification 自检 | L380 | **删除** | 🟡 **退化**（V7） |
| Deliverable 保存 | `save-deliverable` CLI | Write/Edit | 工程差异 |

**裁决**：核心方法论（slot 体系 / sanitizer↔context / concat-after-sanitize / confidence / witness payload / false-positive 清单）逐节对齐；重构在「静态 hints 注入 + Source Completeness Rule + schema 字段强化」三个方向做正向加法，属方法论层实质能力提升；3 处退化（V1/V6/V7）属提示词细节删减，影响面小于增强项。**净评估：持平偏增强，带 3 处轻量可补退化。**

### 4.2 exploit-injection.txt：方法论实质持平

| 子维度 | 原始（453 行） | 重构（447 行） | 定性 |
|---|---|---|---|
| OWASP 四阶段攻击流（Confirmation/Fingerprinting/Enumeration/Exfiltration） | L169-183 | L169-182 | ✅ 持平 |
| Proof Levels 1-4 + Level 3 才 EXPLOITED 门槛 | L214-232 | L213-231 | ✅ 逐字对齐 |
| Mandatory Evidence Checklist + Required Sub-Tasks | L261-274 | L260-273 | ✅ 持平 |
| Bypass Exhaustion Protocol（8-10 变体/分类禁令） | L241-259 | L240-258 | ✅ 持平 |
| Classification（EXPLOITED/POTENTIAL/FALSE POSITIVE） | L276-308 | L275-307 | ✅ 持平 |
| attack_patterns（UNION/Error/Boolean/Time + 标准载荷） | L323-339 | L322-338 | ✅ 持平 |
| **`<vulnerability_entries>{{VULNERABILITY_ENTRIES}}</vulnerability_entries>` 注入块** | **无**（靠读 JSON 文件 L77） | L75-78 新增 | 🟢 **重构增强**（显式喂 queue） |
| 笔误 `Breif`→`Brief` | L376 | L375 | 微增强 |

**裁决**：exploit prompt 方法论实质持平，唯一实质新增是重构显式注入 `<vulnerability_entries>` 块，把 queue 内容直接喂进 prompt context，减少 LLM 漏读 JSON 的风险。

### 4.3 shared 片段适用性：`_static-dataflow-hints` 对 injection 是增强（与 auth AU-3 错配的反例）★

这是三份 gap 文档的核心分野。**同一片段**：

| 片段 | 对 auth（AU-3） | 对 injection（本文） |
|---|---|---|
| `_static-dataflow-hints.txt` | **概念错配** 🟡。auth 方法论是逐端点清单式逻辑检查（HTTPS/rate-limit/cookie/JWT），**不是污点流**；该片段讲 source→sink 可能误导 agent 把认证当污点问题 | **精准匹配** 🟢。injection 本质即污点分析，片段的 source/sink/sanitize_hint 词汇与 `vuln-injection.txt:149-157` 方法论**完全同构**；片段反复强调「线索非结论 / 仍查 concat-after-sanitize」与 prompt 无缝衔接 |

**为什么相反**：injection 是所有 vuln 类型中最典型的「输入流到 sink」污点问题，正是确定性 sink/taint 层（§3）的用武之地。重构把 `_static-dataflow-hints` include 进 vuln-injection 是**正确的架构接线**——这与它被机械 include 进 vuln-auth 的错配（AU-3）形成对照。**结论：该片段对 injection 是重构相对原始的独有能力注入，属增强，非 gap。**

`_cross-route-enumeration.txt` 对 injection **完全适用**（同一注入 sink 常被多路由共享，无认证路由直接决定 `externally_exploitable`），重构把它落进 schema 一等字段（§4.1 V5）是落地补强。

### 4.4 schema 对齐：字段字节级对齐，两版共有弱约束

`InjectionVulnerability` 字段集（`queue_schemas.py:5-22` vs `queue-schemas.ts:41-52`）**字节级对齐，无单边字段**：

| 字段类 | 字段 | 两版 required? | 定性 |
|---|---|---|---|
| 基类 | ID / vulnerability_type / externally_exploitable / confidence / notes | 前 4 required、notes optional | ✅ 一致 |
| injection 专有 | source / path / sink_call / slot_type / sanitization_observed / concat_occurrences / verdict / mismatch_reason / witness_payload | **全 optional（两版均是）** | 🟡 两版共有弱约束 |

**两版共有的 schema 弱约束（非重构独有）**：
- **injection 专有字段全 optional**：`slot_type`/`sanitization_observed`/`concat_occurrences`/`verdict` 是污点分析的核心证据字段，required 与否直接影响 finding 质量。两版都选 optional（容忍 LLM 漏填）。
- **`vulnerability_type` 无 schema 级 enum/Literal**（两版均开放 string）——与 auth 文档 C-1 同。注：重构 `finding_models.py:28-31` 的 `VALID_INJECTION_CATEGORIES` 是**确定性审计层**（`VulnFinding.issue_type`）的白名单，**不是** `InjectionVulnerability.vulnerability_type` 的 schema 约束，两者是不同层的校验。

---

## 5. exploit agent 依赖声明 + queue 校验（与 auth AU-2 同构的 gap）

### 5.1 exploit agent prerequisite：重构软依赖 🟡

| exploit agent | 原始 prerequisites | 重构 prerequisites |
|---|---|---|
| injection-exploit | `['injection-vuln']`（`session-manager.ts:75`，显式） | `[RECON]`（`agents.py:96`，软依赖） |
| xss-exploit | `['xss-vuln']`（`:82`） | `[RECON]`（`:103`） |
| auth-exploit | `['auth-vuln']`（`:89`） | `[RECON]`（`:110`） |
| ssrf-exploit | `['ssrf-vuln']`（`:96`） | `[RECON]`（`:117`） |
| authz-exploit | `['authz-vuln']`（`:103`） | `[RECON]`（`:124`） |

**重构所有 5 个 exploit agent 的 prerequisite 都从显式 `['<vuln>-vuln']` 降级为 `[RECON]`**（不止 auth）。这意味着 `injection-exploit` 可在 `injection-vuln` 未完成/失败时启动，理论上存在拿不到 queue 的风险。

**兜底机制**：两版 queue 获取方式相同——都是 prompt 硬编码读文件（重构 `injection-exploit.txt:80` `{{DELIVERABLES_PATH}}/injection_exploitation_queue.json`；原始 `exploit-injection.txt:77`）。即 prerequisite 降级的影响被「exploit agent 自己读文件 + 运行时 exploitation_checker 校验」兜底，不会因 DAG 软依赖而静默拿空文件——但**校验发生在 exploitation 阶段而非 vuln 阶段**，是降级。详见 GAP-INJ-1。

### 5.2 queue 校验：重构更结构化（增强）

| 维度 | 原始 | 重构 |
|---|---|---|
| 校验逻辑 | `queue-validation.ts:288-299` 函数式 pipeline（createPaths→checkFileExistence→validateExistenceRules→validateQueueContent→determineExploitationDecision）；deliverable/queue **对称校验**（`:110-115`） | `exploitation_checker.py:39-141` 5 级校验（文件存在→JSON 解析→结构→deliverable 对称→非空），多出**空队列作为预期失败**（`empty_vulnerabilities`，`:127-134`，`is_expected=True`）的区分 |
| findings 渲染 | `findings-renderer.ts:124-132` `renderInjectionEntry`：只渲染 path/sink_call + mismatch_reason + notes，**丢弃** source/slot_type/sanitization_observed/concat_occurrences/verdict/witness_payload | `findings_renderer.py:35-53` `render_injection_entry`：渲染 source→path/sink_call/concat_occurrences/sanitization_observed/verdict/witness_payload/notes，**保留更多污点证据** |

**裁决**：重构在 queue 校验（更结构化、可观测）和 findings 渲染（保留更多污点证据）两处**增强**。

---

## 6. 两项目共有缺陷（非重构独有）

| # | 缺陷 | 根因 | 影响 | 证据 |
|---|---|---|---|---|
| C-1 | **`vulnerability_type` 无 schema 级 enum** | 两版均开放 string | agent 可能产出枚举外类型值，下游 queue 消费/统计脆弱 | `queue-schemas.ts:41` / `queue_schemas.py:7` |
| C-2 | **injection 专有字段全 optional** | schema 不强制 `slot_type`/`sanitization_observed`/`concat_occurrences`/`verdict` | 可能产出「有空 ID 无实质污点证据」的 finding；下游 exploit agent 拿不到 verdict 难武器化 | `queue_schemas.py:17-20` / `queue-schemas.ts:46-49` |

---

## 7. 差距矩阵 + 严重度/紧急度评估

> **严重度** = 对注入审计效果（检出能力 / 证据质量 / 交付可信度）的影响；**紧急度** = 结合「重构无独立实测 + 确定性层无真机验证」的现状判断。

### A. 重构增强（非 gap，记录价值）

| # | 项 | 重构新增 | 证据强度 |
|---|---|---|---|
| INJ+1 | **确定性 sink/污点分析层** | 47 规则（32 服务 injection）/ 5 语言 + 完整 SlotContext·SinkCategory·TaintFlow 模型 + 跨函数传播 + LLM 函数级 taint；原始为零 | 代码级：强；效果级：弱（无真机命中率） |
| INJ+2 | **`_static-dataflow-hints` 注入闭环** | 确定性发现 → `static_dataflow_hints.md` → prompt include → agent 消费，无断点 | 闭环完整（e2e 测试验证渲染） |
| INJ+3 | **prompt 净增强** | Source Completeness Rule + `authentication_required`/`accessible_routes` 进 schema + exploit 显式 `<vulnerability_entries>` 注入 | 方法论逐节对齐 + 3 项正向加法 |
| INJ+4 | **queue 校验 + findings 渲染增强** | 5 级校验 + is_expected 区分；findings 保留更多污点证据 | 代码级：强 |
| INJ+5 | **测试覆盖从 0 到完整单元层** | 原始全维度零测试；重构 code_index 30 文件（injection 相关 8 个 104 绿）+ schema/renderer/checker | 单元级：强；E2E：无 |

### B. 真正待审视的 gap

| # | 差距项 | 原始 | 重构 | 对效果的影响 | 严重度 | 紧急度 | 修复难度 | 建议 |
|---|---|---|---|---|---|---|---|---|
| **INJ-1** | **重构无独立 injection 端到端实测** | 有 NodeGoat 完整产出（7 queue + 344 行 evidence，全 EXPLOITED） | 6 次 NodeGoat 全停在 risk-scoring，**无 injection 产出**，全项目无 evidence 文件 | 重构 injection 能力 + **新增确定性层** 均未被实测验证，回归风险不可见 | **中-高** | **高** | 低（跑一次完整白盒） | **立即补一次重构完整白盒扫描**（NodeGoat 或 juice-shop live），确认 vuln-injection→injection-exploit 全链路无回归 + 确定性层真机命中率。最高性价比。与 auth AU-1 同源，但 injection 多一层「确定性层未验证」 |
| **INJ-2** | injection-exploit 依赖声明弱 | `prerequisite=['injection-vuln']` 显式 | `prerequisite=[RECON]` 软依赖（5 个 exploit agent 全部如此） | injection-vuln 失败时 injection-exploit 空跑/报错 | **中** | 中 | 低（agents.py 改一行 prerequisite） | 把 `INJECTION_EXPLOIT.prerequisites` 改为 `[RECON, INJECTION_VULN]`。建议 5 个 exploit agent 一并修（统一与 auth AU-2） |
| **INJ-3** | prompt 3 处轻量退化（V1/V6/V7） | 有（starting_context Endpoint Security L43-50 / Branch Path Exhaustion L148 / Cross-Route Verification 自检 L380） | **删除** | 框架盲区提示丢失（V1）；if/else 分支漏报风险（V6）；交付前路由覆盖自检消失（V7） | **低** | 低 | 低（从原始补回 3 段） | 从原始 vuln-injection.txt 补回这 3 段（尤其 V6 防漏报纪律） |
| **INJ-4** | `VALID_INJECTION_CATEGORIES` 缺 deserialization 独立类 | — | 白名单 6 值无 deserialization，但 DEFAULT_RULES 有 5 条 DESER sink | 确定性 DESER sink 的 finding 可能**被白名单静默丢弃**（确定性层与 finding 校验层错配） | **低-中** | 中 | 低（白名单加 `insecure_deserialization`） | `finding_models.py:28-31` 白名单补 deserialization 类，与 DEFAULT_RULES 的 DESER category 对齐 |
| INJ-5 | `vulnerability_type` 无 enum（C-1） | 共有 | 共有 | 类型值不受校验，下游脆弱 | **低** | 低 | 低（加 Literal/enum） | 两项目通用改进，给 schema 加枚举 |
| INJ-6 | injection 专有字段全 optional（C-2） | 共有 | 共有 | 可能产出无实质污点证据的 finding | **低** | 低 | 低 | `slot_type`/`verdict`/`sanitization_observed` 设为 required |

### 7.1 优先级建议（性价比排序）

1. **INJ-1（补独立实测）**：高影响、低难度。重构 injection 至今无端到端背书，且**新增的整个确定性层从未在真机验证过命中率/假阳率**——这是面向交付的最大风险，也是 §3「显著增强」结论从「代码级」升级到「效果级」的唯一途径。**先跑一次再谈其他**。与 auth AU-1 共担一次扫描成本（一次完整白盒同时验证 auth + injection）。
2. **INJ-2（exploit prerequisite，合并 AU-2）**：中影响、极低难度（改一行）。5 个 exploit agent 一并修。与 INJ-1 同批验证。
3. **INJ-4（deserialization 白名单）**：低-中影响、极低难度。这是确定性层与 finding 校验层之间一个真实的小错配（DESER sink 有规则但 finding 可能被拒），属快速 win。
4. **INJ-3（补回 3 处退化）**：低影响、低难度。V6（Branch Path Exhaustion）防漏报纪律值得补回。
5. INJ-5 / INJ-6：低紧急度，schema 强化可统一排期（与 auth C-1/C-2 共担）。

### 7.2 一句话总结

**两项目注入审计的方法论（slot 标签体系、sanitizer↔context、concat-after-sanitize、confidence、OWASP 攻击流、Level 3 门槛、Bypass Exhaustion）逐节对齐。** 与 auth（持平+错配）截然相反，**重构在 injection 维度是净增强**：① 新增了一整层确定性 sink/污点分析（47 规则/32 服务 injection/5 语言，原始为零），闭环完整且单测全绿；② `_static-dataflow-hints` 这同一片段对 injection 是**精准匹配**（对 auth 却是 AU-3 错配）——印证「同片段对不同漏洞类型适用性不同」；③ prompt 新增 Source Completeness Rule + schema 字段强化 + exploit queue 显式注入；④ queue 校验与 findings 渲染更结构化、保留更多污点证据；⑤ 测试从 0 到完整单元层。

**但增强目前停留在代码级证据**：① **重构至今无一次 injection 端到端实测**（INJ-1，最高优先级，且新增的确定性层真机命中率/假阳率无数据）；② injection-exploit prerequisite 软依赖（INJ-2，与 auth AU-2 同构）；③ prompt 有 3 处轻量退化可补（INJ-3）；④ 确定性层与 finding 校验层有 deserialization 错配（INJ-4）。确定性层有诚实盲区（LDAP/NoSQL/ORM 零规则，恰好是原始 NodeGoat 独立检出的 NoSQL 所在）。**结论：能力增强是架构事实，效果验证是待补缺口——「更强但未证」是不对称现状。**

---

## 8. 关键证据索引

### 8.1 重构 Shannon-py（代码）

| 证据 | 路径 |
|---|---|
| vuln-injection prompt（净增强 + 3 退化） | `prompts/vuln-injection.txt:45,108-110,124`（增强）；`:41` vs 原始 L43-50/L148/L380（退化） |
| injection-exploit prompt（vulnerability_entries 注入） | `prompts/injection-exploit.txt:75-78,80` |
| `_static-dataflow-hints`（对 injection 精准匹配） | `prompts/shared/_static-dataflow-hints.txt:1-10` |
| InjectionVulnerability schema（全 optional、无 enum） | `packages/core/src/shannon_core/models/queue_schemas.py:5-22` |
| agent 注册（injection-exploit prereq=[RECON] 软依赖） | `packages/core/src/shannon_core/models/agents.py:96` |
| 确定性 sink 检测（47 规则，32 服务 injection） | `packages/core/src/shannon_core/code_index/sink_detector.py:45,67-198` |
| 污点模型（SlotContext/SinkCategory/TaintFlow） | `packages/core/src/shannon_core/code_index/parameter_models.py:28-37,52-81,132-142` |
| 跨函数传播（regex + 子串过近似 + 保守传播） | `packages/core/src/shannon_core/code_index/chain_propagator.py:31-45,48-75,206,241-245` |
| LLM 函数级 taint | `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py:80-171,278-288` |
| hints 产出闭环 | `packages/core/src/shannon_core/code_index/audit_input_builder.py:289-307`；写盘 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:524,487-489,493-494` |
| finding 校验去重（VALID_INJECTION_CATEGORIES + 白名单 + 5-tuple） | `packages/core/src/shannon_core/code_index/finding_models.py:28-31,56-62,81-89,105-131` |
| queue 校验（5 级 + is_expected） | `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py:39-141` |
| findings 渲染（保留更多污点证据） | `packages/core/src/shannon_core/services/findings_renderer.py:35-53` |
| 无独立 injection 产出（NodeGoat 停在 risk-scoring） | `workspaces/NodeGoat_shannon-1782059117521/`（workflow.log） |
| 确定性层测试（8 文件 104 绿，无真机 e2e） | `packages/core/tests/code_index/`（test_sink_detector/chain_propagator/llm_taint_analyzer/sink_merger/finding_models/static_dataflow_hints[_e2e]） |

### 8.2 原始 Shannon（代码 + 独立产出）

| 证据 | 路径 |
|---|---|
| vuln-injection prompt（含 V1/V6/V7 退化项原文） | `apps/worker/prompts/vuln-injection.txt:43-50,148,380` |
| exploit-injection prompt（Level 1-4 + attack_patterns） | `apps/worker/prompts/exploit-injection.txt:214-232,241-259,323-339` |
| InjectionVulnerability Zod schema（无 enum、全 optional） | `apps/worker/src/ai/queue-schemas.ts:41-52` |
| injection-exploit 显式 prerequisite | `apps/worker/src/session-manager.ts:75` |
| 无确定性层（检索证据空） | `apps/worker/src/audit/`（8 文件无分析器）；无 tree-sitter 依赖；`vuln-injection.txt` 无 hints include |
| 独立 NodeGoat injection queue（7 条：NoSQL×2 + CommandInjection×5） | `shannon/workspaces/NodeGoat_whitebox-1780678757791/deliverables/injection_exploitation_queue.json` |
| 独立 NodeGoat injection evidence（344 行，全 Successfully Exploited，含 RCE/SSRF/MongoDB dump/NoSQL 绕过） | `shannon/workspaces/NodeGoat_whitebox-1780678757791/deliverables/injection_exploitation_evidence.md` |

### 8.3 共享数据（两项目逐字节相同，仅作体系参照）

| 证据 | 路径 |
|---|---|
| juice-shop injection queue（10 条：5 子类各 2，全 exploitable） | `workspaces/juice-shop_whitebox-1780587584138/deliverables/injection_exploitation_queue.json` |
| juice-shop injection analysis（24306 bytes） | `workspaces/juice-shop_whitebox-1780587584138/deliverables/injection_analysis_deliverable.md` |

### 8.4 交叉参考

- `docs/gap/2026-06-21-auth-effect-gap-analysis.md` — 认证效果（姊妹篇 + 反例；AU-1/AU-2 与 INJ-1/INJ-2 同构，AU-3 错配在 injection 反转为增强）
- `docs/gap/2026-06-21-vuln-agent-gap-analysis.md` — vuln agent 全链路结构对齐（injection 类型注册/schema/exploit 数量）
- `docs/gap/sink-gap-analysis-v2.md` — Sink 点方法论差距（本文 §3 聚焦其确定性实现）
- `docs/gap/2026-06-18-prerecon-recon-gap-analysis.md` — recon 阶段（injection agent 的输入）
- `docs/whitebox-refactoring-assessment.md` — 全维度重构评估
