# Web 列表页 + 扫描页 视觉精修 设计

> 日期：2026-07-16 · 分支：feat/fork-py · 范围：`packages/web/frontend`
> 状态：设计已与用户逐屏确认（visual companion mockup），待写实现计划
> 视觉参考：`.superpowers/brainstorm/64143-1784207887/content/{workspaces-before-after,repos-and-scan,scan-color-variants}.html`

---

## 1. 背景与目标

用户反馈 `/repos`、`/workspaces`、`/scan/new` 三个页面「不够好看」。对比之下，Dashboard / 详情页 / 报告页 用户没有抱怨。

诊断结论：**不是设计方向问题，是执行精度问题**。现有 Claude 风视觉语言（coral 15° 主色 + 暖炭黑/黄暖白双主题 + IBM Plex + 三层 token + source→sink 品牌图标）是对的，但这三个页面没用足这套语言。

**目标**：在保留现有基调的前提下，对三个页面做视觉精修，让它们达到 Dashboard / 报告页的精致度。

**非目标（明确不做）**：
- 不换设计语言（保留 coral + 双主题 + IBM Plex + BrandMark）
- 不改后端 API / 类型契约
- 不改 `/scan/new` 的结构（Tabs + 双栏 + 底部栏 + emoji 全部保留）
- 不做新功能、不动数据流
- 不动 Dashboard / 详情页 / 报告页（仅 `ui/table` 基础组件的全局副作用需回归验证）

---

## 2. 诊断：三个页面为什么「不够好看」

三个共同根因：

1. **裸表格漂在背景上——没用上已有 elevation 语言**。`ReposPage` / `WorkspaceListPage` 的表格直接铺在 `background` 上，无 `Card` 包裹、无浮起、头尾无圆角收边。而 `Card` 组件**已经自带 `shadow-[var(--shadow-card)]`**，这两个页面没用。
2. **列表页缺「概览层」——第一眼空旷扁平**。`WorkspaceListPage` 连页面标题都没有；两页都没有顶部 stat 摘要条。
3. **表格本身细节糙**：表头无轻底色（`TableHead` 只有 `text-muted-foreground`）、行 hover 太淡（`TableRow` 的 `hover:bg-muted/50` 在深色下 ≈8% 几乎不可见）。`/scan/new` 配色「灰扑扑、层次乱」：Tabs 条 `bg-secondary`(16%)、侧栏 `bg-secondary/50`(8%)、小卡 `bg-card`(13%) 三块明度打架还都是低饱和暖灰，coral 主色用得太克制没焦点。

---

## 3. 方案总览（已逐屏确认）

| 页面 | 方案 | 改动性质 |
|---|---|---|
| `/workspaces` | 三件套：标题 + 概览条 + 卡片化表格精修 | 结构 + 配色 |
| `/repos` | 三件套：标题 + 概览条 + 分组 section + 卡片化表格精修 | 结构 + 配色 |
| `/scan/new` | **只换配色 A 暖焦点**（结构/emoji 不动） | 仅配色 |

统一语言：概览条 · `Card` 浮起容器 · 表头轻底 · 行 hover 增强 · 状态色点 · coral 焦点。

---

## 4. 共享新组件

两个页面（workspaces / repos）都要「标题 + 概览条」，抽取共享组件避免重复。

### 4.1 `<PageHeader title subtitle />`
- 位置：`src/components/PageHeader.tsx`
- 结构：`<h1 text-xl font-semibold tracking-tight>` + `<p text-sm text-muted-foreground mt-0.5>`
- 用途：统一三页标题区。`workspaces` 当前**无标题**（直接工具栏），这次补上；`repos` / `scan` 已有标题，迁移到该组件保持一致。

### 4.2 `<StatRow>` / `<StatCard>`
- 位置：`src/components/StatRow.tsx`
- `StatRow`：`grid grid-cols-4 gap-2`（响应式：窄屏 `grid-cols-2`）
- `StatCard`：`rounded-lg border border-border bg-card p-2.5`，内含
  - `.label`：`text-xs text-muted-foreground uppercase tracking-wide`
  - `.value`：`text-lg font-semibold tabular-nums`，支持语义着色（`text-cyan` / `text-green` / `text-red`）
- mockup 见 `workspaces-before-after.html` 右侧概览条。

---

## 5. `/workspaces` 设计

### 5.1 布局
```
PageHeader（标题「工作区」+ 副标题「所有扫描任务与产物」）
StatRow（4 格：运行中 / 已完成 / 失败 / 总成本）
工具栏（搜索 + 状态过滤 + 类型过滤 + 新建扫描 + 刷新）  ← 保留
Card 包裹的表格（StatusBadge + 名称色条保留）
```

### 5.2 概览条数据（客户端聚合，不动契约）
`useWorkspaces().data: Workspace[]` 已有字段：
- 运行中 = `filter(status === "running").length`（值着 `text-cyan`）
- 已完成 = `filter(status ∈ {completed, done}).length`（`text-green`）
- 失败 = `filter(status ∈ {failed, killed}).length`（`text-red`）
- 总成本 = `sum(total_cost_usd)`，币种取首条 `cost_currency`，经 `fmtCost()`

### 5.3 表格
- 外包 `<Card className="overflow-hidden p-0">`（p-0 让表格贴边、圆角收边；Card 自带 shadow 浮起）。
- 名称列原有 `w-0.5` 状态色条 + `StatusBadge` **保留不动**。

---

## 6. `/repos` 设计

### 6.1 布局
```
PageHeader（标题「仓库」+ 副标题「已纳管的扫描目标代码库」）
StatRow（4 格：仓库数 / 总大小 / 就绪 / 克隆中）
工具栏（搜索 + 添加仓库）  ← 保留
按 group 分组：
  SectionHeader（带边框：分组名 + 「N 个仓库」+ 展开箭头 ▾）
  Card 包裹的表格
```

### 6.2 概览条数据（客户端聚合）
`repos: Repo[]` 已有字段：
- 仓库数 = `repos.length`
- 总大小 = `sum(size_bytes)`，经 `fmtSize()`（页面已有该函数）
- 就绪 = `filter(state === "ready").length`（`text-green`）
- 克隆中 = `filter(state ∈ {cloning, pulling}).length`（`text-cyan`）

### 6.3 分组 SectionHeader 升级
当前分组头是朴素 `<button hover:bg-muted/30>`。升级为：
`flex items-center bg-card border border-border rounded-lg px-4 py-2.5`
- 分组名 `font-medium` + 计数 `text-sm text-muted-foreground`（「N 个仓库」）
- 右侧展开/收起箭头（lucide `ChevronDown` / `ChevronRight`，替现状文字「展开/收起」）

### 6.4 表格
- 每组表格外包 `<Card className="overflow-hidden p-0">`。
- `StateCell` 状态徽章 + 来源列 CopyButton **保留不动**。

---

## 7. `/scan/new` 设计（配色 A 暖焦点，结构不动）

**铁律：不改结构**（Tabs 三段、双栏 grid、底部操作栏、emoji 📖🔗🤖 全部保留）。只改配色，落点均在 `ScanNewPage.tsx`：

| 位置 | 现状 | 改为（A 暖焦点） | 意图 |
|---|---|---|---|
| L255 侧栏容器 | `bg-secondary/50 border-l border-border` | `bg-card border-l border-border` | 去 secondary 灰堆叠，与表单同底靠 hairline 分层 |
| L256 侧栏 cap 标题 | `text-muted-foreground` | `text-primary`（coral） | coral 焦点 |
| L262 侧栏信息卡 | `border-border bg-card` | `border-primary/25 bg-primary/[0.06]` | coral 轻底做信息焦点 |
| L286 底部操作栏 | `bg-secondary` | `bg-card border-t border-border` | 去 secondary 灰堆叠，hairline 分层 |
| L206 Tabs 条 | `bg-secondary` | **保留不动** | A 方案不动 Tabs 条 |
| L269/L275 黑盒/白盒提示块 | orange/yellow 极淡底 | **保留不动** | 语义提示色保留 |

注：`Card` 外壳（L204 `<Card className="overflow-hidden">`）已自带 `shadow-card` 浮起，A 方案保留。类型语义色（白盒 badge `bg-cyan/10 text-cyan`、黑盒 `bg-orange/10 text-orange`）保留——coral 是结构焦点，语义色是类型标识，不冲突。

**`SidebarItem.accent` 字段处理**：该字段原本给黑盒第一张信息卡标 orange 边（`border-orange/30`）。A 方案下信息卡统一 coral 轻底，accent 不再生效——类型标识已由类型 badge 承担。实现时移除 `accent` 字段及其分支（黑盒的 orange 提示块 L269 保留，不受影响）。

---

## 8. 基础组件改动（`ui/table.tsx`，全局）

表格精修的两个通用项放进基础组件，让所有表格页面一致受益：

1. **`TableHead` 加表头轻底色**：当前只有 `text-muted-foreground`，无底色。加 `bg-muted/40`（深色下 ≈6% 微底，浅色下同步），让表头与正文分层。
2. **`TableRow` hover 增强**：`hover:bg-muted/50` → `hover:bg-muted/70`（深色下从 ≈8% 提到 ≈11%，清晰可辨但不刺眼）。`data-[state=selected]` 保留。

**全局副作用风险**：`ui/table` 被 `/workspaces`、`/repos`、详情页各 tab、`/repos/:name` 等多处使用。改后需回归这些页面的快照/断言测试（见 §10）。这是期望的一致性提升（所有表格都更精致），但要验证不破坏既有断言。

---

## 9. i18n

新增 key（`src/locales/{zh,en}.json`，用 `npm run i18n:scan` 辅助补全）：
- `workspaces.title`（若不存在）、`workspaces.subtitle`
- `workspaces.stats.running` / `.completed` / `.failed` / `.totalCost`
- `repos.subtitle`、`repos.stats.total` / `.size` / `.ready` / `.cloning`
- `repos.groupCount`（「{{count}} 个仓库」）、`repos.expand` / `repos.collapse` 复用（或改箭头后移除文字）

`/scan/new` 无新增（纯配色）。

---

## 10. 测试策略

memory 提示：前端测试须 `cd packages/web/frontend`；只跑改动相关子集，勿跑全套。

**新增**：
- `PageHeader.test.tsx`、`StatRow.test.tsx`（渲染 / 语义着色 / 空值）

**更新断言**：
- `WorkspaceListPage.test.tsx`：新增标题、概览条 4 格（聚合值）、Card 容器
- `ReposPage.test.tsx`：新增标题、概览条、分组 section header、Card 容器
- `ScanNewPage.test.tsx`：侧栏/底部 class 变更、cap coral、信息卡 coral 轻底

**回归（防 ui/table 全局副作用）**：
- `WorkspaceDetail/*` 各 tab 测试（Overview/Live/Logs/Report/Deliverables）
- `RepoDetailPage.test.tsx`、`ui.test.tsx`（table 组件）
- `tokens.test.ts`（token 未动，应保持绿）

**视觉验证（非自动化）**：
- 深色 + 浅色双主题各过一遍三页
- 表格行 hover 在两主题下都清晰可见

---

## 11. 风险

| 风险 | 缓解 |
|---|---|
| `ui/table` 全局改动破坏详情页等既有断言 | §10 回归子集；若快照大面积崩，考虑页面级 className 覆盖而非改基础组件（退路） |
| 概览条客户端聚合性能 | 列表规模小（几十~几百），`useMemo` 聚合，无虞 |
| 双主题下 hover/表头底色可见度不一 | 设计用 token（`bg-muted/*`），两主题各自校准；视觉验证覆盖双主题 |
| scan 配色与 `SidebarItem.accent` 冲突 | A 方案信息卡统一 coral 轻底，移除 `accent` 字段；类型标识归类型 badge，黑盒 orange 提示块保留 |

---

## 12. 验收标准

- 三页在深色 + 浅色主题下都「达到 Dashboard / 报告页的精致度」
- 概览条数值正确（与表格数据一致）
- 表格行 hover 清晰、表头有轻底、卡片浮起收边
- scan 页结构零变化（仅配色），emoji 保留
- `ui/table` 改动不破坏其他表格页面测试
- 改动相关测试子集全绿
