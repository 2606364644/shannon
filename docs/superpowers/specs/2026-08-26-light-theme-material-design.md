# 亮色主题材质升级（纸纹 ×2 + 白盒蓝图）设计

- 日期：2026-08-26
- 状态：已实现（TDD 红→绿，85+3 测试绿，tsc 0 错）
- 分支：`feat/fork-py`
- 上游：2026-08-25 主题库扩展（OpenDesign 六主题移植）+ 2026-08-26 材质补课的延续

## 0. 背景与问题

亮色组五个主题（mac/warm-paper/github/notion/kami）材质深度不对称：mac（玻璃 vibrancy）、
github（线框）、notion（纯影）三极已成立，但 **warm-paper 与 kami 名叫「纸」却只有颜色差异**
（无任何纹理/材质机制），且全亮色组没有一种「纹理/图案」材质——主题感停留在换色。

本次目标：亮色组从「颜色差异」进到「材质差异」，不动已成立的三个极（克制即身份）。

## 1. 决策记录

| 决策点 | 结论 |
|---|---|
| 范围 | warm-paper/kami 落纸纹 + 新增 blueprint（浅）；notion/github/mac 不动 |
| 材质机制 | 通用 `--canvas-material` / `--canvas-material-size` token + `html body` 消费点（新「层 F' · 画布材质」） |
| 纸纹实现 | 内联 SVG feTurbulence fractalNoise data-URI（零资源文件、stitchTiles 无缝平铺） |
| 新主题 | blueprint（白盒蓝图）——概念主题非品牌移植：whitebox 本义是「读图纸」，绘图网格直扣产品隐喻 |
| 主题数 | 11 → 12（深 6 / 浅 6 对称）；默认主题对、system 解析、快捷翻转均不动 |
| warm-paper 结构 | 获得 paletteClass `theme-warm-paper`，但**色 token 仍单源于 `.light` 基础块**（材质块零颜色定义） |

## 2. 三处材质规格

### 2.1 warm-paper —— 暖纸纤维

- feTurbulence `baseFrequency=0.9`（高频细噪）+ `feColorMatrix saturate 0`（灰噪）+ rect `opacity=0.04`（噪点 alpha 平均再减半 → 实效 ~2%：纸感而非脏感）；tile 180px。
- 「暖纸」从奶油色漆变成真的纸；hairline + 柔暖阴影不动。

### 2.2 kami —— 羊皮纸颗粒

- `baseFrequency=0.55` / `numOctaves=3`（粗频颗粒，tile 220px）+ rect `opacity=0.07`——比 warm-paper 明显一档。
- 象牙卡 = 压在纹理纸面上的光滑纸片；印刷品三件套齐（纸张肌理 + 衬线 + 朱砂）。
- DESIGN.md 反模式禁的是硬阴影/玻璃/渐变——纸纹即纸的本体，不违反。

### 2.3 blueprint（新）—— 白盒制图桌

- **画布**：冷白绘图纸 `214 40% 97%` + 墨蓝双频网格（24px 小格 5% + 120px 大格 9%，双层 linear-gradient ×2 方向）——全主题库唯一图案材质。
- **线**：实色 crisp hairline `215 25% 84%`（蓝图线是画出来的，非 alpha 透出；与 github 的线框语言同族但冷调+网格底）。
- **主色**：制图墨蓝 `224 58% 34%`（与 GitNexus cyan `192 85% 28%` 拉开 32°+ 深度差）。
- **几何/影**：radius 4px（与 mission 同档的技术利落）；冷调紧凑双影（组件 border 已画线，影不加 ring）。
- **TopBar**：`--topbar-bg 214 35% 96%` 冷灰带（与 github 灰带同机制）。
- 无玻璃（图纸是实底材质）。字体共享。

## 3. 机制：层 F' · 画布材质

```css
html body {
  background-image: var(--canvas-material, none);
  background-size: var(--canvas-material-size, auto);
}
```

- `html body` (0,0,2) 压过 events.css 的 `body { background: var(--void) }` shorthand（(0,0,1)；tokens.css 先于 events.css import，同级会输给源顺序）；background-color 仍由 events.css 供（`--void` = `hsl(var(--background))`）。
- 未定义主题回落 `none` 零影响；arc 环境光层（`.dark.theme-arc body`，(0,2,1)）特异性更高不受影响。
- 噪点 attachment 默认 scroll——body 画布背景随文档滚动 = 纸面感（arc 的 fixed 是环境光的语义，不同）。

## 4. 实现面（改动文件）

| 文件 | 改动 |
|---|---|
| `styles/tokens.css` | 头注释补记；`.light.theme-warm-paper` 材质块；`html body` 消费点；kami 块 + 层 F'；`.light.theme-blueprint` 全六层块；扩展主题纪律注释更新（+blueprint 主色条款/圆角/画布材质清单） |
| `lib/theme.ts` | `ThemeId` +`blueprint`；warm-paper `paletteClass` null→`"theme-warm-paper"`；`THEMES` 浅组末尾 +blueprint（preview 硬编码 hsl 同步约定）；`normalizeStored` +`blueprint` |
| `index.html` | FOAC `DEF` map：+`blueprint` 行、`warm-paper` 加 palette class |
| `locales/zh.json` / `en.json` | +`settings.themes.blueprint`（蓝图 / Blueprint） |
| 测试 | `tokens.test.ts`（+kami 纸纹、+亮色材质 describe：消费点/warm-paper 块/blueprint 块）；`theme.test.ts`（12 主题序、warm-paper paletteClass、blueprint normalize/apply/resolve）；`SettingsPage.test.tsx`（+蓝图渲染点击、beforeEach class 清单） |

## 5. 验收

1. 12 主题设置页可选、持久化、FOAC 首帧不闪（warm-paper 存量用户刷新即得纸纹）。
2. 测试绿：tokens / theme / SettingsPage / ThemeToggle / theme-context（85）+ i18n locales（3）；`tsc --noEmit` 0 错。
3. AA：blueprint 各文本/语义色白底 ≥4.5:1（估算已过；dev 预览页复核沿用现行流程）。
4. 视觉走查（dev 预览）：warm-paper/kami 纸纹应「感受到而非看到」（脏=调低 rect opacity）；blueprint 网格在 24px 卡距下不摩尔纹。
