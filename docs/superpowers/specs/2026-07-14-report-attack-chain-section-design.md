# 报告页攻击链独立章节渲染 — 设计

> 日期：2026-07-14 ｜ 分支：feat/fork-py ｜ 范围：shannon-web 前端报告页

## 1. 背景与根因

### 现象
NodeGoat 扫描（`NodeGoat_20260713-231325`）报告页头部显示漏洞数 **6**，但报告正文「按漏洞类型汇总」写的是 Injection 4 + Xss 3 + Auth 6 + Authz 1 + Ssrf 1 = **15**，对不上。

### 根因证据链（前端层）
1. **后端报告 API 只返回 markdown 纯文本**：`packages/web/src/shannon_web/api/workspaces.py:97-107` 挑出 `comprehensive_*.md` 原文返回（`PlainTextResponse`），不读 session.json、不读 exploitation_queue。
2. **前端头部总数 = `splitByVulnBlocks(markdown)` 切出的漏洞块数**：`report-stats.ts:151` `total: blocks.length` → `ThreatOverview.tsx:24` 渲染。
3. **切分依据 `VULN_HEADING_RE` 只认纯大写前缀的单点 vuln ID**：`vuln-block.ts:83`
   ```
   /^### ([A-Z]+)(?:-[A-Z]+)+-(\d+)\b/
   ```
   匹配 `### AUTH-GN-EXPLORE-01`，**不匹配** `### llm-chain-1`（`llm` 小写）。
4. **这份报告里标题有两套 ID 格式**：
   - `### AUTH-GN-EXPLORE-01~06`（auth 单点条目，6 个）→ 匹配 → 进总数 = **6**
   - `### llm-chain-1~13`（攻击链，承载 inj/xss/authz/ssrf 利用证据）→ 不匹配 → 当 prose 渲染，不进总数、不进单漏洞卡片

### 更深根因（本 spec 范围**外**，另开）
- **`llm-chain-N` 是 `attack-chain` agent 产的攻击链**（`models/agents.py:154-162`，`ATTACK_CHAIN`，产 `attack_chains_llm_queue.json`），架构语义上是**攻击链，不是单点漏洞**。
- 本次 inj/xss/ssrf 没有单点漏洞条目，是 `SHANNON_LLM_TRACK_ENABLED=0` **收窄语义**（CLAUDE.md §1，2026-07-14 plan smooth-wandering-dolphin）的设计结果——该开关只关 inj/xss/ssrf 的 vuln agent（taint 类），改由 GitNexus `chain_verdict` 主干兜底；但 GitNexus 轨 queue 全空（`attack_chains_gitnexus_queue.json` = `{"chains": []}`，见 task #8），**兜底失败**，于是 inj/xss/ssrf 的发现全部漏给 attack-chain agent 兜成攻击链。**inj/xss/ssrf vuln agent 没跑不是 bug，是关轨行为**；真正的缺口在 GitNexus 兜底不可靠。
- 报告生成层自己承认：报告 md 第 15 行「范围说明」原文 *"本次 injection/xss/authz/ssrf 四类未产出独立的 per-class VULN 条目，相关利用证据集中在'攻击链'章节"*。

**结论**：前端「6 vs 15」是症状，直接根因是前端把攻击链（llm-chain-N）和单点漏洞（vuln block）混在同一个解析口径里，且攻击链没被独立呈现/计数。本 spec 只修前端呈现层。

## 2. 架构语义（锁定，不可违反）

| 产物 | 来源 agent | 类型 | 报告页渲染归属 |
|---|---|---|---|
| 单点漏洞 | vuln agent（vuln-injection/xss/ssrf/authz/auth）+ GitNexus 轨（AUTH-GN-EXPLORE 等）| 单点漏洞 | 单漏洞卡片网格 |
| 攻击链 | **仅** `attack-chain` agent（`llm-chain-N`）| 攻击链 | **独立攻击链 section** |

**铁律**：
- `VULN_HEADING_RE` **不动**——只认单点 vuln ID 是对的；攻击链 ID（`llm-chain-N`）永远不该被当成单点漏洞。
- 攻击链与单点漏洞在报告页**必须分章节呈现，不混**。

## 3. 设计决策与演进

### 演进记录
最初设想「改 `VULN_HEADING_RE` 让它认 `llm-chain-N`，把攻击链当单点漏洞塞进卡片网格」。**此方案被否决**——违背 §2 架构语义（攻击链 ≠ 单点漏洞，不该混进单漏洞卡片）。最终采用：**正则不动 + 攻击链独立成章 + 计数分开**。

### 三个关键决策
1. **`VULN_HEADING_RE` 保持不变**（只认单点 vuln ID）。
2. **攻击链独立 section**（方案 A）：识别 `## 攻击链` 二级标题章节，整段拎成独立 `<AttackChainSection>`，内部 prose 照常渲染，外加章节标题 + 条目计数 + 视觉分隔。不把 `llm-chain-N` 切成结构化卡片（攻击链 steps 是叙述性长文本，卡片化丢细节且无必要）。
3. **计数分开**（计数方案 1）：头部 `ThreatOverview` 并列显示「单点漏洞 N · 攻击链 M」。

## 4. 前端改动

### 改动 ① 章节边界识别
**新增**轻量函数（建议新建 `packages/web/frontend/src/lib/report-sections.ts`，保持 `vuln-block.ts` 聚焦 vuln 切分）：

```ts
export interface AttackChainSplit {
  before: string;      // 攻击链章节之前的 md
  sectionMd: string;   // 攻击链章节标题行**之后**的内容（不含 ## 标题行本身——标题由组件渲染，避免重复）
  after: string;       // 攻击链章节之后的 md（通常为空，攻击链章节一般在文末）
  count: number;       // 章节内条目数
}

/** 把报告 md 切成 [before, 攻击链章节, after] 三段。
 *  无攻击链章节时返回 null（整段 md 视作单点漏洞 md，attackChainCount=0，老报告兼容）。 */
export function splitAttackChainSection(md: string): AttackChainSplit | null;
```

- **边界识别**：扫描 `^## ` 二级标题，命中 = 标题文本（转小写、去标点/括号后）包含 `攻击链` 或 `attack chain`；命中标题行起到下一个 `^## ` 或文档结尾 = 攻击链章节。**脆弱点（显式记录）**：此识别依赖报告生成层（report agent）的章节标题措辞；若生成层改措辞，需同步本规则（或改用「某二级章节下含 `### llm-chain-` 块」作 fallback 信号）。
- **计数**：章节内 `^### llm-chain-\d+` 标题数量（当前攻击链条目统一用此 ID；若未来 ID 格式变化，改为数章节内 `^### ` 三级标题）。
- **职责边界**：本函数只做「分割 + 计数」，**不**把 `llm-chain-N` 解析为 vuln block、**不**经 `parseVulnBlock`、**不**进 vuln segment——`llm-chain-N` 永不作为单点漏洞。

### 改动 ② 新组件 `AttackChainSection.tsx`
**新增** `packages/web/frontend/src/components/report/AttackChainSection.tsx`：
- props：`{ md: string; count: number }`
- 渲染：章节容器（边框/底色与单漏洞卡片网格视觉区分）+ 标题「攻击链（多步利用路径）」+ 计数徽章（`count` 条）+ 内部 `react-markdown` 渲染 `md`（llm-chain 标题 + 利用 steps 叙述完整保留）。
- `data-testid="attack-chain-section"`、`data-testid="attack-chain-count"`。

### 改动 ③ 头部双计数
- `report-stats.ts`：`ReportStats` 接口（:58-65）新增 `attackChainCount: number` 字段。**`computeStats` 不变**（专注单点漏洞统计，不接收、不计算 attackChainCount，保持职责清晰）；`attackChainCount` 由 `MarkdownView` 在 `computeStats(...)` 返回后追加实际值。`total` 语义不变（仍 = 单点漏洞 blocks 数）；**攻击链不进 `severityDist`/`publicCount`/`preAuthCount`/`typeAggs`**——这些是单点漏洞的统计，攻击链不混入。
- `MarkdownView.tsx`：`const split = splitAttackChainSection(markdown)`；单点漏洞 md = `split ? split.before + split.after : markdown`；对该 md 跑 `splitByVulnBlocks`；`const stats = { ...computeStats(blocks, ...), attackChainCount: split?.count ?? 0 }`；渲染时单漏洞卡片网格照旧，`split` 非空时在单漏洞网格之后渲染 `<AttackChainSection md={split.sectionMd} count={split.count} />`。
- `ThreatOverview.tsx`：左栏（:22-33）显示「单点漏洞 `stats.total`」；**当 `stats.attackChainCount > 0` 时**追加并列「· 攻击链 `stats.attackChainCount`」，`= 0` 时隐藏攻击链部分（老报告只显示单点漏洞数，无「攻击链 0」噪音）。severity 堆叠条/图例/public/pre-auth 不变（仍基于单点漏洞）。
- **攻击链 severity**：不进头部 severity 堆叠条；攻击链自身的 severity（critical/high/…，写在 `sectionMd` 的 `- 严重程度:` prose 里）随 `react-markdown` 在 `<AttackChainSection>` 内自然显示，不单独结构化、不进任何汇总。
- **ThreatOverview 左栏布局**（最终视觉决策）：单点漏洞 = `text-[52px]` 大数字（`text-foreground`，视觉主导）+ `report.singleVulns` label；攻击链 = **中性容器**（`rounded-sm border border-border bg-muted/40`），`report.attackChains` label（font-mono uppercase muted）居左 + 数字 `text-sm font-semibold text-foreground` 居右（`justify-between`），`data-testid="threat-attack-chain"`；M=0 整个容器不渲染。**用中性色（非 primary、非 severity 暖色）**——与正文 `<AttackChainSection>` 的 `border`+`bg-muted` 中性徽章统一视觉语言，攻击链整体读作「另一类、低调」，与 severity 暖色填充的单点漏洞形成概念边界；头部容器比正文徽章稍强（有 `bg` 填充 + `justify-between`），适合概览位。

## 5. 数据流

```
report md（comprehensive_*.md）
  │
  └─ splitAttackChainSection(md) ──→ { before, sectionMd, after, count } | null
        │
        ├─ sectionMd + count ──→ <AttackChainSection>（独立渲染，单漏洞网格之后）
        │
        ├─ before + after（单点漏洞 md；null 时 = 整段 md）
        │     └─ splitByVulnBlocks ──→ vuln segments（N 张单漏洞卡片）
        │
        └─ count ──→ stats.attackChainCount（MarkdownView 追加）──→ <ThreatOverview>（单点漏洞 N [+ · 攻击链 M]）
```

> 注：实现采用「先 `splitAttackChainSection` 抽走攻击链章节，再对 `before + after` 跑 `splitByVulnBlocks`」。这样攻击链章节内的内容（kv-list / 可能的表格）不会进入 vuln 切分，避免 `extractTableVulns` 误伤，也保证 `stats.total` 只数单点漏洞。

## 6. 不改的部分
- `VULN_HEADING_RE`、`VULN_ID_RE`、`parseVulnBlock`、`splitByVulnBlocks` 对 vuln 的切分逻辑。
- 单漏洞卡片网格、`MarkdownVulnCard`、`inferSeverity`、`TypeSummaryCards`。
- severity 堆叠条/图例/public/pre-auth 统计（仍是单点漏洞口径）。
- 后端报告 API（仍返回 md 纯文本）。

## 7. 测试策略
TDD，`packages/web/frontend` 下新增/扩展测试（须 `cd packages/web/frontend` 跑 vitest，见 memory `frontend-test-must-cd-frontend`）：
- `report-sections.test.ts`：`splitAttackChainSection`
  - `## 攻击链（多步利用路径）` 命中 → `before`/`sectionMd`/`after` 边界正确 + `count`
  - 标题措辞变体容错（英文 `## Attack Chains` / 无括号 `## 攻击链`）→ 仍命中
  - 攻击链章节在文末（`after` 为空）与在文中（`after` 非空）两种位置都正确分割
  - 无攻击链章节 → 返回 `null`（老报告兼容）
  - `count` 正确数 `### llm-chain-N`
- `AttackChainSection.test.tsx`：渲染章节标题 + 计数徽章 + 内部 markdown；`data-testid` 齐全。
- `report-stats.test.ts`：`ReportStats` 含 `attackChainCount` 字段；`computeStats` 签名/行为不变（不收 attackChainCount）；攻击链不污染 `severityDist`/`typeAggs`。
- `ThreatOverview.test.tsx`：`attackChainCount > 0` 时并列显示「单点漏洞 N · 攻击链 M」；`= 0` 时隐藏攻击链部分（无「攻击链 0」）。
- `MarkdownView.test.tsx`：用 NodeGoat 这份报告 md 做 fixture → 单点漏洞 6 · 攻击链 13；攻击链 section 出现在单漏洞网格之后。
- **回归**：无攻击链章节的老报告 fixture → 不渲染攻击链 section、`attackChainCount=0`、头部只显示单点漏洞数，行为不变。

## 8. 范围外（另开任务）
- **task #8（高优先级）**：GitNexus 轨 queue 全空修复（NodeGoat inj/xss/ssrf 无确定性候选）。这是 `SHANNON_LLM_TRACK_ENABLED=0` 收窄后 inj/xss/ssrf 兜底失败的直接原因——关轨后 GitNexus `chain_verdict` 兜底**必须可靠**，是双轨可配置战略（CLAUDE.md §1）的前提。inj/xss/ssrf vuln agent 没跑本身**不是 bug**，无需排查。
- **authz 单点条目缺失**：本次 `authz_gitnexus_queue.json` 空且无 authz 单点条目（authz 的 LLM 按 CLAUDE.md 应保留，本次未产出结构化条目，证据仅 chain-5）——单独排查，不在本 spec。
- 报告生成层（`report_assembler.py`）重构让攻击链与单点漏洞在 md 结构上更清晰（当前已分章节，本 spec 不动生成层）。
