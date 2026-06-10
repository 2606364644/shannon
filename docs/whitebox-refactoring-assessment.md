# Shannon 白盒分析重构评估报告

> 对比原始 Shannon（TypeScript）与重构后 Shannon-py（Python）在白盒安全分析三个核心维度（sink / 入口点 / 漏洞）的优劣。
>
> **性质**：活文档（评估报告），持续完善，**不是实现 Spec**。§5.3 的缺口表是未来 Spec 的来源——每个缺口成熟后在 `docs/superpowers/specs/` 单独开 spec（brainstorming → spec → plan）。

---

## 修订历史

| 版本 | 日期 | 要点 |
|---|---|---|
| v1 | 2026-06-09 | 初版，误判为"确定性引擎替代 LLM" |
| v2 | 2026-06-09 | 纠正为"加法层"（LLM 仍跑），补 5 个漏掉问题 |
| v3 | 2026-06-09 | sink 规则数纠正 197+→47，核验 SSRF/路径/XXE/XSS 覆盖更窄 |
| v4 | 2026-06-09 | prompt 逐行 diff 揭出根因：recon 4.1/4.2 删除是能力替换非加法 |
| v5 | 2026-06-10 | 独立六维度量化评估，确认 v4 结论，补充入口点/Sink 覆盖数据 |

---

## 0. 真实范式：确定性预分析层 + 改写后的 LLM 流水线

### 0.1 真实流水线编排

```
run_preflight → run_credential_check → run_auth_validation
→ run_code_index          # 确定性：AST sink/call_graph/propagation
→ run_agent(PRE_RECON)    # LLM：照跑（prompt 已改写，见 §0.3/§2.4）
→ run_save_adjudication   # 确定性：橡皮图章裁定（见 §2.3）
→ run_rebuild_call_chains # 确定性：BFS 重建链
→ run_agent(RECON)        # LLM：照跑（prompt 删了 4.1/4.2，见 §0.3）
→ run_risk_scoring        # 确定性：打分 + 分层 plan
→ run_render_dataflow_hints # 确定性：产出 static_dataflow_hints.md
→ run_vuln_agent × N      # LLM：按漏洞类并行（prompt 删了 cross-route 依赖）
→ render_findings
```

**核心事实**：原始 LLM 流水线**仍在跑**，但 prompt 已被系统性改写（删除对 recon 4.1/4.2 的依赖、注入确定性 hint）。确定性引擎是前置预分析层，其产物经 `_static-dataflow-hints.txt` 折成文本注入。

**推论**：重构**不可能"更差"在底层能力**（LLM 仍跑），但在**删除后无替代的结构化分析面**（authz/模板/路由级 auth）上**主动弱于原始**。见 §0.3。

### 0.2 范式对照

| | 原始 Shannon (TS) | 重构 Shannon-py (Python) |
|---|---|---|
| **分析引擎** | 纯 LLM | **确定性预分析（AST + 规则库 + taint 图）+ 改写后的 LLM 流水线 + 可选 hint** |
| **LLM 角色** | 主执行者 | **仍是主执行者**；确定性层只做前置 hint |
| **结构化索引** | recon LLM 产 4.1/4.2（路由分组/端点安全上下文/框架来源） | **删除**，改用 taint 图（不携带路由 auth/框架来源） |

### 0.3 根因：能力替换，不是纯加法（recon 4.1/4.2 删除的连锁）

重构做了一个**架构性取舍**：用确定性 taint 传播图替换 recon 的 LLM 结构化索引。这导致一条删除链：

```
recon.txt 删除 Section 4.1（共享 handler 路由分组）+ 4.2（端点安全上下文/框架来源/参数完整性）
   ↓ （4.1/4.2 不再产出）
所有 vuln prompt 删除引用它们的内容：
   - _cross-route-enumeration.txt 引入（依赖 4.1）
   - Cross-Route Verification 门控（依赖 4.1）
   - vuln-xss Endpoint Security Context starting_context（依赖 4.2）
   - vuln-authz ### 0) Read Endpoint Security Context + Framework Endpoint Guidance（依赖 4.2）← 最大回退
pre-recon-code.txt 删除：
   - Entry Point Mapper 的 schema 编目 + public/auth 区分（确定性层也没实现）
   - Sink Hunter 强制两步模板流程 + 变体审计 + Coverage Audit 表（确定性层也不覆盖模板文件）
   ↓
替换为：taint 图（parameter_graph.json）+ AST sink + 内联 accessible_routes/authentication_required（自由填写）
```

**为什么这是"替换"而非"加法"**：被删的 recon 4.1/4.2 不是冗余——它们是原始项目路由级可达性/认证/IDOR 分析的**唯一结构化数据源**。taint 图提供 source→sink 流，但**不携带**：每路由的 auth 中间件、共享 handler 的路由分组、框架自动生成标记（finale-rest/epilogue）、模板变量与 input type 的交叉验证。这些能力的"确定性替代"基本不存在（risk_scorer 的 auth 检测只是函数名关键字启发式，§3.3）。

**净效应**：
- 重构**赢**在：Py/TS 的 taint 传播确定性、AST sink 结构化、风险打分可观测。
- 重构**输**在：authz 框架 IDOR 检测（无替代）、模板 sink 检测（双层落空）、路由级 auth/reachability（从表查询退化为 LLM 自由填写）、schema-first 入口（指令都删了）。

---

## 1. Sink 分析对比

### 1.1 检测机制

**原始**：pre-recon 的 "XSS/Injection Sink Hunter" Task 子 agent —— Glob 枚举模板文件 → 逐文件 Read → LLM 判定；业务代码 Grep 危险 API 名定位后 Read 确认。

**重构**：`sink_detector.py:detect_sinks()` 遍历 AST call node，匹配 `SinkRule`，构建 `SinkCallSite`。产物经 hint 间接喂 LLM。

### 1.2 规则库

**原始**：规则硬编码在 prompt 自然语言（XSS `pre-recon-code.txt:289-321`、SSRF `:333-415`）。

**重构**：`DEFAULT_RULES`（**实测 47 条** `SinkRule`，`sink_detector.py:67-215`）。✅ 精确、可维护。但覆盖广度见 §1.6。

### 1.3 Slot 类型系统

基本对齐。重构把 `CMD-part-of-string`/`FILE-include` 并入 `CMD_ARGUMENT`/`FILE_PATH`，新增 `URL`/`GENERIC`。**平手。**

### 1.4 模板/视图文件 Sink 检测 ⚠️ 双层落空

**原始**：pre-recon prompt 有**强制两步流程**（glob 枚举模板 → 逐文件区分转义模式 EJS `<%= %>` vs `<%- %>`、Jinja2 `{{ }}` vs `{{|safe}}`）+ Cross-Variant Verification + Template Coverage Audit 完整性表。

**重构**：⚠️ **双层落空**。
- 确定性层：`sink_detector.py` 只覆盖函数调用级模板 sink（`render_template_string`/`jinja2.render`，`:164-167`），不分析模板文件转义指令。
- LLM 层：pre-recon prompt 的"强制两步模板流程 + 变体审计 + Coverage Audit 表"**整段删除**，只剩一句泛泛"Find all dangerous sinks..."。

**评估**：模板重前端项目，模板类 SSTI/XSS sink 在**确定性层和 LLM 层两头都没有专门覆盖**。❌❌

### 1.5 变体审计 ⚠️ 缺失

**原始**：pre-recon 强制变体覆盖审计（`pre-recon-code.txt:135-136`）。

**重构**：确定性无；LLM 指令也删了。❌

### 1.6 规则覆盖度核验 ⚠️ 结构更优，但覆盖明显更窄

47 条规则按 `(类别 × 语言)` 对照原始目录：

| 类别 | 覆盖语言 | 重构 | 对照原始 |
|---|---|---|---|
| SQL | Py/TS/Go/Java/PHP | 8 条，较全 | ✅ 持平 |
| COMMAND | 全 5 语言 | 14 条，**最完整** | ✅ 优于原始 |
| DESERIALIZATION | Py/Java/PHP | 5 条 | ✅ 较全（缺 marshal/JS） |
| SSRF | 全 5 语言 | 11 条但**仅 HTTP client 一类** | ❌ **13 子类仅 ~3** |
| TEMPLATE(SSTI) | 仅 Python | 2 条 | ⚠️ 缺 TS/PHP |
| XSS | 仅 TS | 2 条 | ❌ **5 上下文仅 ~1.5** |
| FILE | 仅 PHP | 3 条（写入/包含） | ❌ **读取类全缺** |
| REDIRECT | Py/TS | 2 条 | ⚠️ 缺 Go/Java/PHP |
| XXE | — | **0 条，无枚举** | ❌ **完全缺失** |

**最关键覆盖缺口**：(1) SSRF 缺 ~10/13 子类（socket/headless/media/JWKS/cloud metadata）；(2) 路径穿越读取近乎盲区（`fopen`/`readFile`/`open()`/`os.ReadFile`/`fs.readFile` 全缺，原始 `pre-recon-code.txt:133` 明确点名）；(3) XXE 完全无；(4) XSS 5 上下文仅 ~1.5（赋值形态 `innerHTML=x` 让渡 LLM，`eval` 归错类）。

**§1 裁决**：检测引擎重构是质的进步；但**覆盖广度**在 SSRF/路径/XXE/XSS 上**明显窄于原始**。结构更优 ≠ 覆盖更广。⚠️

---

## 2. 入口点分析对比

### 2.1 检测机制

**原始**：pre-recon "Entry Point Mapper" 子 agent —— Grep/Glob 找路由 → Read 提取 → **若有 API schema 直接读** → 标注 public/需认证 → 排除本地工具。

**重构**：`entry_points.py:detect_entry_points()` 按 language AST 模式匹配，confidence 0.30-0.95（硬编码），`needs_llm_review`。覆盖 ≥8 框架/类型。✅ 检测确定性优于原始。

### 2.2 功能完整性对比

| 功能 | 原始 | 重构 | 评估 |
|---|---|---|---|
| HTTP 路由检测 | ✅ LLM | ✅ AST 模式匹配 | **重构更可靠** |
| API Schema 优先读取 | ✅（OpenAPI/Swagger/GraphQL） | ❌ 代码无；LLM 指令也删了 | **原始胜** |
| 认证标注 | ✅ 每入口标 public/需认证 | ❌ 模型无字段；LLM 指令也删了 | **原始胜** |
| 网络可达性过滤 | ✅ 系统性 | ⚠️ 部分（仅 Python async 候选） | **原始胜** |
| Webhook/Upload | ✅ prompt 覆盖 | ❌ 缺失 | **原始胜** |
| 置信度评分 | ❌ | ✅ 0.30-0.95（硬编码） | **重构新增** |
| 多框架支持 | ✅ LLM 理论不限 | ✅ ≥8 框架 | 平手 |
| 后台任务检测 | ✅ | ✅ Celery/RabbitListener | 平手 |

### 2.3 入口裁定：橡皮图章 + Phase 0 不可执行 ⚠️

**重构 `save_adjudication`**（`__init__.py:258-267`）无条件全置 `verdict=CONFIRMED, source=CODE_INDEX`，不看 confidence/`needs_llm_review`。

更深层问题：
1. pre-recon prompt 有 `Phase 0: Entry Point Adjudication` 指令——要求按 confidence 裁定 confirmed/rejected/reclassified，写 `entry_points.json`。**但依赖的 `save-deliverable --type ENTRY_POINTS` 工具在 Python 代码库不存在**（只在 prompt 文本里）。**Phase 0 物理不可执行。**
2. workflow（`workflows.py:112-135`）PRE_RECON 后无条件跑 `run_save_adjudication`，全量覆盖任何裁定输出。
3. `AgentName` 17 个 agent，无 adjudication agent；设计文档自证 "No LLM adjudication yet"。

**影响**：假阳性入口直成调用链根。`entry_point_fusion.py:merge_entry_points`（三源去重）**已实现但 `build_code_index` 从不调用**（死代码）。❌

### 2.4 两层架构（recon 4.1/4.2 生产者被删）

**原始**：pre-recon Entry Point Mapper（Section 5）→ **recon 细化（Section 4.1 共享 handler 分组 + Section 4.2 Endpoint Security Context）**。这两节是后续调用链追踪 + 漏洞研判的关键索引。

**重构**：⚠️ **recon.txt 把 4.1/4.2 整段删除**。无"共享 handler 分组"、无"中间件链"、无"框架来源"细化，也无 `_endpoint-security-context.txt`/`_cross-route-enumeration.txt`（后者整个文件删）。这正是 §0.3 连锁的根。❌

---

## 3. 漏洞分析对比

### 3.1 调用链追踪：确定性但残缺且静默；非空 flow 有前提

**原始**：子 agent Read/Grep"人工跳转"，复杂链几十次工具调用。

**重构**：确定性传播图（`propagation_builder.py`）seed→intra→chain。

**两个真实边界**：
- 调用图残缺且静默：`call_graph.py:17-19` 名匹配首匹配、剪菱形，丢 30-50% 跨文件调用；`run_code_index` 调 `build_code_index` 不写降级报告。
- 非空 flow 有前提：`build_code_index` 单跑 `chains=[]` → flow 恒空；**需 `rebuild_call_chains` + 入口被装饰器命中**才产出非空精确 flow（e2e 测试实跑通过）。

### 3.2 传播不完备 + 写法 bug

声明四项不完备（无不动点、容器读过近似、分支保守、sanitizer 仅提示）属实。

**写法 bug**：`analyze_intra` LHS 正则要求纯标识符，导致 `d["k"]=x`/`a,b=x,y`/`self.x=x`/`lst.append(x)` **全部静默漏检**。"容器过近似"只对读成立，对写是 false-negative。

### 3.3 分层审计：算了但没接

`AUDIT_TIER1` agent + `audit-tier1.txt` 注册但全仓无调用；workflow 按漏洞类跑不按 chain/tier；`audit_input_builder` 按链构造器无生产调用方。未接线的论断正确。

### 3.4 漏洞研判 prompt：跨 prompt 一致的"删 4.1/4.2 依赖 + 加源粒度"

跨 vuln-xss/ssrf/authz/injection 的 diff 发现**高度一致的跨 prompt 模式**：

**一致删除（所有 vuln prompt）**：`@include(_cross-route-enumeration.txt)` + conclusion-trigger 的 Cross-Route Verification 门控。

**vuln-xss 额外删**：Endpoint Security Context (4.2) starting_context + "server-rendered templates 注记"（含 `JSON.stringify` 在 `<script>` 不转义 `</script>` 的具体洞察）。

**vuln-authz 删得最狠（最大回退）**：整段 `### 0) Read Endpoint Security Context (REQUIRED)` 必做首步（查每端点 auth/middleware/framework origin/ownership，优先框架自动生成端点）+ `Framework Endpoint Guidance`（finale-rest/epilogue IDOR 检测逻辑：默认无 ownership 校验→假设 IDOR vulnerable）。**无任何替代**。

**一致新增**：`authentication_required`/`accessible_routes`/Source Completeness Rule（每源独立条目，比原始 `combined_sources` 严格）。但 `accessible_routes` 是**自由填写**，无 4.1 表 backing。

**vuln-injection 特有删除**：Branch Path Exhaustion（分支穷举）+ cross-route-enumeration。

**§3.4 裁决**：重构 vuln prompt 在**源粒度 + 认证/路由标注字段**上更强，但在**分支穷举（injection）+ 路由覆盖校验（全删）+ 端点安全上下文/框架 IDOR（authz，最大回退）**上更弱。**判定逻辑（slot/concat-after-sanitize/witness）两版一致。** vuln-authz 因丢失 recon 4.2 输入 + 框架 IDOR 检测，是三类中**回退最严重**的。⚠️⚠️

### 3.5 研判产物结构化

`finding_models.py`：Pydantic + 白名单（校验 `issue_type` 不校验 `category`）+ 5 元组去重 `(entry_point_id, category, issue_type, vulnerable_function_id, call_chain_path)`。✅ 比原始可靠。

### 3.6 GitNexus FULL 模式：未实现

`__init__.py:161-163` TODO；流水线 `run_code_index` 调 `build_code_index` 不走 gitnexus。❌

---

## 4. 综合评分

| 维度 | 原始 | 重构 | 优势方 | 关键限定 |
|---|---|---|---|---|
| Sink 引擎确定性/结构化 | ★★ | ★★★★★ | **重构** | — |
| Sink 规则可维护性 | ★★ | ★★★★★ | **重构** | 47 条 |
| Sink 覆盖广度 | ★★★★ | ★★ | **原始** | SSRF/路径/XXE/XSS 更窄 |
| Sink 命令注入/反序列化/SQL | ★★★ | ★★★★ | **重构** | — |
| 模板文件 sink 覆盖 | ★★★★ | ☆ | **原始** | 双层落空 |
| 变体审计 | ★★★★ | ☆ | **原始** | prompt 层也删 |
| 入口点确定性+置信度 | ★★ | ★★★★ | **重构** | 硬编码 |
| 入口 schema 优先 | ★★★★★ | ☆ | **原始** | 指令也删 |
| 入口认证标注 | ★★★★ | ☆ | **原始** | 指令也删 |
| 入口裁定把关 | ★★★ | ★ | **原始** | Phase 0 不可执行 |
| recon 4.1/4.2 结构化索引 | ★★★★ | ☆ | **原始** | 生产者删除 |
| 调用链确定性 | ★ | ★★★ | **重构（有限）** | 静默残缺 |
| 跨语言 taint 传播 | LLM 兜底 | 仅 Py/TS | **平手** | Go/Java/PHP 零 |
| 传播写法覆盖 | n/a | ★★ | — | 写容器/属性漏检 |
| 分层按链审计 | 无 | 算了没用 | **平手** | 未接线 |
| vuln prompt 源粒度/认证字段 | ★★★ | ★★★★ | **重构** | — |
| vuln prompt 分支穷举/路由校验 | ★★★★ | ★★ | **原始** | injection 分支穷举删 |
| **vuln-authz 框架 IDOR 检测** | ★★★★ | ☆ | **原始** | 最大回退、无替代 |
| 漏洞研判(slot/verdict) | LLM | LLM（同逻辑） | **平手** | — |
| FULL 模式(GitNexus) | n/a | 未实现 | — | TODO |
| 结果结构化/去重 | ★★ | ★★★★★ | **重构** | — |
| 可测试性 | ★ | ★★★★★ | **重构** | — |
| Token 成本 | 高 | 略降 | **重构（有限）** | LLM 仍主跑 |

---

## 5. 结论

### 5.1 一句话裁决

> **重构是"能力替换"而非"纯加法"：用确定性 taint 图 + AST sink 替换了 recon 的结构化索引（路由分组/端点安全上下文/框架 IDOR）和模板 sink 方法论。在 Py/TS 的确定性 taint 传播、AST sink 结构化、风险打分上明显更优；但在 authz 框架 IDOR、模板 sink、路由级 auth/reachability、schema-first 入口这些"删除后无确定性替代"的面上，重构主动弱于原始——尽管 LLM 流水线仍在跑。**

不存在"全面更差"（原 LLM 仍在），但在 **Go/Java/PHP 后端、模板重前端、schema-first API、SSRF/路径/XXE 风险面、以及授权/IDOR 分析**上，接近或弱于"原项目"。

### 5.2 重构真实的核心价值（成立）

1. 确定性预分析层（sink 规则、taint 传播、调用链、风险评分）方向正确、工程扎实。
2. 结构化与去重（Pydantic + 白名单 + 5 元组）。
3. 可测试性（规则库/传播器单测齐全）。
4. vuln prompt 源粒度增强（Source Completeness Rule、认证字段）。
5. Py/TS 画像上确定性链路完整可用（需链重建激活）。

### 5.3 真实缺口

| # | 缺口 | 严重度 | 备注 |
|---|---|---|---|
| 1 | 模板/视图文件 sink 检测 | **高** | 双层落空 |
| 2 | API schema 优先读取 | 中 | 指令也删 |
| 3 | 入口点认证标注 | 中 | 指令也删 |
| 4 | 网络可达性系统性过滤 | 低 | — |
| 5 | 变体审计 | 低 | prompt 层也删 |
| 6 | Go/Java/PHP 传播支持 | **中-高** | 三语言零增益 |
| 7 | 入口裁定橡皮图章/Phase 0 不可执行 | 中 | save-deliverable 缺失 |
| 8 | 调用图静默残缺 | **高** | 名匹配丢30-50% |
| 9 | 分层按链审计未接线 | 中 | — |
| 10 | GitNexus FULL 未实现 | 中 | — |
| 11 | SSRF 覆盖仅 ~3/13 子类 | **高** | — |
| 12 | 路径穿越读取类 sink 盲区 | **高** | fopen/readFile 全缺 |
| 13 | XXE 完全缺失 | 中-高 | — |
| 14 | XSS 仅 ~1.5/5 上下文 | 中 | — |
| 15 | vuln prompt 删 Branch Path Exhaustion | 中 | injection |
| 16 | 传播写容器/属性漏检 | 中 | false-negative |
| 17 | sink 规则数文档错误 | 低 | 197+→47 |
| **18** | **recon 4.1/4.2 结构化索引删除** | **高** | 根因：路由分组/端点安全上下文/参数完整性全删 |
| **19** | **vuln-authz 丢框架 IDOR 检测** | **高** | Section 0 + Framework Guidance 删，无替代 |
| **20** | **pre-recon 模板 sink 方法论删除** | **高** | 强制两步流程+变体审计+Coverage Audit 删，确定性层也不覆盖 |
| **21** | **Phase 0 入口裁定指令不可执行** | 中 | 依赖不存在的 save-deliverable |

### 5.4 给决策者的判断

- **Python/TypeScript 后端（非 authz 重点）**：重构确定性层**有用**，推荐——但先补 #1/#8/#11/#15/#18/#19。
- **Go/Java/PHP 后端**：确定性层近乎透明，**与原项目等价 + 多余复杂度**，先做 #6。
- **授权/IDOR 是重点目标**：⚠️ **重构当前主动弱于原始**（#19），authz 丢了 recon 4.2 + 框架 IDOR，**短期不如用原始**，或优先补 #18/#19。
- **模板重前端 / schema-first API**：#1/#2/#20 使确定性层增益有限，**原项目更全**。
- **SSRF / 路径穿越 / XXE 主导风险面**：#11/#12/#13 使确定性 sink 层**不如原始 prompt 目录全**。
- **看重工程可维护性/可测试性**：重构明显更优。

### 5.5 修复优先级建议

按"性价比 = 影响面 × 修复难度倒数"：

1. **#18 recon 4.1/4.2 恢复**（高影响、中难度）：把删掉的共享 handler 分组 + 端点安全上下文指令加回 recon.txt，确定性 entry_points 已有路由/装饰器数据可 seed。**一处修复解除 #15/#19 大部分连锁**。
2. **#8 调用图残缺报告**（高影响、低难度）：`run_code_index` 改调 `build_code_index_with_gitnexus` 或至少写出 `degradation_report.json`，让残缺可见。
3. **#11/#12/#13 sink 规则补齐**（高影响、低难度）：加 SSRF 子类（socket/headless/media/JWKS/cloud metadata）+ 路径读取（fopen/readFile/open）+ XXE 规则——纯加 `SinkRule`，有单测框架。
4. **#1/#20 模板 sink**（高影响、中难度）：glob 模板文件 + 转义指令正则/AST，补回原始两步流程。
5. **#7/#21 入口裁定**（中影响、低难度）：实现 `save-deliverable` 或让 Phase 0 真跑，去掉橡皮图章覆盖。

---

## 附录 A：关键文件对照

| 功能 | 原始 (TS) | 重构 (Python) | 状态 |
|---|---|---|---|
| Sink 规则库 | prompt 目录 | `sink_detector.py:67`（47 条） | ✅ 结构优；⚠️ 覆盖窄 |
| 模板 sink 方法论 | pre-recon 强制两步+变体+Audit 表 | **prompt 删 + 代码无** | ❌❌ 双层落空 |
| 入口 schema/auth | Entry Point Mapper 指令 | **prompt 删 + 代码无** | ❌ |
| 入口裁定 | LLM 两层 | save_adjudication 橡皮图章；Phase 0 不可执行 | ❌ |
| **recon 4.1 路由分组** | `### 4.1 Shared Controller Route Groups` | **整段删除** | ❌ 根因 |
| **recon 4.2 端点安全上下文** | `## 4.2 Endpoint Security Context` | **整段删除** | ❌ 根因 |
| vuln-authz 框架 IDOR | Section 0 + Framework Guidance | **删除无替代** | ❌❌ 最大回退 |
| vuln-* cross-route 枚举 | `_cross-route-enumeration.txt` + 门控 | **全删** | ❌ |
| vuln-* 源粒度 | combined_sources(信息性) | Source Completeness Rule(强制) | ✅ |
| vuln-injection 分支穷举 | Branch Path Exhaustion | 删除 | ❌ |
| 调用链 | 子 agent 跳转 | `call_graph.py:17-19` 名匹配 | ⚠️ 残缺静默 |
| 跨语言传播 | LLM 兜底 | `{go,java,php}` 零 | ❌ |
| 传播写法 | n/a | 写容器/属性漏检 | ❌ |
| 漏洞研判 | LLM slot 匹配 | LLM（同逻辑） | ⚠️ |
| 分层审计 | 无 | `AUDIT_TIER1`(注册未调用) | ⚠️ |
| GitNexus FULL | n/a | TODO | ❌ |
| 结果去重 | 无 | 5 元组 | ✅ |
| hint 注入 | 无 | `_static-dataflow-hints.txt` + `<parameter_propagation_data>` + `<phase0_data>` | ✅ |

## 附录 B：prompt diff 证据索引

- `diff shannon/apps/worker/prompts/{vuln-xss,vuln-ssrf,vuln-authz}.txt shannon-py/prompts/...` —— 三 vuln prompt 一致删 `_cross-route-enumeration` + Cross-Route Verification 门控；vuln-authz 额外删 Section 0 + Framework Guidance。
- `diff .../pre-recon-code.txt` —— 删 Entry Point Mapper schema 指令 + Sink Hunter 两步模板流程 + 变体审计 + Coverage Audit；新增 Phase 0（依赖不存在的 save-deliverable）+ `<phase0_data>`。
- `diff .../recon.txt` —— 删整段 4.1/4.2 + Route Mapper 分组指令 + Input Validator 字段枚举 + `_endpoint-security-context.txt`；新增 `<parameter_propagation_data>` + `<no_security_judgments>`。
- 原始 `_cross-route-enumeration.txt`（3119 字节）/ `_endpoint-security-context.txt` 在重构版 `prompts/shared/` 不存在。
