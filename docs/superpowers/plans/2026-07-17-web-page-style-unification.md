# WEB 四页风格统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把首页 `/`、`/workspaces`、`/repos`、`/scan/new` 四页的标题区、统计摘要、主操作按钮统一到 `PageHeader` + `StatRow` + 「primary CTA 置左」规范。

**Architecture:** 纯前端样式/组件迁移。首页是旧手写代码，未跟上后来抽取的 `PageHeader`/`StatRow` 组件 —— 把它迁过去；另外三页的主操作按钮配色/位置跨页不一致，统一为 `primary` + 所在栏最左。不改 `StatRow`/`PageHeader` 组件本身，不动业务表格与表单内容。

**Tech Stack:** React + TypeScript + Tailwind + shadcn/ui + vitest + @testing-library/react + react-i18next。

## Global Constraints

- 只动 `packages/web/frontend`，分支 `feat/fork-py`
- **不改** `StatRow` / `PageHeader` 组件本身（`src/components/StatRow.tsx`、`src/components/PageHeader.tsx`）
- **不改** 各页表格、首页 running 卡片墙与 recent 列表、扫描表单字段与侧栏
- locale 改动必须**中英双语同步**（`src/locales/zh.json` + `src/locales/en.json`）
- 首页 `dashboard.subtitle` 文案**不得含孤立数字 `1`/`15`**（撞 `DashboardPage.test.tsx` 的 `getAllByText("1")` 断言）—— 用「扫描概览与最近活动」
- 每页改完跑该页 `.test.tsx` 回归，**不许破坏现有断言**
- 测试命令：`pnpm test <file>`（= `vitest run`）；类型/构建：`pnpm build`（= `tsc -b && vite build`）。前端工作目录：`packages/web/frontend`
- 每个 Task 完成后单独 commit

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `src/pages/DashboardPage.tsx` | 首页 | 标题→PageHeader、4 Card→StatRow、按钮移 StatRow 下方左置、加 import |
| `src/pages/WorkspaceListPage.tsx` | /workspaces | 新建扫描按钮 outline-sm→primary、移工具栏最左 |
| `src/pages/ReposPage.tsx` | /repos | 添加仓库按钮移工具栏最左（去 justify-end） |
| `src/pages/ScanNewPage.tsx` | /scan/new | 标题手写→PageHeader、底部栏按钮左置(span 右)、加 import |
| `src/locales/zh.json` / `en.json` | i18n | 新增 `dashboard.title` / `dashboard.subtitle` |
| `src/pages/DashboardPage.test.tsx` | 首页测试 | 新增 PageHeader 标题断言 |

---

### Task 1: 首页 DashboardPage 统一（PageHeader + StatRow + 主按钮左置 + i18n）

**Files:**
- Modify: `src/pages/DashboardPage.tsx`
- Modify: `src/locales/zh.json`、`src/locales/en.json`
- Test: `src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `PageHeader` (`src/components/PageHeader.tsx`，props `{title, subtitle?}`)、`StatRow` (`src/components/StatRow.tsx`，props `{stats: StatItem[]}`，`StatItem = {label, value: ReactNode, tone?: "default"|"cyan"|"green"|"red"}`)
- Produces: 首页渲染 `heading`「仪表盘」+ 副标题「扫描概览与最近活动」；统计行用 StatRow（4 格，tone: cyan/green/default/default）

- [ ] **Step 1: 写失败测试 —— 断言 PageHeader 标题与副标题**

在 `src/pages/DashboardPage.test.tsx` 的 `describe("DashboardPage 骨架 + 汇总", ...)` 块末尾（`it("无 running → 显示空态文案", ...)` 之后、闭合 `})` 之前）新增：

```tsx
  it("标题区统一为 PageHeader：显「仪表盘」+ 副标题", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "仪表盘" })).toBeInTheDocument();
    expect(screen.getByText("扫描概览与最近活动")).toBeInTheDocument();
  });
```

- [ ] **Step 2: 跑测试确认 fail**

Run（在 `packages/web/frontend`）: `pnpm test src/pages/DashboardPage.test.tsx`
Expected: 新增用例 FAIL —— 当前首页 heading 文本是 `ft-shannon`，找不到 `name: "仪表盘"`。其余现有用例应仍 PASS。

- [ ] **Step 3: 加 i18n 键（中英）**

在 `src/locales/zh.json` 的 `"dashboard"` 对象内、`"newScan"` 之前插入两键：

```json
  "dashboard": {
    "title": "仪表盘",
    "subtitle": "扫描概览与最近活动",
    "newScan": "+ 新建扫描",
```

在 `src/locales/en.json` 的 `"dashboard"` 对象内、`"newScan"` 之前插入：

```json
  "dashboard": {
    "title": "Dashboard",
    "subtitle": "Scan overview and recent activity",
    "newScan": "+ New scan",
```

- [ ] **Step 4: 改 DashboardPage.tsx —— 加 import**

在 `DashboardPage.tsx` 顶部 import 区（`import { StatusBadge } from "@/components/StatusBadge";` 附近）新增两行：

```tsx
import { PageHeader } from "@/components/PageHeader";
import { StatRow } from "@/components/StatRow";
```

- [ ] **Step 5: 改 DashboardPage.tsx —— 替换标题区 + 统计区**

把 `return` 内的开头（标题行 + 4 个 Card 网格）：

```tsx
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="font-semibold tracking-tight text-2xl">ft-shannon</h1>
        <Link to="/scan/new"><Button>{t("dashboard.newScan")}</Button></Link>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4" role="group" aria-label={t("dashboard.summaryAria")}>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">{t("dashboard.stats.running")}</div>
          <div className="font-mono text-2xl text-cyan">{running.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">{t("dashboard.stats.completedToday")}</div>
          <div className="font-mono text-2xl text-green">{completedToday.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">{t("dashboard.stats.totalVulns")}</div>
          <div className="font-mono text-2xl">{totalVulns}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">{t("dashboard.stats.totalCost")}</div>
          <div className="font-mono text-2xl">{fmtCost(totalCost, data[0]?.cost_currency)}</div>
        </CardContent></Card>
      </div>
```

替换为：

```tsx
    <div className="space-y-4">
      <PageHeader title={t("dashboard.title")} subtitle={t("dashboard.subtitle")} />
      <StatRow stats={[
        { label: t("dashboard.stats.running"), value: running.length, tone: "cyan" },
        { label: t("dashboard.stats.completedToday"), value: completedToday.length, tone: "green" },
        { label: t("dashboard.stats.totalVulns"), value: totalVulns },
        { label: t("dashboard.stats.totalCost"), value: fmtCost(totalCost, data[0]?.cost_currency) },
      ]} />
      <div className="flex items-center gap-3">
        <Link to="/scan/new"><Button>{t("dashboard.newScan")}</Button></Link>
      </div>
```

> 说明：`dashboard.summaryAria` 键替换后不再被引用，**保留不删**（避免扩大改动；孤儿 i18n 键无害）。`Card`/`CardContent`/`StatusBadge`/`Badge` 等 import 保留 —— running 卡片墙与 recent 列表仍在用。

- [ ] **Step 6: 跑测试确认全绿（新用例 + 现有回归）**

Run: `pnpm test src/pages/DashboardPage.test.tsx`
Expected: 全部 PASS，包括：新增「仪表盘」标题用例、汇总数字（`15`/`$4.00`/`getAllByText("1")` 长度 2）、`+ 新建扫描` 按钮 href、i18n 标签（`今日完成`/`Completed today` 等）。

- [ ] **Step 7: 类型检查**

Run: `pnpm exec tsc -b`
Expected: 零错误。

- [ ] **Step 8: Commit**

```bash
git add src/pages/DashboardPage.tsx src/pages/DashboardPage.test.tsx src/locales/zh.json src/locales/en.json
git commit -m "feat(web): 首页统一 PageHeader+StatRow+主按钮左置"
```

---

### Task 2: /workspaces 新建扫描按钮统一（primary + 工具栏最左）

**Files:**
- Modify: `src/pages/WorkspaceListPage.tsx`
- Test（回归基线，不改）: `src/pages/WorkspaceListPage.test.tsx`

**测试设计说明：** 本任务是纯样式（按钮 `outline-sm`→`primary`）+ 纯位置（工具栏中间→最左）迁移，**无新用户可见语义**（按钮文字、href、role 不变）。新增 className/顺序断言会脆弱，故以现有测试（含 `getByRole("button", {name: /新建扫描/})` line 208、标题/概览条用例）为**回归基线**，视觉一致性靠真机冒烟。

- [ ] **Step 1: 建立绿基线**

Run: `pnpm test src/pages/WorkspaceListPage.test.tsx`
Expected: 全部 PASS（确认改动前基线绿）。

- [ ] **Step 2: 改工具栏行 —— 按钮提到最左 + primary 化**

在 `WorkspaceListPage.tsx` 工具栏行，把：

```tsx
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder={t("workspaces.searchPlaceholder")}
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="max-w-xs"
        />
```

与（该行靠后段）：

```tsx
        <Link to="/scan/new">
          <Button variant="outline" size="sm">{t("workspaces.newScan")}</Button>
        </Link>
        <div className="ml-auto flex items-center gap-3">
```

调整为 —— 按钮移到工具栏行**第一个**（Input 之前）并去掉 `variant="outline" size="sm"`。最终工具栏行开头变成：

```tsx
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/scan/new">
          <Button>{t("workspaces.newScan")}</Button>
        </Link>
        <Input
          placeholder={t("workspaces.searchPlaceholder")}
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="max-w-xs"
        />
```

并删除原位置（两个 Select 之后、`ml-auto` div 之前）的那段 `<Link to="/scan/new"><Button variant="outline" size="sm">…</Button></Link>`。最终顺序：`[新建扫描 primary] [搜索] [状态过滤] [类型过滤] … [ml-auto 最后刷新 + ↻]`。

- [ ] **Step 3: 跑回归**

Run: `pnpm test src/pages/WorkspaceListPage.test.tsx`
Expected: 全部 PASS（按钮文字/href 不变，回归不破）。

- [ ] **Step 4: Commit**

```bash
git add src/pages/WorkspaceListPage.tsx
git commit -m "refactor(web): workspaces 新建扫描按钮 primary 化并左置"
```

---

### Task 3: /repos 添加仓库按钮统一（工具栏最左）

**Files:**
- Modify: `src/pages/ReposPage.tsx`
- Test（回归基线，不改）: `src/pages/ReposPage.test.tsx`

**测试设计说明：** 同 Task 2，纯位置迁移（按钮从 `justify-end` 右侧→最左），无新语义。现有测试（概览条 `.uppercase` 定位、副标题、删除 Dialog 等）为回归基线。

- [ ] **Step 1: 建立绿基线**

Run: `pnpm test src/pages/ReposPage.test.tsx`
Expected: 全部 PASS。

- [ ] **Step 2: 改工具栏行 —— CTA 最左、搜索跟后**

把 `ReposPage.tsx` 中：

```tsx
        <div className="flex flex-wrap items-center justify-end gap-3">
          <div className="flex items-center gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("repos.searchPlaceholder")}
              className="w-56"
              aria-label={t("repos.searchPlaceholder")}
            />
            <Button onClick={() => setAddOpen(true)}>{t("repos.addRepo")}</Button>
          </div>
        </div>
```

替换为（去 `justify-end`、去嵌套 inner div、按钮最左、搜索跟后）：

```tsx
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => setAddOpen(true)}>{t("repos.addRepo")}</Button>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("repos.searchPlaceholder")}
            className="w-56"
            aria-label={t("repos.searchPlaceholder")}
          />
        </div>
```

- [ ] **Step 3: 跑回归**

Run: `pnpm test src/pages/ReposPage.test.tsx`
Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add src/pages/ReposPage.tsx
git commit -m "refactor(web): repos 添加仓库按钮左置"
```

---

### Task 4: /scan/new 标题用 PageHeader + 开始扫描按钮左置

**Files:**
- Modify: `src/pages/ScanNewPage.tsx`
- Test（回归基线，不改）: `src/pages/ScanNewPage.test.tsx`

**测试设计说明：** 标题从手写 `<h1>+<p>` 换成 `<PageHeader>`，DOM 结构与 class 几乎一致（heading 文字 `t("scan.title")` 不变），无新语义；底部栏按钮左右互换（外层 div class 不变）。现有测试（含 line 396–401 取 `button.parentElement` 断言 `bg-card` —— 只交换子元素，parent 不变，仍通过）为回归基线。

- [ ] **Step 1: 建立绿基线**

Run: `pnpm test src/pages/ScanNewPage.test.tsx`
Expected: 全部 PASS。

- [ ] **Step 2: 加 import**

在 `ScanNewPage.tsx` 顶部 import 区（`import { Button } from "@/components/ui/button";` 附近）新增：

```tsx
import { PageHeader } from "@/components/PageHeader";
```

- [ ] **Step 3: 标题区换 PageHeader**

把：

```tsx
      {/* 页面标题 */}
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{t("scan.title")}</h1>
        <p className="text-sm text-muted-foreground mt-0.5">{t(subtitleKey)}</p>
      </div>
```

替换为：

```tsx
      {/* 页面标题 */}
      <PageHeader title={t("scan.title")} subtitle={t(subtitleKey)} />
```

- [ ] **Step 4: 底部操作栏 —— 按钮左置、提示右置**

把：

```tsx
        {/* 底部操作栏 */}
        {type !== "correlation" && (
          <div className="flex items-center justify-between px-5 py-3.5 border-t border-border bg-card">
            <span className="text-xs text-muted-foreground">{footerHint}</span>
            <Button size="lg" onClick={onSubmit} disabled={!isValid || submitting}>
              {submitLabel}
            </Button>
          </div>
        )}
```

替换为（仅交换子元素顺序，外层 div className **原样保留**）：

```tsx
        {/* 底部操作栏 */}
        {type !== "correlation" && (
          <div className="flex items-center justify-between px-5 py-3.5 border-t border-border bg-card">
            <Button size="lg" onClick={onSubmit} disabled={!isValid || submitting}>
              {submitLabel}
            </Button>
            <span className="text-xs text-muted-foreground">{footerHint}</span>
          </div>
        )}
```

> correlation 的全宽提交按钮（`type === "correlation"` 那段）不动。

- [ ] **Step 5: 跑回归**

Run: `pnpm test src/pages/ScanNewPage.test.tsx`
Expected: 全部 PASS（含底部栏 `bg-card` 配色用例 line 396–401）。

- [ ] **Step 6: Commit**

```bash
git add src/pages/ScanNewPage.tsx
git commit -m "refactor(web): scan 页标题用 PageHeader + 开始按钮左置"
```

---

### Task 5: 全量验证 + 真机冒烟

**Files:** 无（仅验证）

- [ ] **Step 1: 四页测试同跑**

Run: `pnpm test src/pages/DashboardPage.test.tsx src/pages/WorkspaceListPage.test.tsx src/pages/ReposPage.test.tsx src/pages/ScanNewPage.test.tsx`
Expected: 全部 PASS。

- [ ] **Step 2: 全量类型/构建**

Run: `pnpm build`
Expected: `tsc -b` 零错误，`vite build` 成功。

- [ ] **Step 3: 真机视觉冒烟**

rebuild web 容器（项目用 `up.sh`，见 CLAUDE.md / memory；Dockerfile 改才需 rebuild，本任务只改前端源码 —— 若容器以 bind mount 挂源码则刷新即可，否则 rebuild）。访问 `http://172.27.206.189:7878/`，逐页核对：
- `/`：标题「仪表盘」+副标题；统计行 4 格扁平卡（无大阴影、数字比例字体 tabular-nums）；「+ 新建扫描」在统计行下方最左
- `/workspaces`：标题/统计行不变；「新建扫描」按钮 primary 填充色、工具栏最左
- `/repos`：「添加仓库」按钮工具栏最左、搜索框跟后
- `/scan/new`：标题样式不变；底部栏「开始扫描」按钮在左、footerHint 在右
- 四页标题字号、统计行、主按钮配色/位置**视觉一致**

- [ ] **Step 4: 若 rebuild 了容器，确认无回归**

人工确认四页交互正常（搜索、过滤、提交、取消/删除 Dialog 等）。
