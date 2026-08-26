# Mac 主题果味修订（蓝中性阶梯 + Apple 蓝主色 + 天光玻璃 + 白片分段）设计

- 日期：2026-08-27
- 状态：已实现（TDD 红→绿，88 测试绿，tsc 0 错）
- 分支：`feat/fork-py`
- 触发：用户反馈「mac 色调太灰，有无更果味的改动」

## 0. 「太灰」根因诊断

| 根因 | 证据 |
|---|---|
| 中性面饱和度降过头 | 注释称对齐 `#F2F2F7`，真值 HSL 为 `240 24% 96%`，实写 `240 6% 96%`——蓝味丢了 4 倍，全屏中性面读作纯灰 |
| 玻璃透不出东西 | 2026-08-26 删环境光斑后 body 为纯色，vibrancy 磨砂静止时透出的就是灰（macOS 真身磨砂透的是桌面蓝天） |
| 分段导航灰上灰 | 激活段为灰胶囊 `240 5% 88%`；macOS segmented control 真身是**白片浮起在灰槽** |
| 彩色孤撑 | 唯一彩色是 coral CTA，被中性面积压倒 |

## 1. 四刀（全部有 Apple 真值依据）

1. **primary → apple.com CTA 蓝 `#0071E3`**（`211 100% 45%`），prose 链接深档 `#0066CC`
   （`211 100% 40%`，白底 AA），CTA 光晕同步蓝（旧 coral 光晕在蓝按钮上是脏橙边），ring 同步。
   依据：2026-08-25 分层纪律原文「系统对齐层用参考系统本色 primary」——mac 当时是例外，
   本次回归；品牌 coral 仍在 charcoal/warm-paper 基准主题。
2. **中性阶梯蓝饱和回真值**：画布 `240 24% 96%`（#F2F2F7 精确换算）、secondary/muted
   `240 20% 95%`、accent `240 18% 93%`、muted-foreground `240 8% 40%`。
3. **天光渐变画布材质**：`--canvas-material` = 极淡冷蓝（hue 211）linear-gradient 自上而下
   渐隐（6%→2%→0%，前 1/3 屏可感）+ `--canvas-material-attachment: fixed` 钉视口。
   磨砂玻璃静止时透出天光而非纯灰。与 2026-08-26 删除的 Arc 式三团彩色光斑是两种语言：
   单方向、单色温、物理直觉（光从上来）、≤6% alpha。
4. **分段控件激活段白片化**：灰胶囊 → 白片 `hsl(0 0% 100%)` + hairline + 小落影；
   hover 加深落影不回灰（白片是抬起的实体段）。

## 2. 机制增量

- `html body` 通用消费点新增第三 var：`background-attachment: var(--canvas-material-attachment, scroll)`
  ——纸纹（warm-paper/kami）与网格（blueprint）保持默认 scroll（材质随内容 = 纸面/图纸感），
  mac 天光 fixed（天光钉在视口顶部）。
- severity / `--c-*` 语义色不动（本就是 Apple 系统色）；卡片平面化语言不动（2026-08-26 补课成果）。

## 3. 实现面

| 文件 | 改动 |
|---|---|
| `styles/tokens.css` | mac 块层 A（蓝饱和 + primary/ring）、层 D（links）、层 E（cta 蓝光晕）、层 F'（天光 + fixed）；分段控件白片（含 hover）；块头注释补 ⑦ 果味修订（含与 ⑥ 删光斑的辨析）；html body 消费点 +attachment；头注释同步 |
| `lib/theme.ts` | mac preview：bg/primary 同步新真值（色卡预览恒定展示约定） |
| 测试 | `tokens.test.ts`：mac 块断言重写（画布 240 24% 96% / primary 211 100% 45% / 天光 linear-gradient / attachment fixed / cta 蓝光晕 / 分段白片 box-shadow）+ 消费点 attachment 断言 |

## 4. 验收

1. 测试绿：tokens / theme / SettingsPage / ThemeToggle / theme-context（85→88）+ i18n（3）；`tsc --noEmit` 0 错。
2. 视觉走查（dev 预览）：CTA/链接/focus 环变 apple.com 蓝；画布带 iOS 式蓝调灰；顶部玻璃浮层
   （TopBar/Popover）隐约透冷蓝天光；TopBar 激活导航项为白片浮起。

## 5. 追加决策（同日）：默认浅色回切 warm-paper

- **用户决策**：默认主题保品牌 coral——mac 保持果味修订，但退为纯可选主题；
  `defaultThemeFor("light")` mac → **warm-paper**，快捷翻转/`system` 态解析/FOAC
  （index.html prefers 落点）三处同源回切；THEMES 浅组顺序对调（warm-paper 排最前，
  维持「默认排最前」展示约定）。
- 涟漪测试同步：theme.test / ThemeToggle.test / LoginPage.test（登录页翻转落点）。
- 效果：首次访问浅色用户落在 Claude 浅色（coral + 纸纹）；想要果味去设置页选 Mac。
