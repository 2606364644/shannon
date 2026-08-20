# DeepSec 规则吸收清单

- **来源**：[`source-sink-rules-adoption-review.md`](./source-sink-rules-adoption-review.md) §3/§4/§7 的建议，拆成一条条可以直接动手的规则项。
- **日期**：2026-08-20 生成，同日实施完毕。生成时对照过 `/root/deepsec/packages/scanner/src/matchers/` 原始代码和当时的规则库（source 26 条 / sink 74 条 / candidates 20 组 / storage 13 条），重复的没有再列。
- **状态标记**：☐ 待做 ｜ ◐ 部分完成 ｜ ☑ 完成。
- **范围约定**：这份清单全部改在 GitNexus 轨——要么改确定性层的规则 YAML，要么改 detector 代码，一律不碰 LLM 轨的 prompt（这是 CLAUDE.md §1 定的规矩：扩 sink 覆盖就走这两条路）。
- **怎么算做完**：每条新规则、每处修改都配两个方向的测试——一个真实写法的正例（必须命中），一个长得像但不该命中的负例（不能命中）。改完跑 code_index 定向回归（当前 968 个通过 / 4 个改动前就失败的）。

## 实施结果（截至 2026-08-20）

| 里程碑 | 内容 | 状态 | 测试 |
|---|---|---|---|
| M1 §0 | 修复 ts-res-redirect、php-laravel-whereraw 两条死规则 | ☑ | 6 绿 |
| M2a §1.1-1.2 | RCE 7 条 + SSRF 10 条 sink 规则（1.1.6 探针验证不可行，转 §4.2） | ☑ | 18 绿 |
| M2b §1.3-1.4 | raw SQL 新增 24 条 + 扩改 py-db-cursor-execute + PHP redirect 1 条 | ☑ | 20 绿 |
| M2c §2 | source 规则 42 条（Hono/Fastify/Next/Python/Go/Java/PHP） | ☑ | 13 绿 |
| M3 §3.1/3.3/3.4/3.6/3.7 | 候选表加字段 + 4 个新模式组 + 给既有 fs/os 组加参数过滤 | ☑ | 9 绿 |
| M3 §3.2/3.5 | NoSQL 组收窄 / go 组补 Where·First·NamedQuery | ☐ 没做 | — |
| M4 §4.4 | examples 自动验证（loader + 契约测试 + 3 条代表规则） | ☑ | 1 绿 |
| M4 §4.1 | 赋值型 sink 识别（innerHTML / outerHTML / window.location 等） | ☐ 立项 | — |
| M4 §4.2 | 支持 new_expression（new Function） | ☐ 立项 | — |
| M4 §4.3 | 支持 tagged template（drizzle/prisma） | ☐ 立项 | — |
| M4 §4.5 | noise_tier / tech gate | ☐ 立项 | — |
| M4 §4.6 | 全局排除测试/构建目录（共享排除表 + 四个文件发现入口接入） | ☑ | 17 绿 |

- **规则库规模**：source 从 26 条扩到 68 条，sink 从 74 条扩到 116 条，候选表从 20 组扩到 24 组。sink 新增的 42 条 = RCE 7 + SSRF 10 + raw SQL 24 + PHP redirect 1；候选表新增的 4 组 = 原型污染 / JS yaml.load / JSON.parse(TS) / python json.loads。
- **回归**：code_index 985 个测试通过（规则吸收时基线 968 + §4.6 追加 17）；另有 4 个失败的（build_code_index / gitnexus / php_parser / taint_persist）在改动前就是挂的，与本次无关。本次没有引入任何新失败。
- **§3.2 / §3.5 为什么没做**：这两项都是对候选组的调整（一个收窄、一个扩召回），空口判断容易调错方向，等真机扫出误报/漏报数据后再定，候选表先保持原样。
- **§4 剩下的各项为什么还留着**：它们要么要给 parser 增加新的遍历能力，要么要给 SinkRule 加字段并把全部规则重新标注一遍，是独立的一块工作，不该混在规则吸收里顺手做。4.6 原本也归在这一档，后来发现它根本不用动 parser——只是文件发现层的名单扩充加一个共享模块，就提前做掉了。

---

## §0 死规则修复（2 项，最先做）

库里已经有过几次同样的修复：把 `receiver_pattern: null` 改成能匹配带 receiver 写法的值（先例有 `go-db-query`、`java-stmt-executequery`、`java-jpa-createnativequery` 等）。下面两条是同样的问题。

| # | rule_id | 问题在哪 | 怎么修的 | 状态 |
|---|---|---|---|---|
| 0.1 | `ts-res-redirect` | `receiver_pattern: null` 只匹配裸调用 `redirect(u)`，但 TS/JS 里真实代码都写成 `res.redirect(u)`，带 receiver——这条规则实际上一条都命中不了，还连累 `ssrf_builder.py` 里的 REDIRECT 过滤跟着白跑 | 改成 `"^(res\|response\|ctx)$"`（Express/Koa/Fastify 的惯例变量名） | ☑ |
| 0.2 | `php-laravel-whereraw` | 同一个问题：Laravel 里真实写法是 `$query->whereRaw()` / `DB::whereRaw()`，都带 receiver，裸调用几乎不存在 | 改成 `".+"`（receiver 变量名没法穷举，参照 `java-stmt-execute` 的写法） | ☑ |

测试要求：正例 `res.redirect(u)`、`DB::whereRaw(sql)` 必须命中；负例是 TS 里的裸调用 `redirect(u)`，必须不命中——这种写法本来就不存在，写进测试是为了把语义固定住，防止以后又改回去。

---

## §1 `sink_rules.yml` 新增

命名跟随库里已有的风格：语言前缀-库名-方法名。callee 要完整相等才算命中，不做子串猜测；`receiver_pattern` 一律用 `^...$` 锚定。

### 1.1 Command / RCE（JS/TS）

deepsec 依据：`matchers/rce.ts`。

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.1.1 | `ts-child-process-execsync` | `execSync` | `null` | deepsec 的例子就是解构后裸调用 `execSync("whoami")`；这名字很专一，裸调用也不会误报 | ☑ |
| 1.1.2 | `ts-child-process-spawn` / `ts-child-process-spawn-qualified` | `spawn` | `null` / `"^(cp\|childProcess\|child_process)$"` | 写成了两条：一条管解构后的裸调用，一条管 `cp.spawn(...)` 带对象前缀的写法 | ☑ |
| 1.1.3 | `ts-child-process-spawnsync` / `ts-child-process-spawnsync-qualified` | `spawnSync` | `null` / `"^(cp\|childProcess\|child_process)$"` | 同上，裸调用和带前缀各一条 | ☑ |
| 1.1.4 | `ts-vm-runinnewcontext` | `runInNewContext` | `"^(vm)$"` | 已有的 `ts-vm-runincontext` 只认 `runInContext`，New/This 两个变体是不同的方法名，得各写一条 | ☑ |
| 1.1.5 | `ts-vm-runinthiscontext` | `runInThisContext` | `"^(vm)$"` | 同上 | ☑ |
| 1.1.6 | `ts-function-constructor` | `Function` | `null` | 写探针实际验证过：`new Function("return "+body)` 是 new_expression，`iter_calls()` 抽不出 callee（拿到的是空串），规则写了也匹配不到。所以不上规则，转 §4.2；`test_new_function_not_extracted` 把这个现状固定住 | ☐ 转 §4.2 |

库里已有的没重复加：`ts-eval`（裸 eval）、`ts-child-process-exec`（exec@cp/child_process）、`ts-vm-runincontext`。
deepsec rce.ts 里还匹配 `require('child_process')` 和 import 语句，但它们不是 sink，没有搬过来。

### 1.2 SSRF（JS/TS）

deepsec 依据：`matchers/ssrf.ts`。已有的没重复加：`ts-fetch`、`ts-axios-get`、`ts-needle-get`。

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.2.1 | `ts-axios-post` | `post` | `"^(axios)$"` | axios 的常用方法这次一起补齐 | ☑ |
| 1.2.2 | `ts-axios-put` | `put` | `"^(axios)$"` | | ☑ |
| 1.2.3 | `ts-axios-delete` | `delete` | `"^(axios)$"` | | ☑ |
| 1.2.4 | `ts-axios-patch` | `patch` | `"^(axios)$"` | | ☑ |
| 1.2.5 | `ts-axios-request` | `request` | `"^(axios)$"` | `axios.request(config)` | ☑ |
| 1.2.6 | `ts-http-request` | `request` | `"^(http\|https)$"` | Node 原生 `http.request(url)` / `https.request(url)` | ☑ |
| 1.2.7 | `ts-http-get` | `get` | `"^(http\|https)$"` | `needs_review_default: true`——get 这个词太常见，靠 receiver 限定模块名来收窄 | ☑ |
| 1.2.8 | `ts-undici-request` | `request` | `"^(undici)$"` | | ☑ |
| 1.2.9 | `ts-got-get` | `get` | `"^(got)$"` | | ☑ |
| 1.2.10 | `ts-got-post` | `post` | `"^(got)$"` | | ☑ |

没有搬过来的：`new URL(...)`、`url.parse(...)`——它们只是构造/传递 URL 的中间节点，不是真正发请求的 sink。

### 1.3 Raw SQL

deepsec 依据：`matchers/js-sql-raw.ts`、`py-sql-raw.ts`、`go-sql-raw.ts`、`jvm-sql-raw.ts`。
已有的没重复加：`ts-knex-raw`（raw@knex）、`ts-sequelize-query`、`ts-orm-model-query`（query@.+ 泛匹配）、`go-db-query`（Query@.+）、`go-gorm-raw/exec`、`java-jdbctemplate-query`、`java-jpa-create*`、`java-stmt-execute*`。

**JS/TS：**

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.3.1 | `ts-sequelize-literal` | `literal` | `"^[Ss]equelize$"` | 类和实例两种大小写都算 | ☑ |
| 1.3.2 | `ts-sequelize-fn` | `fn` | `"^[Ss]equelize$"` | `Sequelize.fn(cmd, val)` 拼接 | ☑ |
| 1.3.3 | `ts-knex-whereraw` | `whereRaw` | `"^(knex)$"` | 链式写法 `db('t').whereRaw()` 抽出来的 receiver 是链式中间结果，这条规则管不到，那类写法靠 §3 候选表兜底；本条只管 `knex.whereRaw` 这种直接调用 | ☑ |
| 1.3.4 | `ts-knex-orderbyraw` | `orderByRaw` | `"^(knex)$"` | 用得少，和 1.3.3 一起补上 | ☑ |
| 1.3.5 | `ts-knex-havingraw` | `havingRaw` | `"^(knex)$"` | 同上 | ☑ |
| 1.3.6 | `ts-postgresjs-raw` | `raw` | `"^(sql)$"` | postgres.js 的 `sql.raw` | ☑ |
| 1.3.7 | `ts-postgresjs-unsafe` | `unsafe` | `"^(sql)$"` | postgres.js 的 `sql.unsafe` | ☑ |
| 1.3.8 | `ts-better-sqlite3-prepare` | `prepare` | `"^(db\|database\|sqlite)$"` | better-sqlite3 的 `db.prepare(sql)` | ☑ |
| 1.3.9 | `ts-prisma-queryraw` | `$queryRaw` | `".+"` | `prisma.$queryRaw\`...\`` 是模板标签写法（归 §4.3），但 `$queryRaw(sql)` 是普通调用，本条能命中；`needs_review_default: true` | ☑ |
| 1.3.10 | `ts-prisma-executeraw` | `$executeRaw` | `".+"` | 同上 | ☑ |
| 1.3.11 | `ts-better-sqlite3-exec` | `exec` | `"^(db\|database\|sqlite)$"` | | ☑ |

Drizzle 的 `sql\`...\`` 也是模板标签，抽不出来，归 §4.3。

**Python：**

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.3.12 | `py-asyncpg-fetch` | `fetch` | `"^(conn\|pool\|connection)$"` | asyncpg；`fetchrow`/`fetchval` 用得少，这批没加 | ☑ |
| 1.3.13 | （扩改）`py-db-cursor-execute` | `execute` | `"^(cursor\|cnx\|conn\|db\|database\|session)$"` | SQLAlchemy 的 `session.execute(text(...))`——给已有规则的 receiver 集合加了 `session`，不是新增 | ☑ |
| 1.3.14 | `py-django-extra` | `extra` | `"^(objects)$"` | `Model.objects.extra(where=...)`，写法对齐已有的 `py-django-raw` | ☑ |
| — | `connection.cursor().execute` | | | 链式写法抽不出 receiver，上不了规则，靠 §3 候选表兜底 | |

**Go：**

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.3.15 | `go-db-queryrow` | `QueryRow` | `".+"` | 写法对齐已有的 `go-db-query`（Query@.+） | ☑ |
| 1.3.16 | `go-db-querycontext` | `QueryContext` | `".+"` | | ☑ |
| 1.3.17 | `go-db-queryrowcontext` | `QueryRowContext` | `".+"` | | ☑ |
| 1.3.18 | `go-db-execcontext` | `ExecContext` | `".+"` | | ☑ |
| 1.3.19 | `go-sqlx-get` | `Get` | `".+"` | sqlx；`needs_review_default: true`（Get 词太泛，receiver 变量名又没法穷举） | ☑ |
| 1.3.20 | `go-sqlx-select` | `Select` | `".+"` | crAPI 里实际用到；`needs_review_default: true` | ☑ |
| — | GORM `Where` / pgx | | | `Where` 是 GORM 的常规查询方法，直接上确定性规则误报太多，本来计划走 §3 候选组（§3.5），但那项没做——GORM Where 目前两条轨都没盖到，欠着 | |
| — | pgx | | | pgx 的 `Query`/`QueryRow` 已经被 1.3.15 和 `go-db-query` 盖住了 | |

**Java：**

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.3.21 | `java-conn-preparestatement` | `prepareStatement` | `".+"` | `conn.prepareStatement(sql)` | ☑ |
| 1.3.22 | `java-jdbctemplate-update` | `update` | `".+"` | 对齐已有的 `java-jdbctemplate-query`（query@.+）；`needs_review_default: true`（update 词泛） | ☑ |
| 1.3.23 | `java-jdbctemplate-queryforobject` | `queryForObject` | `".+"` | | ☑ |
| 1.3.24 | `java-jdbctemplate-queryforlist` | `queryForList` | `".+"` | | ☑ |
| 1.3.25 | `java-jdbctemplate-batchupdate` | `batchUpdate` | `".+"` | | ☑ |
| — | MyBatis `@Select("${...}")` | | | 注解里的是字符串不是函数调用，YAML 规则管不到，交给 LLM 轨或以后做注解 detector（review §6 也是这个意思） | |
| — | jOOQ `DSL.sql`/`DSL.field` | | | 用得少，先不加，真实项目里见到了再补 | |

### 1.4 Open redirect 收尾

| # | rule_id | callee | receiver_pattern | 备注 | 状态 |
|---|---|---|---|---|---|
| 1.4.1 | `php-redirect` | `redirect` | `"^(response\|res)$"` | 候选表里已有 redirect 组兜底，这条是把 Laravel `response()->redirect()` 的常见写法提到确定性规则 | ☑ |

`window.location = x` 和设置 `Location:` 响应头都不是函数调用，是赋值/设 header，归 §4.1。

---

## §2 `source_rules.yml` 新增

source 规则和 sink 规则完全不是一套模型：sink 按 callee/receiver 匹配，source 是**文本正则**，靠 `pattern` 里第一个捕获组抓出参数名。deepsec 依据：`matchers/source-*.ts` 系列。已有的没重复加：Express `req.*`、Koa `ctx.request.*`、Django/Flask 的索引写法（`request.args['q']`）、Gin `c.*` 索引写法、Spring `@RequestParam/@PathVariable/@RequestBody`、PHP superglobal 一部分。

### 2.1 JS/TS（Hono / Fastify / Next）

| # | rule_id | pattern | source_type | 状态 |
|---|---|---|---|---|
| 2.1.1 | `ts-hono-query` | `c\.req\.query\(['"](\w+)['"]\)` | query | ☑ |
| 2.1.2 | `ts-hono-param` | `c\.req\.param\(['"](\w+)['"]\)` | path | ☑ |
| 2.1.3 | `ts-hono-json` | `c\.req\.(json\|text)\(\)` | body | ☑ |
| 2.1.4 | `ts-hono-formdata` | `c\.req\.(formData)\(\)` | form | ☑ |
| 2.1.5 | `ts-fastify-query` | `request\.query\.([A-Za-z_]\w*)` | query | ☑ |
| 2.1.6 | `ts-fastify-params` | `request\.params\.([A-Za-z_]\w*)` | path | ☑ |
| 2.1.7 | `ts-fastify-body` | `request\.body\.([A-Za-z_]\w*)` | body | ☑ |
| 2.1.8 | `ts-fastify-headers` | `request\.headers\.([A-Za-z_]\w*)` | header | ☑ |
| 2.1.9 | `ts-next-searchparams` | `nextUrl\.searchParams\.get\(['"](\w+)['"]\)` | query | ☑ |
| 2.1.10 | `ts-next-json` | `\brequest\.(json)\(\)` | body | ☑ |
| 2.1.11 | `ts-next-formdata` | `\brequest\.(formData)\(\)` | form | ☑ |
| 2.1.12 | `ts-fetch-headers-get` | `request\.headers\.get\(['"]([\w-]+)['"]\)` | header | ☑ |

几个实现时定下的口径：

- `ts-fastify-*` 和 Express 的写法长得一样，只有变量名不同（`request.*` vs `req.*`），所以正则单独成条、不合并——合并了会把不是请求对象的变量也吞进来。
- 捕获组里抓的是方法名（`json`/`formData`）的那几条：这类调用没有字段名可抓，就拿方法名当占位参数名用。
- header 名带 `-` 的（比如 `.get('X-Token')`），捕获组必须写 `[\w-]+`，写成 `\w+` 只能抓到一个 `X`。
- NestJS 的 `@Body()` / `@Param("id")` 装饰器这批没加：`@Body()` 不带参数时没有字段名可抓，先看看真实项目里占比再说。

### 2.2 Python（Flask 的 .get() 写法）

| # | rule_id | pattern | source_type | 状态 |
|---|---|---|---|---|
| 2.2.1 | `py-flask-args-get` | `request\.args\.get\(['"](\w+)['"]\)` | query | ☑ |
| 2.2.2 | `py-flask-form-get` | `request\.form\.get\(['"](\w+)['"]\)` | form | ☑ |
| 2.2.3 | `py-flask-json-get` | `request\.json\.get\(['"](\w+)['"]\)` | body | ☑ |
| 2.2.4 | `py-flask-get-json` | `request\.(get_json)\(\)` | body | ☑ |
| 2.2.5 | `py-flask-headers-get` | `request\.headers\.get\(['"]([\w-]+)['"]\)` | header | ☑ |
| 2.2.6 | `py-flask-cookies-get` | `request\.cookies\.get\(['"](\w+)['"]\)` | cookie | ☑ |
| 2.2.7 | `py-flask-files-get` | `request\.files\.get\(['"](\w+)['"]\)` | file | ☑ |

注意 2.2.4：`get_json()` 没有字段名，捕获组抓方法名占位。写正则时有个坑——没有捕获组的话 `m.group(1)` 会直接 IndexError，所以哪怕没字段名也必须留一个捕获组。

### 2.3 Go

| # | rule_id | pattern | source_type | 状态 |
|---|---|---|---|---|
| 2.3.1 | `go-gin-getheader` | `c\.GetHeader\(['"]([\w-]+)['"]\)` | header | ☑ |
| 2.3.2 | `go-gin-shouldbindjson` | `c\.ShouldBindJSON\(&(\w+)\)` | body | ☑ |
| 2.3.3 | `go-net-formvalue` | `r\.FormValue\(['"](\w+)['"]\)` | form | ☑ |
| 2.3.4 | `go-net-postformvalue` | `r\.PostFormValue\(['"](\w+)['"]\)` | form | ☑ |
| 2.3.5 | `go-net-header-get` | `r\.Header\.Get\(['"]([\w-]+)['"]\)` | header | ☑ |
| 2.3.6 | `go-net-cookie` | `r\.Cookie\(['"](\w+)['"]\)` | cookie | ☑ |
| — | Echo/Fiber/Chi/Gorilla 的访问器 | 没加，遇到真实项目再照同样写法补（`ctx.Query("q")` / `c.Query("q")` / `c.URL.Query().Get(...)`） | | |

Go 这批假定变量名是 `c`/`r`（Gin 和 net/http 的惯例）。有误报风险——`c` 也可能是别的变量——如果真机扫出来误吞，备选方案是不限变量名、只按方法名收窄（比如 `\.GetHeader\(...\)`）。

### 2.4 Java

| # | rule_id | pattern | source_type | 状态 |
|---|---|---|---|---|
| 2.4.1 | `j-spring-requestheader` | `@RequestHeader\(['"]?([\w-]+)['"]?\)?` | header | ☑ |
| 2.4.2 | `j-spring-cookievalue` | `@CookieValue\(['"]?([\w-]+)['"]?\)?` | cookie | ☑ |
| 2.4.3 | `j-spring-requestpart` | `@RequestPart\(['"]?(\w+)['"]?\)?` | body | ☑ |
| 2.4.4 | `j-jaxrs-queryparam` | `@QueryParam\(['"](\w+)['"]\)` | query | ☑ |
| 2.4.5 | `j-jaxrs-pathparam` | `@PathParam\(['"](\w+)['"]\)` | path | ☑ |
| 2.4.6 | `j-jaxrs-headerparam` | `@HeaderParam\(['"]([\w-]+)['"]\)` | header | ☑ |
| 2.4.7 | `j-jaxrs-formparam` | `@FormParam\(['"](\w+)['"]\)` | form | ☑ |
| 2.4.8 | `j-httpservlet-getparameter` | `\.getParameter\(['"](\w+)['"]\)` | query | ☑ |
| 2.4.9 | `j-httpservlet-getheader` | `\.getHeader\(['"]([\w-]+)['"]\)` | header | ☑ |
| 2.4.10 | `j-httpservlet-getcookies` | `\.(getCookies)\(\)` | cookie | ☑ |

### 2.5 PHP

| # | rule_id | pattern | source_type | 状态 |
|---|---|---|---|---|
| 2.5.1 | `php-superglobal-cookie` | `\$_COOKIE\[['"](\w+)['"]\]` | cookie | ☑ |
| 2.5.2 | `php-superglobal-files` | `\$_FILES\[['"](\w+)['"]\]` | file | ☑ |
| 2.5.3 | `php-superglobal-server` | `\$_SERVER\[['"](\w+)['"]\]` | header | ☑ |
| 2.5.4 | `php-laravel-input` | `\$request->input\(['"](\w+)['"]\)` | body | ☑ |
| 2.5.5 | `php-laravel-query` | `\$request->query\(['"](\w+)['"]\)` | query | ☑ |
| 2.5.6 | `php-laravel-route` | `\$request->route\(['"](\w+)['"]\)` | path | ☑ |
| 2.5.7 | `php-laravel-file` | `\$request->file\(['"](\w+)['"]\)` | file | ☑ |
| — | `$request->all()` | 没加：没有字段名可抓，而且正常代码里用得极多，误报把握不住，先观察 | | |

---

## §3 `sink_candidates.yml` 扩展

候选表的作用：规则库没命中、但长得可疑的调用，送轻量 LLM 判断是不是真 sink。所以这张表调的是「哪些调用值得送 LLM」——送多了浪费 LLM 调用，送少了丢召回。

这批分两步做：先给候选表加了三个可选字段（改 loader 和 `_matches_candidate`），再用新字段加模式组。

| # | 项 | 内容 | 状态 |
|---|---|---|---|
| 3.1 | **加字段** | `context_patterns`（调用周边的代码文本须包含其一）、`arg_patterns`（某个参数须命中其一）、`exclude_patterns`（命中即排除）。字段写错时 loader 直接报错，和现有字段一个待遇 | ☑ |
| 3.2 | NoSQL 组收窄 | 给现有的 find/findOne/update 组加 `context_patterns`（`$where`、`$regex`、`JSON.parse`），少送点 LLM 调用 | ☐ 没做 |
| 3.3 | 文件路径过滤 | 原计划新建一个 path.join 组，实际没这么干——直接给已有的 TS fs 组和 Go os 组加了 `arg_patterns`：参数里有模板串、拼接或 req 引用才送 LLM，纯字面量路径（`fs.readFileSync('a.txt')`）没有穿越风险，直接跳过 | ☑ |
| 3.4 | 原型污染 | 新组：`Object.assign`、`_.merge`、`mergeWith`、`defaultsDeep`（receiver 限 Object/_/lodash），且调用附近必须出现 req/body 引用才算候选——这几个方法本身合法，危险在把用户输入合进对象。`obj[k]=v` 是赋值不是调用，归 §4.1。分类不硬套现有 SinkCategory，由 LLM 判断时自己归 | ☑ |
| 3.5 | go 组补召回 | go 组的 callees 补 `Where` / `First` / `NamedQuery`，给 1.3.19/1.3.20 之外的 GORM 常规查询留候选兜底 | ☐ 没做 |
| 3.6 | unsafe YAML | JS 的 `yaml.load` 进候选（Python 那边已有 `py-yaml-load` 规则，JS 交给 LLM 判） | ☑ |
| 3.7 | `JSON.parse(用户输入)` | 没过 schema 校验就解析对象输入的候选。不自动判漏洞，给 LLM 提供判断二次注入的线索 | ☑ |

实现时踩过一个坑，值得记下来：候选组之间是「或」的关系，只要有一个组命中就送 LLM。所以想收窄某个既有组时必须直接改那个组——另建一个更严的新组没用，旧组照样先命中。§3.3 就是因此改成直接给 fs 组加 `arg_patterns` 的。

---

## §4 detector 基础能力（要写代码，不是改 YAML）

| # | 项 | 内容 | 做完能解锁什么 | 状态 |
|---|---|---|---|---|
| 4.1 | **赋值型 sink 识别** | 新增 `iter_assignments()` 或独立的 `assignment_sink_detector.py`，识别 `innerHTML`/`outerHTML`/`window.location`/`v-html`/`[innerHTML]`/`dangerouslySetInnerHTML` 这些赋值写法 | `ts-innerhtml` 规则现在躺着没用（真实代码写 `el.innerHTML = x`，是赋值不是调用，检测不到）；review §3 表里另有 4 个未命中项也指它 | ☐ |
| 4.2 | new_expression 支持 | `iter_calls()` 目前抽不出 `new Function(...)`（已用探针验证，`test_new_function_not_extracted` 固定现状），要支持得改 parser | 1.1.6 | ☐ |
| 4.3 | tagged template 支持 | 支持 `` sql`...` `` 模板标签写法（drizzle/prisma） | 1.3.9 的模板形态 | ☐ |
| 4.4 | `examples` 自动验证 | YAML 规则加 `examples.positive/negative`，测试自动跑一遍：每条正例必须命中、负例必须不命中（deepsec 的 `matcher-examples.test.ts` 就是这么做的） | 从机制上防「规则写了却永远匹配不到」——`ts-res-redirect` 就是现成的翻车案例 | ☑ |
| 4.5 | `noise_tier` / tech gate | 加 `noise_tier: precise\|normal\|noisy` 和 `requires.tech/sentinel_files` 字段并接上过滤逻辑，然后规则逐条标注 | 给 `get`/`query`/`update` 这类泛词规则降误报 | ☐ |
| 4.6 | 全局排除测试/构建目录 | 新建共享排除表 `code_index/path_exclusions.py`，文件发现的四个入口（`parser.discover_source_files` 源码清单 / `entry_points` Express 路由扫描 / `schema_entry_parser` OpenAPI 扫描 / `file_discovery` 安全文件清单）统一改用，不再各维护一份名单。排除两层：目录名（`tests`/`__tests__`/`fixtures`/`spec`/`e2e` 等测试目录 + `dist`/`build`/`.next`/`target`/`coverage` 构建产物）和文件名（`*.test.*`/`*.spec.*`/`test_*.py`/`*_test.py`/`*_test.go`，盖住长在测试目录外面的测试文件）。Java 不做文件名匹配——`*Test.java` 有误伤业务类的风险，且主流形态 `src/test/java` 目录级已经盖住 | 测试代码天生就在调危险 API，不排除的话每个测试用例都是一条假 sink 链。只收窄确定性层的文件集合，LLM 轨 agent 自己 grep 全仓，不受影响 | ☑ |

---

## 实施顺序

1. **M1（止血）**：§0 两条死规则修复 + 正反例测试。半天量级。
2. **M2（大头）**：§1 全部 + §2 全部，每条带测试。只改 YAML 和测试，不碰代码。
3. **M3（候选增强）**：§3 的字段扩展和模式组，带少量 loader 代码改动。
4. **M4（detector 能力）**：4.4 先做——examples 的自动验证就位后，M2 新加的每条规则都能照着补正反例，「规则写了却永远匹配不到」这类问题从机制上堵死；其余各项按编号顺序做。

每完成一个里程碑：勾掉对应条目，跑一遍 code_index 回归确认没改坏别的东西。
