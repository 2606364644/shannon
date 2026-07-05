# Shannon Web 前端重设计 · 子项目 4：详情页 5 tab 重做（design）

> 上位 spec：`2026-07-04-shannon-web-redesign-design.md`（伞）。本子项目依赖子项目 1（DSF，已落地）、可与子项目 2/3（已落地）模式对齐。
>
> 范围限定词：**视觉 + 交互精进，`dashboardReducer` 逻辑不动**（伞 §5）。

---

## 1. 背景与现状基线

详情页路由 `/p/:ws` 已存在，5 个 tab 全部已实现（**非空壳**），但视觉/交互粗糙：

- **tab 导航**：手写 `NavLink + .tab-nav/.tab-active`（events.css），**未用 shadcn Tabs**——无 `role=tablist/tab/tabpanel`、无键盘箭头导航。`@/components/ui/tabs.tsx` 已存在但闲置。
- **5 tab 全靠 events.css**：`.overview/.big-numbers/.phase-waterfall/.ledger/.deliverables-layout/.vuln-grid/.deliverables-side/.file-tree/.logs-layout/.logs-files/.log-file/.logs-content/.log-row/.live-tab/.dashboard-panel/.log-stream/.workspace-detail`——本子项目要消化的旧 class。
- **三态/a11y 债**：无 Skeleton（除列表页）、无统一错误态组件；`div onClick` 当可点行（VulnCard/FileTree）缺 `role=button`/`tabIndex`/`onKeyDown`；FileTree toggle 缺 `aria-expanded`；LogStream 无 live region；**LiveTab 零测试**（唯一无覆盖 tab）。
- **DashboardPanel 只用了 reducer 一小部分**：仅读 `current_phase / completed_units/total_units / total_cost / running agents`；reducer 已提供的 `unit_intent / running_units / completed_count / unit_status` 都没用上。
- **LiveTab elapsed 漂移**：本地 `setInterval` 自增，长时间运行与后端时钟漂移。
- **markdown**：自定义 `markdown.css`（TOC/hero/kv-row/code 复制按钮），颜色硬编码、不随主题切换。

**DSF 已就绪**（子项目 1）：15 个 shadcn 组件（含 `Tabs/Table/Card/Dialog/Input/Select/Badge/Skeleton/Sonner/...`）、双主题 token（`--c-*` 语义色 + shadcn token 映射，`tokens.css`）、AppShell 已套在详情页外层、Tailwind + `@tailwindcss/typography` 已装。

**契约边界清晰**：`dashboardReducer.ts` + `formatters.ts` + `api/types.ts`(`NdjsonEvent`) + `dashboardReducer.test.ts` 是**纯数据层零 UI 耦合**——绝对不动。

---

## 2. 目标与范围（A 完整版）

**做**：

1. 5 tab（概览/报告/产物/日志/实时）逐个重做：events.css 结构 class → Tailwind/shadcn。
2. tab 导航换 shadcn `<Tabs>` 视觉（**路由驱动**，深链不丢）。
3. markdown 迁 `@tailwindcss/typography` + Tailwind（TOC/hero/kv-row/code 复制重写），`markdown.css` 退役。
4. 统一三态：Skeleton / Empty / ErrorState。
5. a11y 补齐：shadcn Tabs ARIA/键盘、可点行 `role=button`、`aria-expanded`、LogStream `aria-live`。
6. DashboardPanel 信息增强：读 reducer 已有的 `unit_intent/running_units/completed_count/unit_status`（**只读，不改 state shape**）。
7. LiveTab elapsed 从 `events` 数组的 `PhaseEvent(start)` ts 自算（修漂移，不动 reducer）。
8. LiveTab 补测试（零覆盖→有覆盖）。

**不动**（铁律，§3）：
- `dashboardReducer.ts` / `formatters.ts` / `api/types.ts`(`NdjsonEvent` 14 类 union) / `dashboardReducer.test.ts` / `useEventSource.ts`。
- `DashboardState` / `AgentRow` 的 shape。
- ndjson 事件 schema、后端 API 契约。
- 跨媒介语义色不变量（`.ev-*` 与终端 rich renderer 对齐，伞 §3）。
- IBM Plex 三族字体、`dashboardReducer` 与 core `DashboardState.apply` 的 1:1 对齐。

---

## 3. 契约不动边界（铁律）

**绝对不改一行**：

| 文件 | 角色 | 不动理由 |
|---|---|---|
| `src/state/dashboardReducer.ts` | reducer 本体 + state/AgentRow interface + emptyState/derive/setUnit/row | 与 core `DashboardState.apply` 1:1 对齐契约 |
| `src/state/formatters.ts` | firstNonemptyLine / humanizeToolCall 及辅助 | reducer 依赖，纯数据 |
| `src/api/types.ts` 的 `NdjsonEvent` union | 14 类事件 TypeScript 接口 | ndjson 线格式契约 |
| `src/state/dashboardReducer.test.ts` | 对齐测试用例 | 行为锚点（与 core `test_dashboard_state.py` 平行） |
| `src/api/useEventSource.ts` | SSE 管道（连接/重连/lastEventId/scan_end close） | 数据管道，独立于本子项目 |

**信息增强的安全依据**：reducer 是纯数据层零 UI 耦合（不产 React 元素、不涉 className、不调 React API）。`DashboardPanel` 读取更多已有 state 字段是纯展示层行为，**不触碰 reducer / state shape**，契约安全。

---

## 4. 总体架构

### 4.1 tab 导航：路由驱动 + shadcn Tabs 视觉

`<Tabs>`（shadcn）做导航条：
- `<TabsList>` + 5 个 `<TabsTrigger>`，`value` = 当前 path 段（`overview/report/deliverables/logs/live`，从 `useLocation` 解析）。
- `onValueChange → navigate('/p/:ws/' + v)`。
- content **仍走 `<Outlet/>`**（react-router 子路由），**不用 `<TabsContent>`**——避免双重 mount 与路由不同步。
- 单源真理：`value` 绑 `useLocation`，外部 navigate / 刷新自动同步。
- a11y：白嫖 Radix `role=tablist/tab` + 箭头键导航 + `aria-selected`。
- `DefaultTab` 按 status 智能跳转（completed→report，否则→live，`replace:true`）逻辑不动。
- tab 顺序与中文 label：概览 / 报告 / 产物 / 日志 / 实时（英文 slug 不动，路由兼容）。

### 4.2 文件布局

```
routes/WorkspaceDetail/
  index.tsx              ← tab 导航：shadcn Tabs 视觉 + 路由驱动（替换 .tab-nav NavLink）
  OverviewTab.tsx        ← 换皮 + 三态 + a11y
  ReportTab.tsx          ← 换皮 + 三态（MarkdownView prose 化后的容器）
  DeliverablesTab.tsx    ← 换皮 + 三态 + 可点行 a11y
  LogsTab.tsx            ← 换皮 + 三态 + 虚拟滚动容器自适应
  LiveTab.tsx            ← 换皮 + elapsed 修漂移 + scan_end 行为 + 补测试
components/
  DashboardPanel.tsx     ← 信息增强：用 unit_intent/running_units/completed_count/unit_status
  LogStream.tsx          ← aria-live + 着色 token 化
  StatusBadge.tsx        ← token 化（保留语义色）
  VulnCard.tsx           ← token 化 + shadcn Card/Badge + 可点行 role=button
  FileTree.tsx           ← token 化 + aria-expanded
  MarkdownView.tsx       ← 重写：prose + Tailwind（TOC/hero/kv-row/code 复制）
  ErrorState.tsx         ← 新增统一错误态（复用列表页红横幅模式）
styles/
  events.css             ← 详情页结构 class 迁出（保留 .ev-* + 其他页残留，文件不强制清空）
  markdown.css           ← 退役（内容迁 MarkdownView Tailwind）
  tokens.css             ← 新增 --prose-* 双主题变量
```

### 4.3 迁移/共存策略

- 遵循伞 §6.1 增量迁移 b：本子项目只迁详情页内部样式。
- events.css 详情页结构 class（§1 所列）全迁 Tailwind。**事件专用 `.ev-*` 保留**（伞 §3 不变量）。
- events.css **本子项目不强制清空整个文件**——只迁详情页相关 class，`.ev-*` + body element 规则 + 其他页残留留到子项目 5 / 最终清理。**但详情页 5 tab 渲染路径上不再依赖任何 events.css 结构 class（除 `.ev-*`）**。
- `markdown.css` 整文件退役（B 选项决定）。
- 测试护栏：迁一个 class 删一个 class，`rg` 确认零引用再删。

---

## 5. 各 tab 重设计

### 5.1 详情壳 · tab 导航
见 §4.1。`.tab-nav/.tab-active` 退役。

### 5.2 各 tab

| Tab | 视觉/交互重做 | 三态 | a11y/增强 |
|---|---|---|---|
| **概览** | 3 区块各包 shadcn `Card`；PhaseWaterfall Tailwind 重绘（div + width%）；AgentTable 换 shadcn `Table`（对齐列表页模式）；StatusBadge token 化 | Skeleton（表骨架 5 行）/ ErrorState / Empty（"等待扫描，metrics 将在 pre-recon 后出现"替代裸 `.trace`） | status 矛盾前端兜底保留 → 呈现为 warning Badge（注释说明后端已 flag） |
| **报告** | 容器 Tailwind；MarkdownView prose 化（§5.3） | Skeleton / ErrorState / Empty（"报告尚未生成"） | TOC 侧栏功能保留、Tailwind 重写 |
| **产物** | 双栏 Tailwind grid；VulnCard 用 shadcn `Card` + `Badge`（merge_source）；FilePreview md 走 prose；empty_json/big_json 特殊处理保留 | Skeleton / ErrorState / Empty | **VulnCard 可点行加 `role=button`+tabIndex+onKeyDown**；FileTree `aria-expanded`；`injection_has_no_queue` 文案改 Badge/Tooltip（不暴露实现细节） |
| **日志** | 双栏 Tailwind grid；文件列表 Tailwind；**react-window 虚拟滚动保留**（性能关键），虚拟滚动容器高度自适应替代固定 500px（`FixedSizeList` 的 `height` prop 由容器实测像素值得，如 `ResizeObserver`；具体方案留 plan），避免小屏溢出；.log 行 ts/type/message 解析保留、着色 token 化 | Skeleton / ErrorState / Empty（"无日志文件"） | 非 .log 文件 `pre` 渲染 token 化 |
| **实时** | 容器 Tailwind；DashboardPanel 升级（§5.3）；LogStream 升级 | 连接态：connecting/open/error（SSE 自动重连）/closed | **elapsed 漂移修复**：从 events 取最近 `PhaseEvent(start)` ts 算 elapsed（展示层自算，不动 reducer），每秒 tick 重渲染；**scan_end 提示 + "查看报告"按钮**（navigate，不自动跳避免打断）；LogStream `aria-live=polite` |

### 5.3 共享组件升级

**DashboardPanel（信息增强核心）** —— 补用 reducer 已有字段：
- `unit_intent` → running step 旁显示意图说明（core CLI 渲染器已用此字段，前端补齐对齐）。
- `running_units` / `completed_count` / `unit_status` → step 进度可视化（done/failed/running 三态）。
- 布局：phase 顶栏 + step 进度条 + agent 列表 + cost/elapsed，全 Tailwind + 语义色 token（保留 `.ev-*` 等价语义）。
- **只读 state，不改 shape**——契约安全。

**LogStream**：着色 token 化（`CAT_CLASS` 映射改 Tailwind 语义色 class，与 `.ev-*` 等价）；`aria-live=polite` on 容器；500 行虚拟滚动阈值保留。

**StatusBadge / VulnCard / FileTree**：token 化 + a11y（VulnCard 可点行 `role=button`、FileTree `aria-expanded`）；VulnCard 用 shadcn `Card` + `Badge`。

**MarkdownView（B 选项核心）**：
- `@tailwindcss/typography` `.prose` 基础排版，深/浅主题各一组 `--prose-*` 变量（`tokens.css` 新增）。
- TOC 侧栏：Tailwind sticky + 锚点（功能保留）。
- hero 执行摘要：shadcn `Card`。
- kv-row：Tailwind（`**key:** value` 模式识别保留）。
- code 复制按钮：shadcn `Button`。
- `markdown.css` 整文件退役。

---

## 6. 错误处理（统一三态）

- **各 tab fetch 失败** → 统一 `ErrorState`（复用列表页红横幅模式：`border-destructive/40 bg-destructive/10 text-destructive` + 可选重试按钮）。
- **SSE 连接错误**（实时）→ 连接态徽章（connecting/open/error/closed），不弹 toast（EventSource 自动重连），显示"重连中…"。
- **文件预览失败**（产物）→ 局部 ErrorState（不整页崩，保留左侧 VulnCard 网格可用）。
- **markdown 渲染异常** → fallback 到 raw text（防 react-markdown 崩）。

---

## 7. 测试计划

- **保留现有 22 case**（OverviewTab 3 / ReportTab 3 / DeliverablesTab 12 / LogsTab 4）：选择器尽量改 role-based（避免 brittle class 断言）。
- **LiveTab 补 ~6 case**（零覆盖→有覆盖）：SSE 连接建立 / reducer 增量 fold / DashboardPanel 各字段渲染 / scan_end 提示+"查看报告"按钮 / elapsed 从 PhaseEvent ts 计算 / 连接错误态。
- **升级组件测试同步调整**：DashboardPanel（新增 `unit_intent`/`running_units` 渲染断言）、LogStream（aria-live）、MarkdownView（prose 化后 TOC/hero/kv-row）、VulnCard（role=button）、FileTree（aria-expanded）、StatusBadge。
- **新增 a11y 断言**：tablist role + 键盘激活、aria-expanded、live region 存在性。
- **Radix Tabs 测试陷阱**：用 `fireEvent.mouseDown` 激活（Radix Tabs `TabsTrigger` 在 mousedown 触发 `onValueChange`，jsdom 的 `fireEvent.click` 不发 mousedown），遵循子项目 3 `ScanNewPage.test.tsx` 的 `clickTab` 模式 + memory `radix-ui-testing-activation-gotcha`。
- **现有对齐测试持续绿**：`dashboardReducer.test.ts` / `router.test.ts` / `tokens.test.ts`。

---

## 8. 迁移执行顺序（plan task 切分预告）

1. 详情壳 tab 导航 shadcn Tabs 化（路由驱动，拆 `.tab-nav`）。
2. 共享组件升级（StatusBadge / VulnCard / FileTree token+a11y；DashboardPanel 信息增强；LogStream aria-live+token）—— 各 tab 依赖。
3. MarkdownView prose 化 + `--prose-*` 双主题变量（`tokens.css`）。
4. 逐 tab 重做（概览 → 报告 → 产物 → 日志 → 实时），每 tab：换皮 + 三态 + a11y + 测试，独立 commit。
5. LiveTab 补 ~6 case（重点）。
6. events.css 详情页 class 清理 + markdown.css 退役（最后，`rg` 确认零引用再删）。
7. 双主题冒烟 + dev 预览页补登新组件（`/dev/components`）。

---

## 9. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| 1 | prose 深浅主题对比度（伞 §7 风险2，cyan/magenta 浅底尤其） | `tokens.css` 加 `--prose-*` 双主题变量，dev 预览页把关 + ThemeToggle 切换实测 |
| 2 | react-window 行高/样式迁 Tailwind 滚动错位 | 保留 `FixedSizeList` 行高逻辑，只迁 className，行高测试锚点 |
| 3 | LiveTab elapsed 多 `PhaseEvent(start)` / resume 场景 | 取 events 最后一条 `PhaseEvent(start)` 的 ts，resume 不特殊（新 phase 起算） |
| 4 | shadcn Tabs 路由驱动同步（外部 navigate / 刷新） | value 绑 `useLocation` 解析的 path 段，单源 |
| 5 | 测试 class 断言 brittle | 选 role-based，迁一个改一个 |
| 6 | 范围蔓延（A 完整版较大） | plan 严格拆 task、每 tab 独立 commit、reducer 铁律 |
| 7 | Tailwind preflight vs markdown（伞 §7 风险1a） | B 重写为 prose 正好消除该风险（prose 重新给 react-markdown 输出样式） |

---

## 10. 跨子项目约束遵守（指向伞 spec）

- §3 语义色绑定：`.ev-*` 事件专用 class 保留独立、不并入 shadcn token；cyan/magenta/green/red/yellow 跨媒介不变量。
- §4 四条 shadcn 不变量：语义色映射层 / 事件-日志-Markdown 自定义层 / Plex 字体 / operator 风（radius ≤ 4px、克制阴影）。
- §6.1 增量迁移 b：本子项目迁详情页内部样式，旧文件随迁随清（详情页范围）。
- §6.2 双范式共存：迁完后详情页零 events.css 结构 class 依赖（`.ev-*` 除外），新组件全 Tailwind + shadcn。
- §6.3 测试纪律：新组件配 vitest + Testing Library，保留 `dashboardReducer` 对齐测试持续绿，不引 Storybook。
