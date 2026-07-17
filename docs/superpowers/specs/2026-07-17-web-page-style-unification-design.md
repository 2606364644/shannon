# WEB 四页风格统一设计

> 日期：2026-07-17
> 范围：`packages/web/frontend` 前端四页（首页 `/`、`/workspaces`、`/repos`、`/scan/new`）的标题区、统计摘要、主操作按钮风格统一
> 分支：`feat/fork-py`

## 1. 背景

用户反馈：首页 `http://172.27.206.189:7878/`（`/`）与 `/workspaces`、`/repos` 风格不一致 —— 字体风格、顶部"运行中/已完成"统计框样式不同；并要求 `/scan/new` 的"开始扫描"按钮移到最左，四页风格大体统一。

## 2. 根因

**首页是旧手写代码，未跟随后来抽取的统一组件 `PageHeader` / `StatRow`：**

| 维度 | 首页 `/`（DashboardPage） | `/workspaces`、`/repos` |
|---|---|---|
| 标题 | 手写 `<h1 className="text-2xl">ft-shannon</h1>`（与顶栏品牌重复，且 `text-2xl` 比其他页 `text-xl` 大） | 统一 `<PageHeader>`（`text-xl` + subtitle） |
| 统计框 | 手写 4 个 shadcn `<Card>`（`shadow-card` 浮起阴影 + `p-4` + `gap-4` + `font-mono text-2xl` 数字） | 统一 `<StatRow>`（扁平 `div` 无阴影 + `p-2.5` + `gap-2` + `text-lg font-semibold tabular-nums`） |
| label | `text-xs` 普通字 | `text-xs uppercase tracking-wide` |

**主操作按钮跨页不统一：**

| 页 | 按钮 | 现状 |
|---|---|---|
| `/` | + 新建扫描 | `primary`，标题行右侧 |
| `/workspaces` | 新建扫描 | `variant="outline" size="sm"`（无填充色、`text-xs` 偏小），混在工具栏过滤器中间 |
| `/repos` | 添加仓库 | `primary` 默认 size，与搜索框挤在 `justify-end` 右侧 |
| `/scan/new` | 开始扫描 | `size="lg"`，卡片底部操作栏右侧（`footerHint` 占左） |

## 3. 统一规范

| 元素 | 规则 |
|---|---|
| 标题区 | 全部用 `<PageHeader>`（`text-xl font-semibold tracking-tight` + `text-sm muted` subtitle） |
| 统计摘要 | 全部用 `<StatRow>`（4 格扁平卡，`text-lg font-semibold tabular-nums` 数字 + `uppercase` label） |
| 主 CTA 按钮 | ① `primary` 配色 ② 放所在工具/操作栏**最左** ③ 列表页默认 `size`（md），扫描页提交保留 `lg` |

「CTA 放最左」为统一原则（用户对扫描页的偏好推广到所有页）。

## 4. 逐页改动

### 4.1 首页 `/` — `src/pages/DashboardPage.tsx`
- 删除手写标题 `<h1>ft-shannon</h1>` 及其外层 `flex items-center justify-between` 容器（含右侧按钮）
- 改为 `<PageHeader title={t("dashboard.title")} subtitle={t("dashboard.subtitle")} />`
- 删除手写 4 个 `<Card><CardContent className="p-4">`（含 `font-mono text-2xl` 数字），改为：
  ```tsx
  <StatRow stats={[
    { label: t("dashboard.stats.running"), value: running.length, tone: "cyan" },
    { label: t("dashboard.stats.completedToday"), value: completedToday.length, tone: "green" },
    { label: t("dashboard.stats.totalVulns"), value: totalVulns },
    { label: t("dashboard.stats.totalCost"), value: fmtCost(totalCost, data[0]?.cost_currency) },
  ]} />
  ```
- 「+ 新建扫描」按钮移到 StatRow 下方一行最左：
  ```tsx
  <div className="flex items-center gap-3">
    <Link to="/scan/new"><Button>{t("dashboard.newScan")}</Button></Link>
  </div>
  ```
- running 卡片墙、recent 列表保持不动

### 4.2 `/workspaces` — `src/pages/WorkspaceListPage.tsx`
- 工具栏行内：「新建扫描」`<Button variant="outline" size="sm">` → `<Button>`（primary 默认 size）
- 该 `<Link><Button>` 从过滤器之间移到工具栏行**第一个**（搜索框之前）
- 最终顺序：`[新建扫描] [搜索] [状态过滤] [类型过滤] … [ml-auto 最后刷新 + ↻]`

### 4.3 `/repos` — `src/pages/ReposPage.tsx`
- 原工具栏：`<div className="... justify-end ..."><div className="flex items-center gap-2"><Input search/><Button addRepo/></div></div>`
- 改为：`<div className="flex flex-wrap items-center gap-3"><Button addRepo/><Input search className="w-56"/></div>`（去 `justify-end`，CTA 最左，搜索跟后）

### 4.4 `/scan/new` — `src/pages/ScanNewPage.tsx`
- 标题区手写 `<div><h1 className="text-xl font-semibold tracking-tight">…</h1><p className="text-sm text-muted-foreground mt-0.5">…</p></div>` → `<PageHeader title={t("scan.title")} subtitle={t(subtitleKey)} />`
- 底部操作栏 `<div className="flex items-center justify-between px-5 py-3.5 border-t border-border bg-card">`：
  - 子元素顺序互换 → `<Button size="lg" …>{submitLabel}</Button>`（左）+ `<span className="text-xs text-muted-foreground">{footerHint}</span>`（右）
- correlation 的全宽提交按钮不动

## 5. i18n 改动（`src/locales/{zh,en}.json`）
新增两键（其余复用现有 `dashboard.stats.*` / `scan.*` / `workspaces.newScan` / `repos.addRepo`）：
- `dashboard.title`：`仪表盘` / `Dashboard`
- `dashboard.subtitle`：`扫描概览与最近活动` / `Scan overview and recent activity`

## 6. 不动的部分
- 各页表格、首页 running 卡片墙与 recent 列表、扫描表单字段与侧栏
- `StatRow` / `PageHeader` 组件本身、顶栏 `TopBar`、`AppShell`

## 7. 测试影响
- `DashboardPage.test.tsx`：统计数字（`15` / `$4.00`）、按钮 href、i18n 标签断言不破坏；`uppercase` 仅 CSS 不改 textContent；**约束：subtitle 须无孤立数字 `1`/`15`**（否则撞 `getAllByText("1")` 断言）——「扫描概览与最近活动」无数字，安全
- `WorkspaceListPage` / `ReposPage` / `ScanNewPage` 测试：按钮文字与 href 不变，位置/配色改变不影响 `getByRole`/`getByText` 断言
- 实现时逐一跑四个测试文件 + `tsc`

## 8. 验证
- `pnpm test` 跑四个相关测试文件全绿
- `tsc` / build 零错误
- rebuild web 容器，真机对比 `/`、`/workspaces`、`/repos`、`/scan/new` 四页标题、统计行、主按钮视觉一致
