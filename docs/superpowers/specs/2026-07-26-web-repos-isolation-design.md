# Web repos 仓库隔离 — 设计文档（P2）

- **日期**：2026-07-26
- **分支**：`feat/fork-py`
- **状态**：基于 P2 brainstorming 确认的 admin-中心模型，细节待用户审阅
- **依赖**：P0（认证）+ P1（成员制）必须先完成；**本 spec 调整了 P1 的 ws 创建模型**（见 §3）
- **范围标记**：P2。不含配置/profile/.env 隔离（P3c）

## 1. 背景

用户诉求："工作区也隔离下载的仓库"。P2 brainstorming 摸清现状（repos 全局共享、clone 与 scan 是两个独立 API、clone 先于 scan）后确认核心矛盾：**按 ws 隔离 repo → repo 物理在 ws 内 → clone 在 ws 上下文 → ws 必须先于 clone 存在**。解决方式（用户选定）：

- **按 workspace 隔离**（不按 user、不仅 ACL）
- **admin 创建 workspace**（仅 admin，ws 成为先于 scan 的一等容器），admin 分配成员
- **ws 内所有成员**（manager + member）都能 clone repo + 发起 scan；manager 额外管成员；admin 全能
- clone 凭据仍全局共享（GITLAB_USER/TOKEN，隔离属 P3c）

## 2. 目标 / 非目标

### 目标
- workspace 由 admin 独立创建（先于 scan 的一等容器）
- repo 物理在 ws 目录内（`workspaces/<ws>/repos/<name>`），按 ws 隔离
- clone / pull / checkout / delete repo 都在 ws 上下文（ws 成员鉴权）
- scan 在已有 ws 内跑（不再创建 ws）
- ws 内成员都能 clone + scan；跨 ws 互不可见

### 非目标
- clone 凭据 per-user/per-ws 隔离 → **P3c**
- configs/multi-configs 目录隔离 → **P3c**
- repo 跨 ws 共享/引用 → 不做（按 ws 隔离即每个 ws 独立 clone）
- 普通用户建 ws → 不做（仅 admin）

## 3. 依赖 P1（已对齐）

P1 的 ws 生命周期模型已为 P2 调整到位（P1 Task 4，2026-07-26）：

- **admin 建 ws**：`POST /api/workspaces`（`require_admin`）建空 ws 目录 + admin=manager
- **scan 在已有 ws 内跑**：`create_scan` 校验 ws 已存在 + 当前用户成员/admin；`scan_manager.start` 去掉建 ws 目录与 `_gen_ws_name`

P2 在此基础上做 **repo 按 ws 隔离**，不重复 admin 建 ws / scan 改造。P2 开始时 ws 已存在、scan 在 ws 内——此前提由 P1 保证。

## 4. 数据与物理路径

### 4.1 repo 物理位置
- 现：全局 `repos/<name>` 或 `repos/<group>/<name>`
- 新：`workspaces/<ws>/repos/<name>`（**repo 并入 ws 目录**，ws 内聚：repo + 产物 + 未来 config 都在 ws 下）
- worker 容器已 mount `workspaces/`（docker-compose.yml），路径透明，无需改 mount
- 全局 `repos/` 目录：legacy 仓库迁移（§4.3），新 clone 不再用

### 4.2 不新增 DB 表
repo 归属 = 其物理所在的 ws 目录（隐含），无需 `repo_members` 表。repo 鉴权复用 P1 的 `workspace_member`（能访问 ws 就能访问该 ws 的 repo）。

### 4.3 legacy repo 迁移（启动）
- `repos/` 下已有的 legacy repo：启动迁移到 `workspaces/<legacy_ws>/repos/`？但 legacy repo 不属于任何 ws。
- **决策**：legacy repo 迁移到一个 admin 专属的 `__legacy__` workspace（admin manager），admin 可见、可手动重新分配/clone 到具体 ws。或保留 `repos/` 只读给 admin（不再新 clone）。
- **默认**：legacy repo 移入 `workspaces/__legacy__/repos/`，admin 全能可见。标注可调。

## 5. 后端设计

### 5.1 admin 建 ws + scan 在 ws 内（由 P1 提供，见 P1 Task 4）
P2 不重复实现 admin 建 ws / scan 改造。P2 核心是下面的 repo 按 ws 隔离。
### 5.2 repo 操作改到 ws 上下文（P2 核心，改 repos.py + repo_manager）
- 新路由前缀：`/api/workspaces/{ws}/repos`（从 `/api/repos` 迁移）
  - `GET /api/workspaces/{ws}/repos` — 列该 ws 的 repo（ws 成员可见）
  - `POST /api/workspaces/{ws}/repos` — clone 进该 ws（ws 成员）
  - `GET /api/workspaces/{ws}/repos/{name}` — 详情
  - `DELETE /api/workspaces/{ws}/repos/{name}` — 删除（ws 成员 + scan 引用检查）
  - `POST /api/workspaces/{ws}/repos/{name}/pull` / `/checkout`
  - `GET /api/workspaces/{ws}/repos/{name}/events` — clone SSE
- 所有路由加 `Depends(workspace_member)`（ws 成员鉴权）
- `RepoManager` 构造改为按 ws：clone/pull/checkout/delete/list 都收 `ws_name`，物理目录 `workspaces/<ws>/repos/<name>`
- `_resolve_repo_dir(repos_root, name)` → `_resolve_repo_dir(workspaces_dir / ws / "repos", name)`
- scan 的 `_resolve_repo_path(name)` → `_resolve_repo_path(ws, name)`：在当前 ws 的 repos 目录找
- clone SSE 的 ndjson 落 `workspaces/<ws>/repos/<name>/clone.ndjson`

### 5.4 clone 凭据（不改，P3c）
仍全局 GITLAB_USER/TOKEN。`GitFetcher` 不动。

### 5.5 ws 删除（P1 已有，补充）
`DELETE /api/workspaces/{ws}`（rmtree）天然删掉该 ws 的 repos/（物理在 ws 目录内）。无需额外清 repo 记录（无 repo 表）。

## 6. 前端设计

### 6.1 新建 workspace（admin）
- ws 列表页（`/workspaces`）加"新建 workspace"按钮（仅 admin 可见）→ Dialog 填 name/description → `POST /api/workspaces`
- 建 ws 后跳转/提示去分配成员（P1 成员管理 dialog）

### 6.2 clone 管理移到 ws 内
- **ws 详情页加"仓库"tab**（`/p/:workspace/repos`）：列该 ws 的 repo + clone 新仓库（AddRepoDialog 带 ws 上下文）+ pull/checkout/delete/clone 进度
- 全局 `/repos` 页：改为 admin 跨 ws 总览（按 ws 分组列出所有 repo），普通用户重定向到自己的 ws 列表（或隐藏）。**默认**：全局 `/repos` 保留为 admin 总览，普通用户不见入口。

### 6.3 ScanNewPage 改造
- 扫描必须选一个已有 ws（用户被分配的 ws 下拉；admin 见所有）
- repo source 从当前选定 ws 的 repo 列表取（`GET /api/workspaces/{ws}/repos`）
- 提交 scan 带 workspace=选定的 ws
- "现场 clone"（AddRepoDialog）clone 进当前选定的 ws

### 6.4 RepoCombobox / AddRepoDialog 带 ws 上下文
- RepoCombobox 收 `ws` prop，调 `/api/workspaces/{ws}/repos`
- AddRepoDialog 收 `ws` prop，clone 时 POST `/api/workspaces/{ws}/repos`

## 7. 测试策略

- `test_create_workspace_admin.py`：admin 建ws 成功；普通用户 403；重名 409
- `test_scan_requires_existing_ws.py`：scan 不存在 ws → 422；非成员 → 403；成员 → 202
- `test_repos_ws_isolation.py`：clone 进 ws；ws 成员可见；非成员 403；跨 ws 互不可见
- `test_repo_lifecycle_in_ws.py`：clone/pull/checkout/delete 在 ws 上下文
- `test_scan_resolves_repo_in_ws.py`：scan 的 repo source 解析到当前 ws 的 repos
- `test_legacy_repo_migration.py`：legacy repo → `__legacy__` ws（admin 可见）
- 前端：新建 ws dialog、ws 内仓库 tab、ScanNewPage 选 ws

## 8. 范围边界

- clone 凭据隔离 → P3c
- configs/multi-configs 隔离 → P3c
- 跨 ws repo 共享 → 不做

## 9. 决策记录（默认值，可调）

1. **按 ws 隔离**（非 user、非仅 ACL）——用户选定。
2. **admin 建 ws**（非任意用户、非 clone 即建）——用户选定。ws 成先于 scan 的一等容器。
3. **ws 内所有成员都能 clone+scan**——用户选定。
4. **repo 物理并入 ws 目录**（`workspaces/<ws>/repos/`，非 `repos/<ws>/`）——内聚，worker mount 透明。
5. **legacy repo → `__legacy__` ws**（admin 可见）——标注可调。
6. **全局 `/repos` 页保留为 admin 跨 ws 总览**，普通用户入口隐藏——标注可调。
7. **scan_source 的 RepoSource.value 仍为 repo 名**，ws 由 scan 请求的 `workspace` 字段隐含（不改 RepoSource schema）。
8. **对 P1 的影响**：P1 spec/plan Task 4 已改为 admin 建 ws + scan 在 ws 内（2026-07-26 对齐）；本 spec §5.1 引用 P1，不重复。

---

**下一步**：本文档审阅后，出 P2 实现计划（P1 spec/plan 已对齐 admin 建 ws 模型）。
