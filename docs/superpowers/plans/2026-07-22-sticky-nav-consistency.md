# Sticky 导航统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 supernova-web-frontend 的全局 `TopBar` 与工作区详情页 `Tabs` 改为 sticky，并同步对齐报告页内部已有的 sticky 元素偏移，使各页面滚动时导航常驻可达、且 sticky 层栈正确分层。

**Architecture:** 双层 sticky 栈——全局 `TopBar`(`top-0 z-40`) + 详情页 `Tabs`(`top-12 z-30`)，报告页内部既有 sticky 元素(findings 工具栏 `z-20`、TOC)统一吸附到 `top-20`(80px) 即栈正下方。z 栈严格递减且低于弹窗 `z-50`，保证 dialog/popover 永远不被吸顶 header 遮挡。纯 className 改动，零 JS、零新色。

**Tech Stack:** React 18 + Vite + TypeScript + Tailwind CSS 3.4（`print:` 变体、`top-12`/`top-20` 间距）+ Radix UI（shadcn）+ Vitest + @testing-library/react + msw。

**Spec:** `docs/superpowers/specs/2026-07-22-sticky-nav-consistency-design.md`

## Global Constraints

（每个 task 的需求都隐含以下约束，逐字摘自 spec §3.1 / §3.2 / §5 / §6）

- **z 栈单调（I2）**：弹窗 `z-50` > TopBar `z-40` > Tabs `z-30` > findings `z-20`。新增 sticky 须落入此序列。
- **80px 魔数（I3）**：TopBar(`h-12`=48px) + Tabs(`h-9`=36px) = 84px ≈ 80px。仓里已有 4 处用 80px：`report.css` 的 `scroll-margin-top: 80px`（h1-3 与 vuln-card）、`MarkdownView` 的 `scroll-mt-20`、scroll-spy `rootMargin: "-80px..."`。**本次不改这些数值**。
- **打印退回（I4）**：所有新增 sticky 导航元素带 Tailwind `print:static` 变体。
- **单一来源（I1）**：sticky 导航只许出现在 `TopBar` 与 `WorkspaceDetail` Tabs 两处；页面级组件禁止自带 sticky header。
- **零新色 / 零 JS**：复用既有 `bg-card`/`border-border` token，不引滚动监听。

## File Structure

| 文件 | 责任 | 本次动作 |
|------|------|----------|
| `packages/web/frontend/src/components/layout/TopBar.tsx` | 全局导航 header | 改 `<header>` className（+sticky） |
| `packages/web/frontend/src/components/layout/TopBar.test.tsx` | TopBar DOM 测试 | 加 sticky 类名断言 |
| `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx` | 详情页 shell + Tabs | 包一层 sticky 容器 div |
| `packages/web/frontend/src/routes/WorkspaceDetail/index.test.tsx` | 详情页 shell 测试 | 加 sticky 容器断言 |
| `packages/web/frontend/src/components/MarkdownView.tsx` | 报告渲染（含 TOC + findings 工具栏） | findings/TOC 的 `top` 对齐到 `top-20`；findings 加 testid |
| `packages/web/frontend/src/components/MarkdownView.test.tsx` | 报告渲染测试 | 加 findings/TOC 的 `top-20` 断言 |
| `packages/web/frontend/src/styles/sticky-zindex.test.ts` | **新建**：跨文件 z 栈不变量护栏 | 源码字符串断言（仿 `styles/tokens.test.ts`） |
| `packages/web/frontend/src/styles/report.css` | 报告样式 | 顶部注释文档化 80px 魔数 |

---

### Task 1: 全局 TopBar sticky

**Files:**
- Modify: `packages/web/frontend/src/components/layout/TopBar.tsx`（`<header>` 行）
- Test: `packages/web/frontend/src/components/layout/TopBar.test.tsx`

**Interfaces:**
- Produces: TopBar `<header data-testid="topbar">` className 含 `sticky top-0 z-40 ... print:static`；后续 task 的 z 护栏测试（Task 4）断言此文件含 `z-40`，Task 5 可选打印 CSS 命中 `[data-testid="topbar"]`。

- [ ] **Step 1: 写失败测试**（在 `TopBar.test.tsx` 末尾追加一个 describe）

在 `packages/web/frontend/src/components/layout/TopBar.test.tsx` 文件末尾追加：

```tsx
describe("TopBar sticky 吸顶", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("header 含 sticky/top-0/z-40/print:static（全局吸顶，低于弹窗 z-50）", () => {
    render(
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    );
    const header = screen.getByTestId("topbar");
    expect(header.tagName).toBe("HEADER");
    expect(header.className).toContain("sticky");
    expect(header.className).toContain("top-0");
    expect(header.className).toContain("z-40");
    expect(header.className).toContain("print:static");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/components/layout/TopBar.test.tsx`
Expected: FAIL — 新增的 sticky 用例失败（`screen.getByTestId("topbar")` 抛「Unable to find」，因 header 当前无该 testid）。其余既有用例应仍 PASS。

- [ ] **Step 3: 最小实现**（改 TopBar.tsx 的 header className）

在 `packages/web/frontend/src/components/layout/TopBar.tsx`，把：

```tsx
    <header className="border-b border-border bg-card">
```

改为（加 `data-testid` 便于测试与打印 CSS 定位；加 sticky 吸顶）：

```tsx
    <header data-testid="topbar" className="sticky top-0 z-40 border-b border-border bg-card print:static">
```

（既有 `border-b border-border bg-card` 保留提供分隔与底色；`sticky top-0 z-40` 吸顶且低于弹窗；`print:static` 导 PDF 时退回普通流；`data-testid="topbar"` 供测试与 Task 5 可选打印兜底 CSS 精确命中。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/components/layout/TopBar.test.tsx`
Expected: PASS（含新 sticky 用例 + 全部既有用例）。

- [ ] **Step 5: 提交**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/layout/TopBar.tsx packages/web/frontend/src/components/layout/TopBar.test.tsx
git commit -m "feat(web): TopBar 全局 sticky 吸顶（z-40，低于弹窗 z-50）

补全 report.css scroll-margin:80px 已预埋但未落地的 sticky 意图。
print:static 退回，导 PDF 不重复出现 header。spec §4.1。"
```

---

### Task 2: 工作区详情页 Tabs sticky

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`（`<Tabs>` 块）
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/index.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 TopBar sticky（Tabs `top-12`=48px 紧贴其下）。
- Produces: Tabs 外层 `<div data-testid="wd-tabs-sticky" className="sticky top-12 z-30 print:static">`；Task 4 护栏断言此文件含 `z-30`。

- [ ] **Step 1: 写失败测试**（在 `index.test.tsx` 的 `describe("WorkspaceDetail shell", ...)` 内追加一条用例）

在 `packages/web/frontend/src/routes/WorkspaceDetail/index.test.tsx` 的 `describe("WorkspaceDetail shell", () => { ... })` 块内、最后一条用例之后追加：

```tsx
  it("Tabs 外层容器 sticky 吸顶（top-12 z-30，紧贴 TopBar 下沿）", () => {
    renderAt("/p/ws/overview");
    const sticky = screen.getByTestId("wd-tabs-sticky");
    expect(sticky.className).toContain("sticky");
    expect(sticky.className).toContain("top-12");
    expect(sticky.className).toContain("z-30");
    expect(sticky.className).toContain("print:static");
  });
```

（`renderAt` + `screen` 均已在文件顶部 import；`screen.getByTestId` 是 RTL 定位 data-testid 元素的惯用法，报错也比 querySelector 更友好。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/index.test.tsx`
Expected: FAIL — 新用例 `screen.getByTestId("wd-tabs-sticky")` 抛「Unable to find an element by: [data-testid="wd-tabs-sticky"]」（容器尚未加）。

- [ ] **Step 3: 最小实现**（包一层 sticky div）

在 `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`，把 `<Tabs>` 块：

```tsx
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <TabsList>
          {TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
```

改为（在 `TabsList` 外包一层 sticky div）：

```tsx
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <div data-testid="wd-tabs-sticky" className="sticky top-12 z-30 print:static">
          <TabsList>
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>
```

（`top-12`=48px 紧贴 TopBar 下沿；`z-30` 高于 findings `z-20`、低于弹窗 `z-50`。不改 `TabsList` 自身，避免污染 shadcn 组件。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/routes/WorkspaceDetail/index.test.tsx`
Expected: PASS（含新用例 + 全部既有 tablist/notFound/i18n 用例）。

- [ ] **Step 5: 提交**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/routes/WorkspaceDetail/index.tsx packages/web/frontend/src/routes/WorkspaceDetail/index.test.tsx
git commit -m "feat(web): WorkspaceDetail Tabs sticky 吸顶（top-12 z-30）

切 Tab 不必滚回顶。容器加 data-testid 便于测试定位。spec §4.2。"
```

---

### Task 3: 报告页 findings/TOC sticky `top` 对齐（集成约束）

> **为何必做**：Task 1/2 新增了 84px 的 sticky 栈。`MarkdownView` 内 findings 工具栏现 `sticky top-0` 会被 TopBar 整条盖住（「全部收起/展开」点不到）；TOC 现 `sticky top-4` 的标题与前几条会被盖。两者 `top` 须对齐到 `top-20`(80px) = 栈正下方。纯偏移修正，不改 z/布局/交互（spec §4.2 / N1）。

**Files:**
- Modify: `packages/web/frontend/src/components/MarkdownView.tsx`（findings 工具栏 div + TOC nav，两处 className）
- Test: `packages/web/frontend/src/components/MarkdownView.test.tsx`

**Interfaces:**
- Consumes: Task 1/2 的 sticky 栈（findings/TOC 须让位到 `top-20`）。
- Produces: findings 工具栏加 `data-testid="findings-bar"`；findings/TOC 的 `top` 改为 `top-20`；Task 4 护栏断言此文件含 `z-20`。

- [ ] **Step 1: 写失败测试**（在 `MarkdownView.test.tsx` 追加一个 describe）

在 `packages/web/frontend/src/components/MarkdownView.test.tsx` 文件末尾追加（`MD` fixture 已含 vuln 块 → `hasVulns=true` → findings 工具栏会渲染；heading ≥2 → TOC 会渲染）：

```tsx
describe("MarkdownView sticky top 对齐全局栈（集成约束）", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("findings 工具栏 sticky top-20（吸附在 TopBar+Tabs 栈正下方），z-20 不变", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const bar = container.querySelector('[data-testid="findings-bar"]');
    expect(bar).not.toBeNull();
    expect(bar?.className).toContain("sticky");
    expect(bar?.className).toContain("top-20");
    expect(bar?.className).toContain("z-20");
    expect(bar?.className).not.toContain("top-0"); // 旧值已改
  });

  it("TOC sticky top-20（旧 top-4 已改），不再贴视口顶被 chrome 盖", () => {
    const { container } = render(<MarkdownView markdown={MD} />);
    const toc = container.querySelector('[data-testid="toc"]');
    expect(toc).not.toBeNull();
    expect(toc?.className).toContain("sticky");
    expect(toc?.className).toContain("top-20");
    expect(toc?.className).not.toContain("top-4"); // 旧值已改
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx`
Expected: FAIL — findings 用例因 `[data-testid="findings-bar"]` 当前不存在（null）；TOC 用例因 className 仍含 `top-4`、不含 `top-20`。既有用例应仍 PASS（它们只断言 TOC 的 textContent/links，不断言 className）。

- [ ] **Step 3: 最小实现**（改 MarkdownView.tsx 两处 className + 加 testid）

**3a.** findings 工具栏：在 `packages/web/frontend/src/components/MarkdownView.tsx`，把：

```tsx
            <div className="sticky top-0 z-20 -mx-1 mb-1 flex items-center justify-between gap-2 border-b border-border/60 bg-background/85 px-1 py-1.5 backdrop-blur supports-[backdrop-filter]:bg-background/70">
```

改为（`top-0`→`top-20`，并加 `data-testid` 便于测试定位）：

```tsx
            <div data-testid="findings-bar" className="sticky top-20 z-20 -mx-1 mb-1 flex items-center justify-between gap-2 border-b border-border/60 bg-background/85 px-1 py-1.5 backdrop-blur supports-[backdrop-filter]:bg-background/70">
```

**3b.** TOC nav：同文件，把：

```tsx
          <nav data-testid="toc" aria-label={t("markdown.tocAria")} className="sticky top-4 self-start">
```

改为（`top-4`→`top-20`）：

```tsx
          <nav data-testid="toc" aria-label={t("markdown.tocAria")} className="sticky top-20 self-start">
```

（findings 与 TOC 分处不同 grid 列，同 `top:80px` 不互相重叠；`top-20`(80px) 比栈实际下沿(84px) 少 4px，重叠区由 Tabs `z-30` 遮挡，无视觉残缺。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/components/MarkdownView.test.tsx`
Expected: PASS（含两条新用例 + 全部既有 TOC/vuln/i18n 用例）。

- [ ] **Step 5: 提交**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/components/MarkdownView.tsx packages/web/frontend/src/components/MarkdownView.test.tsx
git commit -m "fix(web): 报告 findings/TOC sticky top 对齐全局栈（top-20）

TopBar+Tabs 新增 84px sticky 栈后，findings 工具栏(top-0)会被整条盖住、
TOC(top-4)标题会被盖。两者 top 统一改 top-20(80px)=栈正下方。
纯偏移修正，z/布局/交互不变。spec §4.2 集成约束。"
```

---

### Task 4: z-index 栈不变量护栏测试

> **为何独立成 task**：z 栈正确（弹窗 > 导航）是跨 6 个文件的不变量，单文件 DOM 测覆盖不到「弹窗 z-50 永远最上」。用源码字符串护栏（仿 `styles/tokens.test.ts`）固化，防后人改 className 时回潮。

**Files:**
- Create: `packages/web/frontend/src/styles/sticky-zindex.test.ts`
- Test: 同上（纯测试，无产品代码改动）

**Interfaces:**
- Consumes: Task 1（TopBar.tsx 含 `z-40`）、Task 2（WorkspaceDetail/index.tsx 含 `z-30`）、Task 3（MarkdownView.tsx 含 `z-20`）已落地。弹窗类 `z-50` 是既有现状。

- [ ] **Step 1: 写测试**（新建文件）

创建 `packages/web/frontend/src/styles/sticky-zindex.test.ts`：

```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// sticky z-index 栈不变量（spec §3.1 / §5-I2）：
//   弹窗 z-50 > TopBar z-40 > Tabs z-30 > findings z-20
// 导航层必须低于弹窗，否则 dialog/popover/tooltip/select 会被吸顶 header 遮挡。
// 本测试读源码字符串（同 styles/tokens.test.ts 风格），防后人改 className 时回潮。
const SRC = resolve(__dirname, "..");
const topbar = readFileSync(resolve(SRC, "components/layout/TopBar.tsx"), "utf8");
const wd = readFileSync(resolve(SRC, "routes/WorkspaceDetail/index.tsx"), "utf8");
const md = readFileSync(resolve(SRC, "components/MarkdownView.tsx"), "utf8");
const dialog = readFileSync(resolve(SRC, "components/ui/dialog.tsx"), "utf8");
const popover = readFileSync(resolve(SRC, "components/ui/popover.tsx"), "utf8");
const tooltip = readFileSync(resolve(SRC, "components/ui/tooltip.tsx"), "utf8");
const select = readFileSync(resolve(SRC, "components/ui/select.tsx"), "utf8");

describe("sticky z-index 栈不变量", () => {
  it("TopBar header 含 z-40（导航层，低于弹窗）", () => {
    expect(topbar).toContain("z-40");
  });
  it("WorkspaceDetail Tabs 容器含 z-30", () => {
    expect(wd).toContain("z-30");
  });
  it("MarkdownView findings 工具栏含 z-20", () => {
    expect(md).toContain("z-20");
  });
  it("弹窗类（dialog/popover/tooltip/select）统一 z-50，永远最上", () => {
    expect(dialog).toContain("z-50");
    expect(popover).toContain("z-50");
    expect(tooltip).toContain("z-50");
    expect(select).toContain("z-50");
  });
});
```

- [ ] **Step 2: 跑测试确认通过**（依赖 Task 1-3 已合入；此 task 无产品代码改动，故无「先失败」环节）

Run: `cd packages/web/frontend && npx vitest run src/styles/sticky-zindex.test.ts`
Expected: PASS（4 条用例全绿）。若任一 FAIL，说明 Task 1-3 的 className 未正确落地——回查对应 task。

- [ ] **Step 3: 提交**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/styles/sticky-zindex.test.ts
git commit -m "test(web): sticky z-index 栈不变量护栏

固化「弹窗 z-50 > TopBar z-40 > Tabs z-30 > findings z-20」跨文件不变量，
防后人改 className 时导航层越级盖住弹窗。spec §5-I2 / §7-T3。"
```

---

### Task 5: 文档化 80px 魔数 + 全量回归 + 构建验证

**Files:**
- Modify: `packages/web/frontend/src/styles/report.css`（顶部注释）

**Interfaces:**
- Consumes: Task 1-4 全部落地。本 task 是收尾：把「80px = sticky 栈总高」写进 `report.css` 注释（spec §3.2 文档化、§5-I3），并跑全量相关测试 + 构建。

- [ ] **Step 1: 在 report.css 顶部注释文档化 80px 魔数**

在 `packages/web/frontend/src/styles/report.css`，把开头注释：

```css
/* 报告页专属：hljs token 配色（接 DSF --c- 语义色）+ 表格 + scroll-margin + pre 背景。
   深色（:root 默认）为主，.light 覆盖一套调亮度保 AA 对比。 */
```

改为（追加一段魔数说明）：

```css
/* 报告页专属：hljs token 配色（接 DSF --c- 语义色）+ 表格 + scroll-margin + pre 背景。
   深色（:root 默认）为主，.light 覆盖一套调亮度保 AA 对比。

   ★ 80px = sticky 栈总高（TopBar h-12=48px + 详情 Tabs h-9=36px ≈ 84px，取整 80）。
   仓里 4 处依赖此常量（改 TopBar/Tabs 高度时须同步全部）：
     - 本文件 .prose :is(h1,h2,h3) 与 [data-testid="vuln-card"] 的 scroll-margin-top
     - MarkdownView.tsx vuln-card 的 scroll-mt-20 + scroll-spy rootMargin("-80px...")
     - MarkdownView.tsx findings 工具栏 / TOC 的 sticky top-20（吸附在栈正下方）
   详见 docs/superpowers/specs/2026-07-22-sticky-nav-consistency-design.md §3.2 / §5-I3。 */
```

- [ ] **Step 1b（可选）：追加打印兜底 CSS**

> spec §4.3 把此规则标为可选——TopBar/Tabs 已靠 `print:static` 变体满足 AC5；本规则作兜底，覆盖未来可能新增、忘加 `print:static` 的 sticky 元素。不执行本步不影响验收。

在 `packages/web/frontend/src/styles/report.css` 末尾追加：

```css
/* 打印兜底：所有 sticky 退回 static，避免 PDF 每页重复出现吸顶元素
   （TopBar/Tabs 已有 print:static 变体；本规则兜底未来新增 sticky）。 */
@media print {
  [data-testid="topbar"],
  [data-testid="wd-tabs-sticky"],
  [data-testid="findings-bar"],
  [data-testid="toc"] { position: static !important; }
}
```

（按 testid 精确命中本次涉及的 4 个 sticky 元素，避免全局 `.sticky` 通配误伤。）

- [ ] **Step 2: 跑 report.css 既有测试确认注释未破坏断言**

Run: `cd packages/web/frontend && npx vitest run src/styles/report.test.ts`
Expected: PASS（既有用例断言 `scroll-margin-top` / `.hljs-*` / `.prose table` 等仍命中；注释不影响）。

- [ ] **Step 3: 跑本次全部 touched 测试做回归**

Run（一次性跑本次涉及的 5 个测试文件）:

```bash
cd packages/web/frontend && npx vitest run \
  src/components/layout/TopBar.test.tsx \
  src/components/layout/AppShell.test.tsx \
  src/routes/WorkspaceDetail/index.test.tsx \
  src/components/MarkdownView.test.tsx \
  src/styles/sticky-zindex.test.ts \
  src/styles/report.test.ts \
  src/styles/tokens.test.ts
```

Expected: 全部 PASS。若 `App.test.tsx`/`WorkspaceDetail.test.tsx` 等未列入的测试也想顺带跑，可加；但 CLAUDE.md 提示「只跑改动相关测试文件」，勿广跑全套。

- [ ] **Step 4: TypeScript + Vite 构建验证**

Run: `cd packages/web/frontend && npm run build`
Expected: 成功（`tsc -b` 无类型错误，`vite build` 产出 `dist/`）。重点关注无「Property '...' does not exist」之类因 className/testid 改动引入的类型错误（预期无，因改动全是字符串字面量）。

- [ ] **Step 5: 提交**

```bash
cd /root/shannon-py
git add packages/web/frontend/src/styles/report.css
git commit -m "docs(web): report.css 文档化 80px sticky 栈总高魔数

把「TopBar+Tabs=80px」及 4 处依赖点写进注释，改高度时强制同步。
spec §3.2 / §5-I3。"
```

---

## 验收对照（spec §8 AC → task）

- AC1（任意页滚到底 TopBar 可见）→ Task 1
- AC2（TopBar+Tabs 双层吸顶；findings/TOC 在栈正下方 `top-20` 可点）→ Task 1 + 2 + 3
- AC3（锚点跳转不被遮，80px 生效）→ 已有 80px 不变 + Task 3 对齐 findings/TOC
- AC4（弹窗盖住 sticky TopBar）→ Task 4 护栏固化 z 栈
- AC5（打印预览 TopBar/Tabs 不重复）→ Task 1/2 的 `print:static`
- AC6（T1–T5 测试全绿、无回归）→ Task 1-5
- AC7（深/浅双主题对比正常）→ 沿用 token，Task 5 构建验证

## 风险提示（执行时留意）

- **R1**：若某页面 sticky 不生效，查该页根容器是否设了 `overflow: auto/hidden`（会令父级 sticky 失效）。已知 `main` 无 overflow，低风险。
- **R2**：findings/TOC 改 `top-20` 只影响 ReportTab（该组件仅报告页用），非报告页无影响。
- **回滚**：纯 className 增删，`git revert` 对应 commit 即可。
