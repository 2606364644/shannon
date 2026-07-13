# 报告页攻击链数量卡片打磨 — 设计

- date: 2026-07-14
- status: approved → implementing (TDD)
- branch: feat/fork-py
- related: `2026-07-14-report-attack-chain-section-design.md`（攻击链独立成章）

## 1. 背景 / 问题

报告页威胁概览条（`ThreatOverview`）左列，攻击链数量当前是 52px 单点漏洞大数字下方一个**独立的带框小卡**（`ThreatOverview.tsx:29-39`）：

```tsx
<div className="mt-2 flex items-center justify-between rounded-sm border border-border bg-muted/40 px-2 py-1">
  <span className="font-mono text-[10.5px] ...">{t("report.attackChains")}</span>
  <b className="font-mono text-sm font-semibold text-foreground">{stats.attackChainCount}</b>
</div>
```

用户反馈两类问题：

1. **突兀 / 大小不一**：这个带 `border/bg` 的独立小框，与左列其它元素（52px **无边框**大数字 + 纯文字 label + 小字统计）视觉语言冲突 → 怎么打磨框都突兀，且「长度和大小都不一样」。
2. **无提示**：没解释「攻击链」是什么、无 tooltip、点击无反应（用户不知道下方还有 `AttackChainSection` 详情可看）。

## 2. 目标

- 统一左列视觉语言（**消除独立框**，靠字号层级区分大/中/小）。
- 给攻击链加 tooltip 说明 + 可点击跳转到下方攻击链章节。
- 保持 `attackChainCount = 0` 不渲染。

## 3. 方案（已批准：中数字行 + 交互）

左列改为统一「数字 + label」语言，无独立框，字号层级 **大 → 中 → 小**：

```
15              ← text-[52px] 大数字（单点漏洞）
单点漏洞         ← text-[11px] label

🔗  3  攻击链 →  ← text-2xl 中数字（攻击链）· 无框 · 橙 · 可点
                ↑ mt-3 与上方拉开层级

公网 8 · pre 5  ← text-xs 小字统计
```

### 3.1 改动

**`ThreatOverview.tsx`** — 攻击链块（line 29-39）从 `<div>` 改为可点 `<button>`：

- 去掉 `border / bg-muted / rounded-sm / px / py`
- 图标 `Workflow`（lucide）`size-5 text-orange`
- 数字 `text-2xl font-bold text-orange`
- label「攻击链」`text-[11px] text-muted-foreground`，`group-hover:text-orange`
- 尾部 `ChevronRight` `size-3.5 text-muted-foreground opacity-40 group-hover:opacity-100`
- 整行 `group mt-3 flex items-baseline gap-2 cursor-pointer`
- 包 shadcn `Tooltip`（hover/focus 显示 hint）
- `onClick` → `document.getElementById('attack-chain-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })`
- `aria-label` 描述按钮用途

**`AttackChainSection.tsx`** — `<section>` 加 `id="attack-chain-section"`（滚动锚点）。

**`locales/zh.json` / `locales/en.json`** — `report` 段新增 `attackChainHint`（tooltip 文案）。

### 3.2 tooltip 文案

- zh：`攻击链：多个单点漏洞串联成多步利用路径。点击查看完整路径。`
- en：`Attack chain: single-point vulns chained into a multi-step exploit path. Click to view.`

## 4. 无障碍

- `<button>` 原生键盘可达（Enter / Space 触发 onClick）。
- tooltip 在 hover / focus 显示（Radix Tooltip）。
- `aria-label` 让屏幕阅读器读出按钮用途。

## 5. 测试计划（`ThreatOverview.test.tsx`，TDD）

- `attackChainCount > 0`：
  - 渲染 `role="button"`（攻击链可点）
  - 渲染图标（`svg` / lucide）
  - 渲染数字（13）+「攻击链」label
  - tooltip hint 文案存在（`attackChainHint`）
  - 点击触发 `document.getElementById('attack-chain-section').scrollIntoView({ behavior: 'smooth' })`
- `attackChainCount = 0`：不渲染（保持，不含「攻击链」字样）

## 6. 不改动（YAGNI）

- **不**提升为 52px 并列大数字（攻击链数量通常 0~几，会视觉失衡）。
- **不**并入「公网可达 · pre-auth」统计行（会弱化攻击链层级）。
- **不**改 severity 配色体系、不改中/右列、不改 `AttackChainSection` 章节内本身。

## 7. 风险与对策

- **Radix Tooltip 在测试中渲染**：Radix Tooltip 默认有延迟 + 需 pointer/hover 交互才挂 Content 到 DOM。测试断 hint 文案时，用 `fireEvent.mouseEnter`/`focus` 触发显示，或断 `TooltipContent` 文案在触发后出现。
- **jsdom 无 `scrollIntoView`**：测试 `vi.spyOn(document, 'getElementById')` 返回带 `scrollIntoView: vi.fn()` 的 stub，点击后断言被调用。
