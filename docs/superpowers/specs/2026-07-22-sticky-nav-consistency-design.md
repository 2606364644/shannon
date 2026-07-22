# Sticky 导航统一（全局 TopBar + 详情页 Tabs）— 设计

> 日期：2026-07-22
> 主题：把 `packages/web/frontend` 的全局导航与工作区详情页 Tabs 改为 sticky，统一各页面滚动时的导航可达性；补全仓里已预埋但未落地的 sticky 意图。
> 关联：延续 `2026-07-17-web-page-style-unification-design.md` 的「各页面风格统一」方向。

---

## 1. 背景：现状与「半成品」证据

当前（`feat/fork-py`）：

- **全局导航 `TopBar`**（`src/components/layout/TopBar.tsx`）是普通 `<header className="border-b border-border bg-card">`，**非 sticky**——页面滚动时整条导航滚走，长内容（尤其报告页）滚到中部后切页面/切主题够不着。
- **工作区详情页 `WorkspaceDetail`**（`src/routes/WorkspaceDetail/index.tsx`）的 `Tabs`（overview/report/deliverables/logs/live）同样**非 sticky**，滚走。
- **报告页 `ReportTab` → `MarkdownView`** 内部**已经有 sticky chrome**：
  - 右栏 TOC：`<nav data-testid="toc" className="sticky top-4 self-start">`
  - findings 工具栏：`<div className="sticky top-0 z-20 ... bg-background/85 backdrop-blur">`

这造成一个**一致性问题**：报告页内部 chrome 是 sticky，但包住它的全局 TopBar 和详情 Tabs 却不是——读报告时，内部工具栏吸住了、外层导航却飞了，体验割裂。

**半成品证据（强信号，方案据此立项，非新决策）：**

仓里已有 4 处地方把「sticky 层总高 = 80px」当成既定事实：

1. `src/styles/report.css`：`.prose :is(h1, h2, h3) { scroll-margin-top: 80px; }`
2. `src/styles/report.css`：`[data-testid="vuln-card"] { scroll-margin-top: 80px; }`
3. `src/components/MarkdownView.tsx`：vuln-card `className="... scroll-mt-20 ..."`（Tailwind `scroll-mt-20` = 5rem = 80px）
4. `src/components/MarkdownView.tsx`：scroll-spy `rootMargin: "-80px 0px -70% 0px"`

`80px ≈ TopBar h-12(48px) + Tabs h-9(36px) = 84px`（4px 余量，可接受）。即设计者**本就预期 TopBar + Tabs 双层 sticky**，锚点跳转与 scroll-spy 才不会被遮——但 `<header>` / `Tabs` 实际没加 sticky，所以这 80px 目前「空转」。本次就是把这个意图补完。

## 2. 目标 / 非目标

**目标（范围 A，本次做）：**

- G1：全局 `TopBar` sticky——所有页面（Dashboard/Workspaces/Repos/Scan/Settings/详情页及其各 Tab）滚动时导航常驻可达。**改在 `AppShell` 一处，全局生效**，不在每个页面重复加。
- G2：`WorkspaceDetail` 的 `Tabs` sticky——切 Tab 不必滚回顶。
- G3：锚点跳转 / scroll-spy 与新的 sticky 层对齐（沿用既有 80px 常量）。
- G4：打印/导出 PDF 时关掉 sticky，避免 header 重复出现。
- G5：固化「sticky 只在两处出现」的不变量，防回潮。

**非目标（明确不做）：**

- N1：**不改报告页 TOC / findings 工具栏的功能与样式**（`MarkdownView` 内已有的 sticky）——它们已工作良好。**唯一例外**：为避免被新增的全局 sticky 栈遮挡，需同步调整这两处的 sticky `top` 偏移（纯偏移修正，见 §4.2 集成约束），不触碰其 z-index、布局、交互。
- N2：不做报告页 TOC 增强、不做阅读进度条、不做新签名元素（前端设计原则「boldness 花在一处」；签名增强留待后续独立设计）。
- N3：不引入新颜色、新字体、新阴影 token——复用 `tokens.css` 既有 `--card`/`--border`/`--shadow-card`。
- N4：不改移动端整体布局（48px TopBar 已够瘦；Tabs 横向滚动是否需要优化留待移动端专项）。

## 3. 设计：双层 sticky 栈

```
┌─────────────────────────────────────────────────────┐
│ TopBar   ← sticky top-0 z-40   (h-12 = 48px, 全局)    │  ← AppShell 一处，所有页面统一
├─────────────────────────────────────────────────────┤
│  ←详情页专属→ Tabs ← sticky top-12 z-30 (h-9 = 36px)  │  ← 仅 WorkspaceDetail
├─────────────────────────────────────────────────────┤
│  页面内容（自由滚动）                                   │
│   - 报告页：MarkdownView 内部已有 sticky TOC(z-auto)  │
│     + findings 工具栏(sticky top-20 z-20，本次对齐)    │
└─────────────────────────────────────────────────────┘
```

### 3.1 z-index 分层（关键修正：低于弹窗）

仓里 `dialog`/`popover`/`tooltip`/`select`（`src/components/ui/*.tsx`）**统一 `z-50`**。全局 header 必须**低于**弹窗，否则弹窗会被 sticky header 盖住。故采用**严格递减栈**：

| 层 | 组件 | z-index | 备注 |
|----|------|---------|------|
| 弹窗类 | dialog / popover / tooltip / select | `z-50` | 既有，不动；永远在最上 |
| 全局导航 | `TopBar`（sticky） | `z-40` | 本次新增 |
| 详情 Tabs | `WorkspaceDetail` Tabs（sticky） | `z-30` | 本次新增 |
| 报告工具栏 | `MarkdownView` findings bar（sticky） | `z-20` | 既有；§4.2 仅改 `top`（z 不动） |
| 报告 TOC | `MarkdownView` TOC（sticky） | z-auto（0） | 既有；§4.2 仅改 `top`（z 不动） |

（旧方案曾误把 TopBar 设 `z-50`——会导致弹窗被 header 遮挡，已修正为 `z-40`。）

### 3.2 sticky 偏移量与「80px 魔数」

- `TopBar`：`top-0`（贴视口顶）。
- `WorkspaceDetail` Tabs：`top-12`（= 48px，紧贴 TopBar 下沿）。
- 内容锚点避让：沿用既有 `scroll-margin-top: 80px` / `scroll-mt-20` / scroll-spy `-80px`，**不改数值**（84px 理论值与 80px 的 4px 差在视觉上无感知，且改 4 处一致数值属无意义 churn）。
- **文档化**：在 `report.css` 顶部注释里写明「`80px` = sticky 层总高（TopBar 48 + Tabs 36），改 TopBar/Tabs 高度时须同步此常量、MarkdownView scroll-spy rootMargin、以及 findings/TOC 的 `top-20`」。
- **MarkdownView 内 sticky `top` 对齐**：findings 工具栏与 TOC 的 sticky `top` 统一设为 `top-20`(80px)，吸附在 TopBar+Tabs 栈正下方（栈实际下沿 84px，80–84 的 4px 重叠区由 Tabs `z-30` 遮挡，无视觉残缺）。

## 4. 改动点（精确到文件 / 类名）

### 4.1 `src/components/layout/AppShell.tsx`（或 `TopBar.tsx`）

sticky 加在 `<header>` 上（TopBar 组件内部），使全局生效。**最小改动**：

```diff
- <header className="border-b border-border bg-card">
+ <header className="sticky top-0 z-40 border-b border-border bg-card print:static">
```

说明：
- 既有 `border-b border-border bg-card` 已提供分隔与底色（对齐 tokens.css「浮起靠 hairline 边框 + 柔阴影，不靠大亮度差」的 Claude 暖纸感取向），**无需再加滚动监听阴影**（YAGNI；保持纯 CSS、零 JS）。
- `print:static`：导出 PDF 时退回普通流（G4）。
- 不用 `backdrop-blur`/半透明：TopBar 是高频阅读区，实色 `bg-card` 对比最稳；半透明留给报告内部 findings 工具栏（已实现）。

### 4.2 `src/routes/WorkspaceDetail/index.tsx`

把 `Tabs` 包一层 sticky 容器（`TabsList` 自身不改，避免污染 shadcn 组件）：

```diff
  <Tabs value={current} onValueChange={(v) => navigate(v)}>
+   <div className="sticky top-12 z-30 print:static">
    <TabsList>
      {TABS.map((tab) => (
        <TabsTrigger key={tab.value} value={tab.value}>{t(tab.labelKey)}</TabsTrigger>
      ))}
    </TabsList>
+   </div>
  </Tabs>
```

**集成约束（必须验证）：** `MarkdownView` 内已有两处 sticky——findings 工具栏 `sticky top-0 z-20`、右栏 TOC `sticky top-4 self-start`。新增 TopBar(48px)+Tabs(36px) 共 84px 的 sticky 栈后，若这两处仍贴 `top:0` / `top:4`，其顶部会被新 chrome 遮挡：findings 工具栏会被 TopBar **整条盖住**（「全部收起/展开」滚到底点不到）；TOC 标题与前几条会被盖住。

→ **修正两处 sticky `top`**（仅改偏移，不改 z-index / 布局 / 交互）：
- findings 工具栏：`sticky top-0 z-20` → `sticky top-20 z-20`（吸附在 TopBar+Tabs 栈正下方）。
- TOC：`sticky top-4 self-start` → `sticky top-20 self-start`。

两者分处不同 grid 列（TOC 左栏 200px、findings 在内容列），同为 `top:80px` **不互相重叠**；`top-20`(80px) 比栈实际下沿(84px) 少 4px，该重叠区由 Tabs `z-30` 遮挡，无视觉残缺。这是本次范围内**对 `MarkdownView` 仅有的两处触碰**，纯偏移修正以适配新 sticky 栈（非功能/样式改动）。

### 4.3 `src/styles/report.css`

- 在文件顶部注释里**文档化 `80px` 魔数**（见 §3.2），不改数值。
- （可选）追加打印兜底，确保所有 sticky 在 PDF 里都退回 static：
  ```css
  @media print {
    [style*="sticky"], .sticky { position: static !important; }
  }
  ```
  但因 4.1/4.2 已用 `print:static` Tailwind 变体、MarkdownView 的 sticky 是 Tailwind class，**优先靠 `print:` 变体**；本 CSS 规则仅作兜底，可选。

## 5. 一致性不变量（防回潮，写入注释 + 测试）

- **I1（单一来源）**：sticky 导航**只许出现在 `TopBar`（AppShell 层）和 `WorkspaceDetail` Tabs 两处**。任何页面级组件（`DashboardPage` / `ReposPage` / 各 Tab 等）**禁止**自带 sticky header——要 sticky 就在 shell 层做，保证全站统一。
- **I2（z 栈单调）**：弹窗 `z-50` > TopBar `z-40` > Tabs `z-30` > findings bar `z-20`。新增 sticky 元素须落入此单调序列，不得越级。
- **I3（sticky 总高常量）**：TopBar + Tabs 高度和 ≈ 80px（魔数）。改任一者高度，须同步 4 处：`report.css` 的 `scroll-margin-top`、`MarkdownView` 的 `scroll-mt-20`、scroll-spy `rootMargin`、以及 findings/TOC 的 `top-20`。
- **I4（打印退回）**：所有 sticky 导航元素带 `print:static`。

## 6. 可访问性 / 动效

- **键盘焦点**：sticky 不影响 Tab 键焦点顺序；TopBar/Tabs 本就是可聚焦链接，无额外工作。
- `prefers-reduced-motion`：本次不引入新动效（`scroll-behavior: smooth` 已在 `report.css` 且已尊重 reduced-motion）；sticky 是布局非动效，无需处理。
- **色彩对比**：`bg-card` + `text-foreground` / `text-muted-foreground` 沿用既有 token，AA 不变。

## 7. 测试（Vitest + RTL，对齐既有 `*.test.tsx` 风格）

仓内已有完整测试基建（`src/routes/WorkspaceDetail/index.test.tsx`、`ReportTab.test.tsx`、`MarkdownView.test.tsx`、`App.test.tsx`、`styles/report.test.ts`、`styles/tokens.test.ts`）。新增：

- **T1（TopBar sticky + print）**：在 `AppShell`/`TopBar` 测试里断言 `<header>` className 含 `sticky`、`top-0`、`z-40`、`print:static`。
- **T2（Tabs sticky）**：在 `WorkspaceDetail/index.test.tsx` 里断言 Tabs 外层容器含 `sticky`、`top-12`、`z-30`、`print:static`。
- **T3（z 栈单调不变量，I2）**：新增 `src/styles/sticky-zindex.test.ts`（仿 `styles/report.test.ts` 的源码字符串校验风格），断言：
  - TopBar header 含 `z-40`
  - WorkspaceDetail Tabs 容器含 `z-30`
  - MarkdownView findings bar 含 `z-20`
  - `dialog.tsx`/`popover.tsx`/`tooltip.tsx`/`select.tsx` 含 `z-50`
  - 并断言数值单调：50 > 40 > 30 > 20（从源码抽取的字符串不变量，防回潮）。
- **T4（MarkdownView sticky top 修正）**：`MarkdownView.test.tsx` 断言 findings 工具栏 className 含 `top-20`（原 `top-0` 已改）、TOC 含 `top-20`（原 `top-4` 已改），且两者 z 不变（`z-20` / z-auto）。若既有测试断言旧 `top-0`/`top-4`，同步更新为 `top-20`。
- **T5（回归）**：跑既有 `ReportTab.test.tsx`、`WorkspaceDetail.test.tsx`、`App.test.tsx`、`MarkdownView.test.tsx`，确认 sticky 改动未破坏锚点跳转 / scroll-spy / Tab 切换。

> 测试策略说明：sticky 的真实吸顶行为依赖真实布局引擎，jsdom 无法测「滚动后是否吸住」；故测**类名不变量**（sticky/top/z 存在且单调），而非视觉行为。这是仓内既有 CSS 测试（`styles/*.test.ts`）的同款思路。

## 8. 验收标准

- AC1：任意页面（Dashboard / Workspaces / 报告页）滚动到底，TopBar 仍可见可点。
- AC2：报告页滚动时，TopBar(48) + Tabs(36) 双层吸顶；`MarkdownView` 的 findings 工具栏与 TOC 同步吸附在栈正下方（`top-20` = 80px），互不遮挡、可正常点击「全部收起/展开」。
- AC3：报告内锚点跳转（TOC 点击、exec-summary hero 的 vuln id 链接）落点不被 sticky 层遮挡（80px 余量生效）。
- AC4：打开任意 dialog / popover（如 AddRepoDialog），弹窗**盖住** sticky TopBar（z 栈正确）。
- AC5：浏览器「打印预览」/导出 PDF，TopBar 与 Tabs 不重复出现（退回 static）。
- AC6：T1–T5 测试全绿；既有相关测试无回归。
- AC7：深色 / 浅色双主题下，TopBar 底色与边框对比正常（沿用 token，预期自动生效）。

## 9. 风险与回滚

- **风险 R1**：某页面 main 内容自身设了 `overflow: auto/hidden`，会令父级 sticky 失效。已知 `main` 是 `mx-auto max-w-[1400px] px-7 py-5`（无 overflow），低风险。若发现个别页 sticky 不生效，查该页根容器 overflow。
- **风险 R2**：findings bar / TOC 改 `top-20` 后，在「非报告页」无影响（该组件只在 ReportTab 用）。低风险。
- **回滚**：纯 className 增删，`git revert` 单 commit 即可；无数据/路由/状态变更。

## 10. 实现顺序（供后续 plan 拆解）

1. `TopBar.tsx`：header 加 `sticky top-0 z-40 print:static`（最小、全局立即生效）。
2. `WorkspaceDetail/index.tsx`：Tabs 包 `sticky top-12 z-30 print:static` 容器。
3. `MarkdownView.tsx`：findings 工具栏 `top-0` → `top-20`、TOC `top-4` → `top-20`（集成约束修正，§4.2）。
4. `report.css`：顶部注释文档化 80px 魔数（+ 可选打印兜底）。
5. 测试 T1–T5。
6. `npm run build`（`tsc -b && vite build`）+ `npm test`（仅相关文件）验证。
