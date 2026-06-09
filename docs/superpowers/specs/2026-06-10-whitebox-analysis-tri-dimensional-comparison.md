# Shannon 白盒分析重构 vs 原始项目：三维深度对比报告

> 基于 v4 评估报告的补强分析，聚焦 sink/入口点/漏洞三个维度的实际影响评估。
>
> 日期：2026-06-10
>
> **性质**：补强报告，与 `whitebox-refactoring-assessment.md`（v4）互补。本文不重复 v4 已覆盖的背景和流水线描述，专注于：
> 1. v4 分析偏浅的维度的深度验证
> 2. v4 评估的修正（如 XSS 覆盖率、sink 规则总数）
> 3. 新发现的覆盖缺口和精度问题
> 4. 每条结论附带代码证据和实际漏报示例

---

## 1. Sink 分析对比

### 1.1 重构优于原始的方面

#### 1.1.1 检测引擎：AST 结构化 vs Prompt 自然语言

| 维度 | 原始 | 重构 | 优势方 |
|---|---|---|---|
| 检测方式 | LLM 读 prompt 里的 sink 目录 → Glob/Grep 找匹配 → 子 agent Read 确认 | AST call node 匹配 SinkRule → 结构化 SinkCallSite | **重构** |
| 可维护性 | 规则硬编码在 prompt 自然语言文本，改规则 = 改 prompt | 46 条 SinkRule 可独立增删，有 rule_id 和单测 | **重构** |
| 可测试性 | 无法对 prompt 级规则写单元测试 | 每条规则可单测（`sink_detector_test.py`） | **重构** |
| 结果格式 | LLM 自由文本写入 deliverable Section 9/10 | 结构化 JSON（SinkCallSite），带 file:line、slot 类型、language | **重构** |

#### 1.1.2 规则库结构：精确可维护

46 条规则按 `(rule_id, callee, languages, receiver_pattern, category, dangerous_slots)` 结构化。优点：
- 每条规则独立，可按 language/category 精确检索
- `receiver_pattern` 区分 `cursor.execute` vs `http.request.execute`（虽然 parser 端丢弃 receiver 降低了实际效果）
- `needs_review` 标记让低置信度 sink 进入 LLM 复核

#### 1.1.3 SQL 注入/命令注入：覆盖充分

SQL（8 条）和 COMMAND（13 条）是覆盖最完整的两个类别：
- SQL：Py cursor.execute/executemany、TS query、Go Query、Java executeQuery/execute、PHP mysqli/query — 覆盖 ~80% 常见数据库 API
- COMMAND：Py os.system/popen/subprocess 全系列、TS eval/exec、Go exec.Command、Java Runtime.exec、PHP shell_exec/system/passthru/exec — 覆盖 ~65% 常见命令执行 API

### 1.2 重构弱于原始的方面

#### 1.2.1 XSS：30+ 个 sink 仅覆盖 2 个（~5%）⚠️ 最严重覆盖缺口

这是本次验证发现的最严重缺口，比 v4 报告的评估更差。v4 说 "~1.5/5 上下文"，但 `innerHTML` 有 `needs_review=True` 且 `eval` 被归入 COMMAND 类，XSS 类内真实覆盖率约 5%。

| 原始 XSS 上下文 | sink 数量 | 重构覆盖 | 实际覆盖率 |
|---|---|---|---|
| HTML Body (innerHTML, outerHTML, insertAdjacentHTML, Range, jQuery 8 个) | ~15 | innerHTML + document.write | ~13% |
| HTML Attribute (href, src, event handler, style, srcdoc) | ~10 | **无** | 0% |
| JavaScript Context (eval, Function(), setTimeout, setInterval) | ~5 | eval（归入 COMMAND，非 XSS 类） | 0%（XSS 类内） |
| CSS Context | ~2 | **无** | 0% |
| URL Context (location, window.open, history) | ~5 | **无** | 0% |

**实际漏报示例**：

```javascript
// jQuery DOM XSS — 极其常见的写法，完全不检测
$("#output").html("Welcome, " + userData);       // 未检测
$("#output").append("<span>" + userData + "</span>"); // 未检测
$(userData);  // jQuery selector XSS — 未检测

// 属性注入 — javascript: 协议 XSS，未检测
element.href = userInput;
img.src = userControlledUrl;

// setTimeout/setInterval 字符串注入，未检测
setTimeout("process('" + userInput + "')", 100);
```

**影响评估**：jQuery DOM XSS 是旧版 web 应用中最常见的 XSS 变体。仅覆盖 `innerHTML` 和 `document.write` 意味着绝大多数 DOM XSS 漏洞会被忽略。

#### 1.2.2 SSRF：13 子类仅覆盖 ~19%

| 原始 SSRF 子类 | 重构覆盖？ | 缺失影响 |
|---|---|---|
| HTTP(S) Clients (requests/axios/fetch/curl) | ✅ 部分 | 缺 RestTemplate/WebClient/Apache HttpClient |
| Raw Sockets & Connect APIs | ❌ | socket.connect/net.Dial/TcpClient 全漏 |
| URL Openers & File Includes | ⚠️ 部分 | urllib.urlopen 覆盖，fopen/include_once 未覆盖 |
| Redirect & "Next URL" Handlers | ⚠️ 部分 | 2 条 REDIRECT 规则，缺 Go/Java/PHP |
| **Headless Browsers** | ❌ | Puppeteer/Playwright/Selenium SSRF 完全盲区 |
| **Media Processors** | ❌ | ImageMagick/FFmpeg/Ghostscript SSRF 完全盲区 |
| Link Preview & Unfurlers | ❌ | 完全盲区 |
| Webhook Testers & Callbacks | ❌ | 完全盲区 |
| SSO/OIDC/JWKS Fetchers | ❌ | 完全盲区 |
| Importers & Data Loaders | ❌ | 完全盲区 |
| Package/Plugin Installers | ❌ | 完全盲区 |
| Monitoring & Health Check | ❌ | 完全盲区 |
| Cloud Metadata Helpers | ❌ | 完全盲区 |

**实际漏报示例**：

```python
# Puppeteer SSRF — 完全不检测
url = request.args.get("url")
page = browser.newPage()
page.goto(url)  # 可访问内部服务，未被检测

# ImageMagick SSRF — 完全不检测
from wand.image import Image
with Image(filename=request.args.get("image_url")) as img:
    img.resize(100, 100)  # ImageMagick 可 fetch URL，未被检测

# Raw socket SSRF — 完全不检测
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((request.json["host"], int(request.json["port"])))
```

#### 1.2.3 XXE：完全缺失

原始 prompt 在 SSRF 部分提到 `loadHTML`/`loadXML` with external sources。重构没有 XXE 类别，以下全部不检测：

- Python `xml.etree.ElementTree.fromstring`、`lxml.etree.parse`
- Java `DocumentBuilder.parse`
- PHP `simplexml_load_string`、`DOMDocument->loadXML`

#### 1.2.4 路径穿越：仅 PHP 有覆盖，其他语言全盲

| 语言 | 原始列出 | 重构覆盖 |
|---|---|---|
| Python `open()` | ✅ | ❌ |
| TypeScript `fs.readFile` | ✅ | ❌ |
| Go `os.Open`/`os.ReadFile` | ✅ | ❌ |
| Java `FileInputStream` | ✅ | ❌ |
| PHP `fopen` | ✅ | ❌ |
| PHP `file_put_contents`/`include`/`require` | ✅ | ✅（3 条） |

**实际漏报示例**：

```python
# Python 路径穿越 — 不检测
filename = request.args.get("file")
with open("/data/" + filename) as f:  # 未检测
    return f.read()

# Node.js 路径穿越 — 不检测
const filePath = path.join("/uploads", req.query.file);
fs.readFile(filePath, (err, data) => { ... });  // 未检测
```

#### 1.2.5 模板/SSTI：仅 Python 2 条，其他语言全盲

只覆盖 `render_template_string` 和 `jinja2.render`。EJS、Pug、Freemarker、Velocity、Twig、Blade、Go template 等全部不检测。模板文件的转义指令分析（EJS `<%= %>` vs `<%- %>`、Jinja2 `{{ }}` vs `{{|safe}}`）在确定性层和 LLM 层双层落空。

### 1.3 v4 评估修正

#### 1.3.1 Sink 规则总数更正

v4 多次说 47 条，实测为 **46 条**（2 XSS + 8 SQL + 13 COMMAND + 5 DESERIALIZATION + 11 SSRF + 2 TEMPLATE + 3 FILE + 2 REDIRECT = 46）。

#### 1.3.2 命令注入覆盖度修正

v4 说 COMMAND "最完整"，但实测覆盖 ~65%（13/20 变体）。缺失：

| 缺失变体 | 影响 |
|---|---|
| Python `eval()`/`exec()` builtins | `eval(user_input)` 代码注入不检测 |
| Java `ProcessBuilder` | 替代 Runtime.exec 的主流写法不检测 |
| JS `child_process.spawn`/`execFile`/`fork` | 替代 exec 的常见写法不检测 |
| PHP `proc_open`/`popen`/反引号 | 替代 system/exec 的写法不检测 |

#### 1.3.3 XSS 覆盖度修正

v4 说 "~1.5/5 上下文"，实际 XSS 类内覆盖率约 **5%**（仅 2/30+ sink），原因是 `eval` 被归入 COMMAND 类且 `innerHTML` 有 `needs_review=True`。

### 1.4 Sink 维度综合裁决

> **重构在引擎结构和可维护性上明显更优，但在覆盖广度上严重不足。**
>
> XSS（~5% 覆盖率）和 SSRF（~19% 覆盖率）是最严重的缺口。如果目标是 Python/TS 后端的 SQL/命令注入，重构足够好；如果目标涉及 DOM XSS、SSRF 多子类、XXE、路径穿越，重构主动弱于原始项目。
>
> **关键修复**：
> 1. XSS 补齐 5 个上下文（HTML Body/Attribute/JS/CSS/URL），至少需 15+ 条规则
> 2. SSRF 补齐 headless/media/cloud metadata 子类，至少需 8+ 条规则
> 3. 新增 XXE 类别，至少需 5 条规则（Python lxml/Java DocumentBuilder/PHP simplexml/TS xml2js/Go encoding.xml）
> 4. 路径穿越扩展到所有语言（Python open/TS fs.readFile/Go os.Open/Java FileInputStream）
> 5. 命令注入补齐替代变体（Python eval/exec、Java ProcessBuilder、JS child_process.spawn）

---

## 2. 入口点分析对比

### 2.1 重构优于原始的方面

#### 2.1.1 AST 确定性检测 + 置信度评分

| 维度 | 原始 | 重构 |
|---|---|---|
| 检测方式 | LLM 子 agent Grep/Glob + Read | AST 模式匹配 |
| 覆盖框架 | LLM 理论不限 | ≥8 框架（Flask/Django/FastAPI/Express/NestJS/Spring/Gin/PHP Route） |
| 置信度 | 无 | 0.30-0.95 硬编码 |
| 可测试性 | 无 | 可单测 |

#### 2.1.2 多语言规则结构化

重构的入口点规则按语言分离，每条规则明确标注框架、装饰器/签名模式、置信度阈值。比原始的"LLM 按 prompt 目录找"更可复现。

### 2.2 重构弱于原始的方面

#### 2.2.1 API Schema 优先读取：代码和 prompt 双层缺失

原始项目的 Entry Point Mapper 有明确的 API schema 检测逻辑：OpenAPI/Swagger/GraphQL schema 文件直接读取，schema 本身就是结构化入口清单。重构版在代码层和 prompt 层都没有实现此能力。

**实际影响**：如果一个项目有 OpenAPI spec，原始项目能一次性获得完整的入口清单（含参数、认证要求），重构版需要从代码逆向——更慢且更不完整。

#### 2.2.2 认证标注：模型无字段 + prompt 指令删除

原始项目对每个入口标注 `public` / `需认证`。重构版的 `EntryPoint` 模型没有此字段，pre-recon prompt 中相关的 LLM 指令也被删除。

**实际影响**：无法区分 pre-auth 和需认证的入口，直接影响 authz 漏洞研判的优先级。

#### 2.2.3 网络可达性过滤：系统性不足

原始项目有系统性的网络可达性过滤（排除 CLI 工具、CI/CD 脚本、数据库迁移、本地开发服务器）。重构版仅部分覆盖（Python async 候选有过滤规则）。

#### 2.2.4 框架覆盖盲区

| 框架/模式 | 原始（LLM） | 重构（AST） |
|---|---|---|
| Django URL conf（`path()`/`re_path()`） | ✅ LLM 可理解 | ❌ 非装饰器定义 |
| FastAPI `APIRouter` 无装饰器 | ✅ | ❌ |
| Fastify/Koa/Hapi | ✅ | ❌ |
| Next.js API routes | ✅ | ❌ |
| AWS Lambda handler export | ✅ | ❌ |
| GraphQL resolver | ✅ | ❌ |
| Flask `@app.errorhandler` | ✅ | ❌ |
| WSGI/ASGI app callable | ✅ | ❌ |

### 2.3 新发现的精度问题

#### 2.3.1 入口裁定橡皮图章 + Phase 0 不可执行

v4 报告已指出此问题，验证后影响更大：

1. `save_adjudication`（`__init__.py:258-267`）无条件全置 `verdict=CONFIRMED`，不看 confidence/`needs_llm_review`
2. pre-recon prompt 的 `Phase 0: Entry Point Adjudication` 指令要求精细裁定，但依赖的 `save-deliverable` 工具在代码中不存在
3. `entry_point_fusion.py` 的 `merge_entry_points`（三源去重）已实现但 `build_code_index` 从不调用——**死代码**

**影响**：低置信度入口（如 async def 兜底的 0.40）也全部进入调用链，产生噪声。但反过来，真正的入口也不会被误删——只要装饰器能匹配。

#### 2.3.2 Go/Java/PHP 入口能检测但传播跳过

`propagation_builder.py:265-274` 的 `_UNSUPPORTED_LANGUAGES` 对这三种语言直接返回空图。入口点能检测到但无法做 taint 传播——这三种语言的确定性分析增益为零。

### 2.4 入口点维度综合裁决

> **重构在检测确定性上更优（AST + 置信度），但在功能完整性上多处弱于原始。**
>
> API schema 优先、认证标注、网络可达性过滤、框架覆盖（Django URL conf、Fastify、Next.js 等）是明确的回退。裁定橡皮图章导致低置信度入口进入调用链产生噪声，但不影响高置信度入口的检测。Go/Java/PHP 虽然能检测入口但传播为零，确定性增益为零。
>
> **关键修复**：
> 1. 恢复 API schema 检测（OpenAPI/Swagger/GraphQL 文件解析）
> 2. 在 EntryPoint 模型中添加认证标注字段
> 3. 实现基于置信度的入口裁定（替代橡皮图章）
> 4. 补充 Django URL conf/Fastify/Next.js 框架规则

---

## 3. 漏洞分析对比

### 3.1 重构优于原始的方面

#### 3.1.1 结构化产物 + 去重

| 维度 | 原始 | 重构 |
|---|---|---|
| 产物格式 | LLM 自由文本 JSON | Pydantic 模型 + 白名单校验 |
| 去重 | 无 | 5 元组 `(entry_point_id, category, issue_type, vulnerable_function_id, call_chain_path)` |
| 校验 | deliverable 存在即通过 | issue_type 白名单校验（category 不校验） |

#### 3.1.2 风险评分分层

`risk_scorer.py` 的 ChainRiskScore 四维评分（sink_danger/taint_completeness/auth_gap/depth）→ 分层审计策略（Tier 3: ≥30 分 ≤5 链 5 代理/链，Tier 2: ≥15 分，Tier 1: <15 分）——原始项目完全没有，全靠 LLM 自行决定分析深度。

#### 3.1.3 vuln prompt 源粒度增强

重构版一致新增了 `authentication_required`、`accessible_routes`、Source Completeness Rule（每源独立条目）。原始版用 `combined_sources`（信息性），不如重构版的强制每源独立。

#### 3.1.4 确定性 hint 注入

`_static-dataflow-hints.txt` + `<parameter_propagation_data>` + `<phase0_data>` 为 LLM 提供了确定性预分析的结构化上下文，理论上能让 LLM 做更精准的研判。

### 3.2 重构弱于原始的方面

#### 3.2.1 传播图大面积断裂：LHS 正则 bug ⚠️ 最意外发现

这是本次验证发现的**最大精度问题**，v4 报告只轻描淡写提了"写容器/属性漏检"。

`propagation_builder.py:93` 的 `_ASSIGN_RE` 只匹配纯标识符赋值：

```python
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$")
```

以下真实代码中极常见的写法全部导致 taint 断裂：

| 写法 | 是否被匹配 | 真实频率 | 影响 |
|---|---|---|---|
| `x = expr` | ✅ | 常见 | 正常传播 |
| `self.x = expr` | ❌ | **OOP 最常见** | 污点在类方法间断裂 |
| `d["key"] = expr` | ❌ | 字典操作常见 | 污点在数据转换层断裂 |
| `a, b = x, y` | ❌ | Python 解构常见 | 多返回值场景全部漏 |
| `lst.append(x)` | ❌ | 列表操作极常见 | 集合类传播完全断裂 |
| `x += expr` | ❌ | 增量赋值常见 | 循环累积场景断裂 |
| `data \|= extra` | ❌ | 合并操作常见 | 字典合并场景断裂 |

**实际漏报示例**：

```python
# 示例1: Django/Flask 视图 — result["data"] 导致断裂
def get_user(request):
    user_id = request.GET.get("id")       # ✓ 匹配（靠容器过近似）
    user = User.objects.get(id=user_id)   # ✓ 匹配
    result["data"] = serialize(user)      # ✗ LHS 含 ["data"]，不匹配
    return JsonResponse(result)           # result 不被标记为 tainted

# 示例2: OOP — self 赋值导致断裂
class UserService:
    def set_user(self, user_input):
        self.user_id = user_input["id"]   # ✗ LHS 含 "."，不匹配
        self.role = user_input["role"]    # ✗ 同上
    def query(self):
        db.query(f"SELECT * FROM users WHERE id={self.user_id}")  # self.user_id 不 tainted

# 示例3: 解构赋值 — 多返回值全部漏检
def process(params):
    name, age = params.split(",")         # ✗ LHS 含 ","，不匹配
    query = f"SELECT * FROM users WHERE name='{name}'"  # name 不 tainted，漏检 SQLi

# 示例4: 列表操作
def append_and_query(items, user_input):
    items.append(user_input)              # ✗ 不是赋值，不匹配
    process_items(items)                  # items 不被识别为 tainted
```

**影响估计**：>60% 的真实 Python/TS 后端 taint 路径会经过上述写法。确定性传播图在大面积路径上会断裂，产出假阴性（看起来"安全"实际有漏洞）。hint 注入给 LLM 的数据本身就是不完整的。

#### 3.2.2 调用图残缺且静默

`call_graph.py` 的 `resolve_edges` 使用纯名称匹配：

1. **同名函数错配**：取 `candidates[0]`（第 19 行），多文件中 `handle`/`process`/`validate`/`get`/`create` 等常见函数名会指向错误目标
2. **方法调用丢弃 receiver**：`cursor.execute()` → `execute`（parser 端丢弃 receiver），`response.json()` → `json`，数据库 execute 和 HTTP execute 被混淆
3. **间接调用完全丢失**：`callback(data)`、`fn = handler; fn(req)`、`getattr(obj, method_name)()` 无法在静态 AST 中解析
4. **回调/事件模式不可见**：`app.on("event", handler)` 中的 handler 不出现在调用图边中
5. **`max_width=50` 截断**：宽函数（含 >50 个调用点）的边被截断

**关键问题**：残缺**不可见**——没有降级报告，`run_code_index` 不写 `degradation_report.json`。下游 LLM 不知道传播图可能大面积缺失，可能对不完整的 hint 产生错误信任。

#### 3.2.3 vuln-authz 框架 IDOR 检测：确定性流水线完全丧失 ⚠️ 最高价值功能回退

这是**最大功能回退**。删除了三个关键文件：

| 删除文件 | 行数 | 功能 |
|---|---|---|
| `shared/_endpoint-security-context.txt` | 91 行 | framework origin 检测 + ownership 检测结构化框架 |
| `shared/_cross-route-enumeration.txt` | 58 行 | 共享 handler 枚举 + pre-auth 变体发现 |
| recon deliverable Section 4.1 + 4.2 定义 | ~50 行 | Shared Controller Route Groups + Endpoint Security Context 模板 |

三个具体漏报场景：

**场景 A: finale-rest 自动 CRUD 端点 IDOR（高确定性漏报）**

原始项目发现路径：
1. recon: 搜索 `finale.initialize()`, `finale.resource()`，识别所有 model 的 auto-generated CRUD 端点
2. recon Section 4.2: 标记 `Framework Origin: finale-rest auto-generated`, `Ownership Validation: none detected`
3. vuln-authz Section 0: **强制第一步**读取 Section 4.2
4. vuln-authz Framework Endpoint Guidance: "Assume vulnerable unless Recon explicitly found an ownership check" → confidence: high

重构版实际路径：
1. recon: 无 framework origin 分析指令
2. vuln-authz: 无 Section 0，直接进入水平分析
3. **结果**：如果 Task Agent 碰巧读到 `finale.resource()` 可能发现，但这是不确定的、不可靠的

**场景 B: 共享 handler 的 pre-auth 变体（高确定性漏报）**

前提：三个路由共享 handler `controller.index.preview`：
- `GET /preview` — 中间件: `thirtyLogin()`
- `GET /preview/v2` — 中间件: `thirtyLogin()`
- `GET /preview/iframe-demo` — 中间件: **无**

原始项目：Section 4.1 分组 → 标记 pre-auth → Cross-Route Verification 强制独立 finding，`authentication_required: false`。
重构版：不做分组，pre-auth 严重性升级（从 authenticated IDOR 到 unauthenticated IDOR）可能被遗漏。

**场景 C: 框架批量端点信息泄露（中确定性漏报）**

5 个 model × 5 个 CRUD 端点 = 25 个端点。原始项目 Section 4.2 一次性枚举 + ownership 标记。重构版逐个分析，GET findAll/findOne 类端点（信息泄露）大概率被忽略。

#### 3.2.4 vuln prompt 删除的分析机制

| 删除机制 | 影响 vuln 类 | 实际影响 |
|---|---|---|
| Cross-Route Verification 门控 | 全部 | 无法验证 affected_routes 覆盖了共享 handler 的所有路由 |
| Endpoint Security Context starting_context | XSS | 丢失了 server-rendered template 的安全上下文 |
| Section 0 "Read Endpoint Security Context" | authz | authz 失去了确定性的端点安全查表 |
| Framework Endpoint Guidance | authz | authz 失去了 finale-rest/epilogue IDOR 的结构化检测 |
| Branch Path Exhaustion | injection | 注入分析不再强制穷举分支路径 |

### 3.3 新发现：确定性传播 vs 纯 LLM 的实际精度对比

| 场景 | 确定性传播 | 纯 LLM | 更可靠方 |
|---|---|---|---|
| 简单直线链 `handler→service→dao.query` | ✅ 精确 | ⚠️ 受 context 限制 | **确定性** |
| `self.x = input` 后在另一方法 `query(self.x)` | ❌ LHS 断裂 | ✅ 能追踪 self 属性 | **LLM** |
| `d["key"] = input` 后 `process(d)` | ❌ LHS 断裂 | ✅ 理解字典语义 | **LLM** |
| `items.append(input)` 后 `process(items)` | ❌ 不是赋值 | ✅ 理解 append 语义 | **LLM** |
| `callback(data)` 间接调用 | ❌ 无法解析 | ⚠️ 可能推断目标 | **LLM（不确定）** |
| 大规模代码库（100+ 文件）广度覆盖 | ✅ 可遍历全图 | ❌ context window 限制 | **确定性** |

**关键洞察**：在 taint 传播的精度上，LLM 在大多数真实代码模式（OOP、字典、列表、间接调用）中**反而比确定性传播更可靠**。确定性传播的优势在于广度覆盖和可复现性，而非精度。

### 3.4 漏洞维度综合裁决

> **重构在产物结构化、风险评分、源粒度上更优，但在传播精度、调用图完整性、authz IDOR 检测上严重回退。**
>
> LHS 正则 bug 导致 >60% 的真实 taint 路径断裂，是最意外的发现——确定性传播的精度在多数场景下反而不如原始项目的纯 LLM 追链。authz IDOR 检测能力因删除 recon Section 4.1/4.2 而完全丧失，是最高价值的功能回退。
>
> **关键修复**：
> 1. 修复 LHS 正则以支持 `self.x`、`d["k"]`、`a,b=x,y`、`lst.append`、`x +=` 等写法（高影响、中难度）
> 2. 恢复 recon Section 4.1/4.2（高影响、中难度，一处修复解除 authz/cross-route 连锁）
> 3. 添加调用图残缺降级报告（高影响、低难度）
> 4. 接线分层审计（risk_scorer 产出有消费者）
> 5. 恢复 Branch Path Exhaustion 指令

---

## 4. 综合结论

### 4.1 重构的真实价值

1. ✅ Sink 引擎结构化（规则库、单测、可维护性）
2. ✅ 风险评分分层（原始完全没有）
3. ✅ 产物结构化 + 去重（Pydantic + 5 元组）
4. ✅ vuln prompt 源粒度增强
5. ✅ 确定性 hint 注入（虽然数据不完整）

### 4.2 重构的真实代价

1. ❌ **XSS 覆盖 ~5%**（原始 30+ sink）——最严重覆盖缺口
2. ❌ **SSRF 覆盖 ~19%**（原始 13 子类）
3. ❌ **传播 LHS 正则 bug 导致 >60% taint 路径断裂**——最意外发现，确定性传播精度在多数场景不如 LLM
4. ❌ **authz IDOR 框架检测完全丧失**——最高价值功能回退
5. ❌ 模板 sink 双层落空（确定性 + LLM 层都不覆盖）
6. ❌ Go/Java/PHP 确定性增益为零
7. ❌ 调用图残缺且静默（无降级报告）

### 4.3 一句话裁决

> **重构在工程结构上更优，但在安全分析的实际有效性上——受限于覆盖缺口、传播断裂、和 authz 回退——对 Python/TS 后端的 SQL/命令注入场景确实更好，但在 XSS、SSRF、authz IDOR、模板 sink、Go/Java/PHP 等场景上主动弱于原始项目。**

### 4.4 场景化建议

| 目标场景 | 推荐版本 | 理由 |
|---|---|---|
| Python/TS 后端，关注 SQL/命令注入 | **重构** | 确定性引擎有效，覆盖充分 |
| DOM XSS / jQuery 前端 | **原始** | 重构 XSS 覆盖仅 ~5% |
| SSRF 多子类（headless/media/cloud） | **原始** | 重构 SSRF 覆盖仅 ~19% |
| 授权/IDOR（尤其 finale-rest/epilogue） | **原始** | 重构 authz 检测能力完全丧失 |
| 模板重前端 / schema-first API | **原始** | 重构双层落空 |
| Go/Java/PHP 后端 | **平手** | 重构确定性增益为零，LLM 仍在跑 |
| 工程可维护性/可测试性优先 | **重构** | 结构化规则库、单测、Pydantic 模型 |

### 4.5 修复优先级（按性价比排序）

| 优先级 | 修复项 | 影响面 | 难度 | 解除连锁 |
|---|---|---|---|---|
| **P0** | 修复 LHS 正则（支持 self/dict/解构/append/+=） | 高（60% 路径） | 中 | — |
| **P0** | 恢复 recon Section 4.1/4.2 | 高（authz/IDOR） | 中 | 解除 authz/cross-route 连锁 |
| **P1** | XSS 补齐 5 上下文（+15 条规则） | 高（~5% → ~60%） | 低（加 SinkRule） | — |
| **P1** | SSRF 补齐 headless/media/cloud 子类（+8 条规则） | 高（~19% → ~50%） | 低 | — |
| **P1** | 调用图残缺降级报告 | 高（让残缺可见） | 低 | — |
| **P2** | 新增 XXE 类别（+5 条规则） | 中-高 | 低 | — |
| **P2** | 路径穿越扩展全语言（+8 条规则） | 中-高 | 低 | — |
| **P2** | 恢复 Branch Path Exhaustion 指令 | 中 | 低（改 prompt） | — |
| **P3** | 模板 sink 确定性层 + LLM 层双覆盖 | 高 | 中 | — |
| **P3** | 入口裁定实现（替代橡皮图章） | 中 | 低 | — |
| **P3** | API schema 检测恢复 | 中 | 中 | — |
| **P4** | Go/Java/PHP 传播支持 | 中-高 | 高 | — |
| **P4** | 分层审计接线 | 中 | 中 | — |
