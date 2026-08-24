# 主题库扩展（OpenDesign 六主题移植）设计

- 日期：2026-08-25
- 状态：已确认（brainstorming 完成，待实现计划）
- 分支：`feat/fork-py`
- 上游方法论：commit `3fb6a5c6`「主题系统全面对齐 open design」——每主题对标一套 [OpenDesign](https://open-design.ai/zh/plugins/systems/) 开源设计系统真值（DESIGN.md）

## 0. 背景与目标

现状 5 主题（深色：charcoal/midnight/graphite；浅色：mac/warm-paper），深浅不对称且材质语言单一（vibrancy「透」材质仅 mac 独有）。本次扩展 6 个新主题（深 3 浅 3）至 11 个，全部从 OpenDesign 库选型移植：借参考系统的表面配色与材质语言，按 supernova 现有 token 六层结构（A shadcn / B 语义 / C 字体圆角 / D prose / E elevation / F vibrancy）落位。

目标：主题库一次成型（方案 A，全量 + 配套优化），零新架构——Arc 深色玻璃直接复用 mac 三件套机制（`--backdrop-*` token + 未定义回落 `none`，组件零改动）。

## 1. 决策记录（已确认）

| 决策点 | 结论 |
|---|---|
| 选型 | 深：Sentry / Arc / Mission Control；浅：GitHub / Notion / kami |
| 实施范围 | 方案 A：全量 6 主题 + 设置页网格可用性验证 + 纪律注释修订 |
| primary 策略 | 每主题本色（见 §3 分层纪律）；arc 例外——玻璃是身份，primary 保持 coral |
| 字体策略 | 默认共享 IBM Plex；仅 kami 覆盖 `--font-sans` 为衬线栈 |
| 默认主题对 | 不变：浅=Mac / 深=Claude 深色（`defaultThemeFor` 不动） |

## 2. 主题格局与命名

| 分组 | 现有 | 新增 |
|---|---|---|
| 深色 | charcoal（Claude 深色·品牌基准）、midnight（Linear）、graphite（Geist） | `sentry`、`arc`、`mission` |
| 浅色 | mac（Apple HIG·vibrancy）、warm-paper（Claude 浅色·品牌基准） | `github`、`notion`、`kami` |

- id 用 kebab-case 短名（`mission` 不用全称，对齐 `mac` 的短命名习惯）；`normalizeStored` 增 6 个合法值。
- 新主题全部为显式选择项，不参与 system 解析与快捷翻转（`oppositeBaseTheme` 逻辑不动）。
- `THEMES` 数组插入位置：深组追加在 graphite 后、浅组追加在 warm-paper 后（组内保持现有顺序）。

## 3. 设计纪律修订（tokens.css 头部与 §扩展主题注释同步改写）

1. **主色**：~~「coral 主色全主题保留」~~ → **分层纪律**：品牌层（charcoal/warm-paper/midnight/graphite）coral 保留；系统对齐层（mac/github/sentry/mission/notion/kami）用参考系统本色 primary；arc 以玻璃透质感为身份、primary 保持 coral（Arc 品牌珊瑚 `#ff5f5f` 与 supernova coral 同族，不构成第二主色）。
2. **字体**：~~「字体始终共享」~~ → 字体默认共享；衬线主题（kami）可覆盖 `--font-sans` 栈。
3. **不变**：severity red/orange/yellow **hue 锁定**（5°/24°/38°，只调 lightness/sat 保 AA）；cyan=GitNexus / magenta=LLM 双轨语义色逐主题单独校对比。

## 4. 六主题规格

通用规则：真值以各 DESIGN.md hex 为唯一事实源，编码时换算为 HSL channel（`H S% L%`）格式；表面带 alpha 的写 `H S% L% / A`。DESIGN.md 未直接给出、由其色板推导的补值（如 arc 深色页面底、sentry popover 抬升面）在表中以括注说明推导依据——实现时此类值允许在保持色板气质的前提下微调。

### 4.1 sentry（`.dark.theme-sentry`）— Sentry 错误监控仪表盘

身份：深紫黑「暗 IDE」仪表盘，永不纯黑，紫调暖黑；与漏洞监控的血统最正。

| token | 真值 | 落位 |
|---|---|---|
| bg | `#150f23` | `--background` |
| card | `#1f1633` | `--card`/`--secondary`/`--muted` 底 |
| popover | `#241d3d`（bg 与 border 间的紫黑抬升面） | `--popover`/`--accent` 底 |
| border | `#362d59` | `--border`/`--input` |
| primary | `#6a5fc1`（Sentry 紫） | `--primary`/`--ring`，fg 白 |
| fg | `#ffffff` / 次级 `#e5e7eb` | `--foreground` 及 *-foreground |
| muted-fg | `#9d96b3` | `--muted-foreground` |

- 语义：`--c-green` 借 lime `#c2ef4e` 气质（green 无 hue 锁）；`--c-red` 源 `#dc2626` 调至 hue 5°；`--c-orange`/`--c-yellow` 源 `#ff9f43`/`#eab308` 调至 hue 24°/38°；`--c-magenta` 用品牌粉 `#fa7faa`；`--c-cyan` 青调对齐 GitNexus 语义校 AA。
- 材质（层 E）：按钮 inset 触感阴影（`rgba(0,0,0,0.1) 0 1px 3px inset` 系）；卡浮层紫调环境光晕（`rgba(22,15,36,0.9)` 气质、降透明度至可用）；**层 F**：定义 `--backdrop-float: blur(18px) saturate(180%)`（DESIGN.md 玻璃浮层配方），不定义 `--backdrop-card`。
- 圆角 8px。字体共享。

### 4.2 arc（`.dark.theme-arc`）— Arc 深色玻璃

身份：mac 的深色玻璃对——半透表面 + Arc 渐变温度暗版环境光，浮起靠玻璃不靠投影。

| token | 真值 | 落位 |
|---|---|---|
| bg | `#14141a` 系（Glass Dark 底 `rgba(20,20,25,·)` 的实底化） | `--background` |
| card | `rgba(30,30,38,0.6)` | `--card`（带 alpha） |
| popover | `rgba(30,30,38,0.75)`（浮层玻璃深一级） | `--popover`（带 alpha） |
| border | `rgba(255,255,255,0.4)`（Border Glass） | `--border`/`--input` |
| primary | coral 保持（`15 60% 56%` 系） | `--primary`/`--ring` |
| fg | `#fafafa`（Ink Inverse） | `--foreground` 及 *-foreground |
| muted-fg | `#8c8c93` | `--muted-foreground` |

- 材质（层 F 三件套深色版）：`--backdrop-card: saturate(180%) blur(24px)`、`--backdrop-float: saturate(180%) blur(36px)`；body 环境光层 = Arc 渐变暗版三团 radial（sunset 暗桃 `#ff7e5f` 系 / 暗珊瑚 / twilight 暗紫 `#7f5af0` 系，饱和度压低），`background-attachment: fixed`，仅 `.dark.theme-arc body` 生效。
- 层 E：极柔 `0 8px 32px rgba(0,0,0,0.08)` 系（Arc 不靠投影浮起）。
- 圆角 16px（squircle 最软，全主题最大）。字体共享。severity 沿用品牌深色值微调。

### 4.3 mission（`.dark.theme-mission`）— Mission Control 指挥中心

身份：深空海军蓝 + 琥珀遥测，「amber on navy」；3 米外低光可读（AA 全过）。

| token | 真值 | 落位 |
|---|---|---|
| bg | `#0B1120` | `--background` |
| card | `#111827` | `--card`/`--secondary`/`--muted` 底 |
| popover/hover 面 | `#1A2535` / active `#1E3A5F` | `--popover` / `--accent` 底 |
| border | `#1E3A5F`（subtle `#162035`） | `--border`/`--input` |
| primary | `#FFB800`（琥珀遥测，primary data） | `--primary`/`--ring`，fg 用深海军 `#06101d` |
| fg | `#E8F0FE` / 次级 `#8BA3C7` / 三级 `#4A6080` | `--foreground`/`--muted-foreground` |

- 语义：`--c-green` `#26DE81`；`--c-cyan` `#00D4FF`（天然 Accent 青，GitNexus 语义正贴）；`--c-red` 源 `#FF4757` 调至 hue 5°；`--c-orange` 源 `#FF9F43`（27°→24°）；`--c-yellow` 调至 38°；`--c-magenta` 粉紫调校 AA。
- 材质（层 E）：深投影 `0 24px 72px rgba(0,0,0,0.42)`；面板分隔线哲学（border-subtle 内线）。
- 圆角 4px 封顶（全主题最硬朗，DESIGN.md 反模式条款：圆角 >4px 禁用）。字体共享（等宽气质由现有 mono 消费点自然获得，不覆盖 `--font-sans`）。

### 4.4 github（`.light.theme-github`）— GitHub Primer

身份：代码优先蓝白精准；实色细线边框哲学、信息密度即品牌。

| token | 真值 | 落位 |
|---|---|---|
| bg | `#ffffff` | `--background` |
| card | `#ffffff`（抬升靠边框非底色差） | `--card` |
| secondary/muted 底 | `#f6f8fa` | `--secondary`/`--muted`/`--accent` 底（accent 用 `#eaeef2` inset 层） |
| border | `#d0d7de`（soft `#d8dee4`）——**实色**，非 alpha hairline | `--border`/`--input` |
| primary | `#0969da`（Primer 蓝，hover `#0550ae`） | `--primary`/`--ring`，fg 白 |
| fg | `#1f2328` / `#656d76` | `--foreground`/`--muted-foreground` |

- 语义：`--c-green` `#1a7f37`（GitHub 绿）；`--c-red` 源 `#cf222e`（354°→5° 微调）；`--c-orange`/`--c-yellow` 源 severity 锁 hue；`--c-cyan`/`--c-magenta` 中性蓝紫调校 AA。
- 材质（层 E）：ring + 微影（`0 1px 3px rgba(31,35,40,0.12), 0 8px 24px rgba(66,74,83,0.12)`），无玻璃无大阴影。
- 圆角 6px。字体共享（system-ui 是 GitHub 哲学，气质靠色/线/密度承载）。

### 4.5 notion（`.light.theme-notion`）— Notion 暖灰极简

身份：暖中性灰（黄棕 undertone，无冷灰）+ 低语边框 + 多层极低透明度阴影。

| token | 真值 | 落位 |
|---|---|---|
| bg | `#ffffff` | `--background` |
| card | `#ffffff`（白卡浮于暖白交替面） | `--card` |
| secondary/muted 底 | `#f6f5f4`（暖白） | `--secondary`/`--muted`/`--accent` 底 |
| border | `rgba(0,0,0,0.1)`（低语边框） | `--border`/`--input` |
| primary | `#0075de`（Notion 蓝） | `--primary`/`--ring`，fg 白 |
| fg | `rgba(0,0,0,0.95)` → `0 0% 5% / 0.95` | `--foreground` |
| muted-fg | `#615d59` / `#a39e98`（占位级） | `--muted-foreground` |

- 语义：`--c-green` `#1aae39`；`--c-orange` 源 `#dd5b00`（25°≈24° 天然合）；`--c-red` 源 `#dc2626` 调 hue 5°；`--c-yellow` 锁 38°；cyan/magenta 校 AA。
- 材质（层 E）：4 层低透明度叠加（0.04/0.027/0.02/0.01 递减，18px→1px 模糊梯度），「感受到而非看到」。
- 圆角 6px（按钮 4/卡 12 的折中）。字体共享（真值 NotionInter 本就无衬线；衬线例外归 kami）。

### 4.6 kami（`.light.theme-kami`）— kami 纸质报告

身份：羊皮纸画布 + 墨蓝唯一彩色 + 衬线主导——「高质量印刷品」而非 UI 面板；报告页白皮书阅读场景。

| token | 真值 | 落位 |
|---|---|---|
| bg | `#f5f4ed`（parchment，**绝不用纯白**） | `--background` |
| card | `#faf9f5`（ivory） | `--card`/`--popover` |
| secondary 底 | `#e8e6dc`（warm sand） | `--secondary`/`--muted` 底 |
| border | `#e8e6dc`（soft `#e5e3d8`）——实色纸质 ring | `--border`/`--input` |
| primary | `#1B365D`（墨蓝，唯一彩色 ≤5% 面积） | `--primary`/`--ring`，fg ivory |
| fg | `#141413` / `#3d3d3a` / `#504e49` / `#6b6a64` 四级全暖灰 | `--foreground`/`--muted-foreground` |

- 语义（降饱和印刷色，hue 天然近 severity 锁）：`--c-red` `#8a3a30`（5° 天然合）；`--c-yellow` `#8a6b1f`（43°→38° 微调）；`--c-green` `#4a6b3a`；`--c-orange`/cyan/magenta 同法降饱和校 AA。
- 材质（层 E）：ring + whisper `0 4px 24px rgba(0,0,0,0.05)`；**禁**硬阴影/玻璃/渐变（DESIGN.md 反模式条款），不定义层 F。
- 圆角 6px。**字体覆盖**（唯一）：`--font-sans` → 衬线栈 `Charter, Georgia, Palatino, "Songti SC", "Source Han Serif SC", serif`；prose 层（层 D）是重头戏：衬线正文 + 墨蓝链接 `#1B365D`（与 primary 同源；`#2D5A8A` Ink Light 是 DESIGN.md 为深色表面定义的变体，kami 为浅色主题不使用）。

## 5. 实现面（改动文件清单）

| 文件 | 改动 |
|---|---|
| `packages/web/frontend/src/styles/tokens.css` | +6 palette 块（各含层 A/B/C/D/E；arc 另含层 F + `.dark.theme-arc body` 环境光层；sentry 含层 F float）；头部与扩展主题段纪律注释按 §3 改写 |
| `packages/web/frontend/src/lib/theme.ts` | `ThemeId` +6；`THEMES` +6 项（含 preview 硬编码 hsl，与 tokens.css 对应块同步维护——沿用现约定）；`normalizeStored` +6 合法值 |
| `packages/web/frontend/src/pages/SettingsPage.tsx` | 深/浅两组**已是 `grid grid-cols-3` 网格**（实现期勘误：非纵向列表）；11 主题下深 6 浅 5 各 2 行，默认零改动，仅当视觉走查发现主题名标签挤压时将两组降为 `grid-cols-2` |
| `packages/web/frontend/src/locales/zh.json` / `en.json` | 各 +6 个 `settings.themes.*` key（现成 kebab→camel 机制：sentry/arc/mission/github/notion/kami） |
| `packages/web/frontend/index.html` | **不动**（FOAC 默认主题对语义不变） |
| 测试 | `theme.test.ts`（新 id 归一/applyClass 挂双类/preview 结构完整）；`tokens.test.ts`（漂移断言扩 6 主题 + kami `--font-sans` 衬线覆盖断言 + arc/sentry 层 F 存在性断言）；`SettingsPage.test.tsx`（11 主题 + system 渲染、网格结构）；`ThemeToggle.test.tsx` / `theme-context.test.tsx` 回归 |

## 6. token 映射规则（DESIGN.md → supernova 六层）

- 表面：`--bg→--background`；`--surface→--card`；hover/active 面→`--popover`/`--accent` 底。
- 文本：`--fg→--foreground` 及 `*-foreground`；`--muted→--muted-foreground`。
- 边框：`--border→--border`/`--input`（github/kami 用实色 hex，notion 用 alpha，深色组按真值）。
- primary：`--accent→--primary`/`--ring`；`--accent-on→--primary-foreground`。
- 层 C：`--radius` 按 §4 各主题值；`--font-sans` 仅 kami 覆盖。
- 层 D：`--prose-*` 全套逐主题（body/headings/links/bold/code/code-bg/quotes/bullets/hr），links 用各主题 primary 系。
- 层 E：`--shadow-card`/`--shadow-cta`/`--shadow-cta-hover`/`--shadow-toolbar`/`--shadow-toolbar-hover` 全套逐主题按 §4 材质语言。
- 层 F：arc 定义 card+float；sentry 仅 float；其余不定义（组件 `var(--backdrop-*,none)` 回落零影响）。

## 7. 验收标准

1. 11 主题全部可在设置页选择、localStorage 持久化、刷新还原；system 选项与快捷翻转行为不变。
2. 新 id 存储归一合法；旧存储值（`dark`/`light`/`frost` 遗留映射）不受影响。
3. AA 对比度：各主题 `foreground`/`muted-foreground`/severity 色在其表面 ≥4.5:1（dev 预览页校验，沿用现行流程）。
4. 相关测试全绿：`theme.test.ts` / `tokens.test.ts` / `SettingsPage.test.tsx` / `ThemeToggle.test.tsx` / `theme-context.test.tsx`（只跑改动相关文件，遵守 CLAUDE.md 测试陷阱约定）。
5. 视觉走查：6 新主题下 Dashboard / 工作区详情 / 设置页 / 报告页（kami 重点看衬线 prose）无破版。

## 8. 参考来源（DESIGN.md 真值，Apache-2.0）

- Sentry：https://open-design.ai/zh/plugins/design-system-sentry/
- Arc Browser：https://open-design.ai/zh/plugins/design-system-arc/ （slug 无 `-browser`）
- Mission Control：https://open-design.ai/zh/plugins/design-system-mission-control/
- GitHub：https://open-design.ai/zh/plugins/design-system-github/
- Notion：https://open-design.ai/zh/plugins/design-system-notion/
- kami：https://open-design.ai/zh/plugins/design-system-kami/
