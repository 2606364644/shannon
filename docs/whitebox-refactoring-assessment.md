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
| v6 | 2026-06-10 | 代码级核验 17 项论断全部通过；修正两处表述：risk_scorer auth 为集合匹配非关键字启发式、file_discovery.py 有模板/schema 分类但断路未接入 |
| v7 | 2026-06-10 | **重大修正**：v3/v5 对 SSRF/XSS 覆盖的评估仅统计了确定性层（47 条 SinkRule），忽略了 LLM prompt 层（`pre-recon-code.txt:304-438`）仍完整保留原始的 13 SSRF 子类 + 5 XSS 上下文目录。SSRF/XSS 覆盖差距被显著高估。同步补充重构版 recon.txt 新增结构（Section 7/8/6.4）和 vuln-authz 方法论增强 |
| v8 | 2026-06-11 | **架构级更新**：GitNexus FULL 模式已实现（替换旧 call_graph + propagation_builder）、入口点融合（4源合并）+ 裁定改为 confidence 阈值、模板 sink 方法论 LLM 层已恢复、sink merger 模块、LLM taint 分析替代 regex（修写法 bug）、PRE_RECON 与 code_index 并行执行。关闭 8 项缺口、部分解决 3 项 |

---

## 0. 真实范式：确定性预分析层 + 改写后的 LLM 流水线

### 0.1 真实流水线编排（v8 更新）

```
run_preflight → run_credential_check → run_auth_validation
→ ┌ asyncio.gather ─────────────────────────────────────────┐
│ │ run_code_index              # GitNexus + AST + LLM taint │
│ │ run_agent(PRE_RECON)        # LLM（prompt 已改写）        │
│ └─────────────────────────────────────────────────────────┘
→ run_merge_sink_reports    # 确定性 + LLM sink 去重合并（新增）
→ run_entry_point_fusion    # 4 源入口合并（GitNexus/Schema/Convention/LLM）（新增，原为死代码）
→ run_save_adjudication     # confidence 阈值裁定（不再是橡皮图章）（已修复）
→ run_agent(RECON)          # LLM：照跑（prompt 删了 4.1/4.2，见 §0.3）
→ run_risk_scoring          # 确定性：打分 + 分层 plan
→ run_render_dataflow_hints # 确定性：产出 static_dataflow_hints.md
→ run_vuln_agent × N        # LLM：按漏洞类并行（prompt 删了 cross-route 依赖）
→ render_findings
```

**核心事实**：原始 LLM 流水线**仍在跑**，但 prompt 已被系统性改写（删除对 recon 4.1/4.2 的依赖、注入确定性 hint）。确定性引擎是前置预分析层，其产物经 `_static-dataflow-hints.txt` 折成文本注入。

**v8 变更**：(1) code_index 与 PRE_RECON **并行执行**（`asyncio.gather`）；(2) 新增 sink merger 合并确定性 + LLM sink；(3) 入口点融合从死代码变为活跃（4 源合并）；(4) 裁定改为 confidence 阈值（≥0.85 CONFIRMED / <0.50 REJECTED / 其余 NEEDS_REVIEW）；(5) GitNexus 替换旧 call_graph + propagation_builder。

**推论**：重构**不可能"更差"在底层能力**（LLM 仍跑），但在**删除后无替代的结构化分析面**（authz/模板/路由级 auth）上**主动弱于原始**。见 §0.3。

### 0.2 范式对照（v8 更新）

| | 原始 Shannon (TS) | 重构 Shannon-py (Python) |
|---|---|---|
| **分析引擎** | 纯 LLM | **确定性预分析（GitNexus 调用图 + AST sink + LLM taint + sink 合并）+ 改写后的 LLM 流水线 + hint** |
| **LLM 角色** | 主执行者 | **仍是主执行者**；确定性层做前置 hint + LLM taint 分析 |
| **结构化索引** | recon LLM 产 4.1/4.2（路由分组/端点安全上下文/框架来源） | **删除**，改用 taint 图 + GitNexus 精确调用图（不携带路由 auth/框架来源） |
| **入口点融合** | 无 | **4 源合并**（GitNexus/Schema/Convention/LLM）✨ |
| **Sink 合并** | 无 | **确定性 + LLM 去重合并** ✨ |
| **Taint 分析** | 无 | **LLM per-function taint + 确定性 cross-function 传播** ✨ |

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

**为什么这是"替换"而非"加法"**：被删的 recon 4.1/4.2 不是冗余——它们是原始项目路由级可达性/认证/IDOR 分析的**唯一结构化数据源**。taint 图提供 source→sink 流，但**不携带**：每路由的 auth 中间件、共享 handler 的路由分组、框架自动生成标记（finale-rest/epilogue）、模板变量与 input type 的交叉验证。这些能力的"确定性替代"基本不存在（risk_scorer 的 auth 检测是调用链节点 ID 与 `auth_middleware_ids` 集合的精确成员匹配，但该集合本身的来源仍依赖函数名模式填充，§3.3）。

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

### 1.4 模板/视图文件 Sink 检测 ⚠️ 确定性层仍断路，LLM prompt 层已恢复（v8 更新）

**原始**：pre-recon prompt 有**强制两步流程**（glob 枚举模板 → 逐文件区分转义模式 EJS `<%= %>` vs `<%- %>`、Jinja2 `{{ }}` vs `{{|safe}}`）+ Cross-Variant Verification + Template Coverage Audit 完整性表。

**重构 v8**：
- 确定性层：`sink_detector.py` 只覆盖函数调用级模板 sink（`render_template_string`/`jinja2.render`），不分析模板文件转义指令。`file_discovery.py:15-17` 的 `SECURITY_FILE_TYPES` 已覆盖 10 种模板扩展名做文件分类清单，但此清单**仍不传递给 `sink_detector` 做转义指令分析**——能力已编码但断路（未变）。⚠️
- LLM 层：✅ **已恢复原始模板方法论**（commit `b3c58bd`）。`pre-recon-code.txt` 恢复了：强制两步模板流程（Step 1 模板文件清单 + Step 2 逐文件转义模式区分）、Cross-Variant Verification、Template Coverage Audit 表。LLM prompt 层模板覆盖**与原始一致**。

**评估**：模板检测从"双层落空"改善为"确定性层断路 + LLM 层完整覆盖"。实际效果：LLM 仍能系统性检测模板 sink，但无确定性 hint 加持。`file_discovery.py` 的模板文件清单仍是确定性层可恢复的接线点。⚠️（从 ❌❌ 升级为 ⚠️）

### 1.5 变体审计 ⚠️ 缺失

**原始**：pre-recon 强制变体覆盖审计（`pre-recon-code.txt:135-136`）。

**重构**：确定性无；LLM 指令也删了。❌

### 1.6 规则覆盖度核验 ⚠️ 确定性层更窄，但 LLM prompt 层完整保留原始目录

**⚠️ v7 重大修正**：v3-v5 本节仅统计了确定性层（47 条 `SinkRule`），忽略了 LLM prompt 层（`pre-recon-code.txt:304-438`）仍完整保留原始项目的全部 SSRF 13 子类 + XSS 5 上下文分类目录。下表区分两层覆盖。

#### 确定性层覆盖（47 条 SinkRule）

| 类别 | 覆盖语言 | 确定性规则 | 对照原始 |
|---|---|---|---|
| SQL | Py/TS/Go/Java/PHP | 8 条，较全 | ✅ 持平 |
| COMMAND | 全 5 语言 | 14 条，**最完整** | ✅ 优于原始 |
| DESERIALIZATION | Py/Java/PHP | 5 条 | ✅ 较全（缺 marshal/JS） |
| SSRF | 全 5 语言 | 11 条但**仅 HTTP client 一类** | ⚠️ 确定性 hint 仅 HTTP client |
| TEMPLATE(SSTI) | 仅 Python | 2 条 | ⚠️ 缺 TS/PHP |
| XSS | 仅 TS | 2 条 | ⚠️ 确定性 hint 仅 innerHTML/document.write |
| FILE | 仅 PHP | 3 条（写入/包含） | ❌ **读取类全缺** |
| REDIRECT | Py/TS | 2 条 | ⚠️ 缺 Go/Java/PHP |
| XXE | — | **0 条，无枚举** | ❌ **完全缺失** |

#### LLM prompt 层覆盖（pre-recon-code.txt Section 9/10）

| 类别 | LLM prompt 覆盖 | 对照原始 |
|---|---|---|
| SSRF | **13/13 子类全部保留**（`pre-recon-code.txt:356-438`：HTTP Client/Socket/URL Opener/Redirect/Headless/Media/Link Preview/Webhook/JWKS/Importer/Installer/Monitoring/Cloud Metadata） | ✅ **与原始一致** |
| XSS | **5/5 上下文全部保留**（`pre-recon-code.txt:312-344`：HTML Body/Attribute/JavaScript/CSS/URL） | ✅ **与原始一致** |

#### 实际影响分析

确定性层覆盖窄意味着：(1) `_static-dataflow-hints.txt` 注入给 LLM 的确定性 hint 不含 SSRF/XSS 非 HTTP-client 子类；(2) LLM 在写报告时虽有完整分类目录参考，但**不再收到针对这些子类的显式搜索指令**（原始 prompt 有详细的多步搜索方法论，重构版精简为一句泛泛指令）。

**真实覆盖差距**：不是"13 子类仅 3 个"或"5 上下文仅 1.5 个"，而是"LLM **仍能检测所有类别**但**不再有确定性 hint 加持 + 搜索方法论精简**"。实际效果是 LLM 在这些类别上的检测可靠性降低（靠 LLM 自由发挥而非强制方法论），但不是完全缺失。

**最关键覆盖缺口**（修正后）：(1) **XXE**：确定性层和 LLM prompt 均无专门覆盖；(2) **路径穿越读取**：确定性层盲区，LLM prompt 仅在 SSRF 的 URL Openers 子类下间接提及；(3) **模板文件转义指令**：两层均无专门分析。

**§1 裁决**：确定性检测引擎是质的进步；确定性层覆盖在 XXE/路径穿越读取上确实窄于原始；SSRF/XSS 的 LLM prompt 覆盖与原始一致，但**缺少确定性 hint 加持**降低了可靠性。结构更优 ≠ 确定性覆盖更广。⚠️

---

## 2. 入口点分析对比

### 2.1 检测机制

**原始**：pre-recon "Entry Point Mapper" 子 agent —— Grep/Glob 找路由 → Read 提取 → **若有 API schema 直接读** → 标注 public/需认证 → 排除本地工具。

**重构**：`entry_points.py:detect_entry_points()` 按 language AST 模式匹配，confidence 0.30-0.95（硬编码），`needs_llm_review`。覆盖 ≥8 框架/类型。✅ 检测确定性优于原始。

### 2.2 功能完整性对比（v8 更新）

| 功能 | 原始 | 重构 | 评估 |
|---|---|---|---|
| HTTP 路由检测 | ✅ LLM | ✅ AST + GitNexus 精确匹配 | **重构更可靠** |
| API Schema 优先读取 | ✅（OpenAPI/Swagger/GraphQL） | ⚠️ `file_discovery.py` 有 schema 文件分类（`.graphql/.gql/.proto/.thrift`）且入口融合支持 schema 源；LLM 指令也删了 | **原始胜（但差距缩小）** |
| 认证标注 | ✅ 每入口标 public/需认证 | ✅ `authentication` 字段已添加（"public"/"required"/"unknown"）+ 4 源融合 | **v8 已修复，平手** |
| 网络可达性过滤 | ✅ 系统性 | ⚠️ 部分（仅 Python async 候选） | **原始胜** |
| Webhook/Upload | ✅ prompt 覆盖 | ⚠️ LLM 融合源可覆盖，但 fusion 的 webhook regex 有修复（commit `08fc720`） | **原始仍略胜** |
| 置信度评分 | ❌ | ✅ 多源 confidence（GitNexus 可变/Schema 0.80/Convention 0.75/LLM 0.60） | **重构明显胜** |
| 多框架支持 | ✅ LLM 理论不限 | ✅ ≥8 框架 + GitNexus 跨语言 | **重构更可靠** |
| 后台任务检测 | ✅ | ✅ Celery/RabbitListener | 平手 |

### 2.3 入口裁定：已修复 confidence 阈值 + Phase 0 可执行 ✅（v8 更新）

**v8 状态**：`save_adjudication`（`__init__.py:384-406`）已改为 **confidence 阈值裁定**：
- confidence ≥ 0.85 → `CONFIRMED`
- confidence < 0.50 → `REJECTED`
- 其余 → `NEEDS_REVIEW`

**Phase 0 已可执行**：workflow（`workflows.py:135-157`）在 PRE_RECON 后按序执行：
1. `run_merge_sink_reports` — 合并确定性 + LLM sink
2. `run_entry_point_fusion` — 4 源入口合并（GitNexus/Schema/Convention/LLM），原为死代码现已激活
3. `run_save_adjudication` — confidence 阈值裁定

**EntryPoint 模型已更新**（`models.py:49-61`）：新增 `authentication: str | None`（"public"/"required"/"unknown"）+ `source: str`（"code_index"/"gitnexus"/"schema"/"llm_pre_recon"）。

**仍存问题**：
1. 融合后 confidence 计算依赖各源的硬编码评分，实际验证度有限。
2. Webhook regex 曾有误报（commit `08fc720` 修复 auth 窗口泄露）。

**评估**：从"橡皮图章 + Phase 0 不可执行"升级为"confidence 阈值裁定 + 4 源融合 + Phase 0 可执行"。✅ 但 confidence 校准和 webhook 边界条件仍需关注。⚠️→✅

### 2.4 两层架构（recon 4.1/4.2 生产者被删）+ 重构版替代结构

**原始**：pre-recon Entry Point Mapper（Section 5）→ **recon 细化（Section 4.1 共享 handler 分组 + Section 4.2 Endpoint Security Context）**。这两节是后续调用链追踪 + 漏洞研判的关键索引。

**重构**：⚠️ **recon.txt 把 4.1/4.2 整段删除**。无"共享 handler 分组"、无"中间件链"、无"框架来源"细化，也无 `_endpoint-security-context.txt`/`_cross-route-enumeration.txt`（后者整个文件删）。这正是 §0.3 连锁的根。❌

**⚠️ v7 补充——重构版新增的替代结构**：虽然 4.1/4.2 的原始形式被删除，重构版 `recon.txt` 新增了以下原始项目不具备的结构化能力：

| 重构版 Section | 内容 | 原始版对应 |
|---|---|---|
| **Section 4**（API Endpoint Inventory） | 每端点的 Method/Path/Required Role/Object ID Params/Authz Mechanism | 4.2 的简化替代（缺框架来源） |
| **Section 6.4**（Guards Directory） | 所有 guard 的分类语义（Auth/Authorization/ObjectOwnership/Network/Protocol） | 4.2 的部分替代 |
| **Section 7**（Role & Privilege Architecture） | 完整角色层级（7.1 Discovered Roles）、权限格（7.2 Privilege Lattice）、角色入口（7.3 Role Entry Points）、角色到代码映射（7.4 Role-to-Code Mapping） | **原始无** ✨ |
| **Section 8**（Authorization Vulnerability Candidates） | 按水平/垂直/上下文三维预排序的授权测试候选（8.1 Horizontal/8.2 Vertical/8.3 Context） | **原始无** ✨ |

**净效应**：原始在**路由级精细分析**（共享 handler 分组、框架来源、IDOR 框架指导）上更强；重构在**结构化角色/授权架构**（角色层级、权限格、预排序候选）上更强。两者互补而非纯替代。

---

## 3. 漏洞分析对比

### 3.1 调用链追踪：GitNexus 精确调用图 + LLM taint + 降级可见（v8 更新）

**原始**：子 agent Read/Grep"人工跳转"，复杂链几十次工具调用。

**重构 v8**：旧 `call_graph.py`（名匹配首匹配、剪菱形，丢 30-50% 跨文件调用）和 `propagation_builder.py` **已完全删除**（commit `e70fb57`），替换为：

1. **GitNexus 精确调用图**（`gitnexus_call_graph.py`）：通过 MCP 协议获取精确调用关系，BFS 链构建 + 环检测 + 上游/下游追踪。降级时生成 `DegradationReport`（含 resolved/unresolved edges + `CoverageGap`）。⚠️ 降级报告**仅在内存中**，未持久化到文件。
2. **LLM per-function taint 分析**（`llm_taint_analyzer.py`）：LLM 分析每个含 sink 的函数的 taint 传播，替代旧 regex 方案。失败时保守标记所有参数为 tainted。
3. **确定性 cross-function 传播**（`chain_propagator.py`）：沿 GitNexus 调用链做确定性 tainted 参数集合传播，深度限制 + call-site 参数映射。

**旧边界已解决**：
- ~~调用图残缺且静默~~ → GitNexus 提供精确调用关系；降级时有 `DegradationReport`（但未持久化）
- ~~非空 flow 有前提~~ → `build_code_index_with_gitnexus` 完整执行 sink 检测 + LLM taint + 传播

**新边界**：降级报告未持久化（仅内存），GitNexus 不可用时 fallback 到 minimal 模式。⚠️（从 ❌ 升级为 ⚠️）

### 3.2 传播不完备 → LLM taint 替代 regex，写法 bug 已修复（v8 更新）

**v8 状态**：旧 `propagation_builder.py` 的 `analyze_intra` regex 方案**已完全删除**，替换为 LLM per-function taint 分析（`llm_taint_analyzer.py`）。

**写法 bug 已消除**：旧 regex LHS 只匹配纯标识符导致 `d["k"]=x`/`a,b=x,y`/`self.x=x`/`lst.append(x)` 全部静默漏检——LLM 方案通过自然语言理解消除此问题。

**仍存边界**：
1. LLM taint 分析**仅对含 sink 的函数执行**（`__init__.py:162-175`），不含 sink 的函数不分析——这节省 token 但可能漏跨函数间接传播。
2. cross-function 传播（`chain_propagator.py`）仍用 regex 做 call-site 参数映射，该层仍有简化。
3. LLM 失败时保守标记所有参数为 tainted（over-approximation，安全但可能有 noise）。

**评估**：核心写法 bug 已消除；传播完备性从"regex 限制"提升为"LLM 理解力 + 确定性传播"混合。⚠️→✅（主要问题已修复）

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

**⚠️ v7 补充——vuln-authz 方法论增强**：虽然框架 IDOR 检测被删，重构版 `vuln-authz.txt` 的方法论结构实际比原始更形式化：

| 重构版增强 | 位置 | 说明 |
|---|---|---|
| **三层分析方法** | lines 132-209 | 水平/垂直/上下文授权分析，每层有明确的 guard 准则和终止条件 |
| **Proof Obligations** | lines 212-217 | 形式化证明义务：guarded iff guard dominates sink |
| **Confidence Scoring** | lines 234-240 | 显式 HIGH/MED/LOW 评分规则 |
| **false_positives_to_avoid** | lines 252-266 | 专门的误判规避清单（含多租户隔离检查） |
| **exploitation_queue_format** | lines 104-119 | 更结构化的 JSON 输出格式（含 guard_evidence, side_effect） |

原始版的框架 IDOR 检测（finale-rest/epilogue 自动推断 + 默认无 ownership 校验→假设 IDOR vulnerable）是**不可替代的领域知识**。重构版的形式化方法论不能弥补这一特定能力的丢失。

### 3.5 研判产物结构化

`finding_models.py`：Pydantic + 白名单（校验 `issue_type` 不校验 `category`）+ 5 元组去重 `(entry_point_id, category, issue_type, vulnerable_function_id, call_chain_path)`。✅ 比原始可靠。

### 3.6 GitNexus FULL 模式：已实现 ✅（v8 更新）

**v8 状态**：GitNexus FULL 模式**已完整实现并集成到流水线**。

**实现组件**：
1. **GitNexusMCPClient**（`gitnexus_mcp.py`）：完整 MCP stdio JSON-RPC 协议，支持 `cypher`/`impact`/`query`/`process` 工具，自动索引。
2. **GitNexusEngine**（`gitnexus_engine.py`）：CLI 集成（`gitnexus analyze`/`gitnexus context`），索引检查 + 陈旧检测 + 360 度上下文。
3. **GitNexus Call Graph**（`gitnexus_call_graph.py`）：精确调用关系 + BFS 链构建 + 降级追踪 + 上游/下游溯源。
4. **Pipeline 集成**（`activities.py:191-207`）：`build_code_index_with_gitnexus` 带 `auto_index=True`，GitNexus 不可用时自动 fallback。

**Pipeline 流程**（`__init__.py:51-227`）：
1. Auto-indexing（可选 GitNexus CLI 索引）
2. Tree-sitter 解析（FuncBlock 提取）
3. GitNexus 调用图（精确关系）
4. Sink 检测（`detect_sinks()`）
5. LLM taint 分析（仅含 sink 函数）
6. Cross-function 传播（确定性）
7. 入口点转换（GitNexus → EntryPoint）

**仍存问题**：降级报告（`DegradationReport`）仅内存中，未持久化到文件。⚠️

✅

---

## 4. 综合评分（v8 更新）

| 维度 | 原始 | 重构 | 优势方 | 关键限定 |
|---|---|---|---|---|
| Sink 引擎确定性/结构化 | ★★ | ★★★★★ | **重构** | — |
| Sink 规则可维护性 | ★★ | ★★★★★ | **重构** | 47 条 |
| Sink 确定性层覆盖广度 | ★★★★ | ★★ | **原始** | XXE/路径穿越读取确实窄 |
| Sink LLM prompt 覆盖 | ★★★★ | ★★★★ | **平手** | SSRF 13/13 + XSS 5/5 两版一致 |
| Sink 命令注入/反序列化/SQL | ★★★ | ★★★★ | **重构** | — |
| 模板文件 sink 覆盖 | ★★★★ | ★★★ | **原始（但差距缩小）** | LLM prompt 层已恢复，确定性层仍断路 |
| 变体审计 | ★★★★ | ★★★★ | **平手** | LLM prompt 层已恢复 Cross-Variant Verification |
| 入口点确定性+置信度 | ★★ | ★★★★ | **重构** | 多源 confidence |
| 入口 schema 优先 | ★★★★★ | ★★★ | **原始** | 融合支持 schema 源，但指令仍删 |
| 入口认证标注 | ★★★★ | ★★★★ | **平手** | ✅ v8 已添加 authentication 字段 |
| 入口裁定把关 | ★★★ | ★★★★ | **重构** ✨ | ✅ confidence 阈值裁定 |
| **入口点融合（4源）** | ☆ | ★★★★ | **重构** ✨ | ✨ v8 新增，原始无 |
| recon 4.1/4.2 路由级索引 | ★★★★ | ☆ | **原始** | 共享 handler/框架来源删除 |
| **recon 角色架构/授权候选** | ☆ | ★★★★ | **重构** ✨ | Section 7/8/6.4，原始无 |
| 调用链确定性 | ★ | ★★★★ | **重构** ✨ | ✅ GitNexus 精确调用图 |
| **Taint 分析** | ☆ | ★★★★ | **重构** ✨ | ✨ v8 新增 LLM + 确定性混合 |
| 跨语言 taint 传播 | LLM 兜底 | 仅 Py/TS | **平手** | Go/Java/PHP 零 |
| 传播写法覆盖 | n/a | ★★★★ | **重构** ✨ | ✅ LLM 替代 regex，写法 bug 已消 |
| 分层按链审计 | 无 | 算了没用 | **平手** | 未接线 |
| vuln prompt 源粒度/认证字段 | ★★★ | ★★★★ | **重构** | — |
| vuln prompt 分支穷举/路由校验 | ★★★★ | ★★ | **原始** | injection 分支穷举删 |
| **vuln-authz 框架 IDOR 检测** | ★★★★ | ☆ | **原始** | 最大回退、无替代 |
| **vuln-authz 方法论结构化** | ★★★ | ★★★★ | **重构** ✨ | 三层分析+Proof Obligations+Confidence |
| 漏洞研判(slot/verdict) | LLM | LLM（同逻辑） | **平手** | — |
| FULL 模式(GitNexus) | n/a | ★★★★ | **重构** ✨ | ✅ 已实现，降级报告未持久化 |
| **Sink 合并（det+LLM）** | ☆ | ★★★★ | **重构** ✨ | ✨ v8 新增 |
| 结果结构化/去重 | ★★ | ★★★★★ | **重构** | — |
| 可测试性 | ★ | ★★★★★ | **重构** | — |
| Token 成本 | 高 | 略降 | **重构（有限）** | LLM 仍主跑 |

---

## 5. 结论

### 5.1 一句话裁决（v8 更新）

> **重构已从"能力替换"进化为"确定性增强 + LLM 互补"：v8 的 GitNexus 调用图、LLM taint 分析、4 源入口融合、sink 合并、confidence 裁定、模板 prompt 恢复等补齐了大部分工程短板。在 Py/TS 后端上，重构已在几乎所有维度优于原始（确定性链路、入口融合、角色架构、authz 方法论、可测试性）。但仍有两个"硬缺口"无替代：(1) recon 4.1/4.2 路由级结构化索引 + vuln-authz 框架 IDOR 检测（#18/#19）；(2) Go/Java/PHP 跨语言传播（#6）。这两个缺口使重构在"授权/IDOR 重点分析"和"Go/Java/PHP 后端"场景下仍弱于原始。**

### 5.2 重构真实的核心价值（成立，v8 扩展）

1. 确定性预分析层（sink 规则、taint 传播、调用链、风险评分）方向正确、工程扎实。
2. **GitNexus 精确调用图**：替换旧名匹配方案，BFS 链构建 + 环检测 + 降级可见。
3. **LLM + 确定性混合 taint 分析**：LLM per-function + 确定性 cross-function 传播，消除旧 regex 写法 bug。
4. **4 源入口点融合**：GitNexus/Schema/Convention/LLM 多源去重 + confidence 阈值裁定。
5. **Sink 合并**：确定性 + LLM sink 去重合并，两源互补。
6. 结构化与去重（Pydantic + 白名单 + 5 元组）。
7. 可测试性（规则库/传播器/融合/合并器单测齐全）。
8. vuln prompt 源粒度增强（Source Completeness Rule、认证字段）。
9. Py/TS 画像上确定性链路完整可用。
10. 角色架构映射（Section 7/8/6.4）+ authz 方法论形式化。

### 5.3 真实缺口（v8 更新：✅ 已解决 / ⚠️ 部分解决 / ❌ 未变）

| # | 缺口 | 严重度 | v8 状态 | 备注 |
|---|---|---|---|---|
| 1 | 模板/视图文件 sink 检测 | **高** | ⚠️ 部分解决 | LLM prompt 层已恢复（两步流程+变体审计+Coverage Audit），确定性层仍断路 |
| 2 | API schema 优先读取 | 中 | ⚠️ 部分解决 | 融合支持 schema 源（confidence 0.80），但 LLM schema 指令仍删 |
| 3 | 入口点认证标注 | 中 | ✅ 已解决 | `authentication` 字段已添加（"public"/"required"/"unknown"） |
| 4 | 网络可达性系统性过滤 | 低 | ❌ 未变 | — |
| 5 | 变体审计 | 低 | ✅ 已解决 | LLM prompt 层已恢复 Cross-Variant Verification |
| 6 | Go/Java/PHP 传播支持 | **中-高** | ❌ 未变 | 三语言零增益 |
| 7 | 入口裁定橡皮图章/Phase 0 不可执行 | 中 | ✅ 已解决 | confidence 阈值裁定 + 4 源融合 + Phase 0 可执行 |
| 8 | 调用图静默残缺 | **高** | ✅ 已解决 | GitNexus 精确调用图 + DegradationReport（未持久化但存在） |
| 9 | 分层按链审计未接线 | 中 | ❌ 未变 | — |
| 10 | GitNexus FULL 未实现 | 中 | ✅ 已解决 | 完整实现：MCP client + engine + call graph + auto-indexing |
| 11 | SSRF 确定性覆盖仅 HTTP client（~1/13 子类） | **中** | ❌ 未变 | LLM prompt 13/13 子类完整，确定性 hint 仍仅 HTTP client |
| 12 | 路径穿越读取类 sink 盲区 | **高** | ❌ 未变 | 确定性层 fopen/readFile 全缺，LLM prompt 也无专门覆盖 |
| 13 | XXE 完全缺失 | 中-高 | ❌ 未变 | 确定性层和 LLM prompt 均无专门覆盖 |
| 14 | XSS 确定性覆盖仅 2 条（innerHTML/document.write） | **中** | ❌ 未变 | LLM prompt 5/5 上下文完整，确定性 hint 仍仅 2 条 |
| 15 | vuln prompt 删 Branch Path Exhaustion | 中 | ❌ 未变 | injection |
| 16 | 传播写容器/属性漏检 | 中 | ✅ 已解决 | LLM taint 分析替代 regex，写法 bug 已消除 |
| 17 | sink 规则数文档错误 | 低 | ✅ 已解决 | 已纠正为 47 条 |
| **18** | **recon 4.1/4.2 结构化索引删除** | **高** | ❌ 未变 | 根因：路由分组/端点安全上下文/参数完整性全删 |
| **19** | **vuln-authz 丢框架 IDOR 检测** | **高** | ❌ 未变 | Section 0 + Framework Guidance 删，无替代 |
| **20** | **pre-recon 模板 sink 方法论删除** | **高** | ✅ 已解决 | 强制两步流程+变体审计+Coverage Audit 已恢复；`file_discovery.py` 模板清单仍待接 `sink_detector` |
| **21** | **Phase 0 入口裁定指令不可执行** | 中 | ✅ 已解决 | pipeline 直接实现裁定逻辑，不再依赖 save-deliverable |

**v8 统计**：21 项缺口中 ✅ 已解决 8 项（#3/#5/#7/#8/#10/#16/#17/#20/#21 = 9 项）、⚠️ 部分解决 2 项（#1/#2）、❌ 未变 10 项（#4/#6/#9/#11-#15/#18/#19）

### 5.4 给决策者的判断（v8 更新）

- **Python/TypeScript 后端**：重构**全面优于原始**——GitNexus 调用图、LLM taint、入口融合、sink 合并、角色架构映射均无对应物。推荐使用。⚠️ 仍建议补 #12/#13（XXE/路径穿越确定性规则）。
- **Go/Java/PHP 后端**：确定性层近乎透明，**与原项目等价 + 多余复杂度**，先做 #6。
- **授权/IDOR 是重点目标**：⚠️ **重构仍弱于原始**（#19），authz 丢了 recon 4.2 + 框架 IDOR，**短期不如用原始**，或优先补 #18/#19。重构的 authz 方法论形式化（三层分析+Proof Obligations）是加分项，但不能替代框架 IDOR 领域知识。
- **模板重前端**：LLM prompt 层已恢复模板方法论（#20 已解决），差距大幅缩小。确定性层断路仍存（#1），但实际影响降为"无确定性 hint"而非"完全盲区"。
- **SSRF / 路径穿越 / XXE 主导风险面**：#12/#13 确定性层确实弱于原始；#11（SSRF）LLM prompt 覆盖与原始一致，确定性 hint 补齐为锦上添花而非必须。
- **看重工程可维护性/可测试性**：重构明显更优。

### 5.5 修复优先级建议（v8 修订）

按"性价比 = 影响面 × 修复难度倒数"：

1. **#18 recon 4.1/4.2 恢复**（高影响、中难度）：把删掉的共享 handler 分组 + 端点安全上下文指令加回 recon.txt，GitNexus 入口点数据可 seed。**一处修复解除 #15/#19 大部分连锁**。~~仍为最高优先级~~。
2. **#12/#13 sink 规则补齐**（高影响、低难度）：加路径读取（fopen/readFile/open）+ XXE 规则——纯加 `SinkRule`，有单测框架。这两项确定性层和 LLM prompt 都无覆盖。
3. **#11/#14 SSRF/XSS 确定性规则扩展**（中影响、低难度）：LLM prompt 已完整覆盖，加确定性规则提升 hint 质量。SSRF 子类（socket/headless/media/JWKS/cloud metadata）+ XSS 更多上下文——纯加 `SinkRule`。
4. **#1 模板确定性层接线**（中影响、低难度）：LLM prompt 层已恢复，仅需将 `file_discovery.py` 的模板文件清单传入 `sink_detector` 做转义指令分析。
5. **降级报告持久化**（低影响、低难度）：`DegradationReport` 写出 `degradation_report.json`，让 GitNexus 降级可见。
6. **#6 Go/Java/PHP 传播**（中-高影响、高难度）：需扩展 tree-sitter + LLM taint 到三语言，工程量大。

~~#8 调用图残缺~~（✅ 已由 GitNexus 解决）、~~#7/#21 入口裁定~~（✅ 已由 confidence 阈值解决）、~~#10 GitNexus~~（✅ 已完整实现）、~~#16 传播写法 bug~~（✅ 已由 LLM taint 解决）、~~#20 模板 prompt~~（✅ 已恢复）、~~#17 规则数~~（✅ 已纠正）。

---

## 附录 A：关键文件对照（v8 更新）

| 功能 | 原始 (TS) | 重构 (Python) | 状态 |
|---|---|---|---|
| Sink 规则库 | prompt 目录 | `sink_detector.py:67`（47 条） | ✅ 结构优；⚠️ 确定性覆盖窄（但 LLM prompt 完整） |
| SSRF 分类目录 | `pre-recon-code.txt:333-415`（13 子类） | `pre-recon-code.txt:356-438`（13 子类完整保留） | ✅ LLM 层一致 |
| XSS 分类目录 | `pre-recon-code.txt:289-321`（5 上下文） | `pre-recon-code.txt:312-344`（5 上下文完整保留） | ✅ LLM 层一致 |
| 模板 sink 方法论 | pre-recon 强制两步+变体+Audit 表 | ✅ LLM prompt 已恢复两步+变体+Audit；⚠️ 确定性层断路 | ⚠️ 从 ❌❌ 升级 |
| 入口 schema/auth | Entry Point Mapper 指令（主要路径） | ✅ 4 源融合含 schema 源；✅ `authentication` 字段已添加 | ✅ v8 已修复 |
| 入口裁定 | LLM 两层 | ✅ confidence 阈值裁定（≥0.85 CONFIRMED / <0.50 REJECTED） | ✅ v8 已修复 |
| **入口点融合** | 无 | `entry_point_fusion.py`（4 源：GitNexus/Schema/Convention/LLM） | ✅ v8 新增 ✨ |
| **Sink 合并** | 无 | `sink_merger.py`（确定性 + LLM 去重合并） | ✅ v8 新增 ✨ |
| **recon 4.1 路由分组** | `### 4.1 Shared Controller Route Groups` | **整段删除** | ❌ 根因 |
| **recon 4.2 端点安全上下文** | `## 4.2 Endpoint Security Context` | **整段删除**（部分替代见下） | ❌ 根因 |
| **recon Section 7 角色架构** | 无 | Section 7 Role & Privilege Architecture（完整角色层级+权限格） | ✅ 重构新增 ✨ |
| **recon Section 8 授权候选** | 无 | Section 8 Authorization Vulnerability Candidates（三维预排序） | ✅ 重构新增 ✨ |
| **recon Section 6.4 Guards** | 无 | Section 6.4 Guards Directory（分类语义） | ✅ 重构新增 ✨ |
| vuln-authz 框架 IDOR | Section 0 + Framework Guidance | **删除无替代** | ❌❌ 最大回退 |
| **vuln-authz 方法论** | LLM 自由分析 | 三层分析方法 + Proof Obligations + Confidence Scoring | ✅ 重构增强 ✨ |
| vuln-* cross-route 枚举 | `_cross-route-enumeration.txt` + 门控 | **全删** | ❌ |
| vuln-* 源粒度 | combined_sources(信息性) | Source Completeness Rule(强制) | ✅ |
| vuln-injection 分支穷举 | Branch Path Exhaustion | 删除 | ❌ |
| 调用链 | 子 agent 跳转 | ✅ `gitnexus_call_graph.py` GitNexus 精确调用图 | ✅ v8 已修复 |
| **Taint 分析** | 无 | `llm_taint_analyzer.py` + `chain_propagator.py`（LLM + 确定性混合） | ✅ v8 新增 ✨ |
| 传播写法 | n/a | ✅ LLM 替代 regex，写法 bug 已消 | ✅ v8 已修复 |
| 跨语言传播 | LLM 兜底 | `{go,java,php}` 零 | ❌ |
| 漏洞研判 | LLM slot 匹配 | LLM（同逻辑） | ⚠️ |
| 分层审计 | 无 | `AUDIT_TIER1`(注册未调用) | ⚠️ |
| GitNexus FULL | n/a | ✅ `gitnexus_mcp.py` + `gitnexus_engine.py` + `gitnexus_call_graph.py` | ✅ v8 已实现 |
| 结果去重 | 无 | 5 元组 | ✅ |
| hint 注入 | 无 | `_static-dataflow-hints.txt` + `<parameter_propagation_data>` + `<phase0_data>` | ✅ |

## 附录 B：prompt diff 证据索引（v8 更新）

- `diff shannon/apps/worker/prompts/{vuln-xss,vuln-ssrf,vuln-authz}.txt shannon-py/prompts/...` —— 三 vuln prompt 一致删 `_cross-route-enumeration` + Cross-Route Verification 门控；vuln-authz 额外删 Section 0 + Framework Guidance。
- `diff .../pre-recon-code.txt` —— 删 Entry Point Mapper schema 指令；v8 已恢复 Sink Hunter 两步模板流程 + 变体审计 + Coverage Audit 表；Phase 0 改为读取 `code_index.json`（已实现）。
- `diff .../recon.txt` —— 删整段 4.1/4.2 + Route Mapper 分组指令 + Input Validator 字段枚举 + `_endpoint-security-context.txt`；新增 `<parameter_propagation_data>` + `<no_security_judgments>`。
- 原始 `_cross-route-enumeration.txt`（3119 字节）/ `_endpoint-security-context.txt` 在重构版 `prompts/shared/` 不存在。
- **v8 新增代码证据**：
  - `packages/core/src/shannon_core/code_index/gitnexus_mcp.py` —— GitNexus MCP client 完整实现
  - `packages/core/src/shannon_core/code_index/gitnexus_engine.py` —— GitNexus CLI engine 完整实现
  - `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` —— GitNexus 精确调用图
  - `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py` —— LLM per-function taint 分析（替代旧 regex）
  - `packages/core/src/shannon_core/code_index/chain_propagator.py` —— 确定性 cross-function 传播
  - `packages/core/src/shannon_core/code_index/sink_merger.py` —— 确定性 + LLM sink 去重合并
  - `packages/core/src/shannon_core/code_index/entry_point_fusion.py` —— 4 源入口融合（GitNexus/Schema/Convention/LLM）
  - `packages/core/src/shannon_core/code_index/__init__.py:380-406` —— confidence 阈值裁定
  - `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:119-129` —— asyncio.gather 并行执行
  - `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:191-207` —— GitNexus pipeline 集成
