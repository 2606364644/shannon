# 警报语义层设计（alarm semantic layer）— 2026-08-27

> 背景：用户观感反馈「漏洞不是全都用红色更好」。诊断结论：五个独立维度（severity /
> ID 身份 / 可达性 / verdict / 编辑强调）同时花「红色」一个通道，红色超支导致 triage
> 信号失真。本 spec 把警报语义收敛为「一个维度一个通道」，并给 severity 加形状通道
> （近单色主题上色相天然弱，形状在全部 13 主题里等强可读）。

## §1 问题：红色都花在哪（现状盘点）

| 维度 | 现状 | 判定 |
|------|------|------|
| severity | 红/橙/黄 pill+dot+左缘 | ✅ 正确用法 |
| ID 身份 | `VulnCard.tsx` ID `font-bold text-red` | ❌ 每条漏洞都红粗体=零信息（且 `Vulnerability` 类型无 severity 字段，红 ID 是无数据支撑的常驻红） |
| 可达性 externally_exploitable | 整卡 `border-red/50` + 红 `●` 徽章（VulnCard / VulnerabilityCard 两处） | ❌ Medium 公网可达看起来比 Critical 还急；与 severity 双重编码冲突 |
| verdict（打通枝） | 红虚线语义正确，但 `.flow` 流动动画常驻所有打通枝 | ⚠️ 语义保留，动效超支 |
| 编辑强调（top risks） | 红左规 | ✅ 保留 |

## §2 设计：红色稀缺预算 + 形状通道

### 2.1 红色合法位置（收敛后，全部由数据决定、互不叠加）

1. severity=Critical 的 pill/dot/左缘；
2. ThreatOverview 堆叠条 Critical 段（图表）；
3. 数据流打通枝红虚线（**静态**，见 §4）；
4. guard-missing（缺失控制语义）；
5. 执行摘要 top risks hero 左规。

撤走的红：VulnCard 红 ID（→ `font-semibold text-foreground`）、可达整卡红边（→ 删除）、
两处可达红徽章（→ 中性 `⌖` 字形徽章，`text-foreground/75`）。可达性是 triage 输入而非
警报本身——它不与 severity 抢红色，改走字形通道。

### 2.2 形状通道：sev-dot 填充比例（本设计的签名）

severity dot 从实心圆升级为「仪表盘填充」，色相走 `currentColor`（由 pill 的
`text-red` 等染色），rank 走填充比例：

```
Low ○（描边空环）  Medium ◑（半填充）  High ◕（3/4）  Critical ●（满）
```

CSS：`.sev-dot{-low|-medium|-high|-critical}`（tokens.css，conic-gradient + inset
box-shadow，零新依赖）。论据：主题库含 openai/kami/notion 近单色主题，red/orange/yellow
色相区分度天然弱——形状通道让 severity 跨 13 主题等强，色相降级为辅助。

### 2.3 线型阶梯：SEV_EDGE 第四通道

Critical 实线 / High 实线 / Medium **虚线**（`[border-left-style:dashed]`）/
Low **点线**（`[border-left-style:dotted]`）。与 2.2 同论据（近单色主题兜底），
滚动扫视时线型节奏本身分级。

### 2.4 单源化：lib/severity-visual.ts

现状 SEV_CAP/SEV_PILL/SEV_DOT/SEV_EDGE 在 4 处重复（VulnerabilityCard / StatsRow /
MarkdownView / QuickReferenceTable，大小写键形两套）。收敛为单一 Capitalized 键模块，
QuickReferenceTable 经 `SEV_CAP[sev]` 归一。视觉零变化处只换 import；形状/线型变化
随单源一次生效。

## §3 动效预算：从环境噪音变阅读辅助

数据流页曾有 3 处常驻无限动画（branch flow / sink pulse / guard gap flow）。
收敛为一页 ≤1 个 ambient 动画：

1. **打通枝流动只在 hovered/selected/直接 hover 时跑**（CSS 触发，动画顺着正在
   看的链流动——特效从装饰变成信息）；`PruningTreeFig.branchClass` 不再输出 `flow`；
2. **sink-pulse 保留**（页面单焦点），1.8s → 2.2s 放缓一档；
3. **guard-gap-flow 降为 hover/focus-within 触发**（静态渐隐虚线常驻，滚动仅交互时）。

`prefers-reduced-motion` 块选择器同步镜像（触发规则特异性高于旧覆盖，须逐选择器对齐）。

## §4 AA 验证器 + severity 文本步（Radix 12-step 方法论的落地形式）

tokens.css 注释称「AA 校验以 dev 预览页为准」——人工口径。改为自动化测试
（`src/styles/__tests__/tokens-contrast.test.ts`）：解析 tokens.css 各主题块
（`:root`/`.light`/`.dark.theme-*`/`.light.theme-*`，剥注释防 `{sev}` 花括号截断），
对每主题 × {red, orange, yellow}：

- severity 文本色 vs 卡底（alpha 卡底先与 `--background` 合成）≥ 4.5:1；
- pill 文本色 vs `bg-{sev}/15` 混合底（severity 色 15% over card）≥ 4.5:1。

**验证器首跑即揪出 21 处真实违规**（charcoal/sentry/notion 卡底即 3.8-4.2；药丸
混合底几乎全线 3.1-4.46）。修法不是逐主题调值，而是 Radix「步阶」机制化——
**同一 hue 的文本步与 tint 底步分离**（实际落地，替代原「hue 锁定内调值」设想）：

- tokens.css：`--sev-text-strength`（深 72% / 浅 76%）+ `--sev-text-toward`
  （深 white / 浅 black），模式基础值定义、扩展主题按模式继承；
- `.sev-text-{red,orange,yellow}` 类：`color-mix(in srgb, hsl(var(--c-{sev})) var(--sev-text-strength), var(--sev-text-toward))`；
- tint 底继续用原色 `bg-{sev}/15`（hue 锁定不动，零主题 token 改动）；
- SEV_PILL / SEVERITY_TEXT（report-stats）文本侧统一换 `sev-text-*`。

验证器同步建模文本步（解析同一对 token 计算生效色）——80 断言全绿。

## §5 不做什么

- 不加新主题（13 个已饱和，公开库是「要加时更快」不是「该多加」）；
- 不动双轨语义色（cyan=GN / magenta=LLM / green=双轨确认——现有正确的通道分置）；
- 不动 ThreatOverview 堆叠条 / top risks hero 红（红色的正确去处）；
- 不给 VulnCard 造 severity 数据（无字段，等数据侧带出再接）。

## §6 测试口径

- `VulnCard.test.tsx`：红边断言反转为「无边框红 + 徽章非 text-red」；新增 ID 非红断言；
- `severity-visual.test.ts`：dot/edge 阶梯类名映射、SEV_CAP 归一；
- `PruningTreeFig.test.tsx`：打通枝 class 含 `branch-vuln` 且**不含** `flow`；
- `tokens-contrast.test.ts`：§4 全主题 × 全 severity 通过；
- 既有 hue 断言（`border-l-red` 等）不回归。
