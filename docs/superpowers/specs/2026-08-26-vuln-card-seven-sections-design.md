# 漏洞卡七节基准结构（design）——生成与渲染同构

> 2026-08-26 立项，直接延续 2026-08-26-vuln-card-consolidation（卡片信息归并）与
> 2026-08-26-report-generation-agent（report_data.json SSOT）。触发：用户验收
> NodeGoat-20260826-083507 报告页 4 点反馈；核心口径升维——**七节结构是报告
> 生成本身的结构**（md 卡与 report_data 原生如此），web 渲染 1:1 呈现，
> 不是前端把另一种结构掰弯。

## 1. 背景与问题诊断

实证：`workspaces/__legacy__/scans/NodeGoat-20260826-083507/deliverables/whitebox/report_data.json`（14 卡）。

用户反馈 4 点 → 根因：

1. **「成因/危害/修复建议挤在一起」**：结构化卡 `VulnerabilityCard.tsx:117` 用
   `grid md:grid-cols-3` 三等分列，中文长文挤成 1/3 卡宽窄条。
2. **「数据流格式好，其他不美观」**：dataflow 折叠列表受认可（不动）；其余节
   缺乏统一节头语言与呼吸感。
3. **理想单漏洞信息结构（用户口径，本 spec §3 基准）**：漏洞成因（研判依据）、
   漏洞危害、问题点（位置/说明/代码片段）、相关接口、POC（curl+Burp）、
   漏洞细节（数据流/防护措施/CVSS/OWASP 分类）、修复建议。现状差距：
   - `cvss` / `owasp_category` / `poc.raw_http` **数据已在 report_data 里但卡片
     一个没渲染**；
   - 问题点无独立节：`evidence.code_snippet` 真实数据**全空**（白盒 LLM 轨无人
     写）、说明无专用字段——需生成侧补；
   - 接口是 7 列大表（Method/Path/参数/认证/路由/Source/Sink），对「通常 1 个
     接口」（14 卡中 9 卡 ep=1）过重，且用户明示「不要因为表格整乱了」。
4. **「接口通常只有一个，多接口应是不同漏洞」**：生成侧已有立场
   （gn_collapse「不同接口绝不合并」、llm_collapse「多参数不拆卡、多接口才拆
   卡」）；多接口卡 = 存储型 XSS 写+触发成对（同链两接口，合理）与系统性发现
   （XSS-VULN-02 一卡 6 接口）。**拆卡非本 spec 范围**（用户确认下批另立）。

时序诊断：白盒 md 交付物 = `render_findings` 底稿 → report-executive agent 改写
→ 注入。结构化 POC 写回（`_write_structured_pocs`）在 `generate_poc_report` 内，
**晚于 render_findings**——md 卡要原生带 POC 节必须前移写回时序。问题点富化
无时序问题（`run_endpoint_enrichment` 本就在 render_findings 之前）。

GN-only 卡 narrative/cvss 全空 = 该扫描（08:35）跑在报告 Agent 化管线落地
（aae92acc 13:59 + b51eb9a4 activity 注册修复）**之前**的时序产物，非代码缺陷；
deep 档富化（`_ENRICHABLE_FIELDS` 白名单）已具备补全能力，重扫验证即可。

## 2. 目标 / 非目标

**目标**
- 七节基准结构成为单漏洞卡的**生成契约**：md 卡（`render_vuln_card`）与
  report_data / web 结构化卡（`VulnerabilityCard`）同构同序。
- 生成侧补「问题点」素材：富化 agent 产 per 卡 `problem_points`
  （location/description/snippet），写回 queue → report_data → 两路渲染。
- POC 写回时序前移，md 卡原生渲染 curl + Burp（raw_http）双格式。
- 重扫 NodeGoat 验收：GN 卡叙事填满 + problem_points 有真实片段。

**非目标**
- 多接口拆卡（下批另立 spec）。
- web md 渲染路径（`MarkdownView` + `parsePocEntries` 并入）不动——仅服务无
  report_data.json 的旧扫描，保留 legacy 兼容。
- 黑盒/融合轨生成侧不动（黑盒无源码，天然无代码片段；渲染层共用卡片自动
  降级）。融合报告 md（`export_report_markdown`）随 `render_vuln_card` 改动
  自动跟随，不单独处理。
- 不动双轨检测/判定主干、不动 merge 语义。

## 3. 七节基准结构（canonical，两路唯一口径）

| # | 节 | 主数据源 | 空数据兜底链 |
|---|---|---|---|
| 1 | 漏洞成因（研判依据） | `narrative.cause`（queue `notes`） | 空 → 整节省略（GN 深富化补） |
| 2 | 漏洞危害 | `narrative.impact` | 空 → 整节省略 |
| 3 | 问题点 | `problem_points[]`：location + description + snippet（**本 spec 新增**，富化 agent 产） | 无 problem_points：位置 ← `endpoints[].source_location → sink_location`；片段 ← `evidence.code_snippet`；说明无则不渲染 |
| 4 | 相关接口 | `endpoints[]` 紧凑块：`METHOD /path`（mono 加粗）+ 小字行（参数/认证/路由注册）；多接口逐块；`role`（write/trigger）有则徽章 | 无 → 整节省略。**弃 7 列表格**（md 弃参数/Sink/链 ID 表，同信息并入块内小字行） |
| 5 | POC | `poc`：**curl ↔ Burp（raw_http）双格式**，web 双 tab + CopyButton，md 双 fenced block（```bash / ```http）；前置条件/预期响应/witness 保留 | `raw_http` 缺 → 由 `request` 确定性拼（同 `buildCurl` 兜底立场）；无 POC → 整节省略 |
| 6 | 漏洞细节 | 数据流折叠列表（**格式不动**，步内防护 inline 保留）+ CVSS（尾分数提亮 + 向量 mono）+ OWASP 分类 badge | 逐项缺省；数据流空 → 无折叠钮 |
| 7 | 修复建议 | `narrative.remediation` | 空 → 整节省略 |

节序对 md 现状的迁移：危害 → 漏洞危害（更名）；受影响入口 → 相关接口
（换格式）；**POC 从漏洞细节区升独立节（第 5 位）**；**修复建议移到末尾**；
「判定/验证」元信息留在卡头行（md 现状保留）。

## 4. 生成侧改动

### 4.1 problem_points 富化（挂 endpoint_enrichment，不加新 agent/新步）

`run_endpoint_enrichment` 的 agent 本来就逐卡 grep/read 源码钉行号——扩其
产出契约是零边际成本的挂载点：

1. `prompts/endpoint_enrichment.txt`：per 卡输出新增
   `"problem_points": [{"location": "file:line", "description": "一句话：此处为何危险",
   "snippet": "≤15 行 repo 里真实读到的源码"}]`；禁止杜撰（与行号同约束）。
2. `_apply_endpoint_enrichment` 扩回填：location/snippet 均非空才写
   `target.report_problem_points`（防幻觉，同 path 校验立场）；空条目丢弃。
3. `queue_schemas.VulnFinding` 加 `report_problem_points: list[dict] | None`
   （同 `report_endpoints` 回填字段模式）。

### 4.2 POC 写回时序前移

- `_write_structured_pocs` 从 `generate_poc_report` 拆出为独立 activity
  （`write_structured_poc`），workflow 里排在 `render_findings` **之前**；
  `generate_poc_report` 保留 PoC md 文档生成（继续消费写回后的 `report_poc`）。
- 失败语义不变：写回失败 non-fatal，md 卡 POC 节缺省。

### 4.3 render_vuln_card 七节重构（md 卡，§3 基准落地）

- 节序重排为 §3 七节；`sec_impact` 文案「危害」→「漏洞危害」；受影响入口
  label →「相关接口」（i18n key 同步 zh/en）。
- `_entry_section_lines` 弃 markdown 表：每接口一块（`METHOD /path（role）` +
  缩进小字行：参数 ｜ 认证 ｜ 路由注册 ｜ Sink 位置 ｜ 链 ID——原表列信息无损
  并入）。
- POC 独立节 `_poc_section_lines`（新）：读 `report_poc`，渲染 curl fenced
  （bash）+ raw_http fenced（http）+ 前置条件/预期响应；witness 保留在节内。
- `_issue_section_lines` 优先吃 `report_problem_points`（location/description/
  snippet 逐条）；无则回落现有 `_card_loc` + 传入 snippet 提取路径。
- `_tech_detail_lines` 移除 PoC 行（升独立节）；CVSS/OWASP 保留。

### 4.4 schema / builder

- `models/report_data.py`：`ProblemPoint(BaseModel){location, description,
  snippet}` + `ReportVulnerability.problem_points: list[ProblemPoint]`。
- `report_data_builder._report_vulnerability` 纯透传
  `report_problem_points` → `problem_points`（不合成、不推断）。

## 5. web 渲染侧（VulnerabilityCard 七节重排）

§3 基准 1:1 落地（此前已过用户设计的短设计，纳入本 spec 统一口径）：

- 七节纵向排布，弃 3 列 grid；统一节头（mono 小标签 + `border-t` 分隔），
  节距 `space-y-4`；空数据整节省略，GN-only 卡自然降级不出空壳节。
- 问题点节：`problem_points[]` 逐条（位置/说明/代码片段）；兜底链按 §3。
- 相关接口节：紧凑块格式（同 §3 第 4 行），多接口逐块。
- POC 节：curl ↔ Burp 双 tab（`raw_http` 优先、`request` 确定性拼兜底）+
  CopyButton + 前置条件/预期响应/witness；**删冗长 method/headers 逐行列表**
  （双格式块是其超集）。
- 漏洞细节节：数据流折叠原样 + CVSS（尾分数提亮 + 向量 mono，确定性切分）+
  OWASP badge。
- i18n：zh/en 新 key（七节标题 + burp/curl tab + 参数/认证/路由注册行标签）。
- 黑盒卡同构适用：`dynamic_evidence` 仍在证据位（POC/细节节间）突出显示。

## 6. 测试与验证

- **TDD core**：`test_report_data` 补 problem_points 组装（透传 + 空缺省）；
  `test_poc_structured` 不动（写回逻辑未变，仅调用点变）。
- **TDD whitebox**：`test_run_endpoint_enrichment` 扩 problem_points 产出校验
  （空 location/snippet 丢弃、按 ID 回填）；workflow 时序（write_structured_poc
  先于 render_findings）在既有 workflow 测试模式上补断言。
- **TDD renderer**：`render_vuln_card` 七节序 + 相关接口无表格行（`|` 表头
  不出现）+ POC 双 fenced block + problem_points 优先/回落。
- **TDD 前端**：`ReportView.test.tsx` 旧「三段 grid」「七列表」断言改写；
  新增七节结构、tab 切换、紧凑接口块、CVSS/OWASP、problem_points 兜底链断言。
- **真实重扫 NodeGoat**：GN 卡 narrative/cvss/owasp 填满（deep 富化生效）；
  problem_points 有真实源码片段；md 与 web 卡七节同序；报告页人工验收。

## 7. 风险与边界

- **report-executive agent 改写风险**（md 卡内容被压缩/丢失，历史回归有先例）：
  `verify_report_vuln_blocks` 重建底稿版走 `render_vuln_card`，七节结构随重建
  恢复；POC fence 丢失同路径兜底。验收时目检。
- **富化 token 增加**：endpoint_enrichment 每卡多读 sink 附近源码——agent 本就
  读码钉行号，边际增量小，可接受。
- **旧数据兼容**：旧 report_data.json 无 `problem_points`（optional）→ 前端
  兜底链生效；旧扫描 md 走 legacy web md 路径，行为不变。
- **黑盒轨**：不走 endpoint_enrichment（whitebox activity）→ problem_points
  恒空，渲染兜底（黑盒 evidence.code_snippet 亦空 → 问题点节只剩位置行或省略）。
  符合「黑盒无源码」的语义，非缺陷。

## 8. 开放问题

无——拆卡（多接口拆不同漏洞）已确认下批另立 spec，本 spec 不预留半成品钩子。
