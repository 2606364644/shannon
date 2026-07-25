# Web 认证与多租户隔离 — 设计文档（P0 认证地基）

- **日期**：2026-07-26
- **分支**：`feat/fork-py`
- **状态**：设计已与用户确认，待 writing-plans 拆实现计划
- **范围标记**：本文档只覆盖 **P0（身份与鉴权地基）**。P1/P2/P3c 为后续独立 spec，仅在末尾概述

---

## 1. 背景

supernova 的 Web 控制台（FastAPI `:7878` 同源 serve SPA）当前**前后端 100% 无鉴权**：无 auth middleware、无 `Depends` 注入、无 login 路由、无 User 模型、无 session/cookie/JWT、前端无登录页/路由守卫/AuthContext/凭证注入。任何能访问 7878 端口的人都能调用全部 API（含删除 workspace、删除仓库、发起扫描、文件系统浏览）。

用户的完整诉求是**多租户隔离平台**：多用户账号体系 + workspace 成员制（admin 给 workspace 分配用户，用户只看被分配的 workspace）+ 产物/仓库/模型配置（.env/profile）全隔离 + 多 workspace 并发扫描。

探查（见 §11 决策记录）确认：完整配置隔离触及 "`os.environ` 作配置总线 + worker 进程级 profile + AuditSession 单例" 等核心架构，难度极高。因此**整体拆为 4 个独立子项目**，各自 spec→plan→实现。本文档是第一个：**P0 认证地基**——一切隔离的前提（"当前用户是谁"）。

## 2. 目标与非目标

### 目标（P0）
- 多用户身份认证：预置账号、登录/登出、会话管理
- 所有 `/api/*` 受保护（除公开端点）
- 前端登录页（claude 风格 shadcn）+ 路由守卫 + 凭证注入 + 401 处理
- 引入 SQLite 存储层（users + sessions），为 P1 成员关系铺路
- 预置账号机制（`users.yaml` + bcrypt hash 工具），无明文密码

### 非目标（P0 不做，留给后续）
- workspace 成员制 / 产物隔离 → **P1**
- repos 隔离 → **P2**
- per-workspace 配置/profile/.env 隔离 → **P3c**
- 用户管理 UI（增删用户、改密界面）→ P1（P0 仅 `users.yaml` 预置 + hash 工具）
- 注册 / 找回密码 → 不做（预置制）
- admin 的具体管理功能 → P0 只埋 `role` 字段 + `require_admin` 依赖可用，功能 P1

## 3. 整体分解（上下文）

```
P0  认证地基（身份）              ← 本文档
P1  workspace 成员制 + 产物隔离   ← 依赖 P0
P2  仓库隔离（repos per-ws）      ← 依赖 P0
P3c 配置隔离（阶段 0→1→2→3→4）    ← 多阶段子工程；用户要并发，阶段4（AuditSession 解耦）必做
```

P0 是所有路线的共同起点。P3c 的阶段 0（配置抽象纯重构）可在 P0 之后较早穿插，不依赖认证。

## 4. 架构设计

### 4.1 凭证/存储机制（已定：方案 A）

服务端 session + HttpOnly Cookie + SQLite。理由：用户要成员制+并发+未来 admin 管理，"可撤销的 server session"是刚需；SQLite 单文件、stdlib 自带、放进现有 bind-mount 目录即持久化，不增部署负担；一次引入，P1 成员关系/P3c workspace 配置元数据可复用同一存储层。用原生 `sqlite3` + 极简迁移（不引 ORM/Alembic）。

### 4.2 组件清单（新增文件）

```
packages/web/src/supernova_web/auth/        ← 新模块
  store.py        SQLite 存储层 + 极简迁移（CREATE TABLE IF NOT EXISTS + schema_meta）
  models.py       User / Session (pydantic)
  passwords.py    passlib[bcrypt] 哈希/校验
  session.py      session 创建/校验/撤销/过期清理
  middleware.py   auth middleware（cookie→session→current_user 注入）
  csrf.py         double-submit CSRF token 校验
  routes.py       /api/auth/{csrf,login,logout,me}
  dependencies.py Depends(current_user) / Depends(require_admin)
  brute.py        per-username 登录失败计数 + 指数退避/临时锁定
  seed.py         users.yaml → SQLite seed（不覆盖已存在用户）

packages/web/frontend/src/auth/
  AuthContext.tsx   {user, loading, login, logout}
  RequireAuth.tsx   路由守卫
  useAuth.ts        hook
packages/web/frontend/src/pages/LoginPage.tsx
packages/web/frontend/src/components/layout/UserMenu.tsx

scripts/web_hash_password.py   生成 bcrypt hash（给 users.yaml 用）

configs/users.yaml.example      预置账号示例（hash 占位）
```

### 4.3 数据流

**登录：**
```
浏览器 GET /login（公开）
提交   POST /api/auth/login {username,password} + X-CSRF-Token
       后端: brute 检查 → 校验密码(bcrypt) → 建 session 行 →
             Set-Cookie: sid  HttpOnly SameSite=Lax Secure
             Set-Cookie: csrf（非 HttpOnly，前端可读）
       → 返回 {user}
前端   AuthContext 存 user → 跳回 ?next= 原页
```

**鉴权（每个 /api/*）：**
```
middleware 读 cookie sid → 查 sessions 表（含过期判断）→ 注入 request.state.user
路由 Depends(current_user) 要求登录；Depends(require_admin) 要求 admin
写操作(POST/PUT/DELETE) 额外校验 X-CSRF-Token == csrf cookie（double-submit）
```

**登出：**
```
POST /api/auth/logout → 删 sessions 行 → 清 cookie → 即时失效
```

**会话过期：**
```
后台清理任务定期删 expires_at < now 的 sessions 行
middleware 对过期 session 视为未登录（不报错，走 401）
```

## 5. 后端设计

### 5.1 SQLite Schema（单文件 DB）

DB 位置：`<workspaces_dir>/auth.db`（随现有 bind-mount 自动持久化）。

```sql
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,        -- bcrypt
  role TEXT NOT NULL DEFAULT 'user',  -- 'admin' | 'user'
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,                -- secrets.token_urlsafe(32)
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
-- P1 预留：workspace_members(workspace_id, user_id, role)
```

迁移：启动时 `CREATE TABLE IF NOT EXISTS` + `schema_meta` 记版本号。P0 两张业务表足够。

### 5.2 模块职责

| 模块 | 职责 |
|---|---|
| `store.py` | sqlite 连接（`check_same_thread=False` + 线程锁或连接池）、建表、users/sessions CRUD |
| `passwords.py` | `hash_password(pw)` / `verify_password(pw, hash)`，passlib CryptContext bcrypt |
| `session.py` | `create_session(user_id)` → 随机 id + 过期；`verify_session(sid)` → User或None（同时更新 `last_seen_at`）；`revoke_session(sid)`；`purge_expired()` 清过期行（FastAPI startup 起周期 asyncio 任务，verify 时惰性兜底） |
| `middleware.py` | ASGI/HTTP middleware：读 sid cookie → verify_session → 注入 `request.state.user`；放行公开路径 |
| `csrf.py` | 写操作校验 `X-CSRF-Token` header == csrf cookie 值（double-submit） |
| `brute.py` | 内存（或 sqlite 表）per-username 失败计数：N 次失败后指数退避锁定 |
| `routes.py` | login/logout/me/csrf 四个端点 |
| `dependencies.py` | `current_user(request)` → 未登录 401；`require_admin` → 非 admin 403 |
| `seed.py` | 启动时读 `users.yaml`，upsert 不存在的用户（**不动已存在的**，避免重启覆盖改过的密码） |

### 5.3 路由清单

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/api/auth/csrf` | 公开 | 发 csrf token（double-submit） |
| POST | `/api/auth/login` | 公开 + brute | 校验密码→建 session→set cookie |
| POST | `/api/auth/logout` | 登录 | 删 session→清 cookie |
| GET | `/api/auth/me` | 登录 | 返回当前 user（前端启动探测） |
| * | `/api/*`（其余全部） | `Depends(current_user)` | 受保护；admin 操作加 `require_admin` |
| GET | `/health`、`/api/auth/{csrf,login}` | 公开 | 放行 |

### 5.4 WebConfig 新字段（`config.py`）

```python
auth_db_path: str       # SUPERNOVA_WEB_AUTH_DB，默认 "<workspaces_dir>/auth.db"
session_secret: str     # SUPERNOVA_WEB_SESSION_SECRET（签名/加密 session id 用）
session_ttl_hours: int  # 默认 12
cookie_secure: bool     # 默认 True（生产 HTTPS）；本地 HTTP 调试 False
users_seed_file: str    # SUPERNOVA_WEB_USERS_SEED，默认 "configs/users.yaml"
```

沿用现有 `@lru_cache get_config()` + 测试 `_reset_config` fixture 模式（见 `packages/web/tests/conftest.py`）。

### 5.5 预置账号

`configs/users.yaml`（gitignored 敏感文件，bind-mount 进容器；提供 `users.yaml.example`）：

```yaml
users:
  - username: admin
    password_hash: "$2b$12$...."   # bcrypt，无明文
    role: admin
  - username: alice
    password_hash: "$2b$12$...."
    role: user
```

管理员维护流程：跑 `python scripts/web_hash_password.py`（交互输入密码 → 输出 bcrypt hash）→ 粘进 `users.yaml` → 重启 web 容器。启动 `seed_users()`：把 yaml 里不存在于 SQLite 的用户 upsert（不覆盖已存在）。

### 5.6 依赖新增

`packages/web/pyproject.toml`：`passlib[bcrypt]`。`sqlite3` 是 stdlib 不加。

## 6. 前端设计（非视觉）

### 6.1 应用根包 AuthProvider

`main.tsx`（Vite 入口）最外层包住 `RouterProvider`：

```tsx
<AuthProvider>
  <RouterProvider router={router} />
</AuthProvider>
```

### 6.2 AuthContext + useAuth（`src/auth/AuthContext.tsx`）

```ts
type AuthState = {
  user: User | null;          // {id, username, role}
  loading: boolean;           // me 探测中
  login(u, p): Promise<void>; // 失败抛 ApiError 给表单显示
  logout(): Promise<void>;
};
```

- mount 时调 `GET /api/auth/me` 探测——200 存 user、401 置 user=null（**me 的 401 不触发跳转**）
- `login` → `POST /api/auth/login` → 存 user
- `logout` → `POST /api/auth/logout` → 置 user=null

### 6.3 RequireAuth 守卫 + router.tsx 改造

```tsx
function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <FullScreenSpinner />;      // DSF 风格
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  return children;
}
```

`router.tsx`：
- `/login` → `LoginPage`（公开，已登录则 `<Navigate to="/">`）
- 其余业务路由整组包在 `<RequireAuth><AppShell><Outlet/></AppShell></RequireAuth>` 下

### 6.4 fetch client 改造（`src/api/client.ts`）

三处：

```ts
// (a) 带凭证
fetch(`/api${path}`, { credentials: 'include', ... })

// (b) 写操作自动加 CSRF（double-submit：从 csrf cookie 读）
if (['POST','PUT','DELETE'].has(method))
  headers['X-CSRF-Token'] = readCookie('csrf');

// (c) 401 全局处理（解耦：注册式回调，不直接耦合 React）
let onUnauthorized = () => window.location.assign('/login?expired=1');
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn };
// request() 内：if (res.status === 401 && !opts.silent) onUnauthorized();
```

**关键边界**：`GET /api/auth/me` 用 `apiGet('/auth/me', { silent: true })`——me 的 401 不跳转，只让 AuthContext 置 user=null；其它登录态 API 的 401 才触发"会话过期→跳 /login?expired=1"。

### 6.5 TopBar 用户菜单（`src/components/layout/TopBar.tsx`）

右侧（现有 `LanguageSwitcher` + `ThemeToggle` 旁）加 `UserMenu`（shadcn `DropdownMenu`）：
- 触发器：用户名首字母头像 + role badge（`admin` 暖橙 `--c-orange`、`user` 中性灰）
- 下拉：用户名 + 角色（只读）、登出（调 `logout()`）

### 6.6 i18n（`locales/{zh,en}.json`）新增 `auth.*`

```json
"auth": {
  "login": { "title": "登录 Supernova", "username": "用户名", "password": "密码",
             "submit": "登录", "invalid": "用户名或密码错误",
             "welcome": "欢迎回来", "sub": "登录以继续" },
  "logout": "登出",
  "sessionExpired": "会话已过期，请重新登录",
  "sessionTtlHint": "会话有效期 12 小时",
  "role": { "admin": "管理员", "user": "用户" }
}
```

中英双语同步（守 memory：zh 值必须是真中文，不能漏翻成英文）。

## 7. 登录页视觉设计（claude 风格）

**已定决策**（经 visual companion 浏览器 mockup 确认）：
- **布局**：B 分屏（左品牌区 + 右表单）
- **左侧品牌区**：B1 暖深渐变 + logo + 标语
- **主题策略**：A 固定亮色（登录页永远亮暖色，不跟随系统；进应用后跟随系统主题）
- **标题字体**：serif（复用项目 DSF 的 `--font-serif` / IBM Plex Serif）
- **配色**：复用亮主题 DSF token，不硬编码

### 7.1 布局规格

```
┌─────────────────────────────────────────┐
│            │                            │
│  左 45%    │      右 55% 表单            │
│  暖深渐变  │   奶油底 #FAF6F0            │
│            │                            │
│   ✦ logo   │   欢迎回来 (serif)          │
│  Supernova │   登录以继续                │
│  白盒·黑盒 │   [用户名________________] │
│  安全审计  │   [密码__________________] │
│            │   [      登 录      ]      │
│            │   会话有效期 12 小时        │
└─────────────────────────────────────────┘
```

### 7.2 配色与字体（复用 DSF token）

| 元素 | 值 | 备注 |
|---|---|---|
| 左侧渐变 | `linear-gradient(155deg, #2E2520, #4A2E22 55%, #6B3A26)` | 暖深，亮/暗主题都用 |
| 右侧表单底 | `#FAF6F0`（亮主题 `--background`） | 登录页强制亮主题 |
| logo 圆 | `--c-orange` (#D97757) + 环形光晕 `box-shadow` | "✦" 符号居中 |
| 标题 | `--font-serif`（IBM Plex Serif） | "欢迎回来" |
| 输入框 | 白底 `--card` + `--border` | shadcn Input |
| 登录按钮 | `--c-orange` 背景白字 | shadcn Button |
| 正文/次文字 | `--foreground` / `--muted-foreground` | 复用 token |

**明暗策略**：登录页根容器强制亮主题（加 `.light` class 或用亮主题 token），不读 `localStorage.supernova-theme`、不读系统偏好。登录后进入 AppShell 再尊重用户/系统主题（现有 `lib/theme.ts` 机制不变）。

**实现复用**：用 shadcn `Card`/`Input`/`Button`/`Label`（项目已有 `src/components/ui/`）。logo 用 CSS 画圆 + ✦ 字符，或 lucide icon。

## 8. 安全设计（P0 必守清单）

| 项 | 措施 |
|---|---|
| Cookie | `HttpOnly` + `SameSite=Lax` + `Secure`（生产 HTTPS；本地 HTTP 调试 `cookie_secure=False`） |
| CSRF | double-submit token，写操作（POST/PUT/DELETE）强校验 |
| 密码 | bcrypt（passlib），永不存明文；`users.yaml` 只放 hash |
| session id | `secrets.token_urlsafe(32)`；TTL 12h；后台任务定期清理过期行 |
| session_secret | 强随机、env 注入、`.env.example` 文档化 |
| 登录暴力防护 | per-username 失败计数 + 指数退避/临时锁定（`brute.py`） |
| users.yaml | 进 `.gitignore`、bind-mount 进容器、提供 `.example` |

## 9. 测试策略

### 后端 pytest（`packages/web/tests/` 新增）
- `test_auth_passwords.py` — bcrypt 哈希/校验、错密拒绝
- `test_auth_session.py` — create/verify/revoke、过期失效
- `test_auth_routes.py` — login 成功/失败、logout 即时失效、me 已登录/未登录 401
- `test_auth_middleware.py` — cookie→user 注入、放行 login/health、CSRF 写操作校验
- `test_auth_seed.py` — users.yaml seed、不覆盖已存在用户
- `test_auth_store.py` — sqlite 建表、`CREATE TABLE IF NOT EXISTS` 幂等、schema_meta 版本
- `test_auth_brute.py` — 失败计数、锁定、成功后重置

**陷阱**（memory）：只跑改动相关测试文件，勿跑全套（Temporal/网络慢测试会 hang）。

### 前端 vitest（`packages/web/frontend/` 新增）
- `AuthContext.test.tsx` — me 探测成功/失败、login/logout、loading 态
- `RequireAuth.test.tsx` — 未登录跳 `/login`、loading 显示 spinner
- `client.test.ts` — `credentials:'include'`、CSRF header 注入、401 silent（me）vs 跳转

**陷阱**（memory）：命令须 `cd packages/web/frontend`（vitest 须显式 cd，cwd 不持久）；Radix Tabs mousedown 激活陷阱（优先受控）。

## 10. 范围边界（P0 不做）

| 项 | 归属 |
|---|---|
| workspace 成员制 / 产物隔离 | P1 |
| repos 隔离 | P2 |
| per-workspace 配置/profile/.env 隔离 | P3c |
| 用户管理 UI（增删用户、改密界面） | P1 |
| 注册 / 找回密码 | 不做（预置制） |
| admin 具体管理功能 | P1（P0 只埋 `role` + `require_admin`） |

## 11. 决策记录

1. **凭证/存储机制选 A**（server session + HttpOnly cookie + SQLite）：可即时撤销、为 P1/P3c 铺存储地基、SQLite 单文件零部署负担。否决签名 cookie（无法撤销，与成员制管理冲突）与 JWT（同源单实例过度设计、refresh 复杂）。
2. **整体分解为 P0/P1/P2/P3c**：完整配置隔离（用户原话"最好 .env 各项都隔离"）经探查确认难度极高（要拆 `os.environ` 配置总线 + 动 worker/activity 全链 + AuditSession 单例），不宜一个 spec 装。P3c 再分阶段 0-4。
3. **登录页视觉**：B 分屏 + B1 暖深渐变标语 + A 固定亮色 + serif 标题——经 visual companion 浏览器 mockup 三轮确认（布局三选一→左侧品牌区三选一→主题策略三选一）。
4. **预置账号用 `users.yaml` + bcrypt hash 工具**：符合用户选的"管理员预置 + 配置文件维护"，无明文密码（hash 由 `web_hash_password.py` 生成）。
5. **workspace 成员制（非 owner 隔离）**：用户明确要"给工作区分配用户"，是成员多对多模型，非 owner 独占。属 P1。
6. **并发需求**：用户确认要"多 workspace 同时扫描"——P3c 阶段 4（AuditSession 解耦 + worker 多 queue）必做。

## 12. 后续子项目概述（非本文档范围）

- **P1 — workspace 成员制 + 产物隔离**：`workspace_members` 多对多表；admin 分配成员 API/UI；workspace 产物 API（列表/报告/日志/交付物/events SSE）按成员过滤；发起扫描创建 workspace 时创建者=该 workspace manager。SQLite 已在 P0 引入，直接加表。
- **P2 — 仓库隔离**：repos 从全局目录 → per-workspace 物理分目录；clone/pull/checkout/delete API 加 workspace 上下文；前端 repos 页按 workspace 切换。
- **P3c — 配置隔离（阶段 0→4）**：0 配置抽象地基（RuntimeConfig 纯重构）；1 profile-as-dict 解析器；2 per-workspace 配置存储 + workflow 参数化；3 activity 改造（白盒 30+ / 黑盒 5 处 `run_claude_prompt` 传 `provider_config`）；4 AuditSession 解耦 + worker 多 queue（解锁并发）。详见 P3c 探查报告（对话记录）。

---

**下一步**：本文档经用户审阅后，调用 `superpowers:writing-plans` 拆 P0 实现计划（task 粒度、TDD、验证点）。
