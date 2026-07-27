# 工作区 / 扫描任务信息架构重设计

> 日期：2026-07-27
> 范围：web 层（前端 `packages/web/frontend/src/` + web 后端 `packages/web/src/supernova_web/`）
> 不触碰：双轨 / 引擎 / cost 计费等核心不变量（CLAUDE.md §1/§2/§4）

## 1. 背景与动机

现状点进某个工作区后无法直接切换到另一个工作区，须退回 `/workspaces` 列表页再点进另一个，交互割裂。
用户在重新思考后把需求升级为**信息架构重设计**，不止「加个切换器」，而是重排工作区 / 扫描两个视角的入口、
内容与权限落点：

1. 顶栏「工作区」不必先跳列表页，直接进该用户置顶的工作区，侧边抽屉切换即可；管理员另有入口跳工作区管理页，
   普通用户只见分配给自己的工作区。
2. 工作区详情页无需顶部状态栏（StatRow），只留扫描任务数等精简信息。
3. 扫描任务列表要有筛选功能（状态 / 类型 / 关键字 / 时间）。
4. 首页从「工作区卡片墙」改成「扫描任务」视图，每条扫描标归属工作区；管理员看全部，普通用户只看归属给自己
   工作区的扫描。

权限模型已有且为标准多对多（`users.role` + `workspace_members` 表），本设计在各落点天然支持一个用户多工作区、
一个工作区多用户，不新增鉴权框架，只复用现有依赖项。

## 2. 信息架构与导航

### 2.1 顶栏导航（`components/layout/TopBar.tsx`）

| 当前 | 改后 |
|---|---|
| `工作区` -> `/workspaces` | `工作区` -> 跳该用户置顶工作区（无置顶 -> 最近归属；无归属 -> 空态引导页） |
| 无管理入口 | admin 可见「工作区管理」-> `/workspaces`（管理全部 ws）；普通用户**不显示** |
| `首页`(`/`) -> 工作区卡片墙 | `首页`(`/`) -> 扫描任务视图（跨 ws，标归属工作区） |

### 2.2 页面职责重新划分

- **首页 `/`（扫描任务视角）**：跨工作区扫描任务表 + 筛选，每行标归属工作区。admin 全部、普通用户归属 ws 的扫描。
  点扫描行 -> 进 ScanDetail。详见 §3。
- **工作区管理页 `/workspaces`（管理视角，admin 专属）**：工作区列表 + 成员分配 + 建/删。**去掉顶部 StatRow**，
  精简为工作区表（名 / 扫描数 / 成员 / 操作）。前端 `RequireAdmin` 包路由，后端 `require_admin`。详见 §6。
- **工作区详情 `/p/:ws`（容器视角，所有有权限用户）**：header（名 + 扫描数徽章，**不加 StatRow**）+ 扫描列表
  带筛选（§4）+ 仓库 / settings 入口 + 侧边抽屉切换器（§5）+ 置顶按钮。
- **扫描详情 `/p/:ws/scans/:scanId`**：header 加同款「切换工作区」按钮，扫描详情页也能直接切 ws。

### 2.3 置顶机制（per-user 个人置顶）

- 每个 user 从归属的多个 ws 里 pin 一个；同一 ws 可被多个不同用户各自 pin，互不影响。
- 存 `users.pinned_workspace` 列（§7.4）。
- 顶栏「工作区」三段跳转逻辑由专用 redirect 组件 `<WorkspacesEntry/>` 承载，集中可测：
  1. pinned 存在 -> `/p/:pinned`
  2. 无 pinned 但有归属 ws -> 跳最近活跃 ws（按 `latest_created_at`）
  3. 无归属 ws -> `/workspaces` 空态引导页（普通用户也可见此页空态，但不见管理功能）
- 详情页 header 加「📌 置顶」按钮，置顶 = 当前 ws。

### 2.4 权限落点（复用现有依赖项，零新增鉴权逻辑）

- `GET /api/scans`（新，§7.1）-> 内部 `current_user` + 按 role 过滤（admin 全部 / 普通 `list_user_workspaces`）。
- `GET /api/workspaces`（总览页）-> 已按 role 过滤；总览页设 admin 专属，前端 `RequireAdmin` 包、后端 `require_admin`。
- 工作区详情 / 扫描操作 -> 复用 `workspace_member` / `workspace_manager`。
- 置顶 `PUT /api/users/me/pinned-workspace` -> `current_user` + `workspace_member(ws)`（只能 pin 有权限的 ws）。
- `pinned_workspace` 是用户属性，与 ws 列表正交；前端经现有 `/auth/me` 返回的 user 档案取 pinned 本地比对，
  `GET /workspaces` 响应**不加** `pinned_by_me`（决策 A）。

## 3. 首页扫描任务视图（`pages/DashboardPage.tsx` 重写）

### 3.1 数据源

新增后端 `GET /api/scans`（跨 ws 聚合，§7.1），返当前用户可见的全部扫描，每条带 `workspace` 字段。
前端不再用 `useWorkspaces()`，改用新 hook `useAllScans()` 拉取。

### 3.2 页面结构

- **顶部**：PageHeader（标题 / 副标题）+「新建扫描」按钮 + 精简统计条（运行中 / 今日完成 / 总漏洞 / 总成本 4 格，
  复用 `StatRow`，数据从扫描聚合）。
- **筛选工具栏**：`<ScanFilters />`（§4 共用组件）。
- **运行中扫描**单独置顶一区（保留现有 running 卡片快速入口直觉，卡片标归属 ws），下方是可筛选的全量扫描表。
- **扫描任务表**：`@tanstack/react-table`，列：状态色条 + StatusBadge / scan_id（mono，进 ScanDetail）/
  **归属工作区**（mono，进 `/p/:ws`）/ scan_type / 漏洞数 / 成本 / 时间。
- **空态**：无扫描 -> 引导「新建扫描」；普通用户无归属 ws -> 引导联系 admin 分配。

### 3.3 权限体现

数据来自 `GET /api/scans`，后端按 role 过滤。admin 看到全部 ws 的扫描，普通用户只看归属 ws 的扫描。
前端不写权限分支，靠数据驱动。

## 4. 扫描列表筛选组件（共用）

### 4.1 `components/ScanFilters.tsx`（受控筛选条）

- **状态**：`Select`，复用 `workspaces.status.*` + `all`。选项 all / running / completed / failed / killed /
  crashed / interrupted。
- **类型**：`Select`，复用 `workspaces.filter.*`。选项 all / whitebox / blackbox / correlation。
- **关键字**：`Input`，placeholder「搜索 scan_id 或工作区名…」。前端 `toLowerCase().includes` 匹配 scan_id 与
  workspace 字段。
- **时间**：`Select`，选项 all / today / 7d / 30d（客户端按 `created_at` 过滤；首版不上日期范围选择器）。
- 受控：`value` + `onChange`，父组件持有 state。

### 4.2 共用筛选逻辑 hook `useScanFilters(scans)`

返回 `{ filters, setFilters, filtered }`，ScanList（工作区详情页）与 DashboardPage（首页）复用，过滤逻辑集中。
两处数据源不同（ScanList = `listScans(ws)`，首页 = `GET /api/scans`），列结构各页自定义。

### 4.3 ScanList（工作区详情页 `routes/WorkspaceDetail/ScanList.tsx`）改造

- header 下方加 `<ScanFilters />`。
- 卡片列表前加 `useMemo` 过滤层（现 `scans` 直接 map -> 改 `filtered.map`）。
- 工作区详情页的 ScanList **无 workspace 列**（当前就在此 ws 内），首页表有。

## 5. 工作区详情页 + 侧边抽屉切换器（`routes/WorkspaceDetail/`）

### 5.1 详情页 header 调整

- 保留：工作区名 + 成员管理 + 仓库 / settings 入口 + StatusBadge + 扫描数徽章。
- 新增「📌 置顶」按钮：点 -> `PUT /api/users/me/pinned-workspace`（§7.2）。已置顶显「已置顶」态。
- 新增「切换工作区」按钮（`≡` 图标）：点开侧边抽屉。
- 不加 StatRow。

### 5.2 侧边抽屉切换器 `components/WorkspaceSwitcher.tsx`

- 点「切换」按钮，**左侧滑出**面板。优先用库内现成 sheet；若 shadcn 未含 sheet，用 `dialog` + 居左定位类实现
  （实现期定，不影响设计）。
- 面板内容：
  - 标题「切换工作区」+ 关闭按钮
  - 搜索框（筛 ws 名）
  - 工作区列表：每项 = 状态色点 + ws 名 + 扫描数 + 📌（若为当前用户置顶）。当前 ws 高亮。点击 ->
    `navigate(/p/${name})` + 自动收起。
  - 列表数据来自 `useWorkspaces()`（已有 5s 轮询，复用）。
  - 底部「+ 新建工作区」按钮（admin 可见，复用 `CreateWorkspaceDialog`；普通用户隐藏）。
- 在 `WorkspaceDetail` 与 `ScanDetail` header 各放一个触发按钮，共享同一组件。

### 5.3 切换后落点

切到新 ws -> 跳 `/p/:newWs`（ws 概览），不保留 scan tab（跨 ws 的 scanId 无意义）。

## 6. 工作区管理页（`pages/WorkspaceListPage.tsx` 精简 + admin 专属）

- 路由层 `RequireAdmin` 包裹（普通用户直接访问 `/workspaces` -> 重定向到 `/`）。
- **去掉顶部 StatRow** 状态条，精简为工作区表：名 / 扫描数 / 成员 / 操作（取消 / 删除）。
- 现有取消 / 删除操作是 manager 级，admin 专属后天然只 admin 可见，与 `require_admin` 一致。
- 现有筛选（状态 / 类型 / 关键字）可保留或精简，实现期定，不强制。

## 7. 后端端点与数据模型

### 7.1 `GET /api/scans`（跨 ws 扫描聚合，首页用）

- 依赖 `current_user`。
- 逻辑：取该用户可见 ws 集合（admin = `indexer.list_workspaces()` 全部名；普通 = `auth_store.list_user_workspaces
  (user.id)`），对每个 ws 调 `ScanStore.list_scans(ws)` 汇总，每条注入 `workspace` 字段（= ws 名）。
- 返回 `ScanSummary[]`（复用现有类型，加 `workspace: str` 字段）。
- 排序：按 `created_at` 倒序。
- 性能：ws 数通常个位数到几十，每 ws `list_scans` 是目录扫描，可接受；不加分页（与现有 `listScans(ws)` 一致）。
  将来 ws 量上来再加 limit / 分页。
- 放 `api/scans.py`（与 per-ws scan 端点并列）。

### 7.2 `PUT /api/users/me/pinned-workspace`（置顶）

- 依赖 `current_user` + `workspace_member(ws)`（body 的 ws，只能 pin 有权限的 ws）。
- body：`{ "workspace": "ws_name" }`。
- 写 `users.pinned_workspace` 列（`AuthStore.update_pinned_workspace(user_id, ws_name)` 新方法）。
- 返回 `{ "pinned": "ws_name" }`。
- 放 `api/users.py`。

### 7.3 `/auth/me` 返回含 pinned

- 现有 `/auth/me`（`auth/routes.py`）已存在，前端 `useAuth` 已通过它拉 user。
- `_user_out(u)` 加 `pinned_workspace` 字段（`User` model 加 `pinned_workspace: str | None`）。
- 前端 `AuthUser` 类型加 `pinned_workspace?: string | null`。
- **不新建** `/api/users/me`（决策 A：pinned 经现有 `/auth/me` 返回，零额外请求）。

### 7.4 数据迁移：`users.pinned_workspace` 列

- `auth/store.py` `_SCHEMA` 的 `users` 表加 `pinned_workspace TEXT` 列。
- 幂等 `ALTER TABLE users ADD COLUMN pinned_workspace TEXT`（套用现有 `must_change_password` 列同款 try/except，
  旧库补列、新库已含均不崩）。
- `create_user` / `get_user` / `get_user_by_username` / `list_all_users` 的 SQL 补该列读写（`get_user_*` 用于
  `_user_out` 返 pinned）。
- 新增 `update_pinned_workspace(user_id, ws_name)` 方法。

### 7.5 不动的东西

- `GET /api/workspaces` 契约不变（不加 `pinned_by_me`）。
- per-ws `listScans(ws)` 端点不变（工作区详情页仍用）。
- `workspace_members` 多对多表不动。
- 权限依赖项 `workspace_member` / `workspace_manager` / `require_admin` 不动。
- `ScanStore.list_scans(ws)` 签名不变（`GET /api/scans` 只是循环调用它）。

## 8. i18n

新增翻译键（中英双语，`i18n/locales/zh.json`、`en.json`），纯加键不删旧：

- 顶栏：`nav.workspaces` 语义不变（仍显「工作区」），行为改跳置顶。
- 抽屉：`workspaceSwitcher.title` / `.search` / `.newWorkspace` / `.pinned` / `.empty`。
- 置顶按钮：`workspaceDetail.pin` / `.pinned` / `.unpin`。
- 扫描筛选：`scanFilters.status` / `.type` / `.keyword` / `.time` + `scanFilters.time.today/7d/30d/all`。
- 首页扫描表：`dashboard.scanTable.*`（列头、归属工作区列、空态）。
- 权限空态：`dashboard.noWorkspace.title` / `.hint`。
- 总览页 admin 专属：前端 `RequireAdmin` 包路由即可，无需新键。

## 9. 测试（TDD）

### 9.1 后端（pytest，`packages/web/tests/`）

- `test_list_all_scans_cross_ws`：admin 见全部 ws 扫描、普通用户只见归属 ws 扫描、每条含 `workspace` 字段。
- `test_pinned_workspace`：pin / unpin、pin 无权限 ws -> 403、`/auth/me` 返 pinned。
- `test_pinned_workspace_column_migration`：旧库（无列）启动补列不崩。

### 9.2 前端（vitest）

- `WorkspaceSwitcher`：列表渲染、当前 ws 高亮、搜索过滤、点击切换导航 + 收起、admin 见新建入口 / 普通不见。
- `ScanFilters` + `useScanFilters`：四维筛选状态变化、过滤逻辑。
- `DashboardPage` 重写：扫描表渲染、归属工作区列、权限过滤（mock `/api/scans`）、空态。
- `WorkspaceDetail`：置顶按钮、切换器入口。
- `WorkspacesEntry`：顶栏「工作区」跳 pinned / 无 pinned 跳最近 / 无归属跳空态。

## 10. 风险与边界

1. **顶栏「工作区」三段跳转**：由 `<WorkspacesEntry/>` 集中承载（pinned -> 最近活跃 -> 空态），逻辑集中可测。
2. **总览页 `/workspaces` 改 admin 专属**：普通用户访问 -> `RequireAdmin` 重定向 `/`。
3. **`GET /api/scans` 性能**：ws 多时 N 次目录扫描。首版不优化，真机若 ws > 20 卡顿再加分页。不在首版加，避免
   过度设计。
4. **ScanNewPage 预填 ws**：现状 `?workspace=` 预填，首页 / 切换器的「新建扫描」入口带 ws 上下文则预填，不带
   走默认。保持现状不破。
5. **i18n key 增量**：纯加键不删旧，不破现有翻译。
6. **不动核心不变量**：纯 web 层，不碰双轨 / 引擎 / cost 计费（CLAUDE.md §1/§2/§4）。

## 11. 实现前需确认的事实（写 plan 时先查）

- shadcn 是否已含 `sheet` 组件，决定 `WorkspaceSwitcher` 用 sheet 还是 dialog + 定位（不影响设计成立）。
- 现有 `WorkspaceListPage` 筛选保留与否（实现期定）。
