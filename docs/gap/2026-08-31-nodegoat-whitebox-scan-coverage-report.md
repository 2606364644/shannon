# NodeGoat 白盒扫描覆盖报告（2026-08-31）

## 1. NodeGoat 是什么

NodeGoat 是 OWASP 维护的一个故意不安全的 Node.js 应用。它模拟一个股票/投资类系统，用了 Express、MongoDB 和 Swig 模板，目的是让安全和开发人员在一个真实结构的项目里练习找漏洞、理解漏洞和修复漏洞。

这次用的对象是：

| 项目 | 内容 |
|---|---|
| 靶场 | OWASP NodeGoat |
| 本地路径 | `workspaces/__legacy__/repos/NodeGoat` |
| 仓库 HEAD | `c5cb68a` |
| 扫描报告 | `workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/` |
| 技术栈 | Node.js、Express、MongoDB、Swig |
| 默认端口 | 4000 |
| 默认数据库 | `mongodb://localhost:27017/nodegoat` |
| 默认账号 | `admin/Admin_123`、`user1/User1_123`、`user2/User2_123` |

NodeGoat 没有单独的 `VULNERABILITIES.md`，官方漏洞说明主要在 `/tutorial` 页面和对应 HTML 里。来源是：

- `app/routes/tutorial.js` 注册 Tutorial 路由。
- `app/views/tutorial/` 放每一类漏洞的说明。
- 源码里有 `Fix for A1`、`Fix for A2` 这类注释。
- `package.json` 里有 A9 的依赖线索。

## 2. NodeGoat 官方有哪些漏洞

把 Tutorial 里的内容按“漏洞条目”拆开，一共是 **15 个**。A1 和 A2 里面还包含多个不同问题，所以拆开统计：

| # | 官方条目 | 通俗说明 | 主要代码 |
|---:|---|---|---|
| 1 | A1-1 服务器端 JS 注入 | 用户输入被拼进 `eval()`，服务器会执行攻击者的 JS | `app/routes/contributions.js` |
| 2 | A1-2 SQL / NoSQL 注入 | 用户输入影响数据库查询。这里实际是 MongoDB `$where` 注入 | `app/data/allocations-dao.js` |
| 3 | A1-3 日志注入 / CRLF | 用户名里带换行时，可以伪造日志记录 | `app/routes/session.js` |
| 4 | A2-1 会话管理缺陷 | session 生命周期、cookie 配置、MemoryStore 等有问题 | `server.js`、`app/routes/session.js` |
| 5 | A2-2 密码猜测 | 密码弱、明文存储、可枚举用户、登录不限次数 | `app/routes/session.js`、`app/data/user-dao.js` |
| 6 | A3 XSS | 用户输入被页面执行，可偷 cookie 或操作页面 | `server.js`、多个模板 |
| 7 | A4 IDOR | 直接改 ID 就能看或改别人的数据 | `app/routes/allocations.js` |
| 8 | A5 安全配置错误 | 安全响应头、请求体大小、框架暴露信息等配置不完整 | `server.js` |
| 9 | A6 敏感数据暴露 | 密码和 SSN、生日、地址、银行信息等敏感数据保护不足 | `server.js`、`app/data/profile-dao.js` |
| 10 | A7 功能级访问控制缺失 | 普通用户也能访问管理员功能 | `app/routes/index.js`、`app/routes/benefits.js` |
| 11 | A8 CSRF | 第三方网站可以借用户已登录的 cookie 发起请求 | `server.js` |
| 12 | A9 已知漏洞组件 | 依赖里有已知漏洞，例如 `marked@0.3.5` | `package.json`、`package-lock.json` |
| 13 | A10 未校验跳转 | `url` 参数没检查，可跳到外部恶意网站 | `app/routes/index.js` |
| 14 | ReDoS | 恶意输入触发复杂正则回溯，卡住 Node.js 主线程 | `app/routes/profile.js` |
| 15 | SSRF | 用户控制的 URL 被服务器请求，可访问内网 | `app/routes/research.js` |

## 3. 本次工具检出了多少

扫描报告里有 **30 条 raw findings**：

| 严重度 | 数量 |
|---|---:|
| critical | 2 |
| high | 23 |
| medium | 4 |
| low | 1 |
| **合计** | **30** |

这 30 条不能直接当成 30 个独立漏洞，因为有些是同一问题报了多次。按“接口 + 字段 + sink”粗略合并后，大约是 **23 个独立技术点**。

按官方 15 个条目看：

| 结果 | 数量 | 条目 |
|---|---:|---|
| 检出 | 7 | A1-1、A1-2、A2-2、A4、A7、A10、SSRF |
| 部分检出 | 5 | A2-1、A3、A5、A6、A9 |
| 未检出 | 3 | A1-3、A8、ReDoS |

简单说：工具找到了几类典型注入、XSS、越权和 SSRF；但日志注入、CSRF、ReDoS 完全没报，会话、XSS、配置、敏感数据、依赖审计只是报了一部分。

## 4. 哪些检出、哪些没检出

### 4.1 完全检出的 7 个

| 官方条目 | 工具 finding | 报告检出的内容 |
|---|---|---|
| A1-1 服务器端 JS 注入 | `INJ-VULN-02` | `preTax`、`afterTax`、`roth` 进入 `eval()` |
| A1-2 SQL / NoSQL 注入 | `INJ-VULN-01` | `threshold` 拼进 MongoDB `$where` |
| A2-2 密码猜测 | `AUTH-VULN-02`、`AUTH-VULN-03`、`AUTH-VULN-06`、`AUTH-VULN-07` | 报了弱密码、明文密码、用户枚举、无限流、默认账号 |
| A4 IDOR | `AUTHZ-VULN-03`、`AUTHZ-GN-1` | 同一个 Allocations IDOR 报了两次 |
| A7 功能级访问控制缺失 | `AUTHZ-VULN-01`、`AUTHZ-VULN-02` | `/benefits` 只判断登录，没判断管理员 |
| A10 未校验跳转 | `AUTHZ-VULN-05`、`OPENREDIR-VULN-01`、`SSRF-GN-03` | `/learn?url=` 同一问题报了三次 |
| SSRF | `SSRF-VULN-01` | `/research` 的 `url + symbol` 进入 `needle.get()` |

### 4.2 部分检出的 5 个

| 官方条目 | 已检出 | 没检到或没检全 | 原因 |
|---|---|---|---|
| A2-1 会话管理缺陷 | session fixation、硬编码 `cookieSecret`、HTTP / `secure` cookie | session 超时、rolling / `maxAge`、`saveUninitialized`、`resave`、MemoryStore 风险 | 认证分析偏向登录和密码，没有完整检查 session 生命周期 |
| A3 XSS | Memos、Benefits、Login、Signup、Allocations 等多条 XSS | 官方 profile 示例、URL 上下文、属性上下文没单独覆盖 | 找到了模板输出问题，但没有按 Tutorial 页面对照，也没有完整区分输出上下文 |
| A5 安全配置错误 | HTTP、HSTS、CSP 有提及 | 请求体大小、`x-powered-by`、Helmet、cookie 生命周期等没系统覆盖 | 没有独立的安全配置检查规则 |
| A6 敏感数据暴露 | 密码明文、HTTP 传输 | SSN、DOB、地址、银行账号、routing number 明文存储 | 敏感字段规则主要识别密码，没覆盖其他个人和银行字段 |
| A9 已知漏洞组件 | `XSS-VULN-01` 提到 `marked@0.3.5` | 没有完整依赖审计，也没区分 runtime / dev dependency | `marked` 被当作 XSS 链的一部分，依赖风险没有作为独立检查维度 |

### 4.3 未检出的 3 个

| 官方条目 | 漏掉的问题 | 原因 |
|---|---|---|
| A1-3 日志注入 / CRLF | 登录失败的 `userName` 没清理就写入 `console.log`，换行可伪造日志 | 注入分析只看 `eval()` 和数据库查询，没把 logger / `console.log` 当 sink |
| A8 CSRF | 状态修改接口没有 CSRF token 校验 | 没有“POST + cookie 会话 + token 缺失”的专项判断 |
| ReDoS | `bankRouting` 进入嵌套量词正则，长输入可能阻塞服务 | 没有 regex 复杂度分析，也没把用户输入进入 `RegExp.test()` 当 DoS sink |

## 5. 30 条工具结果

| # | ID | 严重度 | 内容 |
|---:|---|---|---|
| 1 | `INJ-VULN-01` | high | `threshold` 拼进 MongoDB `$where` |
| 2 | `INJ-VULN-02` | critical | 缴费字段直接进入 `eval()` |
| 3 | `XSS-VULN-01` | high | Memos 存储型 XSS |
| 4 | `XSS-GN-01` | high | Allocations `userId` 反射到模板 |
| 5 | `XSS-GN-04` | high | Benefits 输出未转义 `firstName` |
| 6 | `XSS-GN-05` | high | Profile 写入、Benefits 触发 `firstName` XSS |
| 7 | `XSS-GN-07` | high | Benefits 输出未转义 `lastName` |
| 8 | `XSS-GN-08` | high | `benefitStartDate` XSS |
| 9 | `XSS-GN-10` | high | 同一 `benefitStartDate` XSS 的另一条链 |
| 10 | `XSS-GN-28` | high | `roth` 渲染链；未判定，需要复核 |
| 11 | `XSS-GN-33` | high | Login 回显 `userName` |
| 12 | `XSS-GN-35` | high | Signup 回显 `userName` / `email` |
| 13 | `XSS-GN-36` | high | 同一 Signup XSS 的另一条链 |
| 14 | `AUTH-VULN-01` | high | 登录后未重新生成 session |
| 15 | `AUTH-VULN-02` | high | 弱密码且明文存储 |
| 16 | `AUTH-VULN-03` | medium | 用户枚举 |
| 17 | `AUTH-VULN-04` | critical | `cookieSecret` 硬编码 |
| 18 | `AUTH-VULN-05` | high | 仅 HTTP，cookie 无 `secure` |
| 19 | `AUTH-VULN-06` | high | 登录无限流和账户锁定 |
| 20 | `AUTH-VULN-07` | high | 默认管理员密码 |
| 21 | `AUTHZ-VULN-01` | high | `POST /benefits` 可更新任意用户 |
| 22 | `AUTHZ-VULN-02` | high | `GET /benefits` 无管理员校验 |
| 23 | `AUTHZ-VULN-03` | high | Allocations IDOR |
| 24 | `AUTHZ-VULN-04` | medium | Memos 全量返回 |
| 25 | `AUTHZ-VULN-05` | low | `/learn` 开放重定向 |
| 26 | `AUTHZ-VULN-06` | medium | session fixation，和 `AUTH-VULN-01` 重复 |
| 27 | `AUTHZ-GN-1` | high | Allocations IDOR，和 `AUTHZ-VULN-03` 重复 |
| 28 | `SSRF-VULN-01` | high | `/research` SSRF |
| 29 | `OPENREDIR-VULN-01` | medium | `/learn` 开放重定向，重复 |
| 30 | `SSRF-GN-03` | high | `/learn` 开放重定向，重复 |

其中默认账号、登录无限流、Memos 全量读、硬编码 `cookieSecret` 是官方 15 条之外扩展出来的真实代码问题。是否单独计数，要看评估口径。

## 6. 扫描 finding 与靶场漏洞对象的对应关系

这一点需要把两种“对象”分开，否则容易把扫描器生成的 finding 误当成靶场原本就存在的漏洞对象：

- **扫描 finding**：`report_data.json` 的 `vulnerabilities[]` 中的记录，例如 `INJ-VULN-02`、`AUTHZ-VULN-01`。这些 ID 是本次扫描生成的结果 ID。
- **靶场漏洞对象**：NodeGoat Tutorial 和源码中实际植入、可以独立验证的漏洞点，例如“`preTax` 进入 `eval()`”“`threshold` 进入 MongoDB `$where`”。它们不是扫描报告自动提供的对象。

当前 `report_data.json` **没有** `target_object_id`、`baseline_ref` 之类的靶场对象引用字段。`affected_entries` 即使存在，记录的也是内部 `chain_id`、参数和 sink 关系，不是 NodeGoat 官方漏洞对象；而且 30 条 finding 中有 16 条的 `affected_entries` 为空。因此，报告里的检出证据不能直接证明“对应了靶场上的哪个漏洞对象”，下面的对应关系是基于靶场源码和 Tutorial 做的事后对账，不是报告原生输出。

为了让覆盖率可审计，本节给靶场对象补充规范化 ID。`NG-*` 是本文新增的 canonical ID，不代表扫描报告已有这些字段。一个合格的靶场对象至少应包含：对象 ID、漏洞名称、入口/输入、危险 sink、源码定位、预期影响，以及对应的 finding ID。

### 6.1 靶场漏洞对象与 finding 的映射

状态含义：

- **直接检出**：finding 的入口、根因或 sink 与靶场对象一致。
- **部分检出**：只覆盖了对象的一部分配置、入口或影响面。
- **间接检出**：命中了相关结果或依赖链，但没有把靶场对象作为同类漏洞单独建模。
- **未检出**：报告中没有可以落到该靶场对象的 finding。

| 靶场对象 ID | 靶场漏洞对象（可验证目标） | 主要靶场定位 | 对应扫描 finding | 映射状态 |
|---|---|---|---|---|
| `NG-A1-1` | `preTax`、`afterTax`、`roth` 进入 `eval()`，形成服务器端 JS 注入 | `app/routes/contributions.js` | `INJ-VULN-02`；`XSS-GN-28` 是关联的渲染链，不另计为 A1-1 | 直接检出 |
| `NG-A1-2` | `threshold` 拼入 MongoDB `$where` JavaScript 查询 | `app/data/allocations-dao.js` | `INJ-VULN-01` | 直接检出 |
| `NG-A1-3` | 登录失败时未清理的 `userName` 写入日志，换行可伪造日志 | `app/routes/session.js` | 无 | 未检出 |
| `NG-A2-1` | session fixation、硬编码 `cookieSecret`、HTTP/`secure` 以及 session 生命周期配置问题 | `server.js`、`app/routes/session.js` | `AUTH-VULN-01`、`AUTH-VULN-04`、`AUTH-VULN-05`、`AUTHZ-VULN-06` | 部分检出；超时、`maxAge`、`rolling`、`MemoryStore` 等仍未形成对象 |
| `NG-A2-2` | 弱密码、明文密码、用户枚举、登录无限流和默认账号 | `app/routes/session.js`、`app/data/user-dao.js`、种子数据 | `AUTH-VULN-02`、`AUTH-VULN-03`、`AUTH-VULN-06`、`AUTH-VULN-07` | 直接检出 |
| `NG-A3` | 用户输入在多个模板上下文中未转义并执行 XSS | `server.js`、`app/views/` 多个模板 | `XSS-VULN-01`、`XSS-GN-01`、`XSS-GN-04`、`XSS-GN-05`、`XSS-GN-07`、`XSS-GN-08`、`XSS-GN-10`、`XSS-GN-28`、`XSS-GN-33`、`XSS-GN-35`、`XSS-GN-36` | 部分检出；没有按 Tutorial 的页面和输出上下文完整建模 |
| `NG-A4` | `userId` 无归属校验，可读取或修改其他用户对象 | `app/routes/allocations.js` | `AUTHZ-VULN-03`、`AUTHZ-GN-1` | 直接检出，但重复 |
| `NG-A5` | 安全响应头、请求体限制、框架暴露信息和 cookie 等安全配置不完整 | `server.js` | `AUTH-VULN-05` 及少量描述 | 部分检出 |
| `NG-A6` | SSN、DOB、地址、银行信息等敏感字段保护不足，以及 HTTP 明文传输 | `server.js`、`app/data/profile-dao.js` | `AUTH-VULN-02`、`AUTH-VULN-05` | 部分检出；主要命中密码和传输，未形成敏感字段对象 |
| `NG-A7` | 普通用户可访问或更新管理员/其他用户的 Benefits 数据 | `app/routes/index.js`、`app/routes/benefits.js` | `AUTHZ-VULN-01`、`AUTHZ-VULN-02` | 直接检出 |
| `NG-A8` | 状态修改接口缺少 CSRF token 校验 | `server.js` 及多个 POST 路由 | 无 | 未检出 |
| `NG-A9` | `marked@0.3.5` 等依赖存在已知漏洞 | `package.json`、`package-lock.json` | `XSS-VULN-01` 间接提到 `marked@0.3.5` | 间接检出；没有独立 SCA/依赖漏洞对象 |
| `NG-A10` | `/learn?url=` 未校验即进入 `res.redirect`，可开放重定向 | `app/routes/index.js` | `AUTHZ-VULN-05`、`OPENREDIR-VULN-01`、`SSRF-GN-03` | 直接检出，但同一对象重复且曾被不同轨道命名 |
| `NG-REDOS` | `bankRouting` 进入带嵌套量词的正则，长输入可能阻塞 Node.js | `app/routes/profile.js` | 无 | 未检出 |
| `NG-SSRF` | `/research` 的 `url`、`symbol` 进入服务端 HTTP 请求并回显响应 | `app/routes/research.js` | `SSRF-VULN-01` | 直接检出 |

这张表才是“扫描结果是否落到了靶场漏洞对象”的覆盖矩阵。原来的 30 条 finding 清单只能回答“扫描器报了什么”，不能单独回答“靶场的哪个漏洞对象被报到了”。

### 6.2 检出证据的实际位置

对已经映射到靶场对象的 finding，报告证据仍以以下三个文件为准：

- `report_data.json` 的 `vulnerabilities` 数组，按 finding `id` 查找 `title`、`problem_points`、`endpoints`、`dataflow_steps`、`evidence` 和 `poc`。
- `comprehensive_security_assessment_report.md` 里的同名 `### ID` 章节。
- `exploitable_poc_collection.md` 里的同名 PoC 章节。

报告标记的验证方式都是 **静态分析**。置信度分布是：`INJ-VULN-02`、`SSRF-VULN-01` 为 high，`XSS-GN-28` 为未判定，其余 27 条为 needs_review。所以除前两条相对可信外，大部分结果仍要看报告里的 PoC 和说明，不能直接当成已确认漏洞。更重要的是：这些文件只能提供 finding 侧证据；靶场对象侧还必须回到 Tutorial、源码入口和 sink 位置核对。

### 6.3 Finding 级证据清单

| ID | 报告检出的内容 | 报告记录的接口 / 输入 | 报告置信度 |
|---|---|---|---|
| `INJ-VULN-01` | `GET /allocations/:userId` 的 `threshold` 拼入 MongoDB `$where` | `GET /allocations/:userId`；`threshold (query)` | 待复核 |
| `INJ-VULN-02` | `POST /contributions` 的 `preTax`、`afterTax`、`roth` 传入 `eval()` | `POST /contributions`；三个 body 字段 | 高 |
| `XSS-VULN-01` | `/memos` 的 `memo` 存储，经 `marked@0.3.5` 渲染成存储型 XSS | `POST /memos`、`GET /memos`；`memo (body)` | 待复核 |
| `XSS-GN-01` | `/allocations/:userId` 的 `userId` 反射到模板 action 属性 | `GET /allocations/:userId`；`userId` | 待复核 |
| `XSS-GN-04` | `/signup` 写入 `firstName`，后在 `/benefits` 未转义输出 | `POST /signup`、`GET /benefits`；`firstName (body)` | 待复核 |
| `XSS-GN-05` | `/profile` 更新 `firstName`，后在 `/benefits` 未转义输出 | `POST /profile`、`GET /benefits`；`firstName (body)` | 待复核 |
| `XSS-GN-07` | `/profile` 更新 `lastName`，后在 `/benefits` 未转义输出 | `POST /profile`、`GET /benefits`；`lastName (body)` | 待复核 |
| `XSS-GN-08` | `/benefits` 的 `benefitStartDate` 写入后未转义输出 | `POST /benefits`、`GET /benefits`；`userId (body)`、`benefitStartDate (body)` | 待复核 |
| `XSS-GN-10` | `/benefits` 的 `benefitStartDate` 经另一条链路未转义输出 | `POST /benefits`、`GET /benefits`；`userId (body)`、`benefitStartDate (body)` | 待复核 |
| `XSS-GN-28` | `/contributions` 的 `roth` 渲染链 | `POST /contributions`、`GET /contributions`；`roth (body)` | 未判定 |
| `XSS-GN-33` | 登录失败时 `userName` 回显到 input value | `POST /login`；`userName (body)`、`password (body)` | 待复核 |
| `XSS-GN-35` | `/signup` 错误分支回显 `userName` / `email` | `POST /signup`；`userName (body)`、`email (body)` 等 | 待复核 |
| `XSS-GN-36` | `/signup` 另一错误分支回显 `email` | `POST /signup`；`userName (body)`、`email (body)` 等 | 待复核 |
| `AUTH-VULN-01` | 登录成功后未轮换 session | `POST /login`；`userName`、`password` | 待复核 |
| `AUTH-VULN-02` | 密码强度不足且明文存储 | `POST /signup`；`password` 等注册字段 | 待复核 |
| `AUTH-VULN-03` | 登录错误文案不同，可枚举用户 | `POST /login`；`userName`、`password` | 待复核 |
| `AUTH-VULN-04` | `cookieSecret` 硬编码 | `POST /login` 的会话场景 | 待复核 |
| `AUTH-VULN-05` | 纯 HTTP，会话 cookie 未设 `secure` | `POST /login` 的传输和 cookie 场景 | 待复核 |
| `AUTH-VULN-06` | 登录没有限流和锁定 | `POST /login`；`userName`、`password` | 待复核 |
| `AUTH-VULN-07` | 种子数据里有默认管理员口令 | 默认账号登录场景 | 待复核 |
| `AUTHZ-VULN-01` | `POST /benefits` 可用请求体里的 `userId` 更新他人数据 | `POST /benefits`；`userId (body)` | 待复核 |
| `AUTHZ-VULN-02` | `GET /benefits` 只校验登录，不校验管理员 | `GET /benefits` | 待复核 |
| `AUTHZ-VULN-03` | `/allocations/:userId` 的 `userId` 没有归属校验 | `GET /allocations/:userId`；`userId (query)` | 待复核 |
| `AUTHZ-VULN-04` | `GET /memos` 返回全部用户 memo | `GET /memos` | 待复核 |
| `AUTHZ-VULN-05` | `/learn` 的 `url` 可跳到任意地址 | `GET /learn`；`url (query)` | 待复核 |
| `AUTHZ-VULN-06` | session 固定，和硬编码密钥问题放在一起描述 | `POST /login`；`userName`、`password` | 待复核 |
| `AUTHZ-GN-1` | `/allocations/:userId` 可读任意用户 allocations，和 `AUTHZ-VULN-03` 重复 | `GET /allocations/:userId`；`userId (query)` | 待复核 |
| `SSRF-VULN-01` | `/research` 的 `url`、`symbol` 进入服务端请求并回显响应 | `GET /research`；`url (query)`、`symbol (query)` | 高 |
| `OPENREDIR-VULN-01` | `/learn` 的 `url` 直接进入 `res.redirect` | `GET /learn`；`url (query)` | 待复核 |
| `SSRF-GN-03` | `/learn` 的 `url` 直接进入重定向，和前两条重复 | `GET /learn`；`url (query)` | 待复核 |

`exploitable_poc_collection.md` 里 30 个 ID 都有对应章节，内容包含 curl / raw HTTP、步骤、前提条件和期望结果。要看某条的检出证据，直接搜 `### ID` 即可。

### 6.4 未检出对象在报告里的证据

`report_data.json` 只有上面这 30 条 finding。按 ID、标题、类型和接口看，都没有可以落到以下靶场对象的记录。这里的“未检出”是“报告没有对应 finding”，不是“靶场没有这个漏洞”：

| 官方条目 | 报告里的证据情况 |
|---|---|
| A1-3 日志注入 / CRLF | 没有日志注入或 CRLF 类 finding；30 条里没有对应 `console.log`、logger、CRLF 的记录 |
| A8 CSRF | 没有 CSRF 类 finding；30 条里没有“缺 CSRF token”或“跨站请求伪造”的记录 |
| ReDoS | 没有 ReDoS / 正则复杂度类 finding；30 条里没有对应 `bankRouting` 正则拒绝服务的记录 |

## 7. 报告质量注意点

### 重复项

| 问题 | 重复 ID |
|---|---|
| Allocations IDOR | `AUTHZ-VULN-03`、`AUTHZ-GN-1` |
| `/learn` 开放重定向 | `AUTHZ-VULN-05`、`OPENREDIR-VULN-01`、`SSRF-GN-03` |
| session fixation | `AUTH-VULN-01`、`AUTHZ-VULN-06` |
| `benefitStartDate` XSS | `XSS-GN-08`、`XSS-GN-10` |
| Signup XSS | `XSS-GN-35`、`XSS-GN-36` |
| Benefits `firstName` XSS | `XSS-GN-04`、`XSS-GN-05` |

前五组基本应该合并。最后一组来源不同、sink 相同，是否合并取决于口径。

### 置信度

`report_data.json` 里：

- high：2 条，`INJ-VULN-02`、`SSRF-VULN-01`
- needs_review：27 条
- unadjudicated：1 条，`XSS-GN-28`

所以大部分结果是静态待复核，不能都当成已确认漏洞。

### cookie 描述冲突

有些 XSS 描述说 `connect.sid` 没有 `httpOnly`；`AUTH-VULN-05` 又说 `express-session` 默认开启 `httpOnly`。当前版本是 `express-session@1.15.6`，默认通常是开启的。要看实际 `Set-Cookie` 再修正影响描述。

### 硬编码密钥影响要复核

硬编码 `cookieSecret` 是真问题，但 `express-session` 的 cookie 主要携带签名后的 session id。知道 secret 通常能伪造签名；要冒用用户，还需要拿到或预测服务端存在的 session id。所以“可伪造 cookie”不一定等于“能登录任意账号”。

### QA 未通过

`report_data.json` 记录：

```json
"qa": {
  "passed": false
}
```

使用综合报告时，要区分已确认、待复核和未判定项。

## 8. 后续改进

1. 扫描报告为每条 finding 直接输出 `target_object_id` / `baseline_ref`，形成“靶场漏洞对象 → finding ID”的可追溯映射；缺哪条一眼能看到。
2. 补专项规则：CSRF、日志注入、ReDoS、安全配置、敏感字段。
3. 接入依赖审计，对 `package-lock.json` 跑或导入 npm audit。
4. 报告生成前去重，用“方法 + 路径 + sink 文件行号 + 字段”合并同类项。
5. 对高价值问题做动态验证，优先验证 `eval()`、`$where`、SSRF、CSRF、ReDoS。

## 附录：证据位置

### 扫描报告证据

| 内容 | 路径 |
|---|---|
| 结构化检出记录 | `workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/report_data.json` |
| 综合 Markdown 报告 | `workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/comprehensive_security_assessment_report.md` |
| PoC 集合 | `workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/exploitable_poc_collection.md` |

### 官方漏洞清单来源

| 内容 | 路径 |
|---|---|
| README | `workspaces/__legacy__/repos/NodeGoat/README.md` |
| Tutorial 路由 | `workspaces/__legacy__/repos/NodeGoat/app/routes/tutorial.js` |
| Tutorial 页面 | `workspaces/__legacy__/repos/NodeGoat/app/views/tutorial/` |
| 依赖声明 | `workspaces/__legacy__/repos/NodeGoat/package.json` |
