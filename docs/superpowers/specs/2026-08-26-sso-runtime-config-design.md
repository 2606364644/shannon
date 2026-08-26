# SSO 配置运行时化——设置页可配设计

- 日期：2026-08-26
- 状态：📐 设计完成待实现
- 范围：`packages/web`（后端 auth 子系统 + 前端设置页/用户页）
- 关联：修订 `2026-08-25-sso-auth-design.md`（SSO 认证接入，已实现）——本 spec 将其 §7 的 env 驱动配置模型改为 DB 运行时配置

## 1. 背景与目标

SSO 认证（2026-08-25 spec）已落地，但 5 项配置全部 env 驱动（`WebConfig` 启动时构造 + `lru_cache`），总开关默认关。实际部署（`/app` 容器）的 env 里没有任何 `SUPERNOVA_WEB_SSO_*` 项，结果登录页无 SSO 按钮；要启用必须改部署环境 env 并重启进程——运维摩擦大。

目标：**SSO 全部 5 项配置在 web 设置页直接配置，改完即时生效（无需重启）**；已用 env 配置的部署升级后自动导入不断服。

## 2. 决策记录（已与用户确认）

| 决策点 | 结论 |
|---|---|
| 配置范围 | **全部 5 项进设置页**（总开关 + AUTH_DOMAIN + PUBLIC_BASE_URL + PASSPORT_BASE + 会话时长），存 auth.db |
| env 与 DB 关系 | **env 只做首次种子**：首启表空且 env 有值自动种入；此后 DB（设置页）是唯一运行时真相，env 改了不再生效 |
| 面板组织 | **SSO 全家集中设置页**：SettingsPage 新增「SSO / OA 登录」section（配置卡 + 白名单面板）；白名单面板从 /users 页迁入，/users 回归纯用户管理 |
| 运行时读取方式 | **每请求直读 DB**（sqlite 单行主键查，与白名单开关 `get_whitelist_enabled` 同模式）；不做内存快照——单实例 uvicorn + 本地 sqlite，直读无一致性坑、开销无感 |

## 3. 非目标

- 不把 WebConfig 整体改成配置中心——只有 SSO 5 项运行时可配，其余（端口/目录/seed 等）仍部署时 env（YAGNI）。
- 不做配置变更审计日志表——`updated_at/updated_by` 回显即可（对齐 `sso_whitelist_state` 先例）。
- 不做「未登录自动跳 OA」等 2026-08-25 spec §3 已列非目标。

## 4. 数据模型（auth.db）

新表 `sso_config` **单行表**（照抄 `sso_whitelist_state` 模式）：

```sql
CREATE TABLE IF NOT EXISTS sso_config (
  id INTEGER PRIMARY KEY CHECK (id=1),
  enabled INTEGER NOT NULL DEFAULT 0,
  auth_domain TEXT NOT NULL DEFAULT '',
  public_base_url TEXT NOT NULL DEFAULT '',
  passport_base TEXT NOT NULL DEFAULT 'https://passport.futuoa.com',
  session_ttl_hours INTEGER NOT NULL DEFAULT 24,
  updated_at TEXT,
  updated_by TEXT
);
```

## 5. 种子逻辑（一次性，env → DB）

`store.ensure_sso_config_seeded()`，启动时（store 初始化路径）执行：表空（`SELECT COUNT(*)==0`）才 INSERT，否则直接返回。

| 首启场景 | 种入值 |
|---|---|
| env 有 SSO 配置（任意一项有值/enabled=1） | env 值种入（一次性；此后 env 失效） |
| env 完全未配（如当前 `/app` 部署） | 默认值：enabled=0、passport_base=`https://passport.futuoa.com`、ttl=24、domain/public_base 空 |
| **坏 env 降级**：env `SSO_ENABLED=1` 但 `AUTH_DOMAIN` 空（旧版启动 fail-fast 场景） | 种 `enabled=0` + warning log——**不崩溃**，保升级平滑 |
| **坏 env 降级**：env `PASSPORT_BASE` 非 https | passport_base 种默认值 + warning log |

**原两个启动 fail-fast（`config.py` RuntimeError）删除**——校验语义迁移到写入动作（§6）。

## 6. 校验语义迁移（启动 fail-fast → PUT 时校验）

`PUT /api/auth/sso/admin/config` 请求校验（任一不过 → 400，不落库）：

1. `passport_base` 任何时候必须 `https://` 开头（spec 2026-08-25 §9 https 强制保留）。
2. `enabled=true` 时 `auth_domain` 必填非空。
3. `public_base_url` 可空（运行时回落 `https://{auth_domain}`）；非空时必须 `http(s)://` 开头。
4. `session_ttl_hours` 整数，1 ≤ ttl ≤ 168。

## 7. 后端 API 与读取

### 7.1 新端点（admin-only）

| 端点 | 行为 |
|---|---|
| `GET /api/auth/sso/admin/config` | 返回 `{enabled, auth_domain, public_base_url, passport_base, session_ttl_hours, updated_at, updated_by}`（`public_base_url` 回显**原始配置值**，可空；回落不落库） |
| `PUT /api/auth/sso/admin/config` | 全量更新 5 项 + §6 校验 + 记 `updated_at/updated_by`（当前登录 admin） |

### 7.2 运行时读取

- `store.get_sso_config()`：单行主键查，返回原始行（seed 保证行恒存在）。
- `sso.py` 新增纯函数 `resolve_runtime(row)`：`public_base_url` 为空 → 回落 `https://{auth_domain}`；产出运行时 5 元组。
- `routes.py` 中 5 处 `cfg.sso_*`（`sso/config`、`sso/login`、`sso/callback`、logout 的 `sso_logout_url`）全部改为 `get_sso_config()` + `resolve_runtime()`。
- 公开 `GET /auth/sso/config` 契约不变（`{enabled}`，enabled 来源改 DB）。
- `sso.py` 现有域函数（`build_passport_login_url` / `validate_ticket` / `build_passport_logout_url`）已参数化，**零改动**。
- 白名单 4 个 API 与 `sso_whitelist_state` 开关不动。

## 8. 前端设计

- **SettingsPage** 新增 section「SSO / OA 登录」（eyebrow：`settings.section.sso`），**仅 admin 渲染**（非 admin 隐藏）：
  - `SsoConfigCard`（新组件）：总开关（Switch）+ 4 字段表单（AUTH_DOMAIN / PUBLIC_BASE_URL / PASSPORT_BASE / SESSION_TTL_HOURS）+ 保存按钮（一次 PUT 全量）；加载 GET admin config 回显；400 校验错误内联提示；`updated_at/updated_by` 回显；PUBLIC_BASE_URL 留空提示「默认 https://{auth_domain}」。
  - `SsoWhitelistPanel`（现有组件，**不改动**）迁入此 section。
- **UsersPage**：删除白名单面板挂载与 import。
- **LoginPage / AuthContext / UserMenu**：零改动（`sso/config` 契约不变）。
- **i18n**：zh/en 补 `settings.section.sso`、`ssoConfig.*` 词条（kebab→camel 陷阱，对齐现有 locales 结构）。

## 9. 兼容与迁移

- 已 env 配置并开启的部署：升级首启自动种入 DB，SSO 不中断（种子后 env 失效——部署者心智：设置页是唯一配置入口）。
- 未配 env 的部署：首启种默认值（enabled=0），admin 后续 UI 配置即开。
- auth.db 经 `CREATE TABLE IF NOT EXISTS` 无损演进，无 schema 破坏。

## 10. 安全考量

- 新端点 admin-only，权限边界与白名单 API 一致。
- `passport_base` 可经 UI 修改不扩大攻击面：admin 本可直接建户/改白名单/提角色（同信任级）；https 强制在 PUT 校验保留。
- 公开 `GET /auth/sso/config` 仍只回 `{enabled}`，不泄露配置细节。
- 5 项配置均非密钥（无 secret），admin 回显无泄露面。

## 11. 测试策略（TDD）

**后端 pytest（`packages/web/tests/`）：**

- store：`ensure_sso_config_seeded` 四分支（env 全有种 env 值 / env 无种默认 / 坏 env enabled 缺 domain 降级 / passport 非 https 降级）；种子幂等（表非空不覆盖）；`get_sso_config` / `update_sso_config`（updated_at/by 写入）。
- `sso.py`：`resolve_runtime` 回落分支（public_base 空/非空）。
- routes：admin config GET/PUT（admin 200、非 admin 403）；PUT 校验链 4 用例（§6 各一条）；**运行时即时生效**——PUT enabled=1 后 `sso/login` 302、PUT enabled=0 后 404，全程无重启；现有 SSO 测试 fixture 从 env 配置改为种 DB。

**前端 vitest：**

- SettingsPage：SSO section 仅 admin 渲染；配置卡加载回显/保存成功/400 错误内联；白名单面板在此 section 挂载。
- UsersPage：删除白名单断言（现有测试改）。
- SsoWhitelistPanel 组件未改动，测试不动。

**范围**：只跑新增/改动相关测试文件（CLAUDE.md 预存挂起约定）。

## 12. 验收标准

1. 未配 env 的部署（如当前 `/app`）：启动后 admin 在设置页填 AUTH_DOMAIN + 打开总开关 → 保存后登录页**立即**出现「使用 OA 账号登录」按钮（无重启）；SSO 全流程走通。
2. 已 env 配置的部署：升级首启自动种入，SSO 行为不断；此后改 env 重启不再影响配置。
3. PUT 坏配置（domain 空开开关 / passport 非 https / ttl 越界）→ 400 且 DB 值不变。
4. 白名单面板在设置页 SSO section 内可用；/users 页不再有白名单块。
5. 账密登录/登出/改密/CSRF/BruteGuard 零回归；非 admin 看不到 SSO section、API 403。
