# Shannon 白盒分析重构评估报告

> 对比原始 Shannon（TypeScript）与重构后 Shannon-py（Python）在白盒安全分析三个核心维度的优劣。
>
> 日期：2026-06-09

---

## 0. 根本范式差异

原始 Shannon（TS）和重构 Shannon-py（Python）采用了**根本不同的分析范式**：

| | 原始 Shannon (TS) | 重构 Shannon-py (Python) |
|---|---|---|
| **分析引擎** | 纯 LLM（Claude Code 会话 + 内置 Read/Grep/Glob 工具） | 确定性静态分析（tree-sitter AST + 规则库）+ LLM 复核 |
| **调度方式** | Temporal workflow → `runClaudePrompt` → Claude Code 会话 | Temporal workflow → 确定性 Python 函数 → LLM 验证（Spec C） |
| **确定性** | ❌ 非确定——同一代码可能产出不同分析结果 | ✅ Spec A/B 确定性（给定相同代码，AST 结果一致） |
| **LLM 角色** | **主执行者**——读代码、追链、下判断 | **复核者**——仅对 `needs_review` 标记和漏洞研判做最终判定 |
| **三层架构** | 无（单层 LLM） | Spec A（传播图）→ Spec B（Sink 检测）→ Spec C（LLM 验证） |

原始项目的核心设计哲学是 **"LLM 即引擎"**：Shannon 的 TS 层不做分析，只负责调度和 prompt 编排，所有分析工作由 Claude Code 会话完成。重构项目的哲学是 **"确定性优先，LLM 补位"**：用 AST 和规则库做确定性检测，LLM 降级为验证角色。

---

## 1. Sink 分析对比

### 1.1 检测机制

**原始项目**：

Sink 检测完全由 pre-recon 阶段的 "XSS/Injection Sink Hunter" Task 子 agent 执行。流程是：
1. Glob 枚举模板/视图文件（`html, ejs, hbs, pug, jsx, tsx, vue, svelte, php, erb, jinja2, tmpl`）
2. 逐文件 Read 全文
3. LLM 判断哪些是危险 sink
4. 业务代码通过 Grep 危险 API 名（`exec\(`、`.query\(`、`eval\(`）定位后 Read 确认

**重构项目**：

Sink 检测由 `sink_detector.py` 的 `detect_sinks()` 函数执行，基于 tree-sitter AST：
1. 遍历 FuncBlock 的所有 AST call node
2. 对每个 call 提取 callee + receiver
3. 匹配 `SinkRule` 规则库（197+ 条规则，按 `(language, callee)` 索引）
4. 命中则构建 `SinkCallSite`（含 `dangerous_slots`、`SlotContext`）

**评估**：重构在检测机制上是**质的飞跃**。AST 匹配是确定性的、可测试的、可复现的，而 LLM 判断是非确定的。

### 1.2 规则库

**原始项目**：

规则硬编码在 prompt 文本中：
- XSS sink 目录：`pre-recon-code.txt:289-321`（HTML body / 属性 / JS / CSS / URL 五类上下文，逐类列举 API）
- SSRF sink 目录：`pre-recon-code.txt:333-415`（13 个子类）
- 其他类别在 prompt 中以自然语言列举

规则存储形式是**自然语言描述**，LLM 按描述去代码里找匹配。

**重构项目**：

规则以代码形式定义在 `DEFAULT_RULES` 元组中（197+ 条），每条 `SinkRule` 包含：
- `rule_id`：唯一标识
- `languages`：适用语言
- `callee`：被调用函数名
- `receiver_pattern`：receiver 正则（如 `_DB_CURSOR = re.compile(r"^(cursor|cnx|conn|db|database)$")`）
- `category`：`SinkCategory` 枚举（SQL/COMMAND/FILE/TEMPLATE/DESERIALIZATION/SSRF/XSS/LOG/REDIRECT）
- `dangerous_slots`：精确到参数位的 `(arg_index, SlotContext)` 对
- `needs_review_default`：是否需要 LLM 复核

**评估**：重构的规则库远比原始项目**精确**和**可维护**。新增规则只需添加一条 `SinkRule`，不影响现有规则。原始项目修改 prompt 可能影响 LLM 对所有规则的理解。

### 1.3 Slot 类型系统

**原始项目**（`vuln-injection.txt:121, 155`）：

```
SQL-val | SQL-like | SQL-num | SQL-enum | SQL-ident
CMD-argument | CMD-part-of-string
FILE-path | FILE-include
TEMPLATE-expression
DESERIALIZE-object
PATH-component
```

**重构项目**（`parameter_models.py:SlotContext`）：

```python
SQL_VALUE       # SQL-val/like/num —— 需参数绑定
SQL_IDENTIFIER  # SQL-enum/ident —— 需白名单
CMD_ARGUMENT    # 需数组参数 + shell=False / shlex.quote
FILE_PATH       # 需白名单路径 / resolve+边界检查
TEMPLATE_EXPR   # SSTI —— 需沙箱+autoescape
URL             # SSRF —— 需协议/主机白名单
DESERIALIZE_OBJ # 需可信来源+HMAC
GENERIC         # 未细分
```

**评估**：**基本对齐**，重构将原始的 `CMD-part-of-string` 和 `FILE-include` 合并进了 `CMD_ARGUMENT` 和 `FILE_PATH`，新增了 `URL`（SSRF 专用）和 `GENERIC`。这是合理的简化。

### 1.4 模板/视图文件 Sink 检测 ⚠️

**原始项目**：有专门的**强制两步流程**（`pre-recon-code.txt:128-136`）：
- Step 1：Glob 枚举所有模板/视图文件
- Step 2：逐文件 Read，区分转义模式（EJS `<%= %>` vs `<%- %>`、Jinja2 `{{ }}` vs `{{|safe}}`）

**重构项目**：⚠️ **缺失**。`SinkRule` 规则库仅覆盖函数调用级 sink（`cursor.execute()`、`os.system()`），没有模板文件的转义指令检测。

**评估**：这是重构的一个**明确缺口**。模板注入（SSTI）和 XSS 的一大类 sink 是模板文件中的不转义指令，这些不是函数调用，无法被 `SinkRule` 匹配。原始项目虽然靠 LLM 执行，但至少有专门的流程覆盖。

### 1.5 变体审计 ⚠️

**原始项目**（`pre-recon-code.txt:135-136`）：强制变体覆盖审计——模板若存在于变体目录（品牌、语言、主题、子应用），同名模板必须逐一独立分析，禁止假设变体模板内容相同。

**重构项目**：⚠️ **缺失**。无变体审计机制。

**评估**：这是重构的另一个缺口，但对于大多数项目影响较小。

---

## 2. 入口点分析对比

### 2.1 检测机制

**原始项目**：

由 pre-recon 的 "Entry Point Mapper" Task 子 agent 执行：
1. Grep/Glob 找路由定义（Express `app.get`、Flask `@app.route`、Spring `@RequestMapping`、Gin `router.GET`）
2. Read 路由文件，提取 HTTP 方法、路径、中间件链
3. 若有 API schema 文件（OpenAPI/Swagger/GraphQL），直接读 schema 作为结构化入口清单
4. 标注每个入口的 public/需认证
5. 排除本地工具（CLI/CI 脚本/迁移脚本）

**重构项目**：

由 `entry_points.py` 的 `detect_entry_points()` 函数执行：
- 按 language 分发到 `_detect_python()` / `_detect_go()` / `_detect_typescript()` / `_detect_java()` / `_detect_php()`
- 每种语言有对应的装饰器/参数模式匹配规则
- 每个入口有 confidence（0.30-0.95）和 `needs_llm_review` 标记

**评估**：重构在检测确定性上优于原始（AST 模式匹配 vs LLM Grep），置信度评分是新增亮点。但缺失多项功能完整性。

### 2.2 功能完整性对比

| 功能 | 原始项目 | 重构项目 | 评估 |
|---|---|---|---|
| HTTP 路由检测 | ✅ LLM 识别 | ✅ AST 模式匹配 | **重构更可靠** |
| API Schema 优先读取 | ✅ 有（OpenAPI/Swagger/GraphQL） | ❌ 缺失 | **原始胜** |
| 认证标注 | ✅ 每个入口标 public/需认证 | ❌ 无认证标注字段 | **原始胜** |
| 网络可达性过滤 | ✅ 系统性过滤 | ⚠️ 部分（跳过 test 文件、Go main） | **原始胜** |
| Webhook/Upload 检测 | ✅ prompt 覆盖 | ❌ 缺失 | **原始胜** |
| 置信度评分 | ❌ 无 | ✅ 0.30-0.95 | **重构新增** |
| 多框架支持 | ✅ LLM 理论不限 | ✅ Flask/Express/NestJS/Spring/Gin/Laravel | **平手** |
| 后台任务检测 | ✅ prompt 覆盖 | ✅ Celery task / RabbitListener | **平手** |

### 2.3 两层架构

**原始项目**有明确的**发现 → 细化**两层：
- 发现层：pre-recon Entry Point Mapper → `pre_recon_deliverable.md` Section 5
- 细化层：recon 阶段 → Section 4.1（共享 handler 分组）+ Section 4.2（Endpoint Security Context）

**重构项目**有 `entry_point_fusion.py` 做融合，但无明确的"共享 handler 分组"和"中间件链"细化。

**评估**：原始项目的两层设计是后续调用链追踪和漏洞研判的关键基础设施（`_cross-route-enumeration.txt` 和 `_endpoint-security-context.txt` 都依赖它）。重构需要补充这一层。

---

## 3. 漏洞分析（研判）对比

### 3.1 调用链追踪

**原始项目**：

调用链追踪完全由子 agent 用 Read/Grep 做"人工跳转"。典型循环：
1. 在 controller 读到 `processData(x)` → Grep 搜 `processData` 定义 → Read 命中文件
2. 发现 `db.query("SELECT ... WHERE id=" + x)` → 命中 sink
3. 沿途记录 sanitizer、concat、分支
4. 数据流分叉 → 拆成多条独立 path
5. 每条复杂链可能消耗几十次工具调用

这是原始项目 **token 成本最高的环节**，也是 `maxTurns: 10_000` 的根因。

**重构项目**：

三 spec 联合：
- **Spec A**（`propagation_builder.py`）：确定性过程内+跨函数传播
  - `seed_taints()`：从入口函数参数确定初始 tainted 集
  - `analyze_intra()`：单趟扫描维护 tainted 变量集，检测赋值/拼接/sanitizer
  - `_trace_chain()`：沿 CallChain 跨函数传播，参数映射
- **Spec B**（`sink_detector.py`）：精确 sink call site 检测
- **Spec C**（LLM）：最终判定

**评估**：重构在调用链追踪上是**架构级升级**。从"LLM 逐跳跳转"升级为"确定性传播图"，大幅降低 token 成本和分析不确定性。

### 3.2 传播不完备性

重构项目**明确声明了四项不完备边界**（`propagation_builder.py:13-17`）：

| 不完备 | 描述 |
|---|---|
| 无不动点 | 循环内的 def-use 不处理 |
| 容器过近似 | `d` tainted ⇒ `d[k]` tainted |
| 分支保守 | if/else 任一分支可能污染即视为 tainted |
| sanitizer 仅提示 | 不阻断 taint，有效性交给 LLM |

这些是**有意的简化**，通过 `confidence` 字段和 `notes` 传递给下游 LLM 复核。原始项目没有这样的显式声明——LLM 可能在不知不觉中忽略这些边界。

### 3.3 漏洞研判结构化

**原始项目**：

研判完全在主 agent 的 context window 内完成。输出格式由 prompt 的 `exploitation_queue_format` 定义，但验证靠 LLM 自律。

**重构项目**：

1. `risk_scorer.py`：4 维度评分（sink_danger / taint_completeness / auth_gap / depth）→ 3 级 tier（Tier 3: ≥30 分，满额审计；Tier 2: ≥15，标准审计；Tier 1: <15，轻量扫描）
2. `finding_models.py`：`VulnFinding` Pydantic 模型 + `parse_and_validate_findings()` 白名单验证 + 5 元组去重
3. `_static-dataflow-hints.txt`：把 Spec A/B 结果注入 LLM prompt，作为追链起点和交叉验证

**评估**：重构在结构化上远优于原始。风险评分是全新能力，白名单验证和去重机制让输出更可靠。

### 3.4 判定逻辑（slot 匹配）

**两个项目的判定逻辑完全一致**：

| Sink 上下文 | 正确防御 | 判为漏洞的 mismatch |
|---|---|---|
| SQL val/like/num | 参数绑定 | concat、regex 转义、错误的 slot 防御 |
| SQL enum/ident | 白名单 | 误以为参数绑定能防列名/关键字 |
| Command | 数组参数 + shell=False | concat、黑名单、shell=True |
| File/Path | 白名单路径 / resolve+边界检查 | concat、../黑名单 |
| SSTI | 沙箱 + autoescape | concat、弱沙箱 |
| Deserialize | 仅可信来源 + HMAC | 不可信输入 |

重构的 `vuln-injection.txt` 直接复用了原始的判定逻辑。唯一区别是：原始完全靠 LLM 执行，重构有 Spec A/B 的确定性数据作为验证基础。

---

## 4. 综合评分

| 维度 | 原始 Shannon | 重构 Shannon-py | 优势方 |
|---|---|---|---|
| Sink 检测确定性 | ★★☆☆☆ | ★★★★★ | **重构** |
| Sink 规则结构化 | ★★☆☆☆ | ★★★★★ | **重构** |
| 模板/视图 Sink | ★★★★☆ | ☆☆☆☆☆ | **原始** |
| 变体审计 | ★★★★☆ | ☆☆☆☆☆ | **原始** |
| 入口点确定性 | ★★☆☆☆ | ★★★★☆ | **重构** |
| 入口点置信度评分 | ☆☆☆☆☆ | ★★★★★ | **重构** |
| API Schema 优先 | ★★★★★ | ☆☆☆☆☆ | **原始** |
| 认证标注 | ★★★★☆ | ☆☆☆☆☆ | **原始** |
| 网络可达性过滤 | ★★★★☆ | ★★☆☆☆ | **原始** |
| 调用链确定性 | ★☆☆☆☆ | ★★★★☆ | **重构** |
| 传播不完备声明 | ★★☆☆☆ | ★★★★★ | **重构** |
| 漏洞研判结构化 | ★★☆☆☆ | ★★★★★ | **重构** |
| 风险评分 | ☆☆☆☆☆ | ★★★★★ | **重构** |
| Slot 类型系统 | ★★★★★ | ★★★★☆ | **平手** |
| 判定逻辑 | ★★★★★ | ★★★★★ | **平手** |
| 跨语言传播覆盖 | ★★★★☆ | ★★☆☆☆ | **原始** |
| 去重机制 | ★★☆☆☆ | ★★★★★ | **重构** |

---

## 5. 结论

### 5.1 重构的核心价值

重构在**分析确定性**和**结果结构化**上实现了根本性提升：

1. **确定性**：从"LLM 读完代码凭感觉判断"升级为"AST 精确匹配 + 规则库"，同一代码每次分析结果一致
2. **结构化**：从"prompt 纪律约束 LLM 输出格式"升级为"Pydantic 模型 + 白名单验证 + 5 元组去重"
3. **可测试性**：规则库的每个规则都有对应的单元测试，原始项目的 LLM 行为无法测试
4. **风险评分**：全新能力，4 维度评分 + 3 级 tier，指导下游审计资源分配
5. **Token 成本**：Spec A/B 确定性分析大幅减少 LLM 需要阅读的代码量，LLM 只需验证关键点

### 5.2 重构的 6 个缺口

| # | 缺口 | 严重度 | 建议 |
|---|---|---|---|
| 1 | 模板/视图文件 Sink 检测（转义模式分析） | **高** | 新增 `TemplateSinkRule` 类型，glob 枚举模板文件后 AST/正则分析转义指令 |
| 2 | API Schema 文件优先读取 | **中** | 新增 `schema_detector.py`，识别 OpenAPI/Swagger/GraphQL 文件并解析 |
| 3 | 入口点认证标注 | **中** | 在 `EntryPoint` 模型新增 `auth_required` 字段，AST 检测中间件 |
| 4 | 网络可达性系统性过滤 | **低** | 新增 `network_reachability_filter()` 函数，排除 CLI/CI/迁移脚本 |
| 5 | 变体审计 | **低** | 在模板 sink 检测流程中新增变体目录检测 |
| 6 | Go/Java/PHP 传播支持 | **中** | 为这三门语言实现 typed parameter extractor，移出 `_UNSUPPORTED_LANGUAGES` |

### 5.3 总体判断

**重构在架构层面优于原始项目**。核心分析能力（Sink 检测、调用链追踪、漏洞研判）的确定性、结构化、可测试性都实现了质的提升。6 个缺口是**功能完整性**问题，不是架构问题——`needs_review` + `skipped_languages` + `_static-dataflow-hints.txt` 机制已经为这些缺口预留了 LLM 补位接口。

原始项目的 LLM 灵活性是一把双刃剑：理论上能覆盖更多场景，但实际执行受制于 LLM 的不确定性和 context window 限制。重构项目通过确定性分析消除这种不确定性，同时保留 LLM 作为验证者和补位者——这是更可持续的架构。

---

## 附录 A：关键文件对照

| 功能 | 原始 Shannon (TS) | 重构 Shannon-py (Python) |
|---|---|---|
| Sink 规则库 | `pre-recon-code.txt:289-415`（prompt 文本） | `sink_detector.py:67-198`（`DEFAULT_RULES` 元组） |
| Sink 检测 | "Sink Hunter" Task 子 agent | `sink_detector.py:detect_sinks()` |
| 入口点检测 | "Entry Point Mapper" Task 子 agent | `entry_points.py:detect_entry_points()` |
| 调用链追踪 | 子 agent Read/Grep 跳转 | `propagation_builder.py:build_propagation_graph()` |
| 过程内分析 | 子 agent 逐行理解 | `propagation_builder.py:analyze_intra()` |
| 漏洞研判 | 主 agent slot 匹配 | `risk_scorer.py` + `finding_models.py` + LLM (Spec C) |
| Slot 类型 | `vuln-injection.txt:121` | `parameter_models.py:SlotContext` |
| 判定逻辑 | `vuln-injection.txt:149-161` | `vuln-injection.txt:149-154`（复用） |
| 静态数据流提示 | 无 | `prompts/shared/_static-dataflow-hints.txt` |
| Endpoint 安全上下文 | `_endpoint-security-context.txt` | ⚠️ 缺失 |
| 跨路由枚举 | `_cross-route-enumeration.txt` | ⚠️ 缺失强制 checklist |
| Agent 注册 | `session-manager.ts:14 AGENTS` | `models/agents.py:AgentName` |
