# OpenAI 主题（近单色研究室）设计

- 日期：2026-08-27
- 状态：已实现（TDD 红→绿）
- 分支：`feat/fork-py`
- 上游真值：[OpenDesign design-system-openai DESIGN.md](https://open-design.ai/zh/plugins/design-system-openai/)（Apache-2.0，与 2026-08-25 六主题移植同一上游库）

## 0. 背景与身份

用户请求新增「OpenAI 系设计语言」主题。OpenDesign 库有第一方 OpenAI 设计系统（`design-system-openai`），真值齐备——按项目既定移植方法论（DESIGN.md hex 为唯一事实源 → 六层 token）落位，主题库 12→13（深 6 浅 7）。

身份：**「面向公众开放的研究室」**——纯白画布 + 深青黑墨色（#0d0d0d，微微冷感的近黑，非咄咄逼人的深暗）+ **主 CTA 墨黑**（DESIGN.md「主要按钮 #0d0d0d」档——真身 openai.com/chatgpt.com 的 CTA 就是黑底白字）+ OpenAI 青 #10a37f 退守稀少强调岗（focus ring / 链接 / 成功——中性系统中唯一彩色）+ Söhne/Inter 克制字重 + 细线边框（「颜色的缺席」）+ 默认无影（hover 才有 0 4px 16px 极轻影）+ 留白为主要分隔手段。与近邻主题的区分：github 同为「白+线」骨架但 coral 主色/Plex 字体/6px 圆角；openai 是黑 CTA + 稀少 teal、Inter、12px 软圆角、全库最轻阴影（单层 whisper）。

**primary 档位决策（2026-08-27 实现当日 review 修订）**：DESIGN.md 按钮分档——主要按钮=墨黑、品牌强调按钮（升级/成功路径）=teal、链接/徽章/焦点=teal。supernova `--primary` 驱动全 UI 主按钮，映射「主要按钮」档（墨黑）才忠实；若映射「品牌强调」档（teal）会全界面绿按钮，读作「绿色主题」，恰违反「中性系统里的稀少彩色」铁律（DESIGN.md §7 反模式「仅靠绿色不成 OpenAI 风」的对称面）。teal 保留在 `--ring`（--focus-ring 真值）/ prose 链接 / c-green。

DESIGN.md 反模式条款（§7 使用约束）同步继承：①需同时保留「中性编辑克制感+软圆角+稀少强调色」——仅绿色不成 OpenAI 风；②Signifier 衬线仅限编辑展示层级，产品控件无衬线（supernova 无展示字体消费点，不覆盖 --font-serif——衬线身份已归 kami）；③避免装饰性动效、厚阴影、过大装饰卡。

## 1. 六层 token 规格（`.light.theme-openai`）

通用规则沿用 spec 2026-08-25 §6 映射；hex 真值编码时换算 HSL channel。

### 层 A · shadcn token

| token | 真值 | HSL | 依据 |
|---|---|---|---|
| --background | `#ffffff` 纯白画布 | `0 0% 100%` | --bg |
| --card | `#ffffff` 白卡（轮廓靠细线非色调差，同 github 派） | `0 0% 100%` | §4 卡片 |
| --popover | `#ffffff` | `0 0% 100%` | 浮层默认白 |
| --primary | `#0d0d0d` 墨黑（主要按钮档） | `0 0% 5%` | §4 主要按钮（黑底白字 CTA） |
| --ring | `#10a37f` OpenAI 青 | `165 82% 35%` | --focus-ring（teal 焦点光晕真值） |
| --primary-foreground | `#ffffff` | `0 0% 100%` | --accent-on |
| --secondary/--muted 底 | `#fafafa` 薄雾 | `0 0% 98%` | --surface-warm |
| --accent 底 | `#f5f5f5` 珍珠 | `0 0% 96%` | --surface（hover/胶囊面） |
| --foreground | `#0d0d0d` 墨黑 | `0 0% 5%` | --fg（「深青黑」锚点） |
| --muted-foreground | `#6e6e6e` 板岩 | `0 0% 43%` | --muted |
| --border / --input | `#e5e5e5` 细线（实色） | `0 0% 90%` | --border |
| --destructive | `#ef4146` 调至 hue 5° | `5 84% 48%` | --danger + severity 锁 |

### 层 B · 语义色

- `--c-green: 165 85% 26%`——深青 `#0a7a5e`（--accent-hover 真值档）：品牌青 35% 档白底仅 3.2:1，徽章文本需 AA，压到 hover 档 5.3:1（真值内两档选深档，非自造色）。
- `--c-red: 5 84% 48%`（源 --danger #ef4146 358°→锁 5°+压深保 AA）；orange `24 85% 38%`（源 --warn #f5a623 37°→锁 24°）；yellow `38 85% 30%`；amber `36 78% 33%`（浅组惯例档）。
- `--c-cyan: 190 65% 30%`（GitNexus 轨——与 teal 主色 165° 拉 25°+更蓝，防双绿混淆）；`--c-magenta: 278 55% 45%`（LLM 轨）。

### 层 C · 字体/圆角

- `--radius: 12px`（DESIGN.md 行动按钮 12 / 卡 16 的主导档）。
- `--font-sans` 覆盖：`"Söhne", Inter, system-ui, -apple-system, "Segoe UI", sans-serif`（--font-body 原回退链；Söhne 为 Klim 商用本地多无——真值置首，Inter webfont 已在 index.html 兜底，即实际渲染字体）。第三处字体覆盖（kami 衬线 / mac SF 之后）。
- `--font-mono` 覆盖：`"Söhne Mono", ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace`（--font-mono 原回退链）。
- **不**覆盖 --font-serif（无展示消费点+反模式条款）；**不**定义 --radius-cta（OpenAI 行动按钮是 12px 矩形圆角，胶囊只用于芯片——非 mac 的全胶囊语言）。

### 层 D · prose

body 石墨 `0 0% 24%`（--fg 正文阅读色 #3c3c3c）/ headings 柔黑 `0 0% 10%` / links 深青 `165 85% 26%`（AA 5.3:1，同 c-green 源）/ code `190 65% 28%` + code-bg 珍珠 `0 0% 96%` / quotes·bullets 板岩 `0 0% 43%` / hr 细线 `0 0% 90%`。

### 层 E · elevation（全库最轻）

- `--shadow-card: 0 4px 16px hsl(0 0% 5% / 0.05)`——DESIGN.md 真值 hover 影 `rgba(13,13,13,0.06)` 压一档当静态 whisper，单层无 ring（卡 border 已画线，同 github「线不叠影」纪律）。
- `--shadow-cta`：黑 CTA 近无影——纯中性微落影（`0 1px 2px / 0.18`，hover `0 2px 6px / 0.24`），**无 teal 光晕**（青光晕在黑按钮上是脏边，同 mac「coral 光晕在蓝按钮上是脏橙边」教训）。
- `--shadow-toolbar`：whisper `0 1px 2px hsl(0 0% 5% / 0.05)`；hover 两层轻影。

### 层 F/F' · 不定义

无玻璃（DESIGN.md 无玻璃语言）、无画布材质（「留白为主要分隔手段」——纯白即真值，与 blueprint 网格/warm-paper 纸纹的材质阵营明确区分）、不定义 --topbar-bg（openai.com 顶栏即白+hairline，回落 popover 默认成立）。

## 2. 实现面

| 文件 | 改动 |
|---|---|
| `styles/tokens.css` | +`.light.theme-openai` 块（层 A/B/C/D/E，无 F）；头部扩展主题段注释补 openai 条目 |
| `lib/theme.ts` | `ThemeId` +`"openai"`；`THEMES` 浅组末尾（blueprint 后）+ 项（preview 硬编码 hsl：bg `0 0% 100%` / card `0 0% 96%` 珍珠 / primary `165 82% 35%` / border `0 0% 90%`）；`normalizeStored` +合法值 |
| `locales/zh.json` / `en.json` | `settings.themes.openai` = "OpenAI"（品牌名不译） |
| `index.html` | 不动（Inter webfont 已有；默认主题对语义不变） |
| 测试 | `theme.test.ts`（13 主题数组/openai paletteClass/applyClass 双类/归一/resolve）；`tokens.test.ts`（openai 块漂移断言：真值+字体覆盖+hue 锁+禁玻璃+极轻影）；`SettingsPage.test.tsx`（渲染 OpenAI+点击挂类；classList 清理列表 +theme-openai） |

## 3. 验收

1. 13 主题全部可设置/持久化/刷新还原；system 解析与快捷翻转行为不变（openai 不参与 defaultThemeFor）。
2. 相关测试全绿（theme / tokens / SettingsPage）+ `tsc --noEmit` 0 错。
3. AA：foreground/muted-foreground/severity/绿青各档 ≥4.5:1（深青 5.3:1 / 石墨 10:1 / 板岩 4.9:1 档）；primary 墨黑按钮白字 ≈19:1。
4. 视觉走查：Dashboard / 工作区 / 设置 / 报告页无破版；focus ring 呈 teal 光晕（DESIGN.md --focus-ring 同源）。
