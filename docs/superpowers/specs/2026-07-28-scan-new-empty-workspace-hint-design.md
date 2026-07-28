# 新建扫描页「无可用工作区」空态提示 — Design

> 日期：2026-07-28 ｜ 分支：feat/fork-py ｜ 状态：已实现（TDD，feat/fork-py 未 commit）｜ 25 测试绿 + tsc 零 + build 成功
>
> 关联 memory：`web-workspace-scan-ia-redesign`（workspace=容器 / scan=任务单元 IA）。

## 1. 背景 / 问题

P1/P2 的 workspace-scan 解耦后，扫描目标 workspace 必须显式选定（`ScanNewPage.tsx:117-119` 校验 `isValid = ... && !!workspace`）。后端 `GET /api/workspaces` 对普通用户只返回其被加入成员名单的工作区（`workspaces.py:54-57`）。

**问题**：一个**未被加入任何工作区的普通用户**打开新建扫描页（`/scan/new`）时：
- 能看到入口、能进页面（路由只在 `RequireAuth` 下，不在 `RequireAdmin` 下，`router.tsx:75`）
- workspace 下拉为空（`wsList = []`，`ScanNewPage.tsx:110`）
- 提交按钮永久 disabled（`workspace` 永远空串）
- **没有任何提示**告诉用户为什么不能扫、该怎么办（`ScanFormFields.tsx:130-144` 无空态分支）
- 现有 `selectWsFirst`（"请先选择 workspace"）对"还没选"和"根本没有"一视同仁，会误导

用户决定：在新建扫描页提示普通用户「找管理员加入工作区」；admin 无 ws（新部署）时一并提示「去创建工作区」。

## 2. 目标

普通用户 / admin 在新建扫描页看到空工作区下拉时，给出**明确的、按角色区分的引导**，而不是一个永远禁用的按钮 + 空下拉。

## 3. 非目标（YAGNI）

- 不改后端（可见性过滤已完成）。
- 不改路由 / 入口屏蔽（保留"能进页面"——让用户看到引导，而不是 403/重定向）。
- 不做整页空态替换（不破坏 whitebox/blackbox/correlation 三 tab 布局；correlation 本就不需要 ws）。
- 不做"申请加入工作区"的交互动作（只提示文案，不发起请求）。
- 不动 `selectWsFirst`（它管 repo 区"未选 ws"，语义独立，保留）。

## 4. 现状证据（file:line）

- 新建扫描页：`packages/web/frontend/src/pages/ScanNewPage.tsx:81`；路由 `router.tsx:75`。
- ws 选择器注入点：`packages/web/frontend/src/components/ScanFormFields.tsx:130-144`（`workspaceField`，`<Select>` + `wsList.map`）。
- wsList 状态与加载：`ScanNewPage.tsx:98`（`useState<Workspace[]>([])`）、`ScanNewPage.tsx:109-111`（`useEffect` 内 `apiGet("/workspaces").then(setWsList).catch(() => {})`）。
- 提交校验：`ScanNewPage.tsx:117-119`。
- 角色判定模式：`WorkspaceSwitcher.tsx:54` / `DashboardPage.tsx:43`（组件内 `useAuth()` + `user?.role === "admin"`）。
- 现有 i18n 命名空间：`scan.fields.wsSelectLabel` / `wsSelectPlaceholder` / `selectWsFirst`（`locales/{zh,en}.json`）。
- 测试基线：`ScanNewPage.test.tsx` 现用 `WS_LIST = [ws1, ws2]`，无空列表用例。

## 5. 设计

### 5.1 改动范围（纯前端）

| 文件 | 改动 |
|---|---|
| `components/ScanFormFields.tsx` | `workspaceField` 加空态分支：下拉内 disabled 空态项 + 下方按角色提示行；组件内 `useAuth()` 判 role |
| `pages/ScanNewPage.tsx` | 新增 `wsLoading` 守卫（初始 true，`/workspaces` settle 后 false），透传给 `ScanFormFields` |
| `locales/zh.json` / `locales/en.json` | 新增 3 个 i18n key（见 5.4） |
| `pages/ScanNewPage.test.tsx` | 新增空态用例（见 5.5） |

后端零改动。

### 5.2 核心行为

**(a) loading 守卫（防闪现假空态）**
`ScanNewPage.tsx:98` 当前 `wsList` 初始即 `[]`，`useEffect` 异步填充——首帧 `[]` 会被误判为"无工作区"闪一下提示。新增 `wsLoading`（初始 `true`，`apiGet("/workspaces")` 的 `.then`/`.catch` 均置 `false`），作为 prop 透传给 `ScanFormFields`。**仅当 `!wsLoading && wsList.length === 0` 进入空态分支**；`wsLoading` 期间显示正常空下拉（placeholder），不显示空态提示。

**(b) 下拉内空态项**
`workspaceField` 的 `<SelectContent>`（`ScanFormFields.tsx:137-141`）：当 `wsList.length === 0` 时，渲染一个 **`disabled` 的 `SelectItem`**（值如 `"__empty__"`，文案 `wsEmptyOption`），替代现在点开"什么都没有"。disabled 保证不可被选中、不影响 `workspace` state。

**(c) 下拉下方提示行**
`workspaceField` 的外层 `<div className="space-y-1.5">` 内、`<Select>` 下方加一行提示：lucide `AlertCircle` 图标 + warning 色调（`text-amber-600 dark:text-amber-400` 之类，与项目 amber token 一致），文案按角色：
- 普通用户（`!isAdmin`）：`wsEmptyHintUser` — 「你还没有被加入任何工作区，请联系管理员将你加入后再创建扫描。」
- admin（`isAdmin`）：`wsEmptyHintAdmin` — 「还没有工作区，请先在工作区切换器中新建一个工作区。」

提示行仅在空态（`!wsLoading && wsList.length === 0`）渲染。

**(d) 角色判定**
`ScanFormFields` 内部 `const { user } = useAuth(); const isAdmin = user?.role === "admin";`，遵循 `WorkspaceSwitcher.tsx:54` / `DashboardPage.tsx:43` 的现有模式（组件内判 role，不从父组件透传 prop）。需要新增 `import { useAuth } from "@/auth/AuthContext"`。

### 5.3 Props 变更

`ScanFormFields` 的 `Props` 新增 `wsLoading: boolean`。`ScanNewPage.tsx:215-225` 调用处补传 `wsLoading={wsLoading}`。

### 5.4 i18n 新增 key（`scan.fields.*`）

| key | zh | en |
|---|---|---|
| `wsEmptyOption` | 暂无可用的工作区 | No workspaces available |
| `wsEmptyHintUser` | 你还没有被加入任何工作区，请联系管理员将你加入后再创建扫描。 | You haven't been added to any workspace. Please contact an administrator to be added before creating a scan. |
| `wsEmptyHintAdmin` | 还没有工作区，请先在工作区切换器中新建一个工作区。 | No workspaces yet. Please create one in the workspace switcher first. |

文案以 review 时确认为准。

### 5.5 测试（`ScanNewPage.test.tsx`，跟随现有 mock 模式）

新增用例（mock `/workspaces` 返回 `[]`，mock `useAuth` 角色）：
1. **普通用户 + 空列表** → 渲染 `wsEmptyHintUser` 文案，且**不**渲染 `wsEmptyHintAdmin`。
2. **admin + 空列表** → 渲染 `wsEmptyHintAdmin` 文案。
3. **非空列表**（沿用现有 `WS_LIST`）→ **不**渲染任何 `wsEmptyHint*` 文案。
4. （可选）`wsLoading` 期间不渲染空态提示。

现有用例保持绿（回归）。

### 5.6 不受影响 / 不变量

- correlation tab：不渲染 `ScanFormFields`（`ScanNewPage.tsx:203-213`），无需 ws，行为不变。
- 有工作区的用户：`wsList.length > 0`，正常下拉，看不到空态。
- 提交校验 `isValid` 逻辑不变（`workspace` 仍需显式选定）；空态下按钮仍 disabled——只是现在用户知道**为什么**。
- `selectWsFirst`（repo 区"未选 ws"）保留，语义独立。

## 6. 实现顺序（TDD）

1. 写失败测试（5.5 用例 1-3）→ 跑测试确认失败。
2. 加 i18n key（zh/en）。
3. 改 `ScanFormFields`（useAuth + 空态分支）+ `ScanNewPage`（wsLoading 守卫 + 透传）。
4. 跑测试至全绿。
5. `tsc --noEmit` 零错 + `vite build` 成功（用 `./node_modules/.bin/...` 直跑，遵循 `web-frontend-vitest-pnpm-gotcha` memory）。

## 7. 验收

- 普通用户无 ws → 新建扫描页下拉点开有"暂无可用的工作区"，下方有"联系管理员"提示。
- admin 无 ws → 下方提示"去创建工作区"。
- 有 ws 的用户 → 无任何空态提示，行为如前。
- loading 期间不闪现空态。
- tsc 零错、build 成功、新增测试绿 + 旧测试不回归。
