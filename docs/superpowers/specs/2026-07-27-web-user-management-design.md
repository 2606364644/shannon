# WEB 用户管理页面设计(账号 CRUD + 集中式 ws 成员分配)

> 日期: 2026-07-27 | 分支: feat/fork-py | 状态: 设计待审

## 背景

WEB 当前**无用户管理页面**。调研结论:

- **后端**: `users` 表(`id/username/password_hash/role/created_at/must_change_password`),role 仅 `admin|user` 两档。用户来源 `configs/users.yaml` 启动 seed(已存在不覆盖)。
- **已有 API**: `GET /api/users`(列用户,但用 `current_user` 鉴权——任何登录用户都能列全部用户名)、`POST /api/auth/change-password`(自己改自己)、`login/logout/me/csrf`。
- **store 层**已有 `create_user / list_all_users / update_password / get_user`,**缺** `delete_user / update_role / reset_password / list_user_workspaces_with_role / update_workspace_member_role`。
- **前端**: 页面 Dashboard/Workspaces/ScanNew/Settings/WorkspaceDetail,**无用户管理页**;TopBar 导航无用户入口。
- **工作区成员管理已有**(`api/members.py` + `MemberManagerDialog`),入口在**工作区详情页 header**(`WorkspaceDetail/index.tsx:79`),就近单 ws 操作。两层角色模型: 全局 `users.role`(admin|user) 与 per-ws `workspace_members.role`(manager|member) 正交。
- **`require_admin` 依赖已定义但未用**(users router 用 `current_user`)。

**两个缺口**:
1. admin 对账号的 CRUD(创建/删除/改全局角色/重置密码)——无 UI、无写 API。
2. admin 分配工作区成员要**一个个进 ws 页**——无集中式分配页。

## 目标

1. `/users` 独立页(仅 admin)承载**账号 CRUD**。
2. 同页以**用户为中心**(形态 A)承载**集中式 ws 成员分配**:展开某用户即可勾选加入 ws / 改 ws 角色 / 移除,一次操作一个用户的全部 ws 归属。
3. 收紧 `GET /api/users` 到 admin(信息泄露止血),配套 `MemberManagerDialog` 改手输 username。

## 非目标(YAGNI)

- 不扩角色(仍是 `admin|user`);不做密码复杂规则(仅复用 ≥8 + ≠旧)。
- 不做操作审计表;不做矩阵视图;不做"多用户同时批量分配"(一次一个用户)。
- 不挪工作区成员管理(`MemberManagerDialog` 保留在工作区页,就近单 ws 仍有用)。
- 不回写 `users.yaml`(UI 建的用户只进 auth.db;seed 不覆盖已存在,重启不丢)。

## 关键决策(用户已定)

| # | 决策 | 选定 |
|---|---|---|
| 1 | 管理范围 | 完整 admin CRUD(创建/删除/改全局角色/重置密码/强制改密) |
| 2 | 入口 | UserMenu 下拉"用户管理"项(仅 admin 可见) → `/users` 独立页 |
| 3 | 密码策略 | admin 设临时密码 + 强制 `must_change_password=1`(复用现有提醒闭环) |
| 4 | `GET /api/users` 收紧 | 到 admin;`MemberManagerDialog` 配套改手输 username |
| 5 | 集中分配形态 | A 以用户为中心(展开归属编辑),非 ws 平铺、非矩阵 |
| 6 | 后端形态 | RESTful + Dialog 操作(方案 A,与 members/CreateWorkspaceDialog 风格一致) |

## 架构总览

```
后端                                       前端 /users 页
api/users.py (router 整体 require_admin)   UsersPage.tsx
  ├─ GET    /api/users                       ├─ 用户表格(username/全局role/must_change/created_at)
  │   (补 created_at, must_change 字段)      │   ├─ 行内全局角色 Select(选值→确认 Dialog→提交;admin↔user 高影响操作)
  ├─ POST   /api/users        {username,     │   ├─ 账号操作: 重置密码 / 删除(二次确认)
  │                            password,role}│   └─ [展开归属 ▼] (形态 A)
  ├─ DELETE /api/users/{id}                   │       ├─ 全部 ws 勾选清单(GET /api/workspaces)
  ├─ PATCH  /api/users/{id}  {role}           │       ├─ 已加入 ws 的角色 Select + 移除
  ├─ POST   /api/users/{id}/reset-password   │       └─ 保存 → 调 members 写 API
  │         {new_password}                    ├─ CreateUserDialog
  └─ GET    /api/users/{id}/workspaces        ├─ ResetPasswordDialog
              → [{ws, role}] (按需加载)        └─ DeleteConfirmDialog

api/members.py (现有 + 1 新增)
  ├─ GET/POST/DELETE /api/workspaces/{ws}/members[...]   现有
  └─ PATCH  /api/workspaces/{ws}/members/{username} {role}  新增(改 ws 角色)

api/workspaces.py: GET /api/workspaces                       现有,复用(勾选清单)

auth/store.py (AuthStore 扩展)
  ├─ list_all_users()                         [补 created_at, must_change 字段]
  ├─ create_user()                            [已有,参数对齐]
  ├─ delete_user(id)                          [新增:单事务清 workspace_members + sessions + users]
  ├─ update_role(id, role)                    [新增]
  ├─ reset_password(id, new_hash)             [新增:UPDATE hash + must_change=1]
  ├─ list_user_workspaces_with_role(id)       [新增 → [(ws, role)]]
  └─ update_workspace_member_role(ws,id,role) [新增]
```

**入口/守卫**: `UserMenu` 加"用户管理" `Link`(仅 `user.role==='admin'` 渲染) → 路由 `/users` 用新建 `RequireAdmin`(类比 `RequireAuth`,额外检查 `user.role==='admin'`,否则 `Navigate` 回 `/`)。后端 users router 改为 `app.include_router(users.router, dependencies=[Depends(require_admin)])`(替换 app.py:332 裸挂载)。

**鉴权直通**: members 写 API 需 `workspace_manager`,admin 自动直通(`dependencies.py:30`),admin 在 /users 页操作任意 ws 无障碍。

## 数据流

### 形态 A 展开面板(核心新交互)

```
1. 进 /users        → GET /api/users               → 渲染表格(不预拉归属)
2. 点 [展开归属 ▼]  → 并发:
                      GET /api/users/{id}/workspaces  → 已加入 [{ws,role}]
                      GET /api/workspaces             → 全部 ws 清单(供勾选)
3. 编辑(本地态): 勾选未加入 ws → add; 改角色 → update; 移除 → remove
4. [保存] → 按变更类型调 members API(均 admin 直通):
   add    → POST   /api/workspaces/{ws}/members         {username, role}
   update → PATCH  /api/workspaces/{ws}/members/{username} {role}
   remove → DELETE /api/workspaces/{ws}/members/{username}
   全部成功 → toast + 重拉归属刷新; 任一失败 → toast.error + 重拉归属(以服务端为准)
```

**不引入新写端点**,复用 per-ws members API。一个用户改 N 个 ws = 最多 N 个细粒度请求。理由: 复用已审过鉴权+护栏的 members API,避免再造批量逻辑(批量端点要重新实现"最后一个 manager""workspace_manager 校验")。单用户归属变更量小(个位数),可接受。

### 删用户的事务清理(数据完整性关键)

SQLite 未开 `PRAGMA foreign_keys`,表定义里 `REFERENCES users(id)` **不强制**,删用户不会自动清 `workspace_members` / `sessions`。必须显式:

```
store.delete_user(id) 单事务:
  BEGIN
    DELETE FROM workspace_members WHERE user_id=?   -- 清归属(否则孤儿成员)
    DELETE FROM sessions WHERE user_id=?            -- 失效其登录态
    DELETE FROM users WHERE id=?                    -- 删本体
  COMMIT
```

护栏检查在 route 层先做(自删? 最后一个 admin?),不满足直接 4xx 不进事务。

### 账号 CRUD 其余流

| 操作 | route 层 | store 层 | must_change |
|---|---|---|---|
| 创建 | username 不存在 + role 合法 + pwd≥8 | create_user(role, must_change=True) | 置 1 |
| 改全局角色 | 护栏(不自降/不降最后 admin) | update_role | 不动 |
| 重置密码 | pwd≥8 | reset_password(hash) | 置 1 |
| 删除 | 护栏(不自删/不删最后 admin) | delete_user(清 3 表) | — |

护栏检查时序: route 层先查 → 不满足直接 4xx;满足再调 store(store 内可加 assert 级二次校验防绕过)。

## 错误处理

**后端 HTTP 码**

| 场景 | 码 |
|---|---|
| 未登录 | 401(current_user) |
| 非 admin | 403(require_admin) |
| 自删 / 自降全局角色 | 409 `cannot modify own admin role` |
| 删/降最后一个 admin | 409 `last admin protected` |
| 降最后一个 ws manager | 409(复用 members.py:41 逻辑) |
| username 已存在(创建) | 409 `username exists` |
| 密码 <8(create/reset) | 400(`==旧`仅 change-password 适用,本设计新端点无旧密码概念) |
| 目标用户/工作区不存在 | 404 |
| CSRF 缺失/不匹配 | 403(verify_csrf 现有机制) |
| 全局 role 值非法 | 422(pydantic `Literal["admin","user"]`) |

**前端错误态**
- 表格加载失败: `ErrorState`(复用)+ 重试。
- 展开面板拉取失败: 面内"加载失败 + 重试",不影响其他行。
- 保存部分失败: `toast.error` + **重拉归属以服务端为准**,失败 ws 行标红。
- 账号 CRUD Dialog 失败: `toast.error`(按 status 映射 i18n,复用 `wsConfig.errors` 模式),Dialog 不关闭。
- 删除: 二次确认(显式"确认删除"按钮)防误删。

## 安全护栏(store + route 双重)

store 层事务内权威(防绕过),route 层给清晰 HTTP 码:

- admin 不自删 / 不自降全局角色。
- 不删 / 不降最后一个 admin(全局)。
- 改 ws 角色不降最后一个 ws manager(复用 members.py:40 逻辑)。
- 删用户显式清 `workspace_members` + `sessions`(FK 不强制)。
- username 唯一;密码 ≥8(create/reset,复用 `_NEW_PASSWORD_MIN_LEN`);`==旧`仅 change-password(自己改自己)适用,本设计 create/reset 不校验(admin 设新临时密码,无旧密码语境)。
- create / reset 置 `must_change=1`(接 `2026-07-27-web-default-password-change-reminder` 提醒闭环)。

## 测试策略

**后端 pytest**(只跑改动相关文件,勿广跑全套——见 CLAUDE.md 测试陷阱)

`tests/test_auth_store.py` 扩(store 层 TDD 先行,纯单测):
- `delete_user` 清三表(断言 users/workspace_members/sessions 均无残留)。
- `update_role` / `reset_password`(后者断言 must_change=1)。
- `list_user_workspaces_with_role` 返回 (ws, role)。
- `update_workspace_member_role`。
- `list_all_users` 带上 created_at/must_change。

新 `tests/test_users_routes.py`(集成测,带 auth_store fixture):
- 创建: 成功 + username 重复 409 + 密码短 400 + 非法 role 422 + 非 admin 403 + 置 must_change。
- 删除: 成功 + 自删 409 + 删最后 admin 409 + 删除后成员/session 清空验证。
- 改全局角色: 成功 + 自降 409 + 降最后 admin 409。
- 重置密码: 成功 + must_change 置 1 + 短密码 400。
- `GET /api/users/{id}/workspaces`: 返回归属。
- `GET /api/users` 收紧到 admin(非 admin 403)+ 返回带 created_at/must_change。
- members `PATCH` 改角色 + 降最后 manager 409。

**前端 vitest**(直接跑 `./node_modules/.bin/vitest`,避开 pnpm 陷阱——见 memory):
- `UsersPage`: 表格渲染 + 展开面板拉归属/ws 清单 + 保存调 members API + 部分失败重拉。
- `CreateUserDialog` / `ResetPasswordDialog` / 删除确认: 复用 `ChangePasswordDialog` 测试模式。
- `MemberManagerDialog` 改手输 username 回归(non-admin 不再调 listUsers)。
- `UserMenu`: admin 见"用户管理"项 / non-admin 不可见。
- `RequireAdmin`: 非 admin 跳转。
- i18n: zh/en 键齐全。

**回归**: `MemberManagerDialog.test.tsx` 改手输后断言更新(下拉 → 手输)。

## 铁律边界(不动)

本改动**全在 web auth 层**(users/members router + AuthStore + 前端),不碰:
- 双轨(确定性层 / LLM 轨 / GitNexus 轨)。
- 确定性产物→LLM 轨 hints 桥梁(保持拆除状态)。
- vuln agent / sink/source 规则 / chain_verdict。
- cost 计费 / 双引擎。

## 部署/迁移注意

- **auth.db schema 无破坏性变更**: 不加列(复用现有 `must_change_password`/`created_at`)。`delete_user` 只 DELETE 不改 schema。无需 migration。
- **MemberManagerDialog 改手输是行为变更**: 现有 non-admin manager 从下拉选人 → 手输 username。需重建 web 镜像 + 回归测试。
- **users router 鉴权收紧**: 现有调 `GET /api/users` 的 non-admin 流程(仅 `MemberManagerDialog`)会断,已由手输方案承接。
- **首个 admin**: `users.yaml` 的 admin 是 seed 源,UI 不会删 seed(护栏保最后 admin)。已存在的 admin 记录不受 UI 影响。
- 重建: `docker compose build web && up -d web`。
- **与并行 ws-scan 解耦(spec `2026-07-27-web-workspace-scan-decoupling-design`)的协调**: 两者正交(本 spec 改 auth/users 层,ws-scan 改 scan 层),共享 `app.py`/`router.tsx` 但改动点不重叠——`app.py` 是不同 `include_router` 行(users vs scans);`router.tsx` 是 `/users` 顶级新路由 vs `/p/:workspace/scans/:scanId` scan 路由。合并注意 include 顺序与路由注册位置,无逻辑冲突。两 spec 任一先落都不阻塞另一个。

## 涉及文件清单

**后端(改)**:
- `packages/web/src/supernova_web/auth/store.py`(+5 方法, list_all_users 补字段)
- `packages/web/src/supernova_web/auth/models.py`(User 加 `created_at: str = ""`)
- `packages/web/src/supernova_web/api/users.py`(整体 require_admin + 5 新端点)
- `packages/web/src/supernova_web/api/members.py`(+PATCH 改角色)
- `packages/web/src/supernova_web/app.py`(users router 挂载加 require_admin)

**前端(新)**:
- `pages/UsersPage.tsx` + `.test.tsx`
- `components/CreateUserDialog.tsx` + `.test.tsx`
- `components/ResetPasswordDialog.tsx` + `.test.tsx`
- `components/UserWorkspacesPanel.tsx`(形态 A 展开)+ `.test.tsx`
- `auth/RequireAdmin.tsx`

**前端(改)**:
- `components/layout/UserMenu.tsx`(加"用户管理"项)
- `router.tsx`(加 /users 路由 + RequireAdmin)
- `components/MemberManagerDialog.tsx`(下拉 → 手输 username)
- `api/users.ts`(新,放 CRUD + `GET /users/{id}/workspaces` 调用);现有 `listUsers`/`UserLite` 在 `members.ts`,改手输后无调用方 → 删 `listUsers`(UserLite 挪 users.ts 复用)
- `locales/{zh,en}.json`(users.* / members.input.* 键)

**测试(改/新)**:
- `tests/test_auth_store.py`(扩)
- `tests/test_users_routes.py`(新)
- `tests/test_auth_members.py`(PATCH 改角色,如现存;否则并入)
- `components/MemberManagerDialog.test.tsx`(回归更新)
