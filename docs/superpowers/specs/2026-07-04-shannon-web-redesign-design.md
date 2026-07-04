# Shannon Web 前端系统性重设计（上位 spec）

> 背景：现有前端（`feat/fork-py` merged @dca59c89，React18+Vite，7 页 SPA）功能已完成但视觉 / IA / UX 粗糙——**无全局 Layout**（`App.tsx` 仅 `<RouterProvider>`，每页各管自己像孤儿页）、WorkspaceListPage 一张裸表无搜索 / 筛选 / 排序 / 空态 / 删除、ScanNewPage 手敲绝对路径、无主题切换、`events.css` 146 行散落 class 无组件化。本 spec 是**系统性重设计的伞**，定义 IA / 视觉语言 / 组件库 / 子项目分解 / 跨子项目约束；**各子项目细节在各自子 spec**。

---

## 1. 目标与范围

**做**：信息架构重组 + 全局 Layout + 视觉语言（深 / 浅双主题）+ 各页面重做 + 引入组件库。

**不动**：
- 后端 ndjson 事件 schema（契约稳定，跨子项目不改）。
- 现有 API 契约不改；新增端点在各子项目子 spec 定（子项目 2 新增 `GET /api/fs/browse`；子项目 5 可能新增设置页只读状态端点——引擎 / Temporal / worker 状态，是否复用 `/health` 扩展还是新端点留子项目 5 spec）。
- 跨媒介语义色不变量（与终端 rich renderer 对齐的 cyan / magenta / green / red / yellow 语义绑定）。
- IBM Plex 三族字体。
- `dashboardReducer`（与 core `DashboardState.apply` 1:1 对齐的契约，子项目 4 不动其逻辑）。

**部署前提**：后端本地直跑（Mac / Win，`localhost:7878` + `uv run`）—— 文件浏览器可见整机文件系统，无跨容器挂载问题。

---

## 2. 信息架构（路由）

```
/                → Dashboard 首页（跨 workspace 概览）          ★新（子项目 5）
/workspaces      → 列表（现有 / 挪来）                            （子项目 2）
/scan/new        → 新建扫描                                       （子项目 3）
/p/:ws           → 详情壳 + 二级 tab                              （子项目 4）
  /overview · /report · /deliverables · /logs · /live
/settings        → 设置（主题 + 引擎 / Temporal / worker 状态）   ★新（子项目 5）
/dev/components  → 组件预览页（dev-only，冒烟用，不在主导航）      （子项目 1）
```

**导航结构**：两级——TopBar 主导航（Dashboard / Workspaces / Scan / Settings）+ 详情页内二级 tab（概览 / 报告 / 产物 / 日志 / 实时）。Detail 是 Workspaces 下钻，不进主导航。

---

## 3. 视觉语言

| 维度 | 决策 |
|---|---|
| 主题 | **深 / 浅双主题可切换**；`<html class="dark\|light">` + localStorage `shannon-theme` + 首访 `prefers-color-scheme` fallback |
| 色板 | 语义色 `cyan / magenta / green / red / yellow` **保留**（跨媒介不变量），深 / 浅各一组（浅色调亮 / 调深保 WCAG AA） |
| 字体 | IBM Plex Mono（数据 / 代码）/ Sans（正文）/ Serif（标题）三族不动 |
| 风格 | operator-console 克制：缩圆角（radius 3px）、克制阴影、等宽台账、不照搬"圆润产品级"默认 |

**语义色绑定（不变量，跨子项目禁破坏）**：

| 色 | 语义 | 来源对齐 |
|---|---|---|
| cyan | phase / info / GitNexus 轨 / primary | 终端 `STYLE_MAP` |
| magenta | LLM 轨 / llm 事件 | 终端 `STYLE_MAP` |
| green | 成功 / done / 双轨确认 | 终端 `STYLE_MAP` |
| red | error / fail / exploited 可达 | 终端 `STYLE_MAP` |
| yellow | tool / warn | 终端 `STYLE_MAP` |

事件专用 class（`.ev-phase .ev-llm .ev-tool .ev-agent* .ev-info .ev-warn .ev-error`）**保留独立 class**，LogStream / Markdown 代码高亮 / DashboardPanel 直接消费，不并入 shadcn token。

---

## 4. 组件库选型：shadcn/ui（Radix UI + Tailwind）

**选型理由**（vs 纯手写 / Radix+自CSS / Mantine）：
- a11y 白嫗 Radix 全套 WAI-ARIA（Dialog / Tabs / Tooltip / Combobox / Toast）。
- DataTable 现成（TanStack Table + shadcn），列表页（子项目 2）搜索 / 筛选 / 排序直接复用。
- 双主题 class strategy 成熟。
- 源码 copy 进项目（非 npm 黑盒），可随意改向 operator 风。

**代价（已知，写进各子项目 plan）**：
- Tailwind 范式迁移：现有 `events.css` + `markdown.css` + 内联 className 逐页迁。
- 语义色映射层（见下四条约束）。
- `markdown.css` 重做：Tailwind preflight 清空默认样式，react-markdown 输出要用 `@tailwindcss/typography` 或自写 `.prose` 重给样式。
- operator 风调校：shadcn 默认圆润产品级，需缩圆角 / 拉间距 / 注 Plex / 克制阴影。

### 四条不变量约束（跨子项目，shadcn 引入不得破坏）

1. **语义色映射层**：shadcn token（`--primary --destructive --ring --border`…）映射到语义色（`--primary←cyan`、`--destructive←red`、`--ring←cyan`、`--border←rule`）；事件专用色 `.ev-*` **保留独立 class 不映射**。
2. **事件 / 日志 / Markdown 渲染保留自定义层**：LogStream 上色、react-markdown 主题、DashboardPanel 语义色——operator-console 命脉，不交 shadcn 默认。
3. **Plex 字体保住**：覆盖 shadcn 默认 font，Plex 三族不动。
4. **operator 风调校**：radius ≤ 4px、克制阴影、不照搬默认。

---

## 5. 子项目分解

| # | 子项目 | 内容 | 依赖 |
|---|---|---|---|
| 1 | **DSF 设计系统地基** | 双主题 token 层 + Tailwind 装配 + shadcn 初始化 + 全局 Layout（TopBar / AppShell）+ 基础组件库 + dev 预览页 + 测试基建 | — |
| 2 | **列表页 + 文件浏览器** | WorkspaceListPage 重做（搜索 / 筛选 / 排序 / 删除 / 空态 / loading / 上次刷新）+ 后端 `GET /api/fs/browse` + 前端文件浏览器模态框 | 1 |
| 3 | **扫描页重做** | ScanNewPage 表单：segmented 切换 / 分组 / 即时校验 / 集成文件浏览器 / workspace 名预览 | 1, 2（文件浏览器） |
| 4 | **详情 5 tab 重做** | 概览 / 报告 / 产物 / 日志 / 实时 各自视觉 + 交互精进（`dashboardReducer` 逻辑不动） | 1 |
| 5 | **新功能页** | Dashboard 首页（跨 ws 概览）+ 设置页（主题 + 引擎 / Temporal / worker 状态展示 + 关于/版本） | 1 |

**顺序**：1 →（2 / 3 / 4 / 5 依赖 1，可一定程度并行）。

每个子项目独立 spec → plan → impl 周期。本 spec 后续各子项目 spec：
- 子项目 1 DSF：`2026-07-04-shannon-web-redesign-dsf-design.md`（✅ spec + 已实现）
- 子项目 2 列表 + 文件浏览器：`2026-07-04-shannon-web-redesign-list-fs-design.md`（✅ spec + 已实现）
- 子项目 3 扫描页：`2026-07-04-shannon-web-redesign-scan-design.md`（✅ spec + 已实现）
- 子项目 4 详情 5 tab：`2026-07-04-shannon-web-redesign-detail-design.md`（✅ spec + 已实现）
- 子项目 5 dashboard + settings：`2026-07-04-shannon-web-redesign-dashboard-settings-design.md`（✅ spec，待 plan + 实现）

---

## 6. 跨子项目约束

### 6.1 增量迁移策略（b）
DSF（子项目 1）**只加 Layout 外壳 + 组件库就绪 + Tailwind 装配**，**不动现有页面内部样式**。旧 `events.css` + `markdown.css` 保留（文件头标 `@deprecated`）。子项目 2 / 3 / 4 / 5 各自迁各自页面内部样式，旧文件随最后一个子项目清空。

### 6.2 双范式共存规则（迁移期）
- 新组件全用 Tailwind + shadcn；旧页面内部样式暂留 `events.css`。
- class 命名不撞：事件专用保留 `.ev-*` 前缀；新组件用 shadcn 默认 + Tailwind utility，不重用 `.page / .ledger / .form-area` 等旧名（旧名随各子项目迁页时清除）。
- Tailwind preflight 与旧 CSS 共存：preflight 是 element 选择器（`body / h1 / p / ul`…），旧 `events.css` 也有 element 选择器（`body`），胜负取决于**加载顺序**——DSF entry CSS 须确保 `@import "./events.css"` 在 `@tailwind base` **之后**（class 选择器特异性 > element，旧 `.page / .ledger` 等 class 规则不受影响；`body` 等 element 规则靠显式顺序保证）。

### 6.3 测试纪律
- 子项目 1 起，所有新组件配 vitest + Testing Library 单测（渲染 / 变体 / 基础 a11y）。
- 不引 Storybook；dev 预览页 `/dev/components`（dev-only 路由，生产 build 不暴露）作人工冒烟。
- 保留现有测试（`dashboardReducer` 对齐测试等）持续绿。

---

## 7. 风险（跨子项目）

1. **Tailwind preflight 副作用**：
   - (a) react-markdown 输出被清空样式 → DSF 装 `@tailwindcss/typography` 或自写 `.prose`，子项目 4 报告 tab 验证。
   - (b) preflight 与旧 `events.css` 的 element 选择器（`body` 等）顺序敏感 → DSF entry CSS 保证 `events.css` 在 `@tailwind base` 之后加载（§6.2），套 TopBar 后逐页冒烟。
2. **浅色主题对比度**：语义色浅色变体需验 WCAG AA（cyan / magenta 在浅底尤其要注意）。缓解：DSF 定初值 + 各子项目冒烟实测。
3. **shadcn 默认风格漂移**：圆角 / 阴影 / 字体默认偏产品级。缓解：四条约束 + dev 预览页把关 + ThemeToggle 切换实测。
4. **双范式共存期认知负担**：迁移期 Tailwind + 旧 CSS 并存。缓解：增量迁移策略 b，每子项目独立消化一页，旧名随迁随清。
5. **a11y 回归**：旧页面 a11y 本就薄弱，重设计是补齐契机；新组件 a11y 靠 Radix 白嫖，旧页面随迁移补。
