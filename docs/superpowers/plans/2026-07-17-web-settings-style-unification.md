# /settings 页风格对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

> **背景：** 补漏计划。前一计划 `2026-07-17-web-page-style-unification`（spec `docs/superpowers/specs/2026-07-17-web-page-style-unification-design.md`）已落地于 `/`、`/workspaces`、`/repos`、`/scan/new` 四页（commit `e9281a18`–`f19fb462`），遗漏了 `/settings`。本计划把同一规范（`PageHeader`）套到 settings 页。

**Goal:** 把 `/settings` 标题区对齐到四页已落地的统一规范（`PageHeader`，`text-xl`）。

**Architecture:** 纯前端样式迁移。settings 标题是旧手写 `<h1 className="text-2xl">`，换成 `<PageHeader>`；容器间距 `space-y-6` → `space-y-4`（对齐其他页）。settings 无统计行 / 无主 CTA 按钮（设置页不需要），故只对齐标题区，不动三张 Card。

**Tech Stack:** React + TypeScript + Tailwind + shadcn/ui + vitest + @testing-library/react + react-i18next。

## Global Constraints

- 只动 `packages/web/frontend/src/pages/SettingsPage.tsx`
- **不改** 三张 Card（主题 / 系统状态 / 关于）结构 —— 它们是 settings 主体内容
- **复用** 现有 `settings.title` i18n 键（`设置` / `Settings`），不新增键
- **不破坏** `SettingsPage.test.tsx` 现有断言
- 参照规范：`PageHeader`（`src/components/PageHeader.tsx`，props `{title, subtitle?}`），已落地于四页
- 测试命令：`pnpm test src/pages/SettingsPage.test.tsx`；类型：`pnpm exec tsc -b`；前端工作目录 `packages/web/frontend`
- 分支 `feat/fork-py`

## File Structure

| 文件 | 改动 |
|---|---|
| `src/pages/SettingsPage.tsx` | `h1`→`PageHeader`、`space-y-6`→`space-y-4`、加 import |

---

### Task 1: SettingsPage 标题对齐 PageHeader

**Files:**
- Modify: `src/pages/SettingsPage.tsx`
- Test（回归基线，不改）: `src/pages/SettingsPage.test.tsx`

**测试设计说明：** 标题从手写 `<h1 className="text-2xl">` 换成 `<PageHeader>`，heading 文字 `t("settings.title")`（`设置`/`Settings`）不变，无新语义。现有测试（line 77/89 断言标题文本、line 30–32 断言三张 Card 标题）为回归基线，不新增脆弱的 className 断言。

- [ ] **Step 1: 建立绿基线**

Run（在 `packages/web/frontend`）: `pnpm test src/pages/SettingsPage.test.tsx`
Expected: 全部 PASS（确认改动前基线绿）。

- [ ] **Step 2: 加 import**

在 `SettingsPage.tsx` 顶部 import 区（`import { ErrorState } from "@/components/ErrorState";` 附近）新增：

```tsx
import { PageHeader } from "@/components/PageHeader";
```

- [ ] **Step 3: 标题换 PageHeader + 容器间距统一**

把：

```tsx
    <div className="space-y-6">
      <h1 className="font-semibold tracking-tight text-2xl">{t("settings.title")}</h1>
```

替换为：

```tsx
    <div className="space-y-4">
      <PageHeader title={t("settings.title")} />
```

> 不传 `subtitle`：`PageHeader` 的 `subtitle` 可选，不传则只渲染 `h1`；settings 内容是三张 Card，标题「设置」已足够，与其他页 `h1` 部分一致。如后续想要副标题，加 `subtitle={t("settings.subtitle")}` 并补 i18n 键即可。

- [ ] **Step 4: 跑回归**

Run: `pnpm test src/pages/SettingsPage.test.tsx`
Expected: 全部 PASS（标题文本 `设置`/`Settings` 与三 Card 标题断言不破）。

- [ ] **Step 5: 类型检查**

Run: `pnpm exec tsc -b`
Expected: 零错误。

- [ ] **Step 6: Commit**

```bash
git add src/pages/SettingsPage.tsx
git commit -m "refactor(web): settings 页标题对齐 PageHeader"
```

---

### Task 2: 真机冒烟

**Files:** 无（仅验证）

- [ ] **Step 1: 视觉核对**

刷新（或 rebuild）web，访问 `http://172.27.206.189:7878/settings`，核对：
- 标题「设置」字号与其他页一致（`text-xl`，不再是 `text-2xl`）
- 三张 Card（主题 / 系统状态 / 关于）内容与交互不变（主题 Switch、状态字段、Temporal 徽章等）
- 与 `/`、`/workspaces` 等页标题视觉一致
