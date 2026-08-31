# NodeGoat 白盒扫描覆盖复盘（2026-08-31）

这次检查有两个对象：

- 靶场：`workspaces/__legacy__/repos/NodeGoat`
- 扫描报告：`NodeGoat-20260831-015748` 的 whitebox 综合报告

要回答的问题很直接：NodeGoat 官方标了多少漏洞，扫描检出多少，漏了什么。

## 1. 结论

NodeGoat Tutorial 按“漏洞条目”拆开是 **15 个**：A1 拆 3 项，A2 拆 2 项，A3-A10 各 1 项，再加 ReDoS 和 SSRF。

扫描报告记录了 **30 个 raw findings**：critical 2、high 23、medium 4、low 1。这个数没有去重，同一个问题可能报多次。按“同一接口、同一字段、同一 sink”粗略合并后，大约是 **23 个独立技术点**。

| 覆盖结果 | 数量 | 条目 |
|---|---:|---|
| 检出 | 7 | A1-1、A1-2、A2-2、A4、A7、A10、SSRF |
| 部分检出 | 5 | A2-1、A3、A5、A6、A9 |
| 未检出 | 3 | A1-3、A8、ReDoS |

未完美检出的 8 个条目如下：

| 条目和漏洞名 | 覆盖结果 | 没检到或没检全的问题 | 原因 |
|---|---|---|---|
| A1-3 日志注入 / CRLF | 未检出 | 登录失败日志可被换行符伪造 | 注入分析只看 `eval()` 和数据库查询，没把 `console.log` 当 sink |
| A8 跨站请求伪造（CSRF） | 未检出 | 状态修改接口没有 CSRF token 校验 | 没有“POST + cookie 会话 + token 缺失”的 CSRF 专项规则 |
| ReDoS 正则拒绝服务 | 未检出 | `bankRouting` 进入嵌套量词正则，可阻塞 Node.js | 没有 regex 复杂度分析 |
| A2-1 会话管理缺陷 | 部分检出 | 会话超时、rolling/maxAge、`saveUninitialized`、`resave`、MemoryStore 风险没覆盖 | 认证分析偏登录和密码，没完整检查 session 生命周期 |
| A3 跨站脚本（XSS） | 部分检出 | 官方 profile 页示例和 URL / 属性上下文没单独覆盖 | 找到了其他 XSS，但没拿 Tutorial 页面对照，也没区分输出上下文 |
| A5 安全配置错误 | 部分检出 | 请求体大小、`x-powered-by`、Helmet、cookie 生命周期等配置没覆盖 | 没有独立配置检查轨，HTTP / CSP 只在其他 finding 里顺带出现 |
| A6 敏感数据暴露 | 部分检出 | SSN、DOB、地址、银行账号等敏感字段明文存储没覆盖 | 敏感字段规则只识别到密码 |
| A9 使用已知漏洞组件 | 部分检出 | 只有 marked 被 XSS 链覆盖，其他依赖风险没审计 | 没跑或导入 npm audit，组件风险没有独立维度 |

扫描结果仍有价值：`eval()` 注入、MongoDB `$where` 注入、多个 XSS、授权问题和 SSRF 都被检出了。但它没有覆盖完整官方清单，30 条也不能直接当成 30 个独立漏洞。

## 2. 官方漏洞对照表

| # | 官方条目 | 主要代码 | 扫描对应项 | 结果 | 说明 |
|---:|---|---|---|---|---|
| 1 | A1-1 服务器端 JS 注入 | `app/routes/contributions.js` | `INJ-VULN-02` | 检出 | `preTax`、`afterTax`、`roth` 直接进入 `eval()` |
| 2 | A1-2 SQL / NoSQL 注入 | `app/data/allocations-dao.js` | `INJ-VULN-01` | 检出 | 实际代码是 MongoDB `$where` 注入；SQL 只是教程讲解 |
| 3 | A1-3 日志注入 / CRLF | `app/routes/session.js` | 无 | 未检出 | 未清理的 `userName` 被写入日志 |
| 4 | A2-1 会话管理 | `server.js`、`app/routes/session.js` | `AUTH-VULN-01`、`AUTH-VULN-04`、`AUTH-VULN-05`、`AUTHZ-VULN-06` | 部分检出 | 检出 session fixation、硬编码密钥、HTTP/secure；漏了超时、rolling/maxAge 等 |
| 5 | A2-2 密码猜测 | `app/routes/session.js`、`app/data/user-dao.js` | `AUTH-VULN-02`、`AUTH-VULN-03`、`AUTH-VULN-06`、`AUTH-VULN-07` | 检出 | 覆盖弱密码、明文密码、用户枚举、无限流、默认账号 |
| 6 | A3 XSS | `server.js`、多个模板 | `XSS-VULN-01`、`XSS-GN-*` | 部分检出 | 检出多个 XSS；未单独覆盖官方 profile 示例和 URL 上下文 |
| 7 | A4 IDOR | `app/routes/allocations.js` | `AUTHZ-VULN-03`、`AUTHZ-GN-1` | 检出 | 同一 IDOR 报两次 |
| 8 | A5 安全配置错误 | `server.js` | `AUTH-VULN-05` 及少量描述 | 部分检出 | 只覆盖 HTTP、HSTS、CSP；漏了请求体大小、Helmet 等 |
| 9 | A6 敏感数据暴露 | `server.js`、`app/data/profile-dao.js` | `AUTH-VULN-02`、`AUTH-VULN-05` | 部分检出 | 覆盖明文密码和 HTTP；漏了 SSN、DOB、银行信息明文存储 |
| 10 | A7 功能级访问控制缺失 | `app/routes/index.js`、`app/routes/benefits.js` | `AUTHZ-VULN-01`、`AUTHZ-VULN-02` | 检出 | `/benefits` 读写都只判断登录，没判断管理员 |
| 11 | A8 CSRF | `server.js` | 无 | 未检出 | `csurf` 没启用 |
| 12 | A9 已知漏洞组件 | `package.json`、`package-lock.json` | `XSS-VULN-01` 提到 `marked@0.3.5` | 部分检出 | marked 被检出；没有完整依赖审计 |
| 13 | A10 未校验跳转 | `app/routes/index.js` | `AUTHZ-VULN-05`、`OPENREDIR-VULN-01`、`SSRF-GN-03` | 检出 | `/learn?url=` 报了三次 |
| 14 | ReDoS | `app/routes/profile.js` | 无 | 未检出 | 嵌套量词正则可阻塞 Node.js |
| 15 | SSRF | `app/routes/research.js` | `SSRF-VULN-01` | 检出 | `url + symbol` 直接进入 `needle.get()` |

A3 的根因是 `autoescape: false`，扫描检出了多个同类 XSS。如果只按根因算，可以记为检出；这里按官方示例的具体 sink 记，所以是部分检出。

## 3. 靶场信息

| 项目 | 内容 |
|---|---|
| 项目 | OWASP NodeGoat |
| 本地路径 | `workspaces/__legacy__/repos/NodeGoat` |
| 目标仓库 HEAD | `c5cb68a` |
| 技术栈 | Node.js、Express、MongoDB、Swig |
| 用途 | 演示 OWASP Top 10 在 Node.js 应用中的出现和修复方式 |
| 默认端口 | 4000 |
| 默认数据库 | `mongodb://localhost:27017/nodegoat` |
| 默认账号 | `admin/Admin_123`、`user1/User1_123`、`user2/User2_123` |

NodeGoat 没有单独的 `VULNERABILITIES.md`。漏洞说明主要看四处：

1. **README**：`README.md`，说明运行后访问 `/tutorial`。
2. **Tutorial 页面**：`app/routes/tutorial.js` 注册路由，页面在 `app/views/tutorial/`。

   | 文件 | 内容 |
   |---|---|
   | `a1.html` | Injection：SSJS、SQL/NoSQL、日志注入 |
   | `a2.html` | 认证和会话管理 |
   | `a3.html` | XSS |
   | `a4.html` | IDOR |
   | `a5.html` | 安全配置错误 |
   | `a6.html` | 敏感数据暴露 |
   | `a7.html` | 功能级访问控制缺失 |
   | `a8.html` | CSRF |
   | `a9.html` | 已知漏洞组件 |
   | `a10.html` | 未校验跳转 |
   | `redos.html` | ReDoS |
   | `ssrf.html` | SSRF |

3. **源码注释**：搜 `Fix for A1`、`Fix for A2` 这类注释。重点看 `server.js`、`app/routes/`、`app/data/`。
4. **依赖线索**：`package.json` 里有 `"//": "a9 insecure components"`，`marked` 固定为 `0.3.5`。

## 4. 扫描结果

扫描产物在：

```text
workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/
```

关键文件：

| 文件 | 内容 |
|---|---|
| `comprehensive_security_assessment_report.md` | 综合报告 |
| `report_data.json` | 结构化数据 |
| `exploitable_poc_collection.md` | PoC 汇总 |
| `injection_findings.md` 等分类文件 | 各类型详细发现 |

扫描类型分布：

| 类型 | 数量 |
|---|---:|
| Injection | 2 |
| XSS | 11 |
| Authentication | 7 |
| Authorization | 7 |
| SSRF / Open Redirect | 3 |
| **合计** | **30** |

30 条记录如下：

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
| 10 | `XSS-GN-28` | high | `roth` 渲染链；未判定，需复核 |
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
| 26 | `AUTHZ-VULN-06` | medium | session fixation，与 `AUTH-VULN-01` 重复 |
| 27 | `AUTHZ-GN-1` | high | Allocations IDOR，与 `AUTHZ-VULN-03` 重复 |
| 28 | `SSRF-VULN-01` | high | `/research` SSRF |
| 29 | `OPENREDIR-VULN-01` | medium | `/learn` 开放重定向，重复 |
| 30 | `SSRF-GN-03` | high | `/learn` 开放重定向，重复 |

其中一些是对官方范围的扩展，例如默认账号、登录无限流、Memos 全量读、硬编码 `cookieSecret`。这些是真实代码问题，但是否算独立漏洞要看评估口径。

## 5. 漏检与部分覆盖

这次扫描主要覆盖 Injection、XSS、Auth、AuthZ、SSRF。日志注入、CSRF、ReDoS、配置、敏感字段存储和依赖审计没有形成同等强度的专项检查，也没有自动拿 Tutorial 清单对照，所以出现了下面的缺口。

### 5.1 完全漏检：A1-3 日志注入

`app/routes/session.js` 直接记录未清理的用户名：

```js
console.log("Error: attempt to login with invalid user: ", userName);
```

`userName` 来自 `POST /login`。带换行的输入可以在日志里伪造新记录。

**漏检原因**：注入分析关注 `eval()` 和数据库查询，没有把 `console.log` / logger 当成 sink，也没有检查换行和控制字符。

### 5.2 完全漏检：A8 CSRF

`server.js` 中 CSRF 中间件没有启用：

```js
// const csrf = require('csurf');

/*
app.use(csrf());
*/
```

受影响接口包括 `POST /profile`、`POST /contributions`、`POST /benefits`、`POST /memos`、`POST /signup`、`POST /login`。

报告里有些 XSS 影响提到“可以发起 CSRF”，但那不是 CSRF 检测。缺少的是独立判断：状态修改接口是否用 cookie 会话、是否校验 token、是否能被第三方页面触发。

### 5.3 完全漏检：ReDoS

`app/routes/profile.js` 使用嵌套量词：

```js
const regexPattern = /([0-9]+)+\#/;
regexPattern.test(bankRouting);
```

特定长输入会让回溯时间暴涨。Node.js 主线程被占住后，服务无法继续处理请求。

**漏检原因**：没有 regex 复杂度分析，也没有把用户输入到 `RegExp.test()` 的链路当成 DoS sink。

### 5.4 部分检出：A2-1 会话管理

已检出：session fixation、硬编码 `cookieSecret`、HTTP / `secure` cookie。

`server.js` 的配置还暴露了未覆盖的问题：

```js
saveUninitialized: true,
resave: true
```

同时没有 `maxAge`、rolling、会话超时策略；生产环境还使用 MemoryStore，存在重启失效、内存泄漏和多实例问题。

**漏检原因**：认证分析关注登录和密码，没有完整检查 session 生命周期。

### 5.5 部分检出：A3 XSS

已检出：Memos、Benefits、Login、Signup、Allocations 等多条 XSS 链。

官方示例里的 profile 输出没有单独覆盖。根因在 `server.js`：

```js
autoescape: false
```

`layout.html` 直接输出用户资料：

```html
{{firstName}} {{lastName}}
```

Profile 页还有上下文问题。`website` 只做了 HTML 编码，却被放进 input 属性；另一个链接把 `firstNameSafeString` 放进 `href`。HTML body、属性和 URL 上下文需要的处理不同。

**漏检原因**：XSS 扫描找到了多个模板输出点，但没有按 Tutorial 的具体页面对照，也没有完整区分输出上下文。

### 5.6 部分检出：A5 安全配置错误

已检出或提及：HTTP、HSTS、CSP。

`server.js` 里这些配置也被注释掉了：

```js
// app.disable("x-powered-by");
// helmet.frameguard();
// helmet.noCache();
// helmet.contentSecurityPolicy();
// helmet.hsts();
```

请求体解析也没有大小限制：

```js
app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: false }));
```

**漏掉的问题**：请求体大小、`x-powered-by`、frameguard、noCache、nosniff、cookie 生命周期、生产环境错误输出。

**漏检原因**：没有独立配置检查轨，HTTP / CSP 只在其他 finding 里被顺带提到。

### 5.7 部分检出：A6 敏感数据暴露

已检出：密码明文、HTTP 传输。

`app/data/profile-dao.js` 还明文保存：

- SSN
- DOB
- 地址
- 银行账号
- 银行 routing number

加密保存的示例整段在注释里，生效代码直接写库。

**漏检原因**：认证轨识别了密码，但没有把 SSN、DOB、地址、银行字段纳入敏感数据规则。

### 5.8 部分检出：A9 已知漏洞组件

`XSS-VULN-01` 已提到 `marked@0.3.5`，所以官方 A9 的核心示例在实质上被检出。

`package.json` 固定了：

```json
"marked": "0.3.5"
```

但没有独立依赖审计：没有跑或导入 npm audit，没有区分 runtime 和 dev dependency，也没有系统检查其他依赖。

**漏检原因**：marked 被归入 XSS 链，组件风险没有成为独立分析维度。

## 6. 报告质量注意点

### 重复项

| 问题 | 重复 ID |
|---|---|
| Allocations IDOR | `AUTHZ-VULN-03`、`AUTHZ-GN-1` |
| `/learn` 开放重定向 | `AUTHZ-VULN-05`、`OPENREDIR-VULN-01`、`SSRF-GN-03` |
| session fixation | `AUTH-VULN-01`、`AUTHZ-VULN-06` |
| `benefitStartDate` XSS | `XSS-GN-08`、`XSS-GN-10` |
| Signup XSS | `XSS-GN-35`、`XSS-GN-36` |
| Benefits `firstName` XSS | `XSS-GN-04`、`XSS-GN-05` |

前五组基本应合并。最后一组来源不同、sink 相同，是否合并取决于口径。

### 置信度

`report_data.json` 里：

- high：2 条，`INJ-VULN-02` 和 `SSRF-VULN-01`
- needs_review：27 条
- unadjudicated：1 条，`XSS-GN-28`

所以大部分是静态待复核，不能都当成已确认漏洞。

### cookie 描述冲突

有些 XSS 描述说 `connect.sid` 没有 `httpOnly`；`AUTH-VULN-05` 又说 express-session 默认开启 `httpOnly`。当前版本是 `express-session@1.15.6`，默认通常是开启的。需要看实际 `Set-Cookie` 确认，再修正相关影响描述。

### 硬编码密钥影响要复核

硬编码 `cookieSecret` 是真问题，但 express-session cookie 主要携带签名后的 session id。知道 secret 通常能伪造签名；要冒用用户，还要看能否拿到或预测服务端存在的 session id。因此“可伪造 cookie”不一定等于“可登录任意账号”。

### QA 未通过

`report_data.json` 记录：

```json
"qa": {
  "passed": false
}
```

使用综合报告时，要区分已确认、待复核和未判定项。

## 7. 后续改进

1. **把 Tutorial 当 baseline**：报告生成后输出“官方条目 → finding ID”，缺失项直接显示。
2. **补专项规则**：CSRF、日志注入、ReDoS、安全配置、敏感字段。
3. **接入依赖审计**：对 `package-lock.json` 跑或导入 npm audit，区分 runtime 和 dev dependency。
4. **报告前去重**：用 `method + path + sink file:line + 字段` 合并同类项。
5. **动态验证高价值问题**：优先验证 `eval()`、`$where`、SSRF、CSRF、ReDoS。

## 附录：证据位置

| 内容 | 路径 |
|---|---|
| README | `workspaces/__legacy__/repos/NodeGoat/README.md` |
| Tutorial 路由 | `workspaces/__legacy__/repos/NodeGoat/app/routes/tutorial.js` |
| Tutorial 页面 | `workspaces/__legacy__/repos/NodeGoat/app/views/tutorial/` |
| 应用入口 | `workspaces/__legacy__/repos/NodeGoat/server.js` |
| 路由 | `workspaces/__legacy__/repos/NodeGoat/app/routes/` |
| DAO | `workspaces/__legacy__/repos/NodeGoat/app/data/` |
| 种子账号 | `workspaces/__legacy__/repos/NodeGoat/artifacts/db-reset.js` |
| 依赖声明 | `workspaces/__legacy__/repos/NodeGoat/package.json` |
| 综合报告 | `workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/comprehensive_security_assessment_report.md` |
| 结构化数据 | `workspaces/__legacy__/scans/NodeGoat-20260831-015748/deliverables/whitebox/report_data.json` |
