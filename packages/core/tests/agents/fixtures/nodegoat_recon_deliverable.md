# Reconnaissance Deliverable:

## 0) HOW TO READ THIS
This reconnaissance report provides a comprehensive map of the application's attack surface, with special emphasis on authorization and privilege escalation opportunities for the Authorization Analysis Specialist.

**Key Sections for Authorization Analysis:**
- **Section 4 (API Endpoint Inventory):** Contains authorization details for each endpoint - focus on "Required Role" and "Object ID Parameters" columns to identify IDOR candidates.
- **Section 6.4 (Guards Directory):** Catalog of authorization controls - understand what each guard means before analyzing vulnerabilities.
- **Section 7 (Role & Privilege Architecture):** Complete role hierarchy and privilege mapping - use this to understand the privilege lattice and identify escalation targets.
- **Section 8 (Authorization Vulnerability Candidates):** Pre-prioritized lists of endpoints for horizontal, vertical, and context-based authorization testing.

**How to Use the Network Mapping (Section 6):** The entity/flow mapping shows system boundaries and data sensitivity levels. Pay special attention to flows marked with authorization guards and entities handling PII/sensitive data.

**Priority Order for Testing:** Start with Section 8's High-priority horizontal candidates, then vertical escalation endpoints for each role level, finally context-based workflow bypasses.

## 1. Executive Summary

NodeGoat（OWASP Node.js Goat v1.3.0）是一个刻意内置大量漏洞的 OWASP Top 10 教学应用，业务形态为"RetireEasy"员工退休储蓄管理系统（Employee Retirement Savings Management）。

核心用户面组件即攻击面：
- 会话与认证：GET/POST /login、GET/POST /signup、GET /logout（app/routes/session.js）——express-session 会话，secret 硬编码，cookie 未设 httpOnly/secure，密码明文存储与比对（bcrypt 被注释）。
- 个人资料：GET/POST /profile（app/routes/profile.js）——SSN/DOB/银行账号等 PII 明文入库；bankRouting 用灾难性回溯正则（ReDoS）。
- 缴款比例：GET/POST /contributions（app/routes/contributions.js）——eval() 服务端 JS 注入（SSJS/RCE）。
- 资产配置：GET /allocations/:userId（app/routes/allocations.js + app/data/allocations-dao.js）——用户 ID 取自 URL（IDOR）+ threshold 拼入 MongoDB $where（NoSQL 注入）。
- 福利管理：GET/POST /benefits（app/routes/benefits.js）——本应仅管理员的函数级访问控制缺失（BFLA），且可按 body userId 水平越权改任意员工数据。
- 备忘录：GET/POST /memos（app/routes/memos.js）——全局共享 memo，marked() 渲染（存储型 XSS）。
- 行情研究：GET /research（app/routes/research.js）——needle.get(req.query.url+symbol) SSRF。
- 学习跳转：GET /learn（app/routes/index.js:72）——res.redirect(req.query.url) 开放重定向。

框架/语言/关键技术：Node.js 12 + Express 4.13 + swig 模板引擎（autoescape:false 全局关闭转义）+ MongoDB（原生驱动 2.x）+ marked 0.3.5 + needle 2.2.4。单进程 http.createServer 监听 :4000，无 TLS、无反向代理、无安全响应头；helmet/csurf/nosniff 全部被注释。教程页 /tutorial/a7 明文泄露 admin/Admin_123 凭据，README 披露默认账号 user1/User1_123、user2/User2_123。

## 2. Technology & Service Map

- **Frontend:** 服务端渲染 swig 模板（consolidate 0.14 + swig 1.4.2，server.js:134-137 全局 autoescape:false 关闭转义 → 所有 {{ }} 原样输出 HTML）；Bootstrap 3 + jQuery 1.10.2 + Morris.js 图表（app/assets/vendor）；无 SPA 框架、无前端鉴权库、无运行时 XHR/fetch/axios（纯 HTML form action 与 a href 导航，所有目标路径均已在 index.js 登记）。
- **Backend:** Node.js 12（server.js，单进程 http.createServer 监听 :4000，无 HTTPS——HTTPS 代码被注释）；Express 4.13.4；body-parser（json + urlencoded extended:false）；express-session 1.13（secret 硬编码 'session_cookie_secret_key_here'，saveUninitialized:true、resave:true，cookie httpOnly/secure/sameSite 均未设置）；consolidate+swig 模板；marked 0.3.5（setOptions sanitize:true 但为已知可绕过的正则净化）；needle 2.2.4（出站 HTTP 客户端，SSRF 面）；node-esapi（仅 profile.js:28 用于 website 编码，上下文用错）；bcrypt-nodejs 已依赖但未启用（密码明文存储/比较）；csurf/helmet/dont-sniff-mimetype 均被注释。DAO 层：user/profile/benefits/contributions/allocations/memos/research-dao（app/data/）。
- **Infrastructure:** 单 Docker 容器（node:12-alpine，Dockerfile）暴露 :4000；MongoDB 4.4（docker-compose.yml 内部网络 mongo:27017，端口未发布到宿主机）；配置经 config/config.js + config/env/{NODE_ENV}.js 合并（含硬编码 cookieSecret/cryptoKey）；Procfile 用 forever 启动 server.js；无 CDN/WAF/反向代理/nginx/ingress 配置（grep 零结果）；无自动 REST 框架（finale/epilogue/loopback 均无）。

## 3. Authentication & Session Management Flow

- **Entry Points:** GET/POST /login、GET/POST /signup、GET /logout（app/routes/session.js）；受保护路由（/dashboard、/profile、/contributions、/benefits、/allocations/:userId、/memos、/research、/learn）均依赖 isLoggedInMiddleware 检查 req.session.userId。
- **Mechanism:** express-session 中间件（server.js:78-100）：secret 取 config/config.js 硬编码 cookieSecret（'session_cookie_secret_key_here'，config.js:9）；cookie 名保持默认 connect.sid（自定义 key 选项被注释）；saveUninitialized:true、resave:true；cookie 的 httpOnly/secure/sameSite 全部被注释未生效 → 会话 cookie 可被 JS 读取、经明文 HTTP 传输、无过期时间。登录流程（POST /login，session.js:53-119）：user-dao.js validateLogin 以明文等值比较密码（bcrypt 修复被注释），成功后直接 req.session.userId = user._id，无会话固定防护（regenerate 被注释）；admin 用户 302 到 /benefits，普通用户 302 到 /dashboard。注册流程（POST /signup，session.js:189-252）：addUser 明文存储密码，随后 req.session.regenerate() 再设置 userId 并渲染 dashboard。登出（GET /logout，session.js:121-123）销毁会话并重定向 /。
- **Code Pointers:** app/routes/session.js:44-51（displayLoginPage）、:53-119（handleLoginRequest）、:125-135（displaySignupPage）、:189-252（handleSignup）、:121-123（displayLogoutPage）、:256-274（displayWelcomePage）、:36-43（isLoggedInMiddleware）、:25-35（isAdminUserMiddleware）；app/data/user-dao.js:19-29（addUser）、:44-79（validateLogin）；server.js:78-100（session 中间件配置）；config/config.js:9（cookieSecret）

### 3.1 Role Assignment Process

- **Role Determination:** 角色不由会话/JWT 携带：仅当需要管理员校验时，isAdminUserMiddleware（session.js:25-35）实时从 MongoDB users 集合读取 user.isAdmin 布尔字段判断；会话中只存 userId（_id）。
- **Default Role:** 新注册用户默认无 isAdmin 字段（即普通 user 角色）；唯一 admin 账号由种子数据 artifacts/db-reset.js:15-20 预置（admin/Admin_123，isAdmin:true），普通账号 user1/User1_123、user2/User2_123。
- **Role Upgrade Path:** 应用内无自助提权接口；用户无法自行设置 isAdmin。唯一'升级'通道是管理员凭据在公开教程页 /tutorial/a7.html:31 与 README.md:22 明文泄露，攻击者可借 admin 账号直接获得 admin 角色。
- **Code Implementation:** app/routes/session.js:25-35（isAdminUserMiddleware）；app/data/user-dao.js addUser（不写 isAdmin，默认普通用户）；artifacts/db-reset.js:15-36（种子账号含 isAdmin:true）；app/routes/index.js:24-27（isAdmin 常量定义，但绑定到 /benefits 的两行 index.js:58-59 被注释）。

### 3.2 Privilege Storage & Validation

- **Storage Location:** 权威角色存储为 MongoDB users 文档的 isAdmin 字段（布尔）；会话 cookie connect.sid 仅承载服务端 sessionId，服务端 session 中存 userId（用户 _id）。
- **Validation Points:** isLoggedInMiddleware（session.js:36-43）——仅检查 req.session.userId 是否存在，用于所有需登录路由（index.js）；isAdminUserMiddleware（session.js:25-35）——DB 查询 user.isAdmin，已定义但【未绑定】到 /benefits（index.js:58-59 被注释），即当前对任何路由不生效。
- **Cache/Session Persistence:** express-session 默认内存存储，无 maxAge（会话在进程重启或登出前持续有效，resave:true）；每次 isAdmin 检查均实时查库、无缓存、无刷新机制；无会话空闲超时。
- **Code Pointers:** app/routes/session.js:25-43（两个中间件）；app/routes/index.js:24-27,55-59（中间件绑定）；app/data/user-dao.js getUserById（role 查询）

### 3.3 Role Switching & Impersonation

[not applicable]

## 4. API Endpoint Inventory

| Method | Endpoint Path | Required Role | Object ID Parameters | Authorization Mechanism | Description & Code Pointer |
| --- | --- | --- | --- | --- | --- |
| GET | / | anon | None | None（handler 内 session.js:259-262 自检，无会话则 302 /login） | 欢迎页/仪表盘（displayWelcomePage）：无会话重定向 /login，有会话则按 userId 查库渲染 dashboard。pre-auth 反射面（共享 handler displayWelcomePage 的 / 变体）。session.js:256-274、index.js:30 |
| GET | /allocations/{userId} | user | userId, threshold | isLoggedInMiddleware；【无所有权校验】——用户 ID 取自 URL 路径参数而非会话（IDOR），修复代码被注释 allocations.js:4-9 | 按 URL 传入的 userId 查询资产配置（IDOR）：allocationsDAO.getByUserIdAndThreshold(userId, threshold)（allocations.js:22）；?threshold= 拼入 MongoDB $where 字符串（allocations-dao.js:78，NoSQL JS 注入：this.stocks > '${threshold}'，可注入 0';while(true){}' 等）；userId 回显到 allocations.html:15 form action 与 layout.html:56 导航链接（反射型 XSS 属性上下文）；查询结果含用户名（跨用户 PII）。allocations.js:11-30、index.js:63 |
| GET | /benefits | user | None | isLoggedInMiddleware 仅（本应 isAdmin；index.js:58 的 isAdmin 绑定被注释）——BFLA | 福利管理页面（BFLA）：列出所有非管理员用户的 _id/firstName/lastName/benefitStartDate（users.find({isAdmin:{$ne:true}})，benefits-dao.js:11-15）——任意登录用户可访问管理员功能并批量获取员工 PII；firstName/lastName 未编码渲染到 benefits.html:47-66（跨用户存储型 XSS 载体）。benefits.js:13-27、index.js:55 |
| POST | /benefits | user | userId | isLoggedInMiddleware 仅（index.js:59 的 isAdmin 绑定被注释）——BFLA + 水平越权 | 修改任意员工福利起始日（BFLA+水平越权）：userId 与 benefitStartDate 均取自 body（benefits.js:31-32），users.update({_id:parseInt(userId)},{$set:{benefitStartDate}})，无对象所有权校验——任何登录用户可改任意员工记录；无 CSRF。benefits.js:29-56、index.js:56 |
| GET | /contributions | user | None | isLoggedInMiddleware | 缴款比例页面（渲染当前 preTax/afterTax/roth）。contributions.js:12-26、index.js:51 |
| POST | /contributions | user | None | isLoggedInMiddleware；无 CSRF（csurf 被注释） | 缴款更新：const preTax = eval(req.body.preTax); / afterTax / roth（contributions.js:32-34）——服务端任意 JS 执行（SSJS/RCE 注入）；eval 结果先 isNaN 校验再累计上限 30% 校验，但校验发生在 eval 之后，无法阻止代码执行；成功后渲染含用户姓名的数据（存储型 XSS 载体）。contributions.js:28-70、index.js:52 |
| GET | /dashboard | user | None | isLoggedInMiddleware（会话校验 req.session.userId） | 仪表盘（共享 handler displayWelcomePage 的 /dashboard 变体，与 GET / 同 handler 但不同 auth 档位）。index.js:44、session.js:256-274 |
| GET | /learn | user | url | isLoggedInMiddleware | 学习资源跳转：res.redirect(req.query.url)（index.js:72）——未校验协议/域名，开放重定向（钓鱼/凭据转发面）。index.js:69-73 |
| GET | /login | anon | None | None | 登录表单页渲染（login.html，含 _csrf 隐藏字段但 csurf 未启用）。session.js:44-51、index.js:33 |
| POST | /login | anon | None | None（登录校验：user-dao.js validateLogin 明文密码比对） | 登录处理：明文密码等值比较（bcrypt 被注释）；失败时把 userName 未编码回显到 login.html:110 value 属性（pre-auth 反射型 XSS）；用户名枚举（invalidUserName 与 invalidPassword 分开报错，session.js:63-90）；登录后 302 /dashboard 或 /benefits，无会话固定防护。session.js:53-119、index.js:34 |
| GET | /logout | anon | None | None（无需会话即可调用，销毁会话后 302 /） | 登出：req.session.destroy() 后重定向 /。session.js:121-123、index.js:41 |
| GET | /memos | user | None | isLoggedInMiddleware；memos 为全局共享集合（无对象所有权/归属隔离） | 列出全部备忘录（memos.find({}).sort({timestamp:-1})，memos-dao.js:20-30）：marked(doc.memo) 在 memos.html:31 渲染为 HTML（marked 0.3.5 sanitize 可绕过 → 存储型 XSS，所有查看者受影响）。memos.js:19-33、index.js:66 |
| POST | /memos | user | None | isLoggedInMiddleware；无 CSRF | 新增备忘录：req.body.memo 原样插入 memos 集合（memos-dao.js:11-17，存储型 XSS 写入点）；随后复用 displayMemos 渲染（共享 handler，memos.js:15）。memos.js:11-17、index.js:67 |
| GET | /profile | user | None | isLoggedInMiddleware；对象 ID 取自会话（profile.getByUserId(parseInt(req.session.userId))） | 展示个人资料：SSN/DOB/银行账号明文返回（A6 加密修复被注释 profile-dao.js:8-34），PII 存储型暴露；website 经 ESAPI.encodeForHTML 编码但上下文用错（用于 href URL 处，profile.js:28 注释标注仍可 XSS）；userId 在 layout.html:56 的 /allocations/{{userId}} 链接触发属性上下文。profile.js:13-37、index.js:47 |
| POST | /profile | user | None | isLoggedInMiddleware；对象 ID 取自会话（userId 来自 req.session） | 资料更新：bankRouting 用灾难性回溯正则 /([0-9]+)+#/ 校验（ReDoS，profile.js:47-51，可被长数字串拖垮事件循环）；校验失败路径把 firstName/lastName/ssn/dob/address/bankAcc/bankRouting 未编码回显到 profile.html:41-78（反射型 XSS，firstName 同时进 :78 href 与 :41 value）；成功路径渲染存库的 ssn/dob/bank 等（存储型 XSS 载体）。profile.js:40-91、index.js:48 |
| GET | /research | user | url, symbol | isLoggedInMiddleware；【无出站请求校验】——url/symbol 完全客户端可控 | 行情研究（SSRF）：if(req.query.symbol){ const url = req.query.url + req.query.symbol; needle.get(url, ...) }（research.js:14-26）——url 参数可改为 http://169.254.169.254/latest/meta-data/、http://mongo:27017 等任意内网目标（needle 2.x 默认跟随 30x 重定向扩大波及面），响应体回显（非盲 SSRF）；固定 url 仅前端 hidden input 提示，服务端零校验。research.js:12-26、index.js:76 |
| GET | /signup | anon | None | None | 注册表单页渲染（signup.html）。session.js:125-135、index.js:37 |
| POST | /signup | anon | None | None（无 CSRF，无验证码/速率限制） | 注册处理：addUser 明文存储密码（user-dao.js:19-29），userName/firstName/lastName 等入库（后续 /benefits、/allocations 渲染成存储型 XSS 载体）；校验失败路径将错误信息与 userName/firstName/lastName/password/verify/email 未编码回显到 signup.html（pre-auth 反射型 XSS）；成功则 prepareUserData 随机生成 allocations，req.session.regenerate() 后渲染 dashboard。session.js:189-252、index.js:38 |
| GET | /tutorial | anon | None | None（公开） | 教程首页（渲染 a1 页，layout.html 导航）。tutorial.js:8-11、index.js:79（app.use('/tutorial')） |
| GET | /tutorial/a1 | anon | None | None（公开） | 教程页 A1（注入）——纯静态文档渲染（for 循环 pages 展开，tutorial.js:31）。无用户输入处理。 |
| GET | /tutorial/a10 | anon | None | None（公开） | 教程页 A10（未验证的重定向）——静态文档渲染（tutorial.js:31）；页面讲解 /learn 开放重定向。 |
| GET | /tutorial/a2 | anon | None | None（公开） | 教程页 A2（认证缺陷）——静态文档渲染（tutorial.js:31）。 |
| GET | /tutorial/a3 | anon | None | None（公开） | 教程页 A3（XSS）——静态文档渲染（tutorial.js:31）。 |
| GET | /tutorial/a4 | anon | None | None（公开） | 教程页 A4（不安全的直接对象引用）——静态文档渲染（tutorial.js:31）；页面文本讲解 /allocations/:userId 的 IDOR。 |
| GET | /tutorial/a5 | anon | None | None（公开） | 教程页 A5（安全配置错误）——静态文档渲染（tutorial.js:31）。 |
| GET | /tutorial/a6 | anon | None | None（公开） | 教程页 A6（敏感数据暴露）——静态文档渲染（tutorial.js:31）。 |
| GET | /tutorial/a7 | anon | None | None（公开） | 教程页 A7（功能级访问控制缺失）——静态文档渲染；⚠️ a7.html:31 明文泄露 admin/Admin_123 凭据，且讲解 /benefits 的 BFLA（isAdmin 绑定被注释）——公开页面即 vertical 提权入口。tutorial.js:31 |
| GET | /tutorial/a8 | anon | None | None（公开） | 教程页 A8（CSRF）——静态文档渲染（tutorial.js:31）。 |
| GET | /tutorial/a9 | anon | None | None（公开） | 教程页 A9（不安全组件）——静态文档渲染（tutorial.js:31）。 |
| GET | /tutorial/redos | anon | None | None（公开） | 教程页 ReDoS（正则拒绝服务）——静态文档渲染（tutorial.js:31）；讲解 profile.js:47 的 /([0-9]+)+#/ 灾难性回溯。 |
| GET | /tutorial/ssrf | anon | None | None（公开） | 教程页 SSRF（服务端请求伪造）——静态文档渲染（tutorial.js:31）；页面明文讲解 /research 的 needle.get SSRF 与内网利用。 |

## 5. Potential Input Vectors for Vulnerability Analysis

- **URL Parameters:**
  - ?url= @ GET /learn（index.js:72）— 开放重定向目标（未校验协议/域名）
  - ?url= @ GET /research（research.js:15）— SSRF 前缀拼接
  - ?symbol= @ GET /research（research.js:14）— SSRF 符号拼接（触发条件 req.query.symbol）
  - /allocations/:userId（路径参数）@ allocations.js:17,22 — IDOR 对象标识，且回显到模板属性上下文
  - ?threshold= @ GET /allocations/:userId（allocations-dao.js:62-78）— NoSQL $where JS 注入（this.stocks > '${threshold}'）
  - /tutorial/{page}（路径段，a1..a10/redos/ssrf 12 页）@ tutorial.js:31 — 无输入处理
- **POST Body Fields (JSON/Form):**
  - userName, password @ POST /login（session.js:75-77）— 明文比对，userName 失败回显（pre-auth 反射 XSS）
  - email, userName, firstName, lastName, password, verify @ POST /signup（session.js:189-203）— 明文密码入库，失败回显（pre-auth 反射 XSS）；firstName/lastName 存库成为存储 XSS 载体
  - firstName, lastName, ssn, dob, address, bankAcc, bankRouting @ POST /profile（profile.js:40-46）— ReDoS 输入 + 反射/存储 XSS；PII 明文
  - preTax, afterTax, roth @ POST /contributions（contributions.js:32-34）— eval() SSJS/RCE 注入面
  - userId, benefitStartDate @ POST /benefits（benefits.js:31-32）— BFLA + 水平越权（任意员工福利修改）
  - memo @ POST /memos（memos.js:11）— 存储型 XSS 写入（marked 渲染）
- **HTTP Headers:**
  - Cookie: connect.sid（express-session 会话标识；httpOnly/secure/sameSite 均未启用 server.js:92-100 → 可被 JS 读取、明文 HTTP 传输、无过期；secret 硬编码 config.js:9 可离线伪造会话）
  - （应用无自定义业务头消费；未读取 X-Forwarded-For/Host 等）
- **Cookie Values:**
  - connect.sid（唯一会话 cookie；secret='session_cookie_secret_key_here' 硬编码，express-session 默认签名；saveUninitialized:true 会为未登录访客也签发会话，扩大可伪造/固定面）

## 6. Network & Interaction Map

### 6.1 Entities

| Title | Type | Zone | Tech | Data | Notes |
| --- | --- | --- | --- | --- | --- |
| BrowserUser | ExternAsset | Internet | Web 浏览器（swig 服务端渲染页面） | PII | 匿名与已登录终端用户；无客户端框架/鉴权库 |
| InternalResources | ExternAsset | App | SSRF 可达的内部/云目标（TCP/HTTP） | Secrets | 因 /research 出站请求零校验，可触达：云元数据 169.254.169.254、docker 网络内 mongo:27017、任意内网服务 |
| MongoDB | DataStore | Data | MongoDB 4.4（mongodb 驱动 2.x） | PII, Secrets | 存储 users（明文密码 + isAdmin）、profiles（SSN/DOB/银行账号明文）、memos、allocations、contributions |
| NodeGoatWebApp | Service | App | Node.js 12 / Express 4.13 / swig(autoescape:false) / express-session | PII, Tokens, Secrets | 单进程 http.createServer 直连 :4000；无 TLS、无反向代理/WAF、无安全响应头（helmet/csurf 注释）；模板全局不转义 |
| YahooFinance | ThirdParty | ThirdParty | 外部行情端点（HTTPS） | Public | https://finance.yahoo.com/quote/ — /research 的 needle.get 白名单前缀（仅前端 hidden input 提示，服务端不强制） |

### 6.2 Entity Metadata

| Title | Metadata |
| --- | --- |
| BrowserUser | Access: HTTP :4000; Auth: connect.sid cookie |
| InternalResources | ReachableVia: /research SSRF（needle.get，默认跟随 30x）; Examples: 169.254.169.254/latest/meta-data/, mongo:27017, localhost 任意端口 |
| MongoDB | Engine: MongoDB 4.4; Exposure: 仅 docker-compose 内部网络 mongo:27017（端口未发布到宿主机）; Consumers: NodeGoatWebApp（app/data/*-dao.js）; Credentials: 无认证（默认连接串 mongodb://localhost:27017/nodegoat） |
| NodeGoatWebApp | Hosts: 0.0.0.0:4000（无网关直连）; Endpoints: 18 个 app 级 + 13 个 /tutorial 路由（见 §4）; Auth: express-session，cookieSecret 硬编码 'session_cookie_secret_key_here'; Dependencies: marked 0.3.5, needle 2.2.4, node-esapi, mongodb 2.x, swig 1.4.2 |
| YahooFinance | Endpoints: https://finance.yahoo.com/quote/{symbol}; Protocol: HTTPS :443 |

### 6.3 Flows (Connections)

| FROM → TO | Channel | Path/Port | Guards | Touches |
| --- | --- | --- | --- | --- |
| BrowserUser → NodeGoatWebApp | HTTP | :4000 /、/login、/signup、/logout、/tutorial/* |  | Public |
| BrowserUser → NodeGoatWebApp | HTTP | :4000 /dashboard、/profile、/contributions、/benefits、/allocations/:userId、/memos、/research、/learn | auth:session | PII, Tokens |
| NodeGoatWebApp → BrowserUser | HTTP | :4000 302 重定向（/learn?url= 开放重定向） |  | Public |
| NodeGoatWebApp → InternalResources | TCP | 任意端口/路径（SSRF 面，needle.get） |  | Secrets |
| NodeGoatWebApp → MongoDB | TCP | :27017 | db-internal-only | PII, Secrets |
| NodeGoatWebApp → YahooFinance | HTTPS | :443 /quote/{symbol} |  | Public |

### 6.4 Guards Directory

| Guard Name | Category | Statement |
| --- | --- | --- |
| auth:session | Auth | isLoggedInMiddleware（session.js:36-43）要求 req.session.userId 存在，否则 302 /login；保护所有需登录路由。 |
| auth:admin | Authorization | isAdminUserMiddleware（session.js:25-35）要求 DB 中 user.isAdmin === true；【已定义但未绑定】到 /benefits（index.js:58-59 被注释）→ 当前对任何路由不生效。 |
| db-internal-only | Network | MongoDB 仅绑定 docker-compose 内部网络（mongo:27017 expose，不发布到宿主机端口）；但 /research SSRF 可从应用容器内触达该内部网络。 |

## 7. Role & Privilege Architecture

### 7.1 Discovered Roles

| Role Name | Privilege Level | Scope/Domain | Code Implementation |
| --- | --- | --- | --- |
| admin | 10 | Global | session.js:25-35（isAdminUserMiddleware，DB 实时查 user.isAdmin）；【未绑定】任何路由（index.js:58-59 被注释） |
| anon | 0 | Global | session.js:44-51（displayLoginPage）、:53-119（handleLoginRequest）、:125-135（displaySignupPage）、:189-252（handleSignup）；displayWelcomePage 内自检 session.js:259-262 |
| user | 1 | Global | session.js:36-43（isLoggedInMiddleware）绑定于 index.js:44-76 各需登录路由 |

### 7.2 Privilege Lattice

```
Privilege Ordering (→ means "can access resources of"):
anon → user → admin（admin 覆盖 user 全部能力；/benefits 意向为 admin 专属但实际仅 user 级 isLoggedIn 保护 → 实际可达权限高于设计）

Parallel Isolation (|| means "not ordered relative to each other"):
无平行角色；所有普通用户同属 user 档位，且对象级隔离缺失（/allocations/:userId 任意用户 ID、/benefits body userId）→ user 内部存在水平越权面。
```

### 7.3 Role Entry Points

| Role | Default Landing Page | Accessible Route Patterns | Authentication Method |
| --- | --- | --- | --- |
| admin | /benefits（登录后 302，session.js:117） | 全部 user 路由, /benefits（意向为 admin 专属，实际仅 isLoggedIn 保护） | Session/JWT + DB isAdmin 字段（seed artifacts/db-reset.js admin/Admin_123） |
| anon | /login | /, /login, /signup, /logout, /tutorial, /tutorial/{a1..a10,redos,ssrf} | None |
| user | /dashboard | /dashboard, /profile, /contributions, /allocations/:userId, /memos, /research, /learn, /benefits（BFLA 越权可达） | Session/JWT（connect.sid cookie） |

### 7.4 Role-to-Code Mapping

| Role | Middleware/Guards | Permission Checks | Storage Location |
| --- | --- | --- | --- |
| admin | isAdminUserMiddleware（定义但未应用 → 管理面实际无角色防护） | DB users.isAdmin === true（仅 isAdminUserMiddleware 内部逻辑，未触发） | MongoDB users.isAdmin（布尔）；种子数据预置 |
| anon | 无（公开路由，无中间件） | 无（POST /login、POST /signup 为匿名可触达的认证入口；/ 内部自检重定向） | N/A |
| user | isLoggedInMiddleware | req.session.userId 存在（无角色/作用域细分） | session.userId（服务端内存 session；角色不存于会话） |

## 8. Authorization Vulnerability Candidates

### 8.1 Horizontal Privilege Escalation Candidates

| Priority | Endpoint Pattern | Object ID Parameter | Data Type | Sensitivity |
| --- | --- | --- | --- | --- |
| High | GET /allocations/{userId} | userId | financial | 任意登录用户可查看其他用户（含 admin）的资产配置明细（stocks/funds/bonds）与用户名；对象 ID 直接取自 URL 路径（IDOR），无所有权校验。allocations.js:17,22 |
| Low | GET /contributions | None（来自会话） | financial | 对象取自会话（无水平越权），但渲染含用户 firstName/lastName，配合 autoescape:false 可成为 XSS 落地页。contributions.js:12-26 |
| Medium | POST /benefits | userId（body） | admin_config | 任意登录用户可按 body userId 修改任意员工 benefitStartDate（BFLA 叠加水平越权）；无 CSRF。benefits.js:31-32 |
| Medium | GET /benefits | None（全量列表） | user_data | 批量暴露所有非管理员用户的 _id/firstName/lastName/benefitStartDate（PII）；仅 isLoggedIn 保护（BFLA）。benefits.js:15-22 |
| Medium | GET /memos | None（全局共享） | user_data | memos 为全局集合，任何登录用户可见全部备忘录；且 marked() 渲染使存储型 XSS 波及所有查看者。memos-dao.js:20-30、memos.html:31 |

### 8.2 Vertical Privilege Escalation Candidates

| Target Role | Endpoint Pattern | Functionality | Risk Level |
| --- | --- | --- | --- |
| admin | GET /benefits | 福利管理页面（本应仅管理员）：列出全部非管理员员工 PII | High |
| admin | POST /benefits | 修改任意员工福利起始日（本应仅管理员） | High |
| admin | GET /tutorial/a7 | 公开教程页明文泄露 admin/Admin_123 凭据 → 直接以 admin 身份登录（垂直提权入口） | High |
| admin | POST /signup + GET /dashboard | 自注册获取普通 user 会话后，横向结合 /benefits 与 /allocations 越权（间接提权链） | Medium |

### 8.3 Context-Based Authorization Candidates

| Workflow | Endpoint | Expected Prior State | Bypass Potential |
| --- | --- | --- | --- |
| 个人资料更新 | POST /profile | bankRouting 应匹配 /([0-9]+)+#/（数字+# 后缀） | 灾难性回溯 ReDoS：提交超长数字串（如大量 9 后跟 #）触发指数级匹配耗尽 CPU（单线程阻塞全部请求）。profile.js:47-51 |
| 学习资源跳转 | GET /learn | url 应指向可信学习站点 | 任意 url → 开放重定向，用于钓鱼/恶意站点跳转。index.js:72 |
| 缴款更新 | POST /contributions | preTax/afterTax/roth 应为数字（前端意图经 parseInt 解析） | eval() 在 isNaN 校验之前执行任意 JavaScript（服务端 RCE）；校验存在但发生在执行之后，形同虚设。contributions.js:32-34 |
| 行情查询 | GET /research | url 应固定为 https://finance.yahoo.com/quote/ 白名单前缀 | 客户端直接改写 url 参数 → SSRF：访问云元数据 169.254.169.254、内网 mongo:27017、任意 TCP/HTTP 目标，响应体回显。research.js:14-26 |

## 9. Injection Sources

- **Command Injection:**
  - `eval()` — app/routes/contributions.js:32-34 (POST /contributions 的 preTax/afterTax/roth 直接 eval → 服务端任意 JS 执行（SSJS/RCE）。需登录会话（isLoggedIn）。校验（isNaN、30% 上限）在 eval 之后执行，无法阻止代码执行。)
- **SQL Injection:**
  - `$where（MongoDB JS 引擎）` — app/data/allocations-dao.js:78 (GET /allocations/:userId?threshold= 的 threshold 未净化即拼入 `$where: "this.userId == ${parsedUserId} && this.stocks > '${threshold}'"` → NoSQL JavaScript 注入（非关系型 SQL；注入面为 MongoDB 服务端 JS 执行，可注入 0';while(true){}' 等）。修复代码（parseInt + 0-99 校验）被注释在 allocations-dao.js:64-77。需登录会话。)
- **LFI/RFI:**
  *(scanned, no sources of this kind found)*
- **Path Traversal:**
  *(scanned, no sources of this kind found)*
- **SSTI:**
  - `marked(doc.memo)（markdown→HTML）` — app/views/memos.html:31 (存储型 XSS sink：POST /memos 写入的 memo 经 marked 0.3.5（sanitize:true 为可绕过的正则净化）渲染为 HTML，任意登录用户可触发，所有 /memos 查看者受影响。)
- `swig {{ }} 原样输出（autoescape:false）` — app/views/profile.html:41-78 + server.js:137 (POST /profile 校验失败路径把 firstName/lastName/ssn/dob/address/bankAcc/bankRouting 未编码回显到 value 属性（:41-66）与 href（:78，firstName）→ 反射型 XSS；成功路径渲染存库字段 → 存储型 XSS。根因 server.js:134-137 swig autoescape:false。)
- `swig {{userName}}（pre-auth 反射）` — app/views/login.html:110 (POST /login 失败时 userName 未编码回显到 value 属性 → 匿名可触达的反射型 XSS（无需登录，首字节到 sink）。)
- `swig {{ }} 错误信息与输入值回显` — app/views/signup.html:41-91,133 (POST /signup 校验失败把 userName/firstName/lastName/password/verify/email 与错误串未编码渲染 → 匿名反射型 XSS；成功路径存库字段成为后续页面存储型 XSS 载体。)
- `swig {{userId}} 属性上下文` — app/views/allocations.html:15 + app/views/layout.html:56 (GET /allocations/:userId 的 userId 回显到 form action 与导航链接 href → 属性上下文反射型 XSS（需登录）。)
- `swig {{user.firstName/lastName}}` — app/views/benefits.html:47-66 (GET /benefits 渲染所有非管理员用户姓名（未编码）→ 跨用户存储型 XSS（攻击者注册含恶意姓名的账号即可毒化管理员视图）；仅 isLoggedIn 保护（BFLA）。)
- **Deserialization:**
  *(scanned, no sources of this kind found)*
