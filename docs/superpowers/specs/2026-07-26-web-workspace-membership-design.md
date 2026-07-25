# Web workspace 成员制与产物隔离 — 设计文档（P1）

- **日期**：2026-07-26
- **分支**：`feat/fork-py`
- **状态**：基于 P0 brainstorming 已确认的成员制方向，设计细节待用户审阅
- **依赖**：P0（身份认证）必须先完成
- **范围标记**：P1。不含 repos 隔离（P2）、配置隔离（P3c）

## 1. 背景

P0 完成多用户身份认证（登录/会话/路由守卫）。P1 给 workspace 加**成员制**：满足用户核心诉求"给工作区分配用户、不同用户看不同 workspace、内容隔离"。

当前 workspace 是 `workspaces/<name>/` 目录，无成员概念，所有登录用户都能看所有 workspace 的产物。P1 引入 `workspace_members` 多对多关系 + 产物 API 按成员过滤/鉴权。

## 2. 目标 / 非目标

### 目标
- workspace ↔ users 多对多成员关系（`manager` / `member` 两级）
- **admin 创建 workspace**（仅 admin，`POST /api/workspaces`），ws 成为先于 scan 的一等容器；admin 建后分配 manager/member
- **scan 在已有 ws 内跑**（不创建 ws）：`ScanRequest.workspace` 必须是已存在 ws 且当前用户是其成员/admin
- admin 用户见所有 workspace；普通用户只见自己是成员的
- workspace 产物 API（列表/详情/报告/日志/交付物/events/scan cancel/delete）按成员鉴权（非成员 403）
- 成员管理 API + 前端 UI（manager/admin 加减成员）

### 非目标（留给后续）
- repos 仓库隔离 → **P2**
- per-workspace 配置/profile/.env 隔离 → **P3c**
- 跨 workspace 操作 / workspace 间共享 → 不做（成员制只做归属，不做共享，对应 brainstorming 否决的"owner+可共享"）
- 注册/自助管理 → 不做（P0 预置制延续）

## 3. 数据模型

P0 引入的 SQLite 加一张表（`workspace_members`）。**workspace 标识用目录名 `workspace_name`**（`ScanRequest.workspace` 或自动生成的 `<hostname>_<timestamp>`），不引入 workspace id——workspace 的存在性仍由物理目录决定，成员表只记"谁能看"。

```sql
CREATE TABLE IF NOT EXISTS workspace_members (
  workspace_name TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL DEFAULT 'member',   -- 'manager' | 'member'
  created_at TEXT NOT NULL,
  PRIMARY KEY (workspace_name, user_id)
);
CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_wm_ws ON workspace_members(workspace_name);
```

`schema_meta` 版本递增。

## 4. 角色与权限

两级角色 + admin 全局：

| 操作 | admin（全局） | workspace manager | workspace member | 非成员 |
|---|---|---|---|---|
| 见 workspace 列表 | 所有 | 自己 ws | 自己 ws | 无 |
| 读产物（report/logs/deliverables/events） | 所有 ws | 自己 ws | 自己 ws | 403 |
| 创建 workspace（POST /api/workspaces） | 是 | 否 | 否 | 否（仅 admin） |
| 发起扫描（在已有 ws 内） | 所有 ws | 自己 ws | 自己 ws | 否（须被 admin 分配到 ws） |
| 管理该 ws 成员（加/减） | 所有 ws | 自己 ws | 否 | 否 |
| 删除 workspace | 所有 ws | 自己 ws | 否 | 否 |

**关键**：发起扫描是任何登录用户都能做的（不限制只有 admin），扫描创建 workspace 后创建者=该 workspace manager。这符合"分配工作区"语义——用户自己扫的归自己管，admin 全局兜底。

## 5. 后端设计

### 5.1 store 扩展（`auth/store.py` 加 workspace 成员方法）

- `add_workspace_member(ws_name, user_id, role="member")`
- `remove_workspace_member(ws_name, user_id)`
- `list_workspace_members(ws_name) -> list[tuple[int, str, str]]`（user_id, username, role；join users）
- `list_user_workspaces(user_id) -> list[str]`
- `get_workspace_member_role(ws_name, user_id) -> str | None`（None=非成员）
- `delete_workspace_members(ws_name)`（删 workspace 时清成员）

### 5.2 dependencies 扩展（`auth/dependencies.py`）

- `workspace_member(ws_name: str, user: User = Depends(current_user)) -> User`：admin 或该 ws 成员通过，否则 403
- `workspace_manager(ws_name: str, user: User = Depends(current_user)) -> User`：admin 或该 ws manager 通过，否则 403

### 5.3 路由改造（`api/workspaces.py` / `api/scan.py` / `api/events.py`）

- **GET /api/workspaces**：当前 `WorkspacesIndexer` 列目录，P1 叠加成员过滤——admin 返回所有目录；普通用户只返回 `list_user_workspaces(user.id)` ∩ 目录存在的。**目录存在但无成员记录的（legacy）**：admin 见，普通用户不见。
- **POST /api/workspaces**（新，`require_admin`）：body `{name}` → 建空 ws 目录 + admin 自动成为 manager。admin 建后经成员管理 API 分配其他成员。
- **POST /api/scan**：改为**在已有 ws 内跑**——校验 `req.workspace` 是已存在 ws 目录 + 当前用户是其成员/admin（否则 422/403）；`ScanManager.start` 去掉建 ws 目录（已存在）+ 去掉 add manager（成员关系由 admin 预分配）。`_gen_ws_name` 废弃（ws 必须显式指定且已存在）。
- **workspace 产物路由**（`GET /api/workspaces/{ws}`、`/deliverables`、`/report`、`/logs`、`/events`、`DELETE /api/workspaces/{ws}`、`DELETE /api/scan/{ws}`）：加 `Depends(workspace_member)`（删除操作用 `workspace_manager`）。

### 5.4 成员管理路由（新，挂 workspaces router）

- `GET /api/workspaces/{ws}/members` → 成员列表（member 可见）
- `POST /api/workspaces/{ws}/members` body `{username, role?}` → 加成员（`workspace_manager`）
- `DELETE /api/workspaces/{ws}/members/{username}` → 移除（`workspace_manager`，不能移除自己最后一个 manager）
- `GET /api/users` → 可分配用户列表（`workspace_manager` 可调，给分配 dialog 用）

### 5.5 legacy workspace 迁移（启动）

P0 前已有、目录存在但 `workspace_members` 无记录的 workspace：启动迁移把它们分配给**所有 admin**（保证 admin 能见 legacy ws 并进一步分配）。普通用户默认不见 legacy ws，需 admin 手动分配。

### 5.6 workspace 删除

`DELETE /api/workspaces/{ws}`（已有 rmtree）加：`delete_workspace_members(ws)`。要求 `workspace_manager` 权限。

## 6. 前端设计

- **workspace 列表**：后端已过滤，前端无需大改；`/workspaces` 与 dashboard 的 workspace 卡片自然只显示自己的。
- **admin 新建 workspace**：ws 列表页加“新建 workspace”按钮（仅 admin 可见）→ Dialog 填 name → `POST /api/workspaces`；建后可跳成员管理分配成员。
- **成员管理 UI**：workspace 详情页（`/p/:workspace/...`）顶部或新增"成员"入口（`manager`/`admin` 可见），点开 dialog：成员列表 + "添加成员"（选用户 + role）+ 移除按钮。用现有 shadcn `Dialog`/`Select`/`Table`。
- **隐藏非成员 workspace 的管理入口**：非 manager 隐藏成员管理按钮（后端也守，前端只是 UX）。

## 7. 测试策略

- `test_workspace_members_store.py`：CRUD、`list_user_workspaces`、`get_workspace_member_role`
- `test_workspace_filter.py`：普通用户只见自己的；admin 见所有；legacy 只 admin 见
- `test_workspace_permissions.py`：非成员读产物 403；member 可读不可管；manager 可管；删除权限
- `test_scan_creates_manager.py`：发起扫描 → 创建者=manager
- `test_members_routes.py`：成员管理 API 权限 + 不能移除最后一个 manager
- `test_legacy_migration.py`：无成员记录的 workspace → admin

前端：成员管理 dialog 渲染 + 加成员交互（vitest）。

## 8. 范围边界

P2（repos 隔离）、P3c（配置隔离）不含。现有 `/api/repos`、`/api/multi-configs` 在 P1 仍**所有登录用户共享**（不隔离），等 P2。

## 9. 决策记录（默认值，可调）

1. **workspace 标识用目录名**，不引 workspace id（最小侵入，workspace 存在性=目录）。
2. **manager/member 两级**（创建者=manager），不引更细角色。admin 全局。
3. **workspace 由 admin 创建**（P2 brainstorm 2026-07-26 调整）：仅 admin 能 `POST /api/workspaces` 建 ws；普通用户须被 admin 分配到 ws 才能 scan。scan 不再创建 ws（按 ws 隔离 repo 要求 ws 先于 clone/scan 存在）。原“扫描创建者=manager”模型作废。
4. **legacy workspace → 分配给所有 admin**；普通用户需 admin 手动分配。
5. **成员管理 UI 在 workspace 详情页**（非独立 settings 页）。
6. **不做 workspace 共享/转移**（成员制只做归属；"可共享"在 brainstorming 已否决）。

---

**下一步**：本文档审阅后出 P1 实现计划。
