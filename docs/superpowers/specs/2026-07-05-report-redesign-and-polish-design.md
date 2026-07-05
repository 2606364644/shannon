# Shannon Web 报告页美化 + 全站轻量打磨 — 设计

> 日期：2026-07-05
> 范围：`packages/web/frontend`
> 分支：`feat/fork-py`

## Background

Shannon Web（`packages/web/frontend`，operator-console 风格的安全审计 SPA）的报告页位于详情页 report tab（`/p/:workspace/report`），渲染后端 `comprehensive_security_assessment_report.md`（`PlainTextResponse`，`packages/web/src/shannon_web/api/workspaces.py:96`）。报告页是完成态 workspace 的默认着陆页（`router.tsx:23` completed→report），是用户拿到扫描结论的主入口，但当前视觉体验差。

同时 DSF 设计系统（`tokens.css` 三层 + shadcn）已落地，但部分页面仍耦合迁移期遗留文件 `events.css`（文件头注释明示「不再追加规则」），在浅色主题下视觉断裂。

## 目标

1. **报告页深度重做**到 operator-console 级阅读体验：代码有语法高亮、表格能渲染、排版层次清晰、结构自适应。
2. **其他页面轻量打磨**，统一到 DSF token，消除 `events.css` 遗留耦合导致的浅色主题断裂。
3. 风格保持深色硬朗（小圆角 `--radius: 3px`、IBM Plex 字体、信息密度高），**不动设计语言**。

## 非目标（明确不做）

- 打印 / 导出 PDF（用户决策）。
- Dashboard 卡片加图标 / 趋势、Overview 瀑布图加 tooltip / 图例、Settings 视觉重构（属「全站深度美化」档，超出本次范围）。
- 引入 highlight.js 官方主题 CSS 或换 shiki。
- 新增 shadcn 组件（dropdown / popover / alert 等）。
- 删除 `events.css` 文件本身（T3/T4/T6 只换用法；死规则清理作为可选 follow-up）。

## 关键设计决策（已与用户确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 范围 | 报告页深度 + 全站轻量打磨 | 报告页是核心痛点；其他页面只解耦断裂点，性价比最高 |
| 代码高亮 | 自写 `.hljs-*` token 配色接 DSF `--c-` 语义色 | 双主题无缝、零新依赖、风格辨识度高（不像套外部模板） |
| 风格 | 保持 operator-console 深色硬朗，只打磨 | 符合安全工具产品定位，风险最低 |
| 报告布局 | 左侧 TOC 双栏，无标题时自动隐藏退单栏 | 长报告保留快速跳转，空报告不留空栏 |
| 字体 | 正文 Plex Sans / 标题 Plex Serif / 代码 Plex Mono | 技术报告主流，解决整篇 serif 厚重问题 |
| 打印 | 不做 | 用户决策 |

## 设计

### T1 报告页排版与结构修复

文件：`src/components/MarkdownView.tsx`

| 现状（file:line） | 问题 | 改法 |
|---|---|---|
| `:127` `prose prose-sm max-w-none font-serif` | 整篇 serif 厚重 | `font-sans`，标题用 `prose-headings:font-serif` |
| `:184-199` code renderer 对所有 code 加复制按钮 | 行内 code 噪音 | 仅 block code（`className` 含 `language-`）加复制 + 语言角标；inline 走 prose 默认 |
| `:111` `grid-cols-[220px_1fr]` 固定 | 空报告 TOC 占 220px 空栏 | `headings.filter(level>=2)` 为空时 `<nav>` 不渲染，grid 切 `grid-cols-1` |
| `:88` `展开 ▸/折叠 ▾` 文字 | 无图标 | lucide `ChevronDown` / `ChevronRight` + sr-only 文案 |
| `:173` `flex gap-2`（kv-row） | 长 value 换行错位 | `flex items-baseline gap-2`，kv-key `shrink-0` |
| 无 `remark-gfm` | GFM 表格不渲染 | `remarkPlugins={[remarkGfm]}`（新装依赖） |

测试（`MarkdownView.test.tsx` 新增）：
- TOC 无 `level>=2` 标题时不渲染 `<nav data-testid="toc">`，且外层为单栏。
- inline code 无「复制」按钮；block code 有「复制」按钮 + 语言角标。
- GFM 表格渲染成 `<table>` 而非裸文本。

### T2 报告页高亮配色 + 表格样式

新增 `src/styles/report.css`，在 `src/styles/index.css` 的 `@import "./events.css";` 之后、`@tailwind` 之前加 `@import "./report.css";`（保持 `@import` 先于 `@tailwind` 的 CSS 规范要求，见 `index.css:1-6` 注释）。

内容：
1. **hljs token 配色**（手写，接 DSF 语义色，深浅各一套）：
   - `.hljs-keyword / .hljs-built_in / .hljs-literal` → cyan
   - `.hljs-string / .hljs-regexp` → green
   - `.hljs-number` → magenta
   - `.hljs-comment` → muted-foreground（降亮度）
   - `.hljs-title / .hljs-title.function_ / .hljs-attr` → yellow
   - `.hljs-meta` → red
   - `.light` 覆盖一套（调亮度保 AA 对比，参考 `tokens.css` 浅色 `--c-*`）
2. `.prose table`：`border border-border`、表头 `bg-muted font-mono text-xs`、单元格 `px-3 py-2 border-border`、外层容器 `overflow-x-auto`。
3. `.prose :is(h1,h2,h3) { scroll-margin-top: 80px; }`（避开 TopBar `h-12` + 详情页 header 元信息行遮挡锚跳转）。
4. `.prose pre`：圆角（`--radius`）+ `bg-muted`，内层 `code` 不再额外加背景。

测试：
- `report.css` 含 `.hljs-keyword` 且其 color 引用 `var(--c-`（确保接 DSF token，非硬编码 hex）。
- GFM 表格测试见 T1。

### T3 扫描页解耦 events.css（修浅色断裂）

文件：`src/pages/ScanNewPage.tsx`、`src/components/ScanFormFields.tsx`、`src/components/YamlEditor.tsx`

| 现状 class | 位置 | 替换 |
|---|---|---|
| `.page .scan-page`（根） | `ScanNewPage.tsx:166` | DSF 容器 `space-y-4` |
| `.submit-btn` | `ScanNewPage.tsx:220` | `<Button size="lg" className="w-full">`（去 events.css override） |
| `.trace`（提示 / 错误） | `ScanNewPage.tsx:214,223`；`ScanFormFields.tsx:89,101` | 错误提示场景复用 `ErrorState`；普通 hint 场景用 `text-sm text-muted-foreground`（按各处语境二选一，实现时逐处判定） |
| `.git-extra` | `ScanFormFields.tsx:59` | `border-t border-border pt-4 mt-4 space-y-2` |
| `.ev-warn` | `ScanFormFields.tsx:87` | `text-yellow` + `<Badge>` |
| `.yaml-editor` | `YamlEditor.tsx:37` | `border border-border rounded-md overflow-hidden` |

测试（`ScanNewPage.test.tsx`）：根容器无 `scan-page` class；提交按钮无 `submit-btn` class。

### T4 列表页 status-bar 解耦

文件：`src/pages/WorkspaceListPage.tsx:63`

现状：`<span className={\`status-bar status-${status}\`} />`（events.css）。

改法：DSF 行首色条 `<span className={\`inline-block w-0.5 self-stretch rounded ${STATUS_COLOR[status]}\`} />`，`STATUS_COLOR` 复用 `StatusBadge.tsx:3` 的色映射：
- `running` → `bg-cyan`
- `completed` / `done` → `bg-green`
- `failed` / `killed` → `bg-red`
- `crashed`（及兜底）→ `bg-yellow`

测试（`WorkspaceListPage.test.tsx`）：行首无 `status-bar` class；running 行存在 `bg-cyan` 元素。

### T5 详情页 header 补元信息

文件：`src/routes/WorkspaceDetail/index.tsx`

现状（`:17-29`）：仅 `<h2 className="font-mono text-xl">{workspace}</h2>` + Tabs，无返回 / 无元信息。

改法：
- 顶部加返回按钮 `<Link to="/workspaces" aria-label="返回列表">` + lucide `ArrowLeft`。
- h2 行补元信息：`StatusBadge` + `scan_type` Badge + `repo_path`（`font-mono text-muted-foreground text-sm`）+ `created_at`。
- **数据源复用**：`apiGet<SessionData>(\`/workspaces/${workspace}\`)`（`router.tsx:23` 已有先例；`SessionData` 定义于 `src/api/types.ts:102`，字段 `status / scan_type / repo_path / created_at / web_url`）。
- header 元信息区 loading 用 `Skeleton`；fetch 失败**不阻塞** tab 切换（降级只显 workspace 名 + Tabs）。

测试：mock `SessionData` 返回，断言 header 显 status + repo_path；fetch reject 时仍渲染 h2 + Tabs。

### T6 LogsTab + ThemeToggle 收尾

- `src/routes/WorkspaceDetail/LogsTab.tsx`：`.trace` / `.ev-info` → `text-muted-foreground` / `border-cyan/40 bg-cyan/10`。
- `src/components/layout/ThemeToggle.tsx`：☀️🌙 emoji → lucide `Sun` / `Moon`。
- `src/components/DashboardPanel.tsx`：grep 确认引用情况——若仍被引用，`.spinner` → `.shannon-spinner`（DSF 自带，`index.css:32`）；无引用则不动。

测试：ThemeToggle 含 `Sun` / `Moon` svg；LogsTab 现有测试去 `.trace` 断言。

## 复用面（不新造）

- **DSF token**：`src/styles/tokens.css` 三层（shadcn token + `--c-cyan/magenta/green/red/yellow` + 字体 + prose），tailwind 已映射 `bg-cyan / text-red / border-green/40` 等。
- **shadcn 16 组件**（`src/components/ui/`），无需 `shadcn add`。
- **`StatusBadge` 色映射**（T4 复用 `StatusBadge.tsx:3`）。
- **`apiGet<SessionData>`**（T5 复用 `router.tsx:23` 先例）。
- `react-markdown + rehype-highlight/slug/autolink`（保留）；**仅新装 `remark-gfm`**。
- `lucide-react`（已装）。

## 验证

1. `cd packages/web/frontend && npm test` — vitest，现有测试 + T1–T6 新增断言全绿。
2. `cd packages/web/frontend && npm run build` — `tsc -b && vite build` 须过。
3. `cd packages/web/frontend && npm run dev` + 浏览器冒烟（关键）：
   - completed workspace `/p/:ws/report`：代码块有配色、表格渲染成 `<table>`、正文 sans / 标题 serif、空报告 TOC 隐藏退单栏、inline code 无复制按钮 / block code 有复制 + 语言角标、hero 用 lucide 图标。
   - **浅色主题切换**（TopBar toggle）：报告 / 扫描 / 列表 / 详情 / LogsTab 全部响应、无深色 hex 残留断裂。
   - 扫描页表单 / YamlEditor / 提交按钮双主题正常。
   - 详情页 header 显返回按钮 + 状态 / 类型 / repo。
   - 长报告（NodeGoat comprehensive）锚跳转不被 sticky 遮挡。

## 风险与权衡

- **hljs token 配色覆盖度**：`rehype-highlight` 基于 lowlight，常见语言（JS / TS / Python / Go / YAML / Bash / JSON）token 类覆盖稳定；冷门语言可能个别 token 未着色（降级到 prose 默认色，不报错）。可接受。
- **`scroll-margin-top` 数值**：80px 基于 TopBar `h-12`（48px）+ 详情页 header 元信息行约 32px 估算；冒烟时若仍遮挡则微调。
- **T5 增加一次请求**：header 元信息单独 `apiGet<SessionData>`，与 ReportTab 的 report fetch 并行，不阻塞 tab；`router.tsx` 进站时已发同一请求用于默认 tab 分流，后续可考虑提到上层共享（本次不做，YAGNI）。
- **events.css 死规则清理**：T3 / T4 / T6 换用法后 `events.css` 仍保留 `.page / .submit-btn / ...` 定义；作为 follow-up，grep 确认全项目无引用后再删规则，避免本次爆破半径扩大。
