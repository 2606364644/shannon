# WEB 登录页双主题支持设计

> 日期：2026-07-27
> 主题：登录页（`/login`）从"强制亮色"改为跟随全局深/亮双主题，并支持登录页内切换主题
> 状态：设计待评审

## 1. 背景与动机

WEB 前端已有完整的深/亮双主题系统（`lib/theme.ts` + `index.html` 防 FOUC 引导脚本 + `tokens.css` CSS 变量 + `ThemeToggle` 开关），除登录页外所有页面（经 AppShell）均已接入。

登录页 `LoginPage.tsx` 是**唯一未接入双主题的页面**：根容器硬编码 `className="light"`，强制永远亮主题，不跟随系统偏好、不跟随用户在 Settings/TopBar 设置的主题。

此"强制亮色"是 2026-07-26 web-auth spec §7 的明确决策（经 visual companion 三轮 mockup 确认的"A 固定亮色"）。本设计**更新该决策**：登录页改为跟随全局双主题，并在登录页内提供主题切换开关，与登录后 AppShell 行为一致。

动机：用户在系统/偏好为深色时，登录页仍亮色，与应用内深色主题割裂；且未登录态无法表达主题偏好。统一为跟随全局 + 可切换，消除主题孤岛。

## 2. 现状

### 2.1 登录页现状（`packages/web/frontend/src/pages/LoginPage.tsx`）

- 根容器 `<div className="light flex min-h-screen">` —— 唯一的"强制锁亮色"来源（`grep className="light"` 在 src/ 下仅此一处）。
- **右表单区**：已用 token（`bg-background`、`text-foreground`、`text-muted-foreground`、`text-destructive`、`border-border/60`、`bg-muted/40`）。去掉 `.light` 锁定后会自动翻深色，无需改。
- **左品牌区**：硬编码：
  - `GRADIENT = "linear-gradient(155deg, #2E2520 0%, #4A2E22 55%, #6B3A26 100%)"` —— 左侧背景渐变。
  - `bg-[#D97757]` —— logo 圆 ✦ 的底色。
  - `rgba(217,119,87,0.18)` —— logo 圆光晕（boxShadow）。
- 字体：已用 token（`var(--font-serif)`）。
- **无 ThemeToggle**：登录页不在 AppShell 内，TopBar 的 `ThemeToggle` 看不到。

### 2.2 主题系统现状（不动）

- `lib/theme.ts`：`THEME_KEY="supernova-theme"`；`applyTheme(t)` 给 `<html>` 加 `dark`/`light` class + 写 localStorage；`getInitialTheme()` localStorage 优先、无 stored 时读 `prefers-color-scheme`（仅启动读一次，不实时监听系统变化——已知 gap，不进本 spec）。
- `index.html`：内联引导脚本在 React 加载前同步给 `<html>` 加 class，防 FOUC。
- `tokens.css`：`:root` = 深色（默认），`.light` = 浅色覆盖。`--primary` 双主题都是 coral 同色系（深 `15 60% 56%` / 浅 `15 58% 50%`，均 ≈ #D97757）。语义色 `--c-orange` 等 `--c-` 前缀。
- `ThemeToggle.tsx`：ghost variant icon button，`useState(getInitialTheme)` 初始化，点击翻转 + `applyTheme` 持久化。目前仅在 `TopBar.tsx`（AppShell 内）。
- `tailwind.config.ts`：`darkMode: ["class"]`。

### 2.3 路由与守卫（不动）

- `/login` 是独立公开路由，不进 AppShell。
- 登录成功跳 `next`（默认 `/`），未登录访问受保护页由 `RequireAuth` 跳 `/login?next=...`。

## 3. 方案选型

| 方案 | 内容 | 取舍 |
|---|---|---|
| 方案 1（最小） | 去 `.light` + 放 ThemeToggle + 品牌色保持硬编码 | 改动最小，但 logo 圆/光晕仍硬编码 hex，未收敛 token |
| **方案 2（采用）** | 方案 1 + logo 圆换 `bg-primary`、光晕换 `hsl(var(--c-orange) / 0.18)` | 收敛到设计 token，零视觉损失（`--primary` 双主题都≈#D97757），符合项目 token 理念；渐变保持硬编码（7-26 spec 已验证双主题共用协调） |
| 方案 3（深色精修） | 方案 2 + 深色专门渐变变体 + 分屏交界分隔 | YAGNI，暖深渐变双主题共用已验证协调，不引入 dark 变体 |

**采用方案 2**。复用现有主题机制（不新造 ThemeProvider/Context），品牌色收敛 token，渐变保持硬编码。

**scope 边界（不做）**：
- 不监听 `prefers-color-scheme` 实时变化（独立增强，不塞本 spec）。
- 不引入 ThemeProvider/Context（保持命令式 `lib/theme.ts`）。
- 不为深色主题做专门渐变变体（YAGNI，真机冒烟发现问题再单独 follow-up）。
- 不动 `lib/theme.ts`、`ThemeToggle.tsx`、`tokens.css`、`index.html` 引导脚本、`RequireAuth`。

## 4. 设计

### 4.1 架构与机制

登录页从"强制 `.light` 锁定"改为"跟随全局 `<html>` 主题"，复用现有主题切换机制，不新造。

**全链路单一机制**：
1. 启动时 `<html>` class 由 `index.html` 引导脚本按 localStorage（`supernova-theme`）/ 系统偏好定。
2. 登录页跟随该 class 渲染（去掉 `.light` 锁定后，右表单区 token 自动翻深/浅）。
3. 用户点登录页内 ThemeToggle → 改 `<html>` class + 写 localStorage。
4. 登录后进 AppShell → 读同一 localStorage，主题天然延续。无需任何同步代码。

### 4.2 ThemeToggle 放置

登录页不在 AppShell 内，需在登录页自己放一个 ThemeToggle。

- **位置**：整个视口右上角浮动，`fixed top-4 right-4 z-10`，覆盖在分屏之上，不挤占表单空间。
- **复用**：直接 `import { ThemeToggle } from "@/components/layout/ThemeToggle"`，不写第二个开关组件。`ThemeToggle` 内部 `useState(getInitialTheme)` 初始化，登录页 mount 时读到的就是 `<html>` 当前 class 对应主题，状态正确。
- **可见性**：ghost button（透明底）。深色主题下落在左侧暖深渐变上，Sun 图标白色可见；浅色主题下落在右侧淡暖画布上（渐变双主题共用仍偏深，但右上角浮动 button 落点随主题在深/浅背景间），Moon 图标深色可见。两种主题下都天然适配，无需为登录页做专门样式。

### 4.3 品牌色 token 化

| 元素 | 改前 | 改后 | 理由 |
|---|---|---|---|
| logo 圆底色 | `bg-[#D97757]` | `bg-primary` | `--primary` 双主题都是 coral 同色系，换 token 零视觉损失，且随主题微调更协调 |
| logo 光晕 | `rgba(217,119,87,0.18)` | `hsl(var(--c-orange) / 0.18)` | 收敛到语义色 token，与主题联动 |
| 左侧渐变 | 硬编码 `GRADIENT` | **保持硬编码** | 7-26 spec 已验证暖深渐变双主题共用协调；深色下与右侧深色 `--background` 靠材质（渐变纹理 vs 纯色）+ 明度细微差区分，不靠强对比 |
| 字体 / 右表单区 | 已 token | 不变 | — |

**深色主题下左右区分**：靠材质（渐变纹理 vs 纯 `--background`）+ 明度细微差（渐变 `#2E2520` 偏暖棕、深色 `--background` `34 7% 10%` 偏中性炭黑），不靠强对比。真机冒烟若发现深色下左右糊在一起，再单独考虑 dark 渐变变体（follow-up，不进本 spec）。

**logo 圆可见性**：`bg-primary`（coral）落在暖深渐变（双主题共用）上，深/浅主题下都是 coral 圆落在深渐变上，对比一致，可见。

### 4.4 改动清单（仅 `LoginPage.tsx`）

1. 根容器：`<div className="light flex min-h-screen">` → `<div className="flex min-h-screen">`（去 `light`）。
2. 新增 import：`import { ThemeToggle } from "@/components/layout/ThemeToggle";`
3. 根容器内（分屏之外）加浮动 ThemeToggle：`<div className="fixed top-4 right-4 z-10"><ThemeToggle /></div>`。
4. logo 圆：`bg-[#D97757]` → `bg-primary`。
5. logo 光晕：`rgba(217,119,87,0.18)` → `hsl(var(--c-orange) / 0.18)`。
6. `GRADIENT` 常量、右表单区、字体、表单逻辑：不动。

## 5. 测试

### 5.1 不破坏的现有测试（不变量）

- `lib/theme.test.ts`：锁 `THEME_KEY` / `applyTheme` / `getInitialTheme`。**不动 theme.ts，零影响**。
- `ThemeToggle.test.tsx`：锁开关点击翻转 + 持久化。**复用组件不改，零影响**。
- `LoginPage.test.tsx` 现有断言：渲染欢迎语/输入框/按钮（按文案/label/role 查找），改动后仍成立。

### 5.2 新增测试（`LoginPage.test.tsx` 扩展）

1. **不再强制亮色**：渲染 LoginPage，断言根容器 className 不含 `"light"`。
2. **ThemeToggle 存在且可翻转**：渲染 LoginPage，按 `aria-label` = `t("theme.toggleAria")` 找到主题切换按钮，点击后 `<html>` class 在 `dark`/`light` 间翻转。
3. **跟随全局主题**：分别设 `<html class="dark">` 与 `<html class="light">` 渲染 LoginPage，断言根容器均不强制锁 light（验证"跟随"而非"锁定"）。

### 5.3 真机冒烟（plan 落实为手动验收）

- 深色主题：登录页左暖深渐变 + 右深色表单，ThemeToggle（Sun 图标）右上角可见可点；点 → 翻浅色。
- 浅色主题：左暖深渐变 + 右淡暖表单，ThemeToggle（Moon 图标）可见可点；点 → 翻深色。
- 主题延续：登录页设深色 → 登录 → AppShell 也是深色。
- 防 FOUC：刷新登录页无主题闪烁。

### 5.4 不做的测试

不新增 `lib/theme.test.ts` / `ThemeToggle.test.tsx` 的测试（覆盖的机制本次未动，加测试越界）。

## 6. 风险与回退

- **风险低**：改动集中在单文件 `LoginPage.tsx`，不动主题机制核心、不动守卫、不动 token 定义。
- **视觉风险**：深色下左右分屏对比弱化（靠材质区分）。若真机冒烟不可接受，回退为方案 3（加 dark 渐变变体）或临时恢复 `.light` 锁定。
- **回退**：`git revert` 单 commit 即可恢复强制亮色。

## 7. 对既有决策的更新

本设计更新 2026-07-26 web-auth spec §7 的"A 固定亮色"决策：登录页从强制亮色改为跟随全局双主题 + 登录页内可切换。其余 §7 视觉决策（B 分屏 + B1 暖深渐变标语 + serif 标题 + coral 主色）不变。暖深渐变双主题共用这一既有判断保留。
