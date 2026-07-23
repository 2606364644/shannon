# 2026-07-24 · 报告页 PoC 并入卡片 + 布局/交互修复（design）

> 范围：web 前端 report 页（`MarkdownView`）。**纯渲染层**，不动后端报告组装。
> 分支：`feat/fork-py`。TDD。

## 1. 背景与目标

用户在 report 页面提出 4 个体验问题：

1. 详细 PoC 当前是**独立章节**（`# 可利用漏洞 PoC 集合`，渲染在所有漏洞卡片之后），与对应漏洞卡片脱节 → 要**并入对应漏洞卡片**。
2. 点击左侧 TOC 目录跳转时会**聚焦**目标（浏览器原生锚点 focus 副作用：outline / 焦点跳动）→ 要**只跳转、不聚焦**。
3. 长 URL / 无空格代码行**溢出卡片**（代码块 `overflow-x:auto` 横滚）→ 要**代码块也换行**。
4. 漏洞卡片上方那条 **sticky 浮动**的「Findings + 全部收起/展开」条（一直吸顶、位置怪）→ 要**去掉浮动**，把收起按钮**挪进 TOC 侧栏**。

## 2. 现状（已核实）

- **后端**（`web/api/workspaces.py:108-114`）：`GET /{ws}/report` 返回 `{主报告 body}\n\n---\n\n{PoC md}`。
  - 主报告 = `report.md`（assemble 的 evidence/findings + `inject_attack_chains` 追加的攻击链）。
  - PoC md = `exploitable_poc_collection.md`（`poc_generator.render_poc_md` 产）。
- **PoC md 结构**（`poc_generator.py:645-684`）：
  ```
  # 可利用漏洞 PoC 集合（白盒）
  > 目标 host ... 共 N 条 ...
  ## 概览
  | ID | 类型 | 路径 | 认证 | 置信度 |
  ...
  ## 详细 PoC
  ### ✓ INJ-VULN-01 · injection @ GET /login
  **置信度：已确认可复现** ｜ 认证：需登录 ｜ 来源：GitNexus
  **curl:**
  ```bash
  curl -i -X GET '...'
  ```
  **Burp Repeater (raw):**
  ```http
  GET /login HTTP/1.1 ...
  ```
  ---
  ### ● XSS-VULN-02 · xss @ ...
  ```
  - 每条 PoC 的 `###` heading **含 vuln ID**（`source_id` = `vuln.ID`），前缀置信符号 `✓/●/⚠`。
- **前端 MarkdownView**：把整份 markdown 渲染。`splitAttackChainSection` 切攻击链；`splitByVulnBlocks` 切单点漏洞为卡片（卡片 body 默认展开，含块内完整字段）。PoC 章节目前**未被特殊处理**，作为 prose 段独立成章渲染在末尾。
- **TOC**：原生 `<a href="#id">` + CSS `scroll-behavior: smooth`；id 从渲染后 DOM 真实读取（`makeSegmentSlugPlugin` 段级 slug）。
- **findings-bar**：`MarkdownView.tsx:604-618`，`sticky top-20 z-20`，含 `vuln-expand-all`（控制 `collapsedIds` = 漏洞卡片折叠）。
- **代码块**：`report.css` `.prose pre { overflow-x: auto }`；body 用 Tailwind `break-words`。

## 3. 设计

### 3.1 PoC 并入对应漏洞卡片（需求 1）

**新增** `lib/report-sections.ts`：

- `splitPocSection(md)`：识别 `# 可利用漏洞 PoC 集合` 一级标题（中英文容错，关键词「PoC 集合」/「PoC Collection」），切 `{ before, pocMd }`。命中前截断，`before` = 主报告 + 攻击链；`pocMd` = PoC 整章。无 PoC → 返回 `null`（老报告兼容）。与现有 `splitAttackChainSection` 同模式。
- `parsePocEntries(pocMd)`：取 `## 详细 PoC` 之后内容，按 `### ` 切条目；每条用正则 `/([A-Z]+-(?:VULN|GN)-\d+)/` 从首行 heading 提 vuln ID；条目体 = 去首行 heading 后的 md（meta 行 + curl 块 + Burp 块）。返回 `PocEntry[] = { id, md }[]`（保序）。

**MarkdownView 改动**：

1. 切分顺序：先 `splitPocSection`（PoC 在最末）→ 再对 `before` 跑 `splitAttackChainSection` → `splitByVulnBlocks` 切 `before+after`。
2. 构建 `pocById: Map<string, string>`（id → 条目体 md）。
3. 渲染漏洞卡片 body 末尾：若 `pocById.has(block.id)`，追加「复现 PoC」区块（分隔线 + 小标题 + 该条目体 md，复用 `ReactMarkdown` + `proseComponents`，自动得高亮 + 复制按钮）。i18n key `markdown.pocSection`（中「复现 PoC」/ 英「Replay PoC」）。
4. **兜底**：`pocById` 中 id 不属于任何已渲染 vuln block 的条目，在卡片网格末尾用独立小卡（`data-testid="poc-orphan"`）列出，不丢信息。
5. PoC 独立章节（含 `# 标题` / `## 概览` 表 / `## 详细 PoC`）从主渲染流**完全移除**，不再单独成章。

**不变量**：vuln block 的 ID 与 PoC `source_id` 同源（均来自 `vuln.ID`），匹配可靠；GN 前缀（GitNexus 轨）同样被 `splitByVulnBlocks` 与 PoC 正则认。

### 3.2 TOC 点击只跳转、不聚焦（需求 2）

TOC 条目 `<a>`、hero 顶部风险锚链接 `<a>`：加
```tsx
onClick={(e) => {
  e.preventDefault();
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}}
```
保留 `href={"#"+id}` 供无障碍 / 键盘 / 右键复制。不调 `.focus()`，消除浏览器原生锚点的 focus 副作用（outline / 焦点跳动）。ThreatOverview 攻击链按钮已是 scrollIntoView 不 focus，不动。

### 3.3 代码块换行（需求 3）

`report.css`：
```css
.prose pre,
.prose pre code {
  white-space: pre-wrap;
  word-break: break-all;
  overflow-wrap: anywhere;
}
```
去掉 `.prose pre` 的 `overflow-x: auto`（长行断行而非横滚）。**表格** `display:block; overflow-x:auto` 保留不动（用户未要求）。复制按钮取整段原文逻辑不变（`flatten` 读 codeChild 文本，与白空格无关）。

### 3.4 浮动条改侧栏（需求 4）

- **删除** `findings-bar` 整块（`MarkdownView.tsx:604-618`）。
- 把漏洞卡片「全部收起/展开」按钮（`vuln-expand-all`，控制 `collapsedIds`）挪进 TOC `<nav>` header 区，与目录章节折叠按钮 `toc-toggle-all`（控制 `collapsedSections`）并列、语义区分（label 区分「卡片」vs「目录」）。
- 边界：报告无 TOC（`twoCol === false`，极简报告）时，「全部收起/展开」降级放在 vuln-grid 上方**非 sticky** 普通行（不浮动）。
- 打印兜底（`report.css` `@media print`）同步：去掉 `[data-testid="findings-bar"]` 选择器（已删），新增的降级行本就非 sticky 无需处理。

## 4. 不变量 / 约束

- **纯前端**：不动后端 `report` endpoint、不动 `poc_generator` / `report_assembler`。
- **不丢信息**：每条 PoC 必须可见——或并入对应卡片，或末尾兜底。
- **双轨语义不动**：`VULN_HEADING_RE` / `VULN_ID_RE` 不动；`llm-chain-N`（攻击链）仍不进 vuln 切分（[[shannon-web-attack-chain-section-implemented]] 锁定）。
- **现有锚点命中不变**：TOC id 仍从 DOM 真实读取；3.2 只改跳转交互不改 id 生成。
- **sticky 栈常量 80px**（`report.css` 头注释）：本次不新增/改 sticky 元素（findings-bar 删除是减少），常量不动。

## 5. 测试策略（TDD）

- `lib/report-sections.test.ts`（或扩展既有）：`splitPocSection` 命中/无 PoC/中英文；`parsePocEntries` 多条、GN 前缀、无 `## 详细 PoC` 兜底。
- `MarkdownView.test.tsx`：
  - PoC 并入对应卡片（卡片 body 含 curl，独立章节不渲染）。
  - 兜底：PoC id 无对应卡片 → 末尾 `poc-orphan` 出现。
  - 概览表/`# 可利用漏洞 PoC 集合` 不再出现在 DOM。
  - TOC 点击不 focus：模拟点击 → `scrollIntoView` 被调、无 focus 副作用（jsdom 下断言 preventDefault + scrollIntoView mock）。
  - 现有 TOC href 命中 / 攻击链 / severity 回归不破。
- CSS：人工 / build 通过即可。**同步更新 `styles/sticky-zindex.test.ts` L25-27**：删除「MarkdownView findings 工具栏含 z-20」断言（findings-bar 已移除，z-20 层不再存在）。z-index 栈降为 `弹窗 z-50 > TopBar z-40 > Tabs z-30`，弹窗最上不变量仍守。

## 6. 范围外

- 后端 PoC 生成质量 / `generate_poc_report` 是否跑（[[poc-curl-burp-not-in-web-ui]]）。
- 攻击链章节渲染（独立成章，本次不动）。
- 表格换行（保留横滚）。
- 全站其他 tab（仅 report 页）。
