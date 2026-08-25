# SSO 认证接入（富途 OA passport）设计

- 日期：2026-08-25
- 状态：📐 设计完成待实现
- 范围：`packages/web`（后端 auth 子系统 + 前端登录/用户展示）
- 关联：无前序 spec（现有账号密码认证为 P0 已落地能力，见 `packages/web/src/supernova_web/auth/`）

## 1. 背景与目标

现有 web 控制台仅支持账号密码登录（`POST /api/auth/login` + `sn-sid` 会话）。目标：并行接入富途 OA SSO（passport.futuoa.com ticket 校验），**两种认证方式共存**，登录页提供「使用 OA 账号登录」按钮；SSO 用户经 nick 白名单管控，首次登录自动建户；顶栏展示 OA 头像与昵称。

### OA 协议要点（接入方约定）

1. **登录跳转**：`https://passport.futuoa.com/site/login.html?returnUrl=https://AUTH_DOMAIN/xxx`（returnUrl 须 URL 编码）。
2. **ticket 校验**：OA 登录成功后 302 回 returnUrl 并附 `AUTH_TICKET` 参数；服务端 GET
   `https://passport.futuoa.com/api/v1/validateTicket?authTicket=AUTH_TICKET&authDomain=AUTH_DOMAIN`，
   响应形如：
   ```json
   {"result":0,"code":0,"message":"success","data":{
     "oaToken":"xxx","oaTokenInitTime":1787209979,"oaTokenInvalidTime":1787267579,
     "userInfo":{"uid":8537,"lang":"zh-cn","nick":"xxx","name":"xxx","avatarUrl":"https://..."}}}
   ```
   验证 `oaTokenInitTime <= now < oaTokenInvalidTime`；提取 `userInfo.nick` 作用户名、`userInfo.avatarUrl` 作头像（浏览器直连加载，服务端不代理）。
3. **登出**：清本系统登录态后跳 `https://passport.futuoa.com/site/logout.html?returnUrl=xxx`。

### AUTH_DOMAIN 概念

`AUTH_DOMAIN` 是**接入方（本系统）的外部访问域名**，非 passport 域名：

- returnUrl 的 host——告诉 OA 登录后跳回哪里；
- validateTicket 的 `authDomain` 参数——passport 核验 ticket 签发给哪个域（跨站重放防护，域名需在 OA 侧登记备案）。

## 2. 决策记录（已与用户确认）

| 决策点 | 结论 |
|---|---|
| SSO 用户与本地用户关系 | **白名单内 JIT 自动建户**（默认 role=user）；admin 在 /users 页提角色/分配工作区，与现有体系完全兼容 |
| 登录入口形态 | **登录页共存两种方式**：账密表单保留 + SSO 按钮；未登录访问受保护页照旧跳 `/login`，不自动跳 OA |
| 白名单管理 | **/users 页（admin-only）新增「SSO 白名单」管理块**，存 auth.db 新表 |
| 流程架构 | **后端主导 302 链**：安全逻辑（ticket 校验/有效期/防重放/白名单）全在服务端，ticket 不进前端路由与 SPA 历史 |
| SSO 会话时长 | 24h（用户要求 cookie 有效期 1 天），账密会话维持 12h 不变 |
| 登出 returnUrl | 指向本系统登录页 `https://AUTH_DOMAIN/login` |

## 3. 非目标

- 不做「未登录自动 302 跳 OA」（三档模式 redirect 档），后续需要再加 env 档位。
- 不做 OA 侧单点登出通知（OA 登出不会反向吊销我们的 session；我们的 session 独立 24h 过期）。
- 不做服务端代理/缓存头像图片（明确禁止：浏览器直连 CDN）。
- 不改造现有账密登录/CSRF/BruteGuard 行为（零回归）。

## 4. 总体流程

```
浏览器                    我们的后端                          passport.futuoa.com
  │ ①点击「OA 登录」按钮      │                                    │
  ├─GET /api/auth/sso/login?next=/p/xx ──────────────────────────▶│
  │                         │ ②302 → login.html?returnUrl=…      │
  │◀── https://AUTH_DOMAIN/api/auth/sso/callback?next=%2Fp%2Fxx（URL 编码）
  ├──────────────────────────────────────────────────────────────▶│ 用户在 OA 输凭证
  │ ③OA 登录成功，302 回调并附 AUTH_TICKET                          │
  ├─GET /api/auth/sso/callback?AUTH_TICKET=xxx&next=/p/xx ────────▶│
  │                         │ ④服务端 GET validateTicket（httpx，10s 超时）
  │                         │ ⑤校验链（§5.2）                      │
  │                         │ ⑥JIT 建户 + 建 session + Set-Cookie  │
  │◀──302 next + sn-sid(24h) + sn-csrf ───────────────────────────│
```

登出：前端 `POST /api/auth/logout`（现有 CSRF 保护端点）→ 响应含 `sso_logout_url` 时前端 `window.location.assign` 跳 OA 登出 → OA 登出后 returnUrl 回 `/login`。

## 5. 后端设计

### 5.1 文件组织

- **新增 `auth/sso.py`**：SSO 域逻辑——passport 客户端（validateTicket 调用，httpx `MockTransport` 可注入）、响应解析与有效期校验、returnUrl/logout URL 拼接、`next` 安全校验。纯逻辑为主，便于单测。
- **`auth/routes.py` 扩展**：SSO 端点 + 白名单管理端点（沿用该文件的 `_cookie_secure`/`_cookie_kwargs` cookie 工具）。
- **`auth/store.py` 扩展**：新表/新列的 CRUD。
- **`auth/session.py` 扩展**：`create(user_id, ttl_hours=None)` 可选参数，None 落默认 `self.ttl`。
- **`config.py` 扩展**：SSO env 配置（§7）。

### 5.2 端点与校验链

| 端点 | 权限 | 行为 |
|---|---|---|
| `GET /api/auth/sso/config` | 公开 | `{enabled}`——前端据此渲染 SSO 按钮 |
| `GET /api/auth/sso/login?next=<path>` | 公开 | 校验 next（§5.3）→ 302 passport login.html，returnUrl=`{public_base}/api/auth/sso/callback?next={enc}` 整体 URL 编码 |
| `GET /api/auth/sso/callback?AUTH_TICKET=..&next=..` | 公开 | 校验链全过 → 建 session → 302 next；任一失败 → 302 `/login?sso_error=<code>` |
| `POST /api/auth/logout`（现有，扩展） | 登录 | 响应加可选 `sso_logout_url`（仅 session `auth_method='sso'` 且 SSO 开启） |
| `GET /api/auth/sso/whitelist` | admin | `{whitelist: [nick,...]}` |
| `POST /api/auth/sso/whitelist` | admin | body `{nick}` 增白名单（重复幂等 200） |
| `DELETE /api/auth/sso/whitelist/{nick}` | admin | 删除 |

**callback 校验链（顺序，失败即 302 `/login?sso_error=<code>`）：**

1. `sso_enabled` 为真，否则 404（端点不存在语义，不泄露开关状态细节）。
2. `AUTH_TICKET` 参数存在（假设：参数名字面为 `AUTH_TICKET`，见 §10 假设）。
3. **防重放**：ticket 未在 `sso_used_tickets` 表中（校验通过后写入；清理挂到现有周期任务 `purge_expired_sessions` 顺带执行，删 >24h 记录）。
4. validateTicket HTTP 200 且 `result==0 && code==0` 且 `data.userInfo.nick` 非空。
5. **有效期**：`oaTokenInitTime <= now < oaTokenInvalidTime`（服务器 UTC，Unix 秒）。
6. **白名单**：`nick ∈ sso_whitelist`，否则 `sso_error=not_whitelisted`。

**成功路径：**

- JIT 建户：`get_user_by_username(nick)` 未命中 → `create_user(nick, 随机不可逆密码 hash, role="user", auth_provider="sso")`。随机 hash = `secrets.token_urlsafe(32)` 再 hash——SSO 户**无法**走账密登录（随机串不可知）。二次登录不重复建户（幂等）。
- 每次登录 upsert `users.avatar_url`（OA 头像可能变更）。
- `SessionManager.create(user_id, ttl_hours=cfg.sso_session_ttl_hours)`；sessions 行记 `auth_method='sso'`。
- 响应：302 next + `Set-Cookie: sn-sid`（`httponly; samesite=lax; secure=<按 scheme>; max_age=24h; path=/`，复用 `_cookie_kwargs` 逻辑但 max_age 用 SSO TTL）+ 续签 `sn-csrf`（非 httponly，对齐现有登录）。
- 记录 ticket 至 `sso_used_tickets`。

### 5.3 `next` 安全校验（防 open redirect）

仅允许站内相对路径：非空、以 `/` 开头、不以 `//` 或 `/\` 开头（协议相对/反斜杠绕过）。不合法一律回落 `/`。`sso/login` 与 `sso/callback` 两处都校验（callback 的 next 来自我们签发的 returnUrl，但仍防御性校验）。

## 6. 数据模型（auth.db）

对齐现有「ALTER TABLE 幂等补列 + OperationalError 吞掉」模式：

```sql
ALTER TABLE users ADD COLUMN avatar_url TEXT;                       -- SSO 头像（可空）
ALTER TABLE users ADD COLUMN auth_provider TEXT DEFAULT 'password'; -- 'password'|'sso'（信息性）
ALTER TABLE sessions ADD COLUMN auth_method TEXT DEFAULT 'password';

CREATE TABLE IF NOT EXISTS sso_whitelist (
  nick TEXT PRIMARY KEY,
  added_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sso_used_tickets (
  ticket TEXT PRIMARY KEY,
  used_at TEXT NOT NULL
);
```

`store.py` 新增：`get/add/remove/list_sso_whitelist`、`is_nick_whitelisted`、`mark_ticket_used`/`is_ticket_used`/`purge_used_tickets`、`update_avatar`、补列迁移。`User` model 加 `avatar_url: str | None`、`auth_provider: str = "password"`。

## 7. 配置（config.py，env 驱动）

| env | 默认 | 说明 |
|---|---|---|
| `SUPERNOVA_WEB_SSO_ENABLED` | `0` | 总开关；关闭时 SSO 端点 404、前端不显示按钮 |
| `SUPERNOVA_WEB_SSO_AUTH_DOMAIN` | 空 | **裸域名**（如 `codescan.futu5.com`）；传 validateTicket 的 `authDomain`。需在 OA 侧登记 |
| `SUPERNOVA_WEB_SSO_PUBLIC_BASE_URL` | `https://{auth_domain}` | returnUrl 拼接用完整 origin；内网 http 部署时覆盖 |
| `SUPERNOVA_WEB_SSO_PASSPORT_BASE` | `https://passport.futuoa.com` | passport 基址 |
| `SUPERNOVA_WEB_SSO_SESSION_TTL_HOURS` | `24` | SSO 会话时长（sn-sid max_age 同步） |

**fail-fast**：`sso_enabled=1` 且 `sso_auth_domain` 为空 → 启动报错（对齐 env-loader 风格）。

## 8. 前端设计（基于现有登录页改造，复用 shadcn 组件）

- **LoginPage**：账密表单下方加分隔线 + 「使用 OA 账号登录」按钮（仅 `sso/config.enabled` 时渲染）→ `window.location.assign('/api/auth/sso/login?next=' + encodeURIComponent(next))`。解析 `?sso_error=` 显示 i18n 错误文案（`not_whitelisted` → 「账号未授权，请联系管理员开通」；其余 → 通用 SSO 失败文案）。
- **AuthContext**：`AuthUser` 加 `avatar_url?: string | null`；`logout()` 若响应含 `sso_logout_url` → `window.location.assign`（OA 登出后回 `/login`），否则维持现状。
- **UserMenu**：有 `avatar_url` → 圆形 `<img src referrerPolicy="no-referrer">`（浏览器直连 CDN，服务端零参与）；无 → 现状首字母回退。用户名即 nick，无需改。
- **UsersPage**：新增「SSO 白名单」管理块：nick 输入 + 添加 + 列表删除；SSO 关闭时显示「未启用」提示块。
- **i18n**：zh/en 新增键（注意 kebab→camel 转换陷阱，对齐现有 locales 结构）。

## 9. 安全设计汇总

| 项 | 措施 |
|---|---|
| ticket 传输 | 仅服务端调用 validateTicket；ticket 不整串写日志（掩码前 8 字符）；ticket 不进前端路由/SPA 历史 |
| 防重放 | `sso_used_tickets` 表一次性消费 |
| open redirect | `next` 白名单式校验（§5.3） |
| cookie | `httponly + samesite=lax + secure（X-Forwarded-Proto 感知）+ max_age=24h + path=/` |
| 白名单信息泄露 | 拒绝文案统一「账号未授权」，不回显白名单内容 |
| SSO 户账密面 | JIT 建户密码为随机不可逆 hash，SSO 户不可走 `/login` 账密路径 |
| validateTicket 调用 | 强制 https（passport base 校验）+ 10s 超时 + 校验 `result`/`code`/时间窗 |
| 头像 | 服务端不 fetch 头像 URL（防 SSRF）；浏览器 `<img>` 直连 + `referrerPolicy="no-referrer"` |
| CSRF | callback 为 GET 302 建会话（OAuth2 授权码同型，无写操作面）；登出复用现有 CSRF 保护的 POST `/logout`，不引入裸 GET 登出端点 |

## 10. 假设与开放问题

- **假设 1**：回调参数名字面为 `AUTH_TICKET`（用户提供协议原文如此）。若实际为大写/下划线变体，改 callback 端点的 query 解析即可（单点）。
- **假设 2**：`SUPERNOVA_WEB_SSO_AUTH_DOMAIN` 需 OA 管理员登记后方可通过 validateTicket 校验；具体取值部署时确认。
- **假设 3**：OA `nick` 具备人类可读唯一性（同一 nick 视为同一人）。若 OA 侧 nick 可重名，需改用 `uid` 做用户键（当前按用户指定用 nick，白名单/建户均以 nick 为键）。
- 开放：多 worker 部署时内存态不可用——本设计全落 SQLite，无此问题。

## 11. 测试策略（TDD）

**后端 pytest（`packages/web/tests/` 新文件，httpx `MockTransport` 注入）：**

- `sso/login`：302 目标 URL 与 returnUrl 双层 URL 编码正确性；非法 next 回落 `/`；SSO 关闭 404。
- `sso/callback` 分支：成功（建户+session+cookie 属性断言：httponly/samesite/secure/max_age）；白名单拒绝；token 过期/未生效；`result≠0`；nick 缺失；网络/超时失败；ticket 重放拒绝；缺 ticket。
- JIT 幂等：二次登录不重复建户、avatar_url 更新。
- whitelist CRUD：admin 增删查、非 admin 403、重复添加幂等。
- logout：SSO 会话返回 `sso_logout_url`（URL 编码正确）；账密会话无该字段。
- `next` open redirect 用例：`//evil.com`、`/\evil.com`、`https://evil.com`、空。
- 配置：enabled 但缺 auth_domain → 启动 fail-fast。

**前端 vitest：**

- LoginPage：enabled 渲染 SSO 按钮 / disabled 不渲染；`sso_error` 文案。
- UserMenu：avatar_url 显示 `<img>`；无 avatar 首字母回退。
- AuthContext：logout 响应含 `sso_logout_url` 时跳转、不含时现状。
- UsersPage：白名单块增删交互。

**范围**：只跑新增/改动相关测试文件（CLAUDE.md 预存挂起约定）。

## 12. 验收标准

1. `SUPERNOVA_WEB_SSO_ENABLED=0`：全系统行为与现状逐字节一致（SSO 端点 404、前端无按钮）。
2. 开启后：登录页两方式并存；SSO 全流程（跳 OA → 回调 → 白名单内建户 → 顶栏 nick+头像 → 登出跳 OA 登出回 /login）走通。
3. 白名单外 nick 被拒且不建户；ticket 重放被拒。
4. 账密登录/登出/改密/CSRF/BruteGuard 零回归。
