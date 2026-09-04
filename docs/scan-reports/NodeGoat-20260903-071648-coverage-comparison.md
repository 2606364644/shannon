# NodeGoat 扫描覆盖度对比分析报告

- **扫描任务**：`NodeGoat-20260903-071648`  
- **报告入口**：`http://10.2.22.187:7878/p/__legacy__/scans/NodeGoat-20260903-071648/report`  
- **本地融合报告**：`workspaces/__legacy__/scans/NodeGoat-20260903-071648/combined/run-1/report_data.json`  
- **对照源码**：`workspaces/__legacy__/repos/NodeGoat`  
- **源码基线提交**：`c5cb68a7084e4ae7dcc60e6a98768720a81841e8`  
- **分析日期**：2026-09-04  

## 1. 结论

**没有全检出。** 扫描报告共输出 **23 条**漏洞，覆盖了 NodeGoat 中最核心的 eval RCE、NoSQL 注入、默认口令、IDOR、会话固定、SSRF 和部分 XSS 场景；但按 NodeGoat 官方教程和源码内注释构成的“种子漏洞基线”对照，**仍有多个明确植入的漏洞类别缺失**。

以本报告整理出的 **18 个 canonical 漏洞点**计算：

| 覆盖情况 | 数量 | 占比 |
|---|---:|---:|
| 完整检出 | 8 | 44.4% |
| 部分/间接检出 | 1 | 5.6% |
| 未检出 | 9 | 50.0% |

主要缺失类别包括：

1. **登录失败日志 CRLF / Log Injection**
2. **认证用户枚举：用户名错误与密码错误提示不同**
3. **弱口令策略**
4. **Session Cookie/超时/安全响应头等配置类问题**
5. **CSRF**
6. **敏感数据明文存储 / HTTP 明文传输 / 硬编码密钥**
7. **Open Redirect：`GET /learn?url=`**
8. **ReDoS：Profile 页 Bank Routing 正则**
9. **A9/SCA 的系统性依赖漏洞检测**

因此，如果评价口径是“NodeGoat 教学靶场的种子漏洞是否全部发现”，当前结果只能判断为 **核心注入/访问控制覆盖较好，但整体覆盖不完整**。

## 2. 对比口径

NodeGoat 官方 README 明确其目标是演示 OWASP Top 10 在 Node.js 中的落地。仓库内教程页面包含 `a1.html` 到 `a10.html`、`redos.html`、`ssrf.html`，且源码中保留了大量 `Fix for A1/A2/A3/A4/A5/A6/A7/A8/A9` 注释。本次以以下三类证据取并集形成 canonical 基线：

1. 官方教程中明确标注的 demo 场景；
2. 源码注释中明确标注的 vulnerable point；
3. 实际可达的源码数据流，例如 `eval()`、`$where`、`res.redirect(req.query.url)`。

该口径评估“教学种子漏洞覆盖度”，不把现代 `npm audit` 中所有历史传递依赖告警都计入主覆盖率，否则会混入大量非 NodeGoat 特意构造的应用层场景。依赖问题在后续单独说明。

## 3. 被分析报告的总体数据

融合报告结构化数据如下：

| 指标 | 值 |
|---|---:|
| 最终漏洞条目 | 23 |
| critical | 2 |
| high | 18 |
| medium | 3 |
| 已动态实证 / verified | 7 |
| 未覆盖 / untested | 16 |
| injection | 2 |
| XSS | 15 |
| auth | 1 |
| authz | 4 |
| SSRF | 1 |
| attack chains | 0 |

已实证的 7 条集中在：

- `INJ-VULN-01`
- `AUTH-VULN-05`
- `AUTHZ-VULN-01`
- `AUTHZ-VULN-02`
- `AUTHZ-VULN-03`
- `AUTHZ-VULN-04`
- `SSRF-VULN-01`

15 条 XSS 均为 `untested`，黑盒 XSS verdict 文件为空：

```json
{
  "vuln_class": "xss",
  "accepted_ids": [],
  "verdicts": [],
  "rejected": []
}
```

## 4. Canonical 漏洞覆盖矩阵

状态说明：

- **检出**：至少有一条报告 finding 准确命中根因、入口或 sink；
- **部分检出**：相关风险被命中，但漏洞类别、完整入口或攻击面映射不完整；
- **未检出**：23 条最终 finding 中没有对应条目。

| # | NodeGoat 漏洞点 | 主要源码位置 | 报告命中 | 状态 |
|---:|---|---|---|---|
| 1 | Server-side JS Injection / eval RCE | `app/routes/contributions.js:31-34` | `INJ-VULN-01` | 检出 |
| 2 | NoSQL / `$where` JS 注入 | `app/data/allocations-dao.js:57-80` | `INJ-VULN-02` | 检出 |
| 3 | 登录失败日志 CRLF / Log Injection | `app/routes/session.js:64` | 无 | 未检出 |
| 4 | 默认口令 + 密码明文存储 | `artifacts/db-reset.js:12-37`, `app/data/user-dao.js:17-31,57-67` | `AUTH-VULN-05` | 检出 |
| 5 | 用户枚举 / 差异化登录错误 | `app/routes/session.js:59-97` | 无 | 未检出 |
| 6 | 弱口令策略 | `app/routes/session.js:138-175` | 无 | 未检出 |
| 7 | 登录后会话固定 / 未 regenerate | `app/routes/session.js:104-117` | `AUTHZ-VULN-04` | 检出 |
| 8 | Session Cookie 缺少 `httpOnly/secure/maxAge`、无超时、HTTP 明文 | `server.js:77-102,144-147` | 无 | 未检出 |
| 9 | 全局 `autoescape:false` 造成的 Profile 存储型 XSS | `server.js:135-142`, `app/routes/profile.js`, `app/views/profile.html` | `XSS-VULN-03`, `XSS-GN-26` | 检出 |
| 10 | Allocations IDOR | `app/routes/allocations.js:11-31` | `AUTHZ-VULN-01` | 检出 |
| 11 | Security Misconfiguration：`x-powered-by`、默认 Cookie 名、安全响应头缺失 | `server.js:38-65,77-102` | 无 | 未检出 |
| 12 | Sensitive Data Exposure：SSN/DOB 明文、硬编码密钥 | `app/data/profile-dao.js:42-75`, `config/env/all.js` | 无 | 未检出 |
| 13 | Benefits 功能级越权 / 写操作 IDOR | `app/routes/index.js:54-60`, `app/routes/benefits.js`, `app/data/benefits-dao.js` | `AUTHZ-VULN-02`, `AUTHZ-VULN-03` | 部分检出 |
| 14 | CSRF | `server.js:7,104-113`, `app/routes/profile.js:40-105`, 官方 `a8.html` | 无 | 未检出 |
| 15 | `marked 0.3.5` 不安全依赖 / Memo XSS | `package.json:17`, `server.js:124-129`, `app/views/memos.html:31` | `XSS-GN-24` | 检出 |
| 16 | Open Redirect | `app/routes/index.js:69-73` | 无 | 未检出 |
| 17 | ReDoS | `app/routes/profile.js:52-61` | 无 | 未检出 |
| 18 | SSRF | `app/routes/research.js:12-29` | `SSRF-VULN-01` | 检出 |

## 5. 报告已有 finding 与基线的对应关系

| 报告 ID | 报告核心结论 | 对应 canonical 点 | 判定 |
|---|---|---|---|
| `INJ-VULN-01` | `/contributions` 的 `preTax/afterTax/roth` 进入 `eval()`，已 RCE | 1 | 有效，高价值 |
| `INJ-VULN-02` | `/allocations/:userId?threshold=` 进入 MongoDB `$where` | 2 | 有效，静态可信；未黑盒复现 |
| `XSS-VULN-01` | `/login` 的 `userName` 回显 XSS | 9 的外延场景 | 有效；与 GN 系列重复 |
| `XSS-VULN-02` | `/signup` 的 `userName/email` 回显 XSS | 9 的外延场景 | 有效；与 GN 系列重复 |
| `XSS-VULN-03` | `/profile` 的 `firstName` 进入属性与 `href` | 9 | 有效 |
| `XSS-VULN-05` | `/allocations/:userId` 的 `userId` 注入 form action | 9 的外延场景 | 有效；与 `XSS-GN-01` 重复 |
| `XSS-VULN-06` | `/benefits` 的 `benefitStartDate` 存储型 XSS | 9 的外延场景 | 有效；与 GN 系列重复 |
| `XSS-GN-01` | allocation userId 在表单 action 原样输出 | 9 | 重复 |
| `XSS-GN-03` | benefitStartDate 回到 benefits 模板 | 9 | 重复 |
| `XSS-GN-07` | benefitStartDate 经 DB 回显，但参数列显示 `dob` | 9 | 疑似重复且元数据不一致 |
| `XSS-GN-15` | `preTax` 经 `eval()` 后进入模板 | 1 的衍生 XSS | 可疑/衍生条目，易与 RCE 混淆 |
| `XSS-GN-24` | `marked 0.3.5` 渲染 memo 导致存储型 XSS | 15 | 有效；但路径写作 `/memos)`，有元数据缺陷 |
| `XSS-GN-26` | profile 多字段进入未转义模板 | 9 | 重复 |
| `XSS-GN-28` | login 模板未转义 | 9 | 重复 |
| `XSS-GN-30` | signup 模板未转义 | 9 | 重复 |
| `XSS-GN-32` | login 模板未转义 | 9 | 重复 |
| `XSS-GN-37` | login 模板未转义 | 9 | 重复；endpoint 写作 `/login,` |
| `AUTH-VULN-05` | 默认 admin 口令且密码明文存储 | 4 | 有效，已实证 |
| `AUTHZ-VULN-01` | allocation userId 可水平越权 | 10 | 有效，已实证 |
| `AUTHZ-VULN-02` | benefits body userId 可越权修改 | 13 | 有效，但更像写操作 IDOR + 缺失角色控制 |
| `AUTHZ-VULN-03` | 任意登录用户可访问 benefits | 13 | 有效，但主要描述 GET，未单独列 POST 角色缺失 |
| `AUTHZ-VULN-04` | 登录未 regenerate session | 7 | 有效，已实证 |
| `SSRF-VULN-01` | `/research` 的 `url/symbol` 导致 SSRF | 18 | 有效，已实证 |

## 6. 明确漏报项

### 6.1 Log Injection / CRLF Injection

`app/routes/session.js:64` 将未编码的 `userName` 直接输出到日志：

```js
console.log("Error: attempt to login with invalid user: ", userName);
```

同文件 66-80 行即给出 CRLF / Log Injection 修复建议。官方 `a1.html` 将其列为 **A1 - 3 Log Injection**。最终报告没有任何 log injection、CRLF injection 或日志伪造 finding。

### 6.2 认证用户枚举

登录失败时返回两类不同错误：

- 不存在用户：`Invalid username`
- 密码错误：`Invalid password`

对应 `app/routes/session.js:59-97`。同文件 86-87、95-96 行明确注释了统一错误信息的修复方案，官方 A2 教程也列为 password guessing 支撑面。最终报告未检出。

### 6.3 弱口令策略

`app/routes/session.js:144`：

```js
const PASS_RE = /^.{1,20}$/;
```

146-149 行的注释给出了至少 8 位且包含大小写与数字的修复正则。当前策略接受 `"1"` 这类弱口令。最终报告未检出弱口令策略问题。

### 6.4 Session Cookie / 会话生命周期配置

`server.js:77-102` 中：

- 未设置 `cookie.httpOnly`；
- 未设置 `cookie.secure`；
- 未设置 `maxAge`；
- `saveUninitialized: true`；
- `resave: true`；
- 使用默认 `connect.sid` Cookie 名；
- 应用通过 `http.createServer()` 启动，而非 HTTPS。

官方 A2/A5/A6 教程和源码注释均明确列出这些风险。报告仅检出会话固定，未检出 Cookie 属性、会话超时与明文传输配置问题。

### 6.5 CSRF

`server.js:7` 注释掉了：

```js
// const csrf = require('csurf');
```

`server.js:104-113` 注释掉了 `csrf()` 中间件。虽然 profile/login/signup 等模板写了 `_csrf` 字段，但服务端没有校验逻辑。官方 `a8.html` 明确给出对 `/profile` 的 CSRF 攻击示例。最终报告没有 CSRF finding。

### 6.6 Security Misconfiguration

`server.js:38-65` 的整段安全头配置被注释，包括：

- `app.disable("x-powered-by")`；
- frameguard；
- noCache；
- CSP；
- HSTS；
- nosniff。

`server.js:77-102` 的 generic cookie name 修复也被注释。官方 A5 教程将其作为核心场景。最终报告没有 Security Misconfiguration / Security Headers 类 finding。

### 6.7 Sensitive Data Exposure

`app/data/profile-dao.js:42-75` 将 `ssn`、`dob` 直接明文写入用户集合；100-105 行也没有解密逻辑，因为加密方案被整体注释。`config/env/all.js` 中存在默认 `cookieSecret` 与 `cryptoKey`。`server.js:144-155` 只启动 HTTP，HTTPS 被注释。最终报告未单独输出这些数据暴露风险。

需要注意：`INJ-VULN-01` 的动态利用证据中确实导出了包含 SSN、银行账号等 PII 的数据，但这是 eval RCE 的影响证明，报告中没有把“SSN/DOB 明文存储”作为独立根因建立 finding。

### 6.8 Open Redirect

`app/routes/index.js:69-73`：

```js
app.get("/learn", isLoggedIn, (req, res) => {
    // Insecure way to handle redirects by taking redirect url from query string
    return res.redirect(req.query.url);
});
```

白盒中间产物 `dismissed_findings.json` 已经识别到该链，但因 SSRF 判定器认为它“不是 SSRF”，该候选被直接判 safe；系统缺少 Open Redirect 输出通道，导致该已知漏洞被丢弃。这是明确的轨间分类/分发缺陷。

### 6.9 ReDoS

`app/routes/profile.js:52-61`：

```js
const regexPattern = /([0-9]+)+\#/;
const testComplyWithRequirements = regexPattern.test(bankRouting);
```

源码注释和官方 `redos.html` 均明确说明存在 catastrophic backtracking。最终报告没有 ReDoS finding。

## 7. 依赖组件 / SCA 覆盖

NodeGoat 的 A9 明确使用 `marked 0.3.5`。报告通过 `XSS-GN-24` 命中了其可导致的 memo XSS，属于结果级检出；但报告没有生成 A9/SCA finding，也没有列出 `package.json` / `package-lock.json` 中的组件漏洞。

补充执行：

```bash
npm audit --package-lock-only --omit=dev --json
```

当前审计结果显示生产依赖树存在 **51 个 vulnerable package**，严重度分布为：

| 严重度 | 数量 |
|---|---:|
| critical | 16 |
| high | 24 |
| moderate | 5 |
| low | 6 |

其中直接依赖至少包括 `marked`、`mongodb`、`express`、`express-session`、`body-parser`、`helmet`、`underscore`、`swig`、`forever` 等。若按现代 SCA 口径评估，当前扫描的缺失更大；若仅按 NodeGoat 官方教学口径，则至少缺少“A9 insecure dependency”作为独立漏洞类别。

## 8. 报告质量与重复度

1. **XSS 条目重复明显**  
   15 条 XSS 可大致收敛为约 7 类 sink family：
   - login 模板；
   - signup 模板；
   - profile 模板；
   - allocations 表单 action；
   - benefits 存储回显；
   - memos `marked()`；
   - contributions eval 衍生链。  
   其中 login、signup、allocation、benefits、profile 均有多条重复。

2. **元数据规范化不足**  
   多个 `XSS-GN-*` finding 缺失 endpoint 或 OWASP category；`XSS-GN-24` 的 endpoint 为 `/memos)`；`XSS-GN-37` 的 endpoint 为 `/login,`；`XSS-GN-07` 标题指向 `benefitStartDate` 但参数列为 `dob`。

3. **验证率偏低**  
   23 条中仅 7 条 verified，16 条 untested。XSS 数量最多，但黑盒验证为 0，削弱了报告的可信度分层。

4. **轨间分类导致漏报**  
   Open Redirect 候选被 SSRF 判定器以“非 SSRF”理由丢弃，而不是转交 URL Redirection 类漏洞处理，直接造成官方 A10 漏报。

## 9. 总体评价

| 维度 | 评价 |
|---|---|
| 注入能力 | 较强，eval RCE 与 `$where` 注入均准确命中 |
| 访问控制 | 较强，allocations IDOR、benefits 越权、会话固定均已发现 |
| SSRF | 准确命中 |
| XSS | 候选较多，但重复明显且全部未动态验证 |
| 认证 | 命中默认口令/明文口令与会话固定，漏掉枚举、弱口令、Cookie/session 配置 |
| 配置类漏洞 | 基本未覆盖 |
| CSRF / Redirect / ReDoS | 完全漏报 |
| SCA | 未系统覆盖，仅间接到 marked XSS |
| 报告去重 | 需要改进 |

## 10. 建议

1. **补齐漏洞类别**：至少新增 Open Redirect、CSRF、Security Misconfiguration、Sensitive Data Exposure、ReDoS、Log Injection、SCA track。
2. **修复轨间分发**：当某候选被当前 track 判为“非本类漏洞”但已有明确危险 sink 时，应转交其他 track 或 fallback 类别，而不是直接 dismissed。
3. **XSS 去重**：按 `template + source parameter + sink context + persistence boundary` 归并，避免同一 login/signup/profile 链路输出多条 finding。
4. **补强黑盒验证**：优先对 15 条 XSS 增加浏览器级 DOM/JS 执行验证，而不是仅以 HTTP 响应判断。
5. **增加配置审计**：检查 `httpOnly`、`secure`、`sameSite`、`maxAge`、HTTPS、安全响应头、默认 Cookie 名、`x-powered-by`、错误信息差异。
6. **增加依赖扫描**：直接解析 `package-lock.json`，输出 vulnerable package、路径、修复版本与可达性。
7. **修复报告元数据**：规范 endpoint、参数、OWASP category 和 title，避免 `/memos)`、`/login,`、参数错位等问题影响下游聚合。

## 11. 最终判断

当前报告对 NodeGoat 的 **高危代码执行与访问控制主链检出效果较好**，但它**不能被视为“全量检出”**。按教学靶场种子漏洞口径，完整检出率约为 **8/18（44.4%）**，含部分检出也仅为 **9/18（50.0%）**。若纳入现代依赖审计口径，则还会新增大量未覆盖的组件级风险。
