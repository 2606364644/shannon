# Settings 页重设计

> 日期：2026-07-28
> 范围：web 前端 `packages/web/frontend/src/`（纯前端，不动后端）
> 不触碰：双轨 / 引擎 / cost 计费等核心不变量（CLAUDE.md §1/§2/§4）；不动后端 API；不新增功能区块
> 流程说明：本 spec 经 brainstorming 确认（布局=分区重组+eyebrow、主题控件=Segmented 三段式），写文档但**不 commit**，审过后直接 TDD 实现。

## 1. 背景与动机

Settings 页（`/settings`）现状功能完整（主题切换 / 系统状态 / 关于 / per-ws 提示 / 账户安全 5 区块），但：

- **视觉平淡**：5 张 Card 在 `space-y-4` 里纵向平铺，无信息层级、节奏单调，缺设计感。
- **主题切换有真 bug**：`ThemeToggle`（TopBar）与 Settings 页的 Switch 各自独立 `useState`、无共享状态，一处切换另一处不同步（LoginPage 浮动 ThemeToggle 同病）。
- **amber 状态色未走 token**：`tokens.css` 与 `tailwind.config.ts` 均未定义 `amber`，但 SettingsPage / UsersPage / TopBar 三处用了 `border-amber/50 text-amber`，脱离 `--c-*` 语义体系。
- **"跟随系统"主题能力丢失**：`getInitialTheme` 仅在无 stored 时读一次 `prefers-color-scheme`，用户切过主题后不再跟随系统；UI 也只给了 dark/light 两态 Switch。

用户决策（brainstorming 确认）：本次做**视觉与布局升级 + 可用性 bug 修复**，不新增功能、不动后端。

## 2. 现状（已核实）

### 2.1 SettingsPage（`pages/SettingsPage.tsx`，107 行）

- `<div className="space-y-4">` 包 PageHeader + 5 × Card（CardTitle 统一 `font-semibold tracking-tight text-base`）。
- 主题卡：`Switch`（`checked={theme==="light"}`），独立 `useState(getInitialTheme())`（line 19）。
- 系统状态卡：`<dl className="grid grid-cols-[140px_1fr] gap-y-2 font-mono text-sm">`，状态 Badge 用 `border-green/40 text-green` / `border-red/40 text-red`。
- 账户安全卡：`must_change_password` 时显 `border-amber/50 text-amber` Badge + 改密 Button（开 ChangePasswordDialog）。
- 加载 `Skeleton`、错误 `ErrorState`。

### 2.2 主题系统（`lib/theme.ts` + `index.html`）

- `Theme = "dark" | "light"`（**无 system**）。
- `getInitialTheme()`：localStorage 优先，无 stored 读 `prefers-color-scheme: light`，否则 dark。**仅初始化读一次，不监听系统变化**。
- `applyTheme(t)`：`<html>` 移除 dark/light、加 t、写 localStorage。
- `index.html` 引导脚本（防 FOUC）：读 localStorage，**仅认 `"dark"`/`"light"`**，其余读 prefers-color-scheme。**不认 `"system"`**。
- 消费者：`SettingsPage`（独立 state）、`ThemeToggle`（独立 state）、`LoginPage` 浮动 `ThemeToggle`。**三处各自 `useState`，无共享 context** → 不同步。

### 2.3 amber token 缺失（已核实）

- `tokens.css` 无 `--amber` / `--c-amber`；`tailwind.config.ts` 无 `amber` 颜色映射。
- 三处用法：`SettingsPage.tsx:95`、`UsersPage.tsx:87`、`TopBar.tsx:78` 均 `border-amber/50 text-amber`（TopBar 另有 `hover:bg-amber/10`）。脱离 `--c-green/red/orange` 语义体系，深底跳色 / 样式不生效。

### 2.4 设计系统（不动，复用）

- `tokens.css` 三层：A=shadcn（`:root`深/`.light`浅）、B=语义 `--c-*`、C=IBM Plex + `--radius`。
- `--primary` 双主题 coral（≈ #D97757）。
- 全站 PageHeader + Card 堆叠模式；`--shadow-card` hairline + 柔阴影，card↔bg 差 ~4%。

## 3. 方案选型（已与用户确认）

- **布局方向 = 分区重组 + eyebrow**：5 区块按语义重排 3 分区（个人化 / 系统 / 关于），eyebrow 拉层次，同组小卡并排。
- **主题控件 = Segmented 三段式**：`[浅色 | 深色 | 跟随系统]`，补回"跟随系统"能力，作页面 signature。
- **bug 修法**：ThemeContext 共享状态修不同步；新增 `--c-amber` 统一三处 amber 用法。

**不选**：双栏锚点导航（5 固定区块 over-engineered）；视觉预览卡片（实现过重）；保持 Switch（放弃 signature + 不补跟随系统）。

## 4. 设计

### 4.1 信息架构：分区重组

3 分区，每区 eyebrow（coral 竖条 `▍` + `text-xs uppercase tracking-wider text-muted-foreground` + 可选一行副说明）：

| 分区 | 区块 | 布局 |
|---|---|---|
| ▍个人化 | 主题切换 · 账户安全（改密） | `md:grid-cols-2` 并排两小卡 |
| ▍系统 | 系统状态 | 独占宽卡（dl 列表） |
| ▍关于 | 关于 · per-ws 配置提示 | `md:grid-cols-2` 并排（保持对称） |

节奏：分区间距（`space-y-8`）> 分区内卡片间距（`gap-4`）。窄屏（<md）全部回落单列。

PageHeader 加一行 `text-sm text-muted-foreground` 副说明（如"管理外观与账户"）。

### 4.2 主题控件：Segmented 三段式

替换现 Switch 为三段选择器（浅色 ☀ / 深色 ○ / 跟随系统 ❒）。优先复用 shadcn `ToggleGroup` / `Tabs` 基件（explore 见 `components/ui/tabs.tsx` 存在），否则 `Button` group 自建，不引第三方。

**"跟随系统"语义**：
- 选中 → localStorage 存 `"system"`；实际渲染的 `<html>` class 由 `prefers-color-scheme` 决定。
- **监听** `matchMedia("(prefers-color-scheme: light)")` change，系统切换时实时翻 `<html>` class（否则名不副实）。

### 4.3 ThemeContext 共享（修不同步）

新增 `lib/theme-context.tsx`：
- `ThemeProvider`：持有 `theme: Theme`（含 `"system"`），mount 时 `getInitialTheme` 初始化；提供 `setTheme(t)`（调 `applyTheme` + 更新 state + 持久化）。
- `useTheme()`：返回 `{ theme, setTheme }`，消费失败抛错（强制 Provider 包裹）。
- `lib/theme.ts` 扩展：`Theme` 加 `"system"`；`applyTheme` 支持 system（解析 effective class + 注册 matchMedia 监听；切回显式 dark/light 时清理监听）；新增 `resolveEffectiveTheme(theme)`。

**Provider 位置**：放在 router 根（AppShell **外层**），同时覆盖 AppShell（TopBar ThemeToggle + SettingsPage）与 LoginPage 浮动 ThemeToggle。

**改造消费者**：
- `ThemeToggle.tsx`：删独立 `useState`，改 `useTheme()`。TopBar 图标按钮做**快捷翻转**：切到当前 effective theme 的反色并落为**显式态**——system 用户点一下 → 显式切到另一态（localStorage 从 `"system"` 变为 `"dark"`/`"light"`），即"我要明确换到对面"的语义；`title` 提示当前态。三态选择留在 Settings 页 segmented。
- `SettingsPage.tsx`：删独立 `useState`，主题卡换 segmented 消费 `useTheme()`。
- `LoginPage.tsx`：浮动 ThemeToggle 自动受益（同步），仅 import 不变。

**`index.html` 引导脚本**：扩展认 `"system"`（读到 system → 按 prefers-color-scheme 决定 dark/light），保持旧 `"dark"`/`"light"` 向后兼容，避免刷新 FOUC。

### 4.4 amber token 化

- `tokens.css` 加 `--c-amber`（深 / 浅双值，sat 对齐现有 `--c-orange/yellow` 暖梯度，深底不刺眼）。
- `tailwind.config.ts` 的颜色映射补 `amber: "hsl(var(--c-amber) / <alpha-value>)"`（与现有 cyan/green/red/orange 同模式，支持 `/50` alpha）。
- 三处用法统一走 `--c-amber`：`SettingsPage:95`、`UsersPage:87`、`TopBar:78`（含 `hover:bg-amber/10`）。语义不变（must_change 提醒）。

### 4.5 改动清单

| 文件 | 改动 |
|---|---|
| `lib/theme.ts` | `Theme` 加 `"system"`；`applyTheme` 支持 system + matchMedia 监听；新增 `resolveEffectiveTheme` |
| `lib/theme-context.tsx`（新） | `ThemeProvider` + `useTheme` |
| `index.html` | 引导脚本认 `"system"`，保持旧值兼容 |
| `router.tsx`（或根布局） | 挂 `ThemeProvider`（覆盖 AppShell + LoginPage） |
| `pages/SettingsPage.tsx` | 分区重组 + eyebrow；主题卡换 segmented；删独立 useState 用 useTheme；amber 走 token |
| `components/layout/ThemeToggle.tsx` | 删独立 useState 用 useTheme（行为保持快捷翻转） |
| `pages/UsersPage.tsx` / `components/layout/TopBar.tsx` | amber 走 token（仅 className） |
| `styles/tokens.css` | 加 `--c-amber` 深浅双值 |
| `tailwind.config.ts` | `amber` 映射到 `--c-amber` |
| `locales/{zh,en}.json` | 加键：分区 eyebrow、segmented 三态 label、PageHeader 副说明（纯加键不删旧） |

## 5. 不变量与边界

- **纯前端**：不动后端 API、不改 5 区块实质内容、不新增功能区块。
- **不碰核心不变量**：双轨 / 引擎 / cost 计费（CLAUDE.md §1/§2/§4）零影响。
- **i18n 只加键不删旧**，不破现有翻译。
- **设计系统不变**：`--primary` coral、IBM Plex、`--radius`、`--shadow-card` 体系不动；仅**新增** `--c-amber`。
- **主题渲染语义不变**：`<html>` 仍只挂 `dark`/`light` class（`"system"` 是存储/逻辑态，不直接挂 `<html>`），tokens.css 的 `:root`/`.light` 分支不动。

## 6. 测试（TDD，先红后绿）

### 6.1 theme 逻辑（`lib/theme.test.ts` 扩展）

- `applyTheme("system")`：写 localStorage `"system"`；`<html>` class 按 prefers-color-scheme 解析；注册 matchMedia 监听。
- `resolveEffectiveTheme("system")`：mock matchMedia light/dark → 返回正确 effective。
- matchMedia change 触发：`<html>` class 实时翻转；切回 dark/light 时清理监听。
- 现有 `applyTheme("dark"/"light")` / `getInitialTheme` 不回归。

### 6.2 ThemeContext（`lib/theme-context.test.tsx` 新）

- Provider 初始化读 `getInitialTheme`。
- `setTheme` → applyTheme 调用 + state 更新 + localStorage。
- **两消费者同步**：同挂 Provider 的两个 `useTheme()` 消费者，一个 setTheme → 另一个读到新值（锁不同步 bug 不回归）。

### 6.3 SettingsPage（`SettingsPage.test.tsx` 更新 + 新增）

- 3 分区 eyebrow 存在（个人化 / 系统 / 关于）。
- 主题 segmented 三态：点"跟随系统" → localStorage `"system"`。
- 改密 badge：`must_change_password` true 显 badge、false 不显（不回归）。
- loading / error 态（不回归）。

### 6.4 amber token

- `tokens.css` 含 `--c-amber`（深/浅）；`tailwind.config.ts` amber 映射（结构断言）。
- `UsersPage` / `TopBar` amber className 改动不破其现有测试（跑相关测试确认）。

### 6.5 不做的测试

- 不改后端，零后端测试。
- 不为 LoginPage 写新测试（ThemeToggle 行为由 6.2 覆盖，LoginPage 仅 import 不变）。

## 7. 风险与回退

- **风险低**：纯前端，改动集中在 theme 层 + SettingsPage + 三处 amber className + 两个配置文件。
- **"跟随系统" matchMedia 监听**：需正确清理（unmount / 切回显式主题时 removeEventListener），否则泄漏。TDD 覆盖。
- **index.html 引导脚本**：扩展时保持向后兼容（旧 stored `"dark"`/`"light"` 仍生效）。
- **回退**：未 commit 前任意回退；commit 后 `git revert` 单 commit。

## 8. 实现前需确认的事实（TDD 第一步先查）

- `tailwind.config.ts` 现有 `--c-*` → 颜色映射的精确写法（照抄加 `amber`）。
- shadcn 是否已有 `ToggleGroup` 可作 segmented 基件（优先复用；无则 `Tabs` 或 Button group 自建）。
- `UsersPage` / `TopBar` amber 改动是否触动其现有测试（实现期跑相关测试确认不回归）。
