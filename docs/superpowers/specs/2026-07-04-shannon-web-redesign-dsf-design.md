# 子项目 1 · DSF 设计系统地基

> 上位 spec：`docs/superpowers/specs/2026-07-04-shannon-web-redesign-design.md`（IA / 视觉语言 / 四条 shadcn 约束 / 子项目分解 / 迁移策略）。本子 spec 只聚焦 DSF 实现细节，不重复上位内容。

## 范围与完成定义

**做**：
1. 双主题 token 层（三层 CSS 变量 + shadcn token 映射 + 浅色调亮 AA）
2. Tailwind 装配（`tailwind.config.ts` + PostCSS + `@tailwindcss/typography`）
3. shadcn/ui 初始化（CLI + `components.json` + 首批组件 copy）
4. 全局 Layout（`<AppShell>` + `<TopBar>` + `<ThemeToggle>`），套到所有现有路由
5. 基础组件库（清单见 §4）
6. dev 预览页 `/dev/components`（dev-only 路由，罗列组件各状态，生产 build 不暴露）
7. 测试基建（每组件 vitest 单测 + token 漂移断言 + ThemeToggle 测试）

**不做**（留给子项目 2-5）：
- 任何业务页面**内部**内容重做（列表 / 扫描 / 详情 5 tab / Dashboard / 设置页内部）。
- 文件浏览器、DataTable 业务集成（DSF 只就绪 `<Table>` 原子，TanStack 集成留子项目 2）。

**完成定义**：
- 访问任意现有路由（`/`、`/scan/new`、`/p/:ws/*`）均见新 TopBar 外壳；页面内部内容暂留旧样式（增量迁移，旧 `events.css` 保留）。
- `/dev/components` 罗列全部基础组件的深 / 浅双主题各状态。
- ThemeToggle 切换并刷新后主题持久化。
- 现有测试全绿（`dashboardReducer` 对齐测试等不被破坏）+ 新组件测试绿。

---

## 1. 双主题 token 层

### 1.1 strategy
- `<html class="dark | light">`，class 由 `ThemeToggle` 写入。
- 持久化 key：`localStorage["shannon-theme"] = "dark" | "light"`。
- 首访（无 key）：读 `window.matchMedia("(prefers-color-scheme: dark)")` 决定，默认深色（与现有 `--void:#0B0F14` 一致）。
- 防 FOUC：在 `index.html` 内联一小段 `<script>`（main bundle 之前）读 localStorage 写 `<html class>`。

### 1.2 三层 CSS 变量（`src/styles/tokens.css`，三层分离）

**层 A · shadcn token**（深 / 浅各一组，供 shadcn 组件消费）：

| token | 深（hex 初值） | 浅（hex 初值，实施时验 AA） |
|---|---|---|
| `--background` | `#0B0F14` (void) | `#FFFFFF` |
| `--foreground` | `#C9D1D9` (ink) | `#1F2733` |
| `--card` | `#141A22` (panel) | `#FFFFFF` |
| `--card-foreground` | `#C9D1D9` | `#1F2733` |
| `--popover` | `#141A22` | `#FFFFFF` |
| `--popover-foreground` | `#C9D1D9` | `#1F2733` |
| `--primary` | `#22D3EE` (cyan) | `#0891B2`（cyan 调深保 AA） |
| `--primary-foreground` | `#0B0F14` (void) | `#FFFFFF` |
| `--secondary` | `#1F2733` (rule) | `#F1F4F8` |
| `--secondary-foreground` | `#C9D1D9` | `#1F2733` |
| `--muted` | `#1F2733` | `#F1F4F8` |
| `--muted-foreground` | `#6B7785` (trace) | `#6B7785` |
| `--accent` | `#1F2733` | `#E8EEF4` |
| `--accent-foreground` | `#C9D1D9` | `#1F2733` |
| `--destructive` | `#F85149` (red) | `#DC2626`（red 调深保 AA） |
| `--destructive-foreground` | `#0B0F14` | `#FFFFFF` |
| `--border` | `#1F2733` (rule) | `#D1D9E0` |
| `--input` | `#1F2733` | `#D1D9E0` |
| `--ring` | `#22D3EE` (cyan) | `#0891B2` |
| `--radius` | `3px` | `3px` |

> shadcn 原生用 HSL / oklch channel 三元组（`H S L` 不含 `hsl()` 包裹，便于 `hsl(var(--x))` 拼接）。实施时把上表 hex 转 HSL 三元组写法（或升 oklch，依项目 shadcn 版本）；上表 hex 是**语义真值的来源**，转换不得改语义。

**层 B · 语义色**（深 / 浅各一组，跨媒介不变量，事件 / 日志 / Markdown / DashboardPanel 消费）：

| token | 深 | 浅（实施时验 AA） |
|---|---|---|
| `--cyan` | `#22D3EE` | `#0891B2` |
| `--magenta` | `#BC8CFF` | `#7C3AED` |
| `--green` | `#3FB950` | `#1A7F37` |
| `--red` | `#F85149` | `#DC2626` |
| `--yellow` | `#D29922` | `#B58105` |

事件专用 class（`.ev-phase` 等）继续定义在 `events.css`，消费层 B 变量（深 / 浅自动切）。

**层 C · 字体 / 间距 / 圆角 / 阴影**：
- `--font-mono: "IBM Plex Mono", ui-monospace, monospace`
- `--font-sans: "IBM Plex Sans", system-ui, sans-serif`
- `--font-serif: "IBM Plex Serif", Georgia, serif`
- 间距：用 Tailwind 默认 spacing scale（4px grid），不自定义。
- 字号：用 Tailwind 默认（`text-sm/base/lg/xl`），不自定义 major-third（保 shadcn 默认协调）。
- 圆角：`--radius: 3px`（operator 克制；shadcn 默认 `0.5rem` 偏圆，覆盖）。派生 `--radius-sm: 2px`、`--radius-md: 3px`、`--radius-lg: 4px`。
- 阴影：克制——`--shadow-1: 0 1px 2px rgba(0,0,0,.3)`、深色用、浅色降透明度。

### 1.3 Plex 字体注入
- `index.html` 已 preconnect + load Google Fonts（IBM Plex Mono/Sans/Serif），不动。
- `tailwind.config.ts` `theme.extend.fontFamily`：`mono / sans / serif` 注入 Plex 三族，**覆盖 shadcn 默认 font**。

---

## 2. 全局 Layout

### 2.1 `<AppShell>`（`src/components/layout/AppShell.tsx`）
- 结构：`<TopBar /> + <main class="app-main">{children / Outlet}</main>`。
- `<main>` 容器：`max-width: 1400px; margin: 0 auto; padding: 20px 28px`（对齐旧 `.page` 尺寸，子项目 2-5 迁页时改消费 Tailwind 容器类）。
- 在 `router.tsx` 包一层 `<AppShell>` 作为根 layout（所有路由共用）。

### 2.2 `<TopBar>`（`src/components/layout/TopBar.tsx`）
```
┌───────────────────────────────────────────────────────────┐
│ ⬡ Shannon   Dashboard  Workspaces  Scan  Settings    🌓 ⚙ │
└───────────────────────────────────────────────────────────┘
```
- 左：`⬡ Shannon` 字标（Plex Serif，cyan ⬡ + ink "Shannon"）→ 点回 `/`。
- 中：主导航 NavLink（Dashboard / Workspaces / Scan / Settings）。active 态：cyan + 下边框 2px；inactive：trace 色。**DSF 阶段（迁移期）启用状态**：
  - `Workspaces` NavLink 指现有路由 `/`（WorkspaceListPage 当前位置，子项目 2 改路由为 `/workspaces` 后同步改 NavLink 目标）——**启用**。
  - `Scan` NavLink 指 `/scan/new`——**启用**。
  - `Dashboard` NavLink（未来 `/`）——**disabled**（页未建，子项目 5 启用）。
  - `Settings` NavLink（未来 `/settings`）——**disabled**（页未建，子项目 5 启用）。
- 右：
  - 运行中扫描指示器（可选，DSF 阶段先占位 slot，子项目 5 接 SSE）：小转圈 + 当前 ws 名。
  - `<ThemeToggle>` 🌓。
  - ⚙ → `/settings`（DSF 阶段设置页未建，先 `<NavLink to="/settings" disabled>` 或隐藏，子项目 5 启用）。

### 2.3 `<ThemeToggle>`（`src/components/layout/ThemeToggle.tsx`）
- shadcn `<Button variant="ghost" size="icon">`，图标 🌓 / ☀️ / 🌙 随当前主题切换。
- 点击：当前 dark → light（反之亦然），写 `<html class>` + `localStorage`。
- 初始值：mount 时读 localStorage / prefers-color-scheme（与 §1.1 防 FOUC 脚本一致）。
- 尊重 `prefers-reduced-motion`（图标过渡禁用）。

### 2.4 二级 tab 保留
- Detail 页现有 `<nav class="tab-nav">`（`WorkspaceDetail/index.tsx`）**保留不动**（已是水平条，与 TopBar 视觉同源）。子项目 4 重做 Detail 时再决定是否换 shadcn `<Tabs>`。

---

## 3. Tailwind 工程化

### 3.1 依赖
- 新增 devDeps：`tailwindcss` `postcss` `autoprefixer` `@tailwindcss/typography` `tailwindcss-animate`（shadcn 依赖）、`class-variance-authority` `clsx` `tailwind-merge`（shadcn `cn()` 工具）、`lucide-react`（shadcn 图标，克制使用，不冲淡 Plex 风）。
- shadcn 组件按需 copy（不全装），首批见 §4。

### 3.2 `tailwind.config.ts`
- `darkMode: ["class"]`
- `content: ["./index.html", "./src/**/*.{ts,tsx}"]`
- `theme.extend`：
  - `fontFamily: { mono, sans, serif }` ← Plex 三族
  - `colors`：注入语义色（`cyan / magenta / green / red / yellow` 各 `DEFAULT + fg`），引用层 B 变量
  - shadcn 标准 `colors` 块（`border / input / ring / background / foreground / primary ...`）引用层 A 变量（HSL/oklch 写法）
  - `borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 1px)", sm: "calc(var(--radius) - 2px)" }`
  - `keyframes / animation`：`accordion-down/up`（shadcn 标配）+ braille spinner（复用现有 `@keyframes spin`）
- `plugins: [require("@tailwindcss/typography"), require("tailwindcss-animate")]`

### 3.3 entry CSS（`src/styles/index.css`）
- `@tailwind base; @tailwind components; @tailwind utilities;`
- `@import "./tokens.css";`（层 A/B/C 变量，`:root` 深、`.light` 浅）
- 旧 `events.css`：保留 `import "./events.css"`（迁移期），文件头加 `@deprecated` 注释。

### 3.4 preflight 协调（已知坑）
- Tailwind preflight 清空 element 默认样式 → `react-markdown` 输出（`<p> <h2> <ul> <code> <strong>`）失样式。
- 缓解：react-markdown 容器加 `className="prose prose-sm dark:prose-invert"`（`@tailwindcss/typography`），子项目 4 报告 tab 验证。DSF 阶段先在 `MarkdownView` 容器加 `prose` 类验证不破，详细调校留子项目 4。
- 事件专用 class `.ev-*` 不受 preflight 影响（class 选择器特异性）。

### 3.5 旧 CSS 处置（增量迁移）
- `events.css` / `markdown.css` **保留**，文件头标 `@deprecated 迁移期保留，子项目 2-5 逐页消化`。
- 新组件全 Tailwind，禁止向 `events.css` 追加新规则。
- 旧 class 名（`.page / .ledger / .form-area / .segmented / .submit-btn`…）**不重用于新组件**（避免迁移期语义模糊）。

---

## 4. 基础组件清单（shadcn 为基 + operator 调校）

每组件：`<组件>` 职责 · shadcn 基 · operator 调校点 · 测试要点。

| 组件 | 职责 | shadcn 基 | 调校点 |
|---|---|---|---|
| `Button` | 主操作 | `button.tsx` | variant: `default(primary-cyan) / secondary / ghost / destructive / outline`；size: `sm / default / icon`；radius 3px |
| `Input` | 文本输入 | `input.tsx` | Plex Mono；focus ring ← cyan；placeholder trace 色 |
| `Textarea` | 多行 | `textarea.tsx` | 同 Input |
| `Select` | 下拉 | `select.tsx`（Radix） | Plex Mono；option 样式 |
| `Checkbox` | 勾选 | `checkbox.tsx` | accent ← cyan |
| `Switch` | 开关 | `switch.tsx` | checked ← cyan |
| `Table` | 表格原子 | `table.tsx` | 等宽台账风（header trace 色 + 正常字重，对齐旧 `.ledger th`）；DataTable 业务集成留子项目 2 |
| `Card` | 卡片容器 | `card.tsx` | 含可达性左边框变体（`.reachable` `border-left: 3px solid var(--red)`，供 VulnCard 复用） |
| `Badge` | 徽章 | `badge.tsx` | 双轨徽章变体：`llm`(magenta 💭) / `gitnexus`(cyan 🔍) / `both`(green ✓) + `confidence` + 可达性 ●；StatusBadge 复用 |
| `Dialog` | 模态 | `dialog.tsx`（Radix） | 文件浏览器（子项目 2）/ 设置确认 / 删除确认复用 |
| `Tabs` | 区段切换 | `tabs.tsx`（Radix） | Dashboard 区段（子项目 5）；Detail 二级 tab 暂不动 |
| `Tooltip` | 悬浮提示 | `tooltip.tsx`（Radix） | 表单 ⓘ 标注复用（如 `--latest` 语义） |
| `Toast` | 通知 | `toast.tsx` + `use-toast`（Radix） | 提交失败 / 操作反馈复用（替代裸 `.err-banner`） |
| `Empty` | 空态 | 自写（非 shadcn） | icon + 文案 + CTA button slot |
| `Skeleton` | loading | `skeleton.tsx` | 列表 / 卡片 loading 占位 |
| `Spinner` | 转圈 | 自写 | braille 帧动画（复用现有 `@keyframes spin`），`prefers-reduced-motion` fallback |
| `ThemeToggle` | 主题切换 | 自写（用 Button） | 见 §2.3 |

**所有组件**：
- 接 `className` prop（用 `cn()` 合并），允许业务覆盖。
- 双主题：深 / 浅均正确渲染（dev 预览页双主题各状态展示）。
- a11y：focus visible / aria-label / 键盘操作（Radix 基础组件白嫖；自写组件 Empty/Spinner/ThemeToggle 手补）。

---

## 5. dev 预览页 `/dev/components`

- 路由 `/dev/components`，**仅 dev build 暴露**：`router.tsx` 用 `import.meta.env.DEV` 守卫，生产 build 不注册该路由。
- 内容：罗列 §4 全部组件，每种 variant × 深浅双主题（用 ThemeToggle 切换或并列两 panel）。
- 不在 TopBar 主导航（仅 dev 直接访问 URL）。
- 用途：人工冒烟组件视觉 + 双主题 + 风格是否漂移。

---

## 6. 测试策略

- **不引 Storybook / 视觉回归**（人工冒烟 + dev 预览页兜底）。
- vitest + Testing Library（已有）：
  - 每组件：渲染快照 / variant 切换 / 基础 a11y（role / label / 键盘焦点）。
  - `<ThemeToggle>`：点击切换 → `<html class>` 变化 + `localStorage` 写入 + 首访读 `prefers-color-scheme`（mock `matchMedia`）。
  - `<TopBar>`：NavLink active 态渲染、主题切换 / 设置入口存在。
  - **token 漂移断言**（`tokens.test.ts`）：断言 `tokens.css` 含全部层 A shadcn token + 层 B 语义色 token（深 / 浅各一组），防漏定义。
- 现有测试保持绿：`dashboardReducer.test.ts` / `useEventSource.test.ts` / 各组件测试不被破坏（旧组件暂不动，新组件独立文件）。

---

## 7. 迁移策略（增量，边界）

DSF **不动现有页面内部**，仅：
1. 套 `<AppShell>` + `<TopBar>` 到 `router.tsx` 根。
2. 装 Tailwind / shadcn / tokens。
3. 就绪基础组件库（不强制业务页消费）。

**允许的副作用**（迁移期可接受）：
- 旧页面套上 TopBar 后，`<main>` 容器 padding 与旧 `.page` padding 叠加可能微移——子项目 2-5 迁页时改容器统一。
- Tailwind preflight 加全局后，旧页面 element-level 默认样式（如 `<body>` margin）由 preflight 接管，需冒烟确认旧页面视觉不破。

**禁止**：
- 改业务页内部 className / 删旧 `events.css` 规则（留子项目 2-5）。
- 改 `dashboardReducer` / `useEventSource` / API client 等现有逻辑。

---

## 8. 任务拆解（writing-plans 种子，粗粒度）

1. Tailwind 工程化：装依赖 + `tailwind.config.ts` + PostCSS + entry CSS + Plex 注入 + `cn()` 工具（TDD：config 存在 + cn 合并）。
2. tokens.css：层 A（shadcn token 深 / 浅）+ 层 B（语义色深 / 浅）+ 层 C（字体 / 圆角 / 阴影）（TDD：token 漂移断言）。
3. shadcn 初始化：`components.json` + CLI copy 首批组件（Button / Input / Dialog / Tabs / Tooltip / Toast / Select / Checkbox / Switch / Card / Badge / Skeleton / Table）。
4. 主题基建：防 FOUC 脚本（`index.html`）+ `<ThemeToggle>` + 主题 hook（TDD：切换 / 持久化 / 首访 fallback）。
5. `<TopBar>` + `<AppShell>`：导航 + 字标 + 右侧 slot（TDD：NavLink active / 入口存在）。
6. 路由壳改造：`router.tsx` 根包 `<AppShell>`；新增 `/dev/components`（dev-only）。
7. 自写组件：`Empty` / `Spinner`（braille）/ Card 可达性变体 / Badge 双轨变体（TDD：渲染 / variant）。
8. dev 预览页：罗列组件 × 双主题。
9. 冒烟：现有路由套 TopBar 后视觉不破（旧 events.css 共存验证）+ 双主题切换 + dev 预览页。

---

## 9. 风险

| 风险 | 缓解 |
|---|---|
| Tailwind preflight 破坏旧页面 element 样式 | §3.4 + §9 冒烟（套 TopBar 后逐页看）；react-markdown 加 `.prose` |
| shadcn 默认圆润风漂移 | 四条约束 + radius 3px + dev 预览页把关 |
| 浅色主题对比度不足（cyan/magenta 浅底） | 层 B 浅色初值已调深；实施时 dev 预览页实测 AA |
| hex→HSL 转换改语义 | §1.2 表 hex 为真值来源，转换不得改语义；token 漂移断言 |
| 迁移期双范式 class 撞 | §3.5 命名隔离（`.ev-*` / 旧名不重用于新组件） |
| 主题 FOUC | §1.2 内联防 FOUC 脚本（main bundle 前） |
