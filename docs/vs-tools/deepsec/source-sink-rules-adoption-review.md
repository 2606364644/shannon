# DeepSec Source/Sink 规则吸收评估

- **检查日期**：2026-08-12（2026-08-19 复核：规则基线与 §3 缺口状态全部未变，建议仍有效；新发现 `ts-res-redirect` 死规则，见 §3.2）
- **对比对象**：`/root/deepsec` 与当前项目 `/root/shannon-py`
- **结论**：可以吸收；建议按“确定性 AST 规则、候选/LLM 规则、检测器基础能力”分层迁移，不能直接复制 DeepSec 的文件级正则 matcher。

## 1. 结论摘要

DeepSec 与当前项目的规则模型不同：

- DeepSec 有 198 个 matcher 插件。matcher 以文件级正则为主，输出 `CandidateMatch`，后续交给 LLM 或人工复核。
- 当前项目采用 source/sink/taint 分层：
  - `source_rules.yml`：26 条 source 规则；
  - `sink_rules.yml`：74 条 AST sink 规则；
  - `sink_candidates.yml`：20 组 LLM 补召回候选；
  - 输出带参数位和 source/sink 位置的 `SourcePoint`、`SinkCallSite`，并进入 taint flow。

因此，DeepSec 规则应按以下方式吸收：

1. 能由 AST 精确表达的调用 API，迁移到 `source_rules.yml` / `sink_rules.yml`；
2. 依赖字符串构造、附近上下文或属性赋值的模式，迁移到 `sink_candidates.yml` 或新增专门 detector；
3. 路由、框架、配置和技术栈识别规则，不应直接塞进 source/sink YAML，应作为入口点或技术栈 gate。

## 2. 当前项目基线

相关代码位于：

```text
packages/core/src/supernova_core/code_index/
├── source_detector.py
├── sink_detector.py
├── source_discovery_llm.py
├── sink_discovery_llm.py
├── storage_detector.py          # 二阶存储 taint 轨（另有 storage_discovery_llm.py）
└── data/
    ├── source_rules.yml
    ├── sink_rules.yml
    ├── sink_candidates.yml
    └── storage_rules.yml        # 二阶存储 sink（ORM save 等），见二阶存储双轨 spec
```

当前 AST parser 支持：

```text
Python / Go / TypeScript(JavaScript) / Java / PHP
```

当前规则库统计：

| 规则库 | 数量 | 说明 |
|---|---:|---|
| `source_rules.yml` | 26 | Express、Koa、Django、Flask、DRF、Gin、Spring、PHP superglobal 等 |
| `sink_rules.yml` | 74 | SQL、command、deserialization、SSRF、template、XSS、file、redirect |
| `sink_candidates.yml` | 20 组 | 按语言+receiver 精确匹配的 LLM 补召回候选模式表 |
| `storage_rules.yml` | 13 | 二阶存储 sink（Java ORM save 等），服务二阶存储 taint 双轨 |

定向回归测试：

```text
88 passed
```

规则检查阶段没有修改代码；本次新增本评估文档。

## 3. 代表性缺口验证

使用 DeepSec matcher 中的典型样例跑当前 AST detector，结果如下：

| 模式 | 当前结果 | 判断 |
|---|---:|---|
| `child_process.execSync(x)` | 未命中 | 应新增 command sink |
| `cp.spawn("sh", ["-c", x])` | 未命中 | 应新增 command sink |
| `spawnSync(...)` | 未命中 | 应新增 command sink |
| `Function(userInput)` | 未命中 | 应新增 command sink |
| `vm.runInNewContext(...)` | 未命中 | 应新增 command sink |
| `axios.post(url)` / `axios.request(url)` | 未命中 | SSRF 缺口 |
| `https.request(url)` / `http.get(url)` | 未命中 | SSRF 缺口 |
| `knex.whereRaw(...)` | 未命中 | SQL raw 缺口 |
| `Sequelize.literal(...)` | 未命中 | SQL raw 缺口 |
| `db.prepare(...)` | 未命中 | SQLite/raw SQL 缺口 |
| `db.query(...)` | 命中通用 `ts-orm-model-query` | 已有泛化覆盖 |
| `document.write(x)` | 命中 | 已有规则 |
| `el.innerHTML = x` | 未命中 | 属性赋值 detector 缺失 |
| `node.outerHTML = x` | 未命中 | 属性赋值 detector 缺失 |
| `res.redirect(x)` | 未命中 | `ts-res-redirect` 是死规则（见 §3.2），修 `receiver_pattern` 即可确定性命中 |
| `window.location = x` | 未命中 | assignment sink 缺失 |
| Hono `c.req.query("q")` | 未命中 | source 缺口 |
| Next `request.nextUrl.searchParams.get(...)` | 未命中 | source 缺口 |
| Fastify `request.query.q` | 未命中 | source 缺口 |

### 3.1 现有 `ts-innerhtml` 规则的问题

`data/sink_rules.yml` 中虽然存在 `ts-innerhtml`，但 `TypeScriptParser.iter_calls()` 只遍历 `call_expression`。因此：

```typescript
el.innerHTML = userInput;
```

不会被抽取为 call，`callee: innerHTML` 规则无法命中真实的属性赋值场景。

这不是补一条 YAML 即可解决的问题，需要新增 property assignment、JSX 属性或模板绑定 detector。

### 3.2 `ts-res-redirect` 是死规则（2026-08-19 复核发现）

`sink_rules.yml` 中的 `ts-res-redirect`（`callee: redirect`、`receiver_pattern: null`、`languages: [typescript]`）从 rule_id 看意图是覆盖 Express `res.redirect(url)`，但 `sink_detector._rule_matches()` 的语义是 **`receiver_pattern: null` 只匹配裸调用**：

```python
if rule.receiver_pattern is None:
    return receiver is None   # 裸调用才命中
```

实测（TypeScriptParser + detect_sinks）：

```text
redirect(u)    -> 命中 ts-res-redirect   # TS/JS 现实中不存在这种写法
res.redirect(u) -> 未命中                  # 真实场景 100% 带 receiver
```

也就是说这条规则在生产中**永远命不中**。连带影响：`vuln_chain_builders/ssrf_builder.py:46` 的 REDIRECT sink 过滤（防 open redirect 污染 SSRF 分类）也因无确定性 REDIRECT sink 可滤而空转。这正是 §5.1「examples 契约」要防的"规则写入 YAML、detector 实际永远命不中"的现存活案例。

对照：`py-flask-redirect` 同为 `receiver_pattern: null`，但 Flask 的 `redirect(url)` 确实是裸调用（`from flask import redirect`），实测命中——同型规则在 Python 是活的。

修复是一行：`receiver_pattern` 从 `null` 改为 `.+`（或收窄为 `^(res|response|ctx)$`），并按 §5.1 补正反例测试。

## 4. 建议优先吸收的规则

### 4.1 P0：可直接进入现有 AST sink 体系

#### JavaScript/TypeScript RCE

参考：

```text
/root/deepsec/packages/scanner/src/matchers/rce.ts
```

建议新增：

```text
execSync
spawn
spawnSync
Function
vm.runInNewContext
vm.runInThisContext
```

当前 TypeScript parser 已能够抽取 `vm.runInNewContext(code, ctx)` 这类调用的 callee 和 receiver，因此这批规则可以直接映射到 `command` 类 sink。

#### JavaScript/TypeScript SSRF

参考：

```text
/root/deepsec/packages/scanner/src/matchers/ssrf.ts
```

当前已有 `fetch`、`axios.get`，建议补充：

```text
axios.post
axios.put
axios.delete
axios.patch
axios.request
http.request
https.request
http.get
https.get
undici.request
got.get / got.post
```

`new URL(...)`、`url.parse(...)` 更适合作为 URL 构造/传播节点，不建议直接当最终 sink。

#### Raw SQL API

参考：

```text
/root/deepsec/packages/scanner/src/matchers/js-sql-raw.ts
/root/deepsec/packages/scanner/src/matchers/py-sql-raw.ts
/root/deepsec/packages/scanner/src/matchers/go-sql-raw.ts
/root/deepsec/packages/scanner/src/matchers/jvm-sql-raw.ts
```

建议补充以下调用 API：

```text
JavaScript/TypeScript:
  Sequelize.literal
  Sequelize.fn
  knex.whereRaw
  knex.orderByRaw
  knex.havingRaw
  sql.raw
  sql.unsafe
  db.prepare
  db.exec
  Prisma $queryRaw / $executeRaw
  Drizzle sql

Python:
  asyncpg conn.fetch / fetchrow / fetchval
  session.execute
  Model.objects.extra
  connection.cursor().execute

Go:
  QueryRow
  QueryContext
  QueryRowContext
  ExecContext
  GORM Where
  sqlx Select / Get / NamedQuery
  pgx Query / QueryRow

Java:
  prepareStatement
  createStatement().execute*
  JdbcTemplate.update
  queryForObject
  queryForList
  batchUpdate
  MyBatis @Select("${...}")
  jOOQ DSL.sql / DSL.field
```

调用 API 本身可以作为确定性 sink；是否真的存在注入，应继续由危险参数、字符串构造和 taint/LLM 判定决定。

### 4.2 P0：source 规则补充

#### JavaScript/TypeScript

建议补充：

```text
Hono:
  c.req.query("q")       -> query
  c.req.param("id")       -> path
  c.req.json()             -> body
  c.req.formData()         -> form
  c.req.text()             -> body

Fastify:
  request.query.q         -> query
  request.params.id       -> path
  request.body            -> body
  request.headers         -> header

NestJS:
  @Body() body            -> body
  @Param("id") id        -> path
  @Query("q") q          -> query
  @Headers("x") value    -> header

Next.js route handler:
  request.nextUrl.searchParams.get("q") -> query
  await request.json()                   -> body
  request.formData()                     -> form
  request.headers.get(...)               -> header
  params.id                              -> path
```

当前 Express、Koa 已有较好覆盖，Hono、Fastify、NestJS、Next route handler 是主要缺口。

#### Python

当前主要覆盖索引写法，建议补充：

```python
request.args.get("id")
request.form.get("name")
request.json.get("name")
request.get_json()
request.headers.get("X-Token")
request.cookies.get("sid")
request.files.get("upload")
```

#### Go

建议补充：

```go
c.GetHeader("Authorization")
c.ShouldBindJSON(&body)
r.FormValue("id")
r.PostFormValue("name")
r.Header.Get("X-Token")
r.Cookie("sid")
```

并扩展 Echo、Fiber、Chi、Gorilla 等常见访问器。

#### Java

当前已有 `@RequestParam`、`@PathVariable`、`@RequestBody`，建议补充：

```text
@RequestHeader
@CookieValue
@RequestPart
@QueryParam
@PathParam
@HeaderParam
@FormParam
HttpServletRequest.getParameter
HttpServletRequest.getHeader
HttpServletRequest.getCookies
```

#### PHP

建议补充：

```text
$_COOKIE
$_FILES
$_SERVER
$request->input(...)
$request->query(...)
$request->route(...)
$request->all()
$request->file(...)
```

### 4.3 P1：适合进入 candidate/LLM 层

#### NoSQL 注入

参考：

```text
/root/deepsec/packages/scanner/src/matchers/js-nosql-injection.ts
/root/deepsec/packages/scanner/src/matchers/py-nosql-injection.ts
```

高价值上下文包括：

```text
$where + 字符串拼接
.find(JSON.parse(req.body))
new RegExp(req.query.x)
$regex 直接绑定 request 输入
aggregate 中出现 $where
```

当前项目已有宽泛的 `find/findOne/update` 候选，但建议扩展候选 schema，增加：

```yaml
context_patterns:
arg_patterns:
exclude_patterns:
```

#### 路径穿越

当前 `sink_candidates.yml` 已覆盖 `readFile`、`writeFile`、`createReadStream`、Go `os`、Java `Files`、PHP 文件 API 等大方向。

可继续吸收：

```text
path.join(..., req.body.path)
path.resolve(..., params.dir)
readFile(`...${req.body.path}`)
writeFileSync(req.body.path, ...)
```

#### Open redirect

参考：

```text
/root/deepsec/packages/scanner/src/matchers/open-redirect.ts
```

建议补充：

```text
res.redirect(...)
Location: req.body.next
window.location = params.next
```

其中 `res.redirect()` 可进入确定性 sink——且不必新写规则：修复 §3.2 的 `ts-res-redirect` 死规则（`receiver_pattern: null` → `.+`）即可命中；`window.location`、`Location` header 应进入 assignment/header detector 或 candidate 层。

#### Prototype pollution / object injection

参考：

```text
/root/deepsec/packages/scanner/src/matchers/object-injection.ts
```

高价值模式包括：

```text
Object.assign({}, req.body)
_.merge({}, req.body)
defaultsDeep(...)
obj[req.query.key] = ...
```

当前 `SinkCategory` 尚无 `prototype_pollution` 或 `object_injection`，建议先作为独立 candidate，不要伪装成现有 generic sink。

#### Unsafe JSON/YAML deserialization

`JSON.parse(req.body)` 不应自动判定为漏洞；更适合作为未 schema 校验的对象输入 candidate。`eval(JSON.parse(input))` 则可以作为 command sink，JS `yaml.load` 也应加入候选或确定性规则。

## 5. DeepSec 工程化能力值得吸收

### 5.1 `examples` 作为规则契约

DeepSec 的 matcher 都带 `examples`，并由：

```text
/root/deepsec/packages/scanner/src/__tests__/matcher-examples.test.ts
```

自动验证每个示例至少命中一次。

建议给当前 YAML 规则增加：

```yaml
examples:
  positive:
    - ...
  negative:
    - ...
```

然后自动生成 source/sink 规则测试，避免出现“规则写入 YAML，但 detector 实际永远命不中”的情况。

### 5.2 `noiseTier` 与技术栈 gate

DeepSec 提供：

```text
precise / normal / noisy
requires.tech
requires.sentinelFiles
```

当前项目主要依赖 `needs_review_default` 和 `receiver_pattern`。建议逐步增加：

```yaml
noise_tier: precise | normal | noisy
requires:
  tech: [express]
  sentinel_files: [package.json]
```

对于 `query`、`get`、`open`、`render`、`execute`、`find` 等通用方法，技术栈 gate 和 receiver 约束比继续扩大正则更重要。

### 5.3 全局排除测试和构建目录

DeepSec 统一排除：

```text
node_modules
 dist
build
.next
tests
fixtures
*.test.*
*.spec.*
```

当前 parser 已跳过部分 vendor/build 目录，但 sink detector 本身仍会遍历所有函数块，建议在 detector 入口统一处理测试、fixture 和生成代码。

### 5.4 属性赋值和模板绑定作为一等 sink

建议新增：

```text
iter_assignments()
iter_jsx_attributes()
iter_template_bindings()
```

或者新增独立的 `assignment_sink_detector.py`，处理：

```text
el.innerHTML = value
node.outerHTML = value
dangerouslySetInnerHTML
v-html
[innerHTML]
window.location = value
```

不要继续将这些模式伪装成 `callee` 规则。

## 6. 不建议立即直接迁移的内容

1. **当前 parser 不支持的语言**：Ruby、Rust、Kotlin、Swift、C#、Dart、Crystal、Lua、Clojure、Apex 等，需要先补 parser、FuncBlock 和 taint 语义。
2. **纯路由/鉴权/配置 matcher**：public endpoint、CORS、Dockerfile、Terraform、Android manifest 等应放到独立 surface/config detector。
3. **宽泛文件级正则**：DeepSec 可以依赖后续 LLM 过滤；当前确定性 sink 会进入 taint graph，不能直接复制，否则会污染链路。

## 7. 推荐实施顺序

### 第一阶段：扩充调用型规则

修改：

```text
data/source_rules.yml
data/sink_rules.yml
```

优先加入：

- 修复 `ts-res-redirect` 死规则 `receiver_pattern`（一行，见 §3.2）；
- Hono/Fastify/Nest/Next source；
- Python/Go/Java/PHP method-style source；
- `spawn`、`spawnSync`、`execSync`、`Function`、`vm.runInNewContext`；
- `axios` 其他 HTTP 方法；
- `http(s).request`；
- Go/Python/Java/JS raw SQL API。

每条新增规则同步补 AST 正向测试。

### 第二阶段：增强候选规则

修改：

```text
data/sink_candidates.yml
sink_discovery_llm.py
source_discovery_llm.py
```

增加：

- `context_patterns`；
- `arg_patterns`；
- NoSQL `$where` / `$regex` / `JSON.parse(req...)`；
- `path.join` / `path.resolve`；
- redirect header/location；
- prototype pollution。

### 第三阶段：补齐 detector 基础能力

- assignment sink；
- JSX/template sink；
- `examples` 自动化契约；
- `noise_tier`；
- tech/sentinel gate；
- 全局测试、fixture、构建目录过滤。

## 8. 最终判断

当前项目可以吸收 DeepSec 中最有价值的调用型规则，优先收益最大的方向是：

1. RCE；
2. SSRF；
3. raw SQL；
4. 框架 source；
5. NoSQL/路径穿越/开放重定向的上下文候选规则。

XSS 属性赋值、JSX/Vue/Angular 模板绑定和 `window.location` 等规则，需要先补齐 assignment/template detector，再谈确定性规则迁移。
