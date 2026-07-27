# 下线工作区管理页（WorkspaceListPage）—— 能力并入 Dashboard + 切换器

> 状态：设计已定，待实现（回家做）｜ 2026-07-28 摸底确认 5 处决策（见 §3.2 / §3.3 / §3.6 / §9），可落地性已验证
> 日期：2026-07-27
> 分支：`feat/fork-py`

## 1. 背景与问题

当前工作区相关有 4 个界面，其中两个是**同质化的跨工作区监管表**：

| 界面 | 路由 | 行粒度 | 独有能力 |
|------|------|--------|---------|
| **Dashboard** `DashboardPage` | `/`（所有人） | per-scan | StatRow + 运行中卡片 + 四维筛选（状态/类型/关键字/时间） |
| **工作区管理页** `WorkspaceListPage` | `/workspaces`（仅 admin） | per-workspace | 取消/删除 + 关联(②)展开 |

两者重叠部分（跨 ws 表 + 成本/漏洞数/状态/筛选）Dashboard 做得更好；WorkspaceListPage 的**真正独有价值只剩 destructive CRUD**（取消、删除）和关联展开。

此外导航栏 admin 多一项 `工作区管理`（`nav.workspaceManage`），与 `工作区`（`nav.workspaces`）抢同一心智位，**突兀**。

### 顺带澄清：两个被叫「关联」的功能（别搞混）

1. **黑盒复用白盒结果**（`--latest`/`--repo`，`scan_type="blackbox"`）——黑盒按 URL 找最近白盒 deliverables 继续扫。**这才是日常说的「关联」**，单位是扫描任务，就是一行普通黑盒扫描，**不需要任何特殊 UI**。
2. **`correlation` scan_type**（`supernova_multi` 多仓编排，YAML 驱动）——跑 N 个 repo 白盒再合并拓扑/漂移/队列，产父工作区带 `links.child_workspaces`。注释标 `Phase B 接入`，**边缘、基本没用**。只有它才置 `is_correlation=true`。

WorkspaceListPage 的「🔗 展开子工作区」只服务于 ②。结论：**总表上不值得为 ② 加展开层**。

## 2. 决策

**下线 `WorkspaceListPage`（`/workspaces`）**，把它的独有能力拆分安家：

| 能力 | 新家 | 依据 |
|------|------|------|
| 取消运行中扫描 | **Dashboard 表格**，per-scan「操作」列（admin） | `cancelScan(ws, scanId)` 已是 per-scan，行级天然贴合；高频操作放总表合理 |
| 删除工作区 | **切换器抽屉**（`WorkspaceSwitcher`），列表行 trash 图标（admin/manager） | 用户指定「在新增工作区按钮附近」；列表就在「新建工作区」按钮正上方，行内删除最直接 |
| 关联(②)展开 | **丢弃** | 边缘特性，不值得为它在总表加交互层 |

导航栏 `工作区管理` 项一并移除，nav 统一为 `概览 | 工作区 | 扫描 | 设置`（所有角色一致）。

## 3. 组件改动清单

### 3.1 `components/layout/TopBar.tsx`
- 删除 admin 专属的 `nav.workspaceManage → /workspaces` nav 项。
- `items` 不再按 role 分叉，直接用 `NAV`。
- i18n `nav.workspaceManage` 键可留可删（留着无害，删更干净）。

### 3.2 `pages/DashboardPage.tsx`（核心）
- 扫描表新增 admin-only **「操作」列**：
  - `user.role === "admin"` 时渲染列；普通用户无此列（表头也不出）。
  - 行内 `s.is_running || s.status === "running"` → 显示「取消」按钮。
  - 点击 → 确认 Dialog 用 **per-scan 文案** `workspaceDetail.scans.cancelConfirmTitle/Desc`（插值 `{{scanId}}`，与 `ScanList.tsx` 取消卡片统一）；**不复用** ws 级 `workspaces.deleteDialog.cancelDesc`——那个用 `{{ws}}`，措辞「取消扫描 ws-a?」主语含糊。→ 调 `cancelScan(s.workspace, s.scan_id)`。
  - 结果 toast 复用 `workspaces.cancelViaSignal` / `workspaces.cancelWasDead`；失败 `workspaces.actionFailed`。
  - 成功后刷新：`useAsync(listAllScans)` **已暴露 `refresh`**（`lib/useAsync.ts:33`），解构多取一个即可，**无需改 hook / 加 reload 计数器**。
- 表头列名复用 `workspaces.table.actions`（"操作"）。
- StatRow / 运行中卡片 / ScanFilters 不动。

> 注：Dashboard 取消是 **per-scan**（精确到 scanId），比旧 WorkspaceListPage 的 ws 级 `cancelActiveScan`（先 listScans 找 active 再取消）更准。

### 3.3 `components/WorkspaceSwitcher.tsx`（核心）
- 抽屉底部已有「新建工作区」（admin）保留；**顺带修现存小 bug**：`onCreated={() => {}}` 空回调 → `onCreated={refresh}`（否则新建后只靠 useWorkspaces 5s 轮询兜底才刷新列表）。
- **新增删除入口**（admin/manager，即 `user.role === "admin"` 先行；manager 精细化后置）：
  - **行结构重构**（spec 原版漏的 HTML/a11y 陷阱）：当前每行是单个 `<button>`（`WorkspaceSwitcher.tsx:58`），trash 嵌进去 = button-in-button 无效 HTML + 点击冒泡触发 `pick(w.name)` 跳转。改为行 `<div role="button" tabIndex={0} onClick onKeyDown(Enter/Space)>` + trash 作**内部独立 `<button>`** + `e.stopPropagation()`，保持单行视觉不变。
  - trash 图标（`lucide-react` 的 `Trash2`），`size-3.5`，行右侧。
  - 点击 → 确认 Dialog 复用 `workspaces.deleteDialog.deleteTitle/deleteDesc`（`{{ws}}` 插值）。
  - 确认 → `deleteWorkspace(w.name)` → 成功 toast + `refresh()`（`useWorkspaces` 已暴露）；失败 `workspaces.actionFailed`。
  - **删的是 `currentWorkspace` → 必 `nav("/")`**（非可选）：否则 ws 已删、路由仍停 `/p/{ws}`，几秒后 `WorkspaceDetail` 的 `apiGet` 404 才降级到 notFound，有错愕感。
- i18n 复用 `workspaces.deleteDialog.*` + `workspaces.actionFailed`；trash 的 `aria-label` 新增 `workspaceSwitcher.deleteAria`（"删除工作区"）。

### 3.4 `router.tsx`
- 删除 `{ path: "/workspaces", element: <RequireAdmin><WorkspaceListPage /></RequireAdmin> }`。
- 加安全 redirect（旧链接/书签不 404）：`{ path: "/workspaces", element: <Navigate to="/" replace /> }`。
- 删 `WorkspaceListPage` import。

### 3.5 `components/WorkspacesEntry.tsx`
- 空态跳转 `nav("/workspaces")` → `nav("/")`（Dashboard 自带空态「暂无扫描 + 新建扫描」）。
- 其余三段逻辑（pinned → recent）不变。

### 3.6 `routes/WorkspaceDetail/index.tsx`
- 两处「← 返回列表」`to="/workspaces"` → `to="/workspaces-entry"`（notFound 分支 + 主 header 分支）。从 ws 详情「返回」回工作区聚合入口比回全站 Dashboard(`/`)更贴合「上一级」语义；`/workspaces-entry` 自带 pinned/recent 导航。
  - 注：§3.5 的 WorkspacesEntry **空态**仍跳 `/`（Dashboard 自带「暂无扫描 + 新建扫描」空态，比 entry 更适合兜底）——两处语境不同，各自合理，别为了统一而统一。
- i18n `workspaceDetail.backToList` 文案可选改为「← 返回工作区」（非必须，只改 `to` 也行）。

### 3.7 删除文件
- `pages/WorkspaceListPage.tsx`
- `pages/WorkspaceListPage.test.tsx`

## 4. API（均已有，无新增）

```ts
// per-scan 取消（Dashboard 用）
cancelScan(ws: string, scanId: string): Promise<CancelResult>
  // DELETE /api/workspaces/{ws}/scans/{scanId}；res.via="signal" | res.was_dead

// ws 级删除（切换器用）
deleteWorkspace(ws: string): Promise<{ deleted: string }>
  // DELETE /api/workspaces/{ws}

// useWorkspaces() 已暴露 refresh()
```

## 5. 权限

- **Dashboard 取消**：`user.role === "admin"`（对齐旧 WorkspaceListPage 的 `RequireAdmin`；manager 精细化后置，YAGNI）。
- **切换器删除**：`user.role === "admin"` 先行。切换器对所有角色展示（挂在 ws 详情 header），但删除/新建仅 admin 可见。
- 普通用户：Dashboard 无操作列、切换器无 trash/新建——体验不降级（本来也进不了 WorkspaceListPage）。

## 6. 测试

- **新增**
  - `DashboardPage.test.tsx`：admin 渲染操作列 + 取消按钮；点击 → `cancelScan` 被调 + toast；普通用户无操作列。
  - `WorkspaceSwitcher.test.tsx`：admin 行显 trash；点击 → 确认 Dialog → `deleteWorkspace` 被调 + `refresh`；普通用户无 trash。
- **删除**：`WorkspaceListPage.test.tsx`。
- **更新**
  - `router.test.ts`：删 WorkspaceListPage 引用，加 `/workspaces → /` redirect 断言。
  - `App.test.tsx` / `TopBar.test.tsx`：移除 `nav.workspaceManage` 断言。
  - `WorkspacesEntry.test.tsx`：空态跳转目标改 `/`。

## 7. 不做（YAGNI）

- 关联(②)在 Dashboard 的任何展开/特殊呈现——丢弃。
- manager 角色精细化 gating——admin-only 先行。
- Dashboard 上 per-scan「删除扫描」——只做取消；删扫描留在 scan 详情页（如有）。
- 任何后端改动——纯前端，API 全已有。

## 8. 实现顺序建议（回家照着干）

1. 删 nav 项 + 路由 redirect + 删文件 + 改跳转目标（3.1/3.4/3.5/3.6/3.7）——先把页面摘干净，跑测试修断言。
2. Dashboard 加操作列 + 取消（3.2）。
3. 切换器加 trash + 删除（3.3）。
4. 补/改测试（§6），`pnpm test` 绿。
5. commit + push。

## 9. 风险（2026-07-28 摸底后已澄清）

- ~~Dashboard 的 `useAsync` 若无 refetch，需小改 hook 或加 reload 机制——实现时确认。~~ **已消除**：`lib/useAsync.ts:33` 已暴露 `refresh`，解构即用。
- ~~删除当前 `currentWorkspace` 后停留行为——建议跳 `/`，实现时定。~~ **已定**：删 currentWorkspace 必 `nav("/")`（见 §3.3）。
- **新增（摸底发现）**：WorkspaceSwitcher 行内 trash 的 button-in-button 嵌套——行结构须重构为 `div role=button` + 内嵌 button（见 §3.3），spec 原版漏了这个 HTML/a11y 陷阱。
