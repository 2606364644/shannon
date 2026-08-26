# 白盒报告单源化渲染 + QA review（design）

> 2026-08-26 立项。承接 2026-08-26-report-generation-agent（report_data.json SSOT
> 总纲，T1-T8 已落地但白盒 md 未切导出）与 2026-08-26-vuln-card-seven-sections
> （七节基准已落地）。触发：用户验收 NodeGoat-20260826-101619（产物 18:34，
> 七节落地后重生成）HTML 报告页 4 点反馈 + 「报告需要 review 环节」诉求；
> 核心诊断——**md 链路与 JSON 链路是两套独立渲染逻辑，只共享 queue SSOT，
> 漂移是常态**，本次把总纲「JSON 为主，md 从 JSON 导出」真正落到白盒轨。
> 用户已定两项决策：**全面切 JSON 导出（真单源）**、**自动 QA + 显式呈现**。

## 1. 背景与问题诊断

实证：`workspaces/__legacy__/scans/NodeGoat-20260826-101619/deliverables/whitebox/`
（25 卡，report_data.json 18:34 生成）。

### 1.1 用户反馈 → 根因（代码级）

| # | 反馈 | 根因 |
|---|---|---|
| 1 | burp PoC 看不到 | **PoC 双源**：结构化卡 POC 节 gated 在 `v.poc`（`VulnerabilityCard.tsx:268`），只认 queue `report_poc`；md 报告页的 ` ```http ` 另有独立来源——`exploitable_poc_collection.md`（`poc_generator` 的 `HttpRequestSpec` 渲染），仅由后端 `/report` 端点拼接进 md 响应（`scans.py::report_for` L252-256），report_data 组装从不读它 |
| 2 | 执行摘要/类型汇总/速查表丢失 | 速查表在 report_data schema **无对应模型**（`models/report_data.py` ReportData L183-192）；`stats.by_type.key_findings` builder 从不填（`report_data_builder.py` L204-205）；执行摘要依赖 run_report_polish 成功，ReportView 对 null 静默省略 |
| 3 | 卡片无法折叠 | 结构化卡迁移时未移植 md 路径折叠体系（`MarkdownView.tsx` L462-470 每卡 chevron + L544-561 批量收起/展开）；`VulnerabilityCard` 仅 dataflow 子区折叠（L88/L438-450） |
| 4 | 接口丢参数 | builder 确定性路径 `parse_endpoint_string`（`report_data_builder.py` L50-75）只解析 method/path/role/auth，**从不填 params/行号链**；params 只来自 agent 富化 `report_endpoints`（101619 实测 15/25 卡）；queue 现成的 `affected_parameters`（md 速查表在用）未被映射 |
| + | SINK 问题点贴代码（位置 file:line + 片段 + 说明） | 生成侧已支持（problem_points 19/25 卡三要素齐，含 SINK 位带 snippet）；剩 6 卡 auth 类不走 endpoint_enrichment，无确定性兜底 |
| + | 需要 review 环节 | 现有 QA 仅 3 项字段存在性检查（taint_endpoints_present/title_present/severity_valid，`activities.py` run_report_polish L1942-1964）+ 缺 endpoints 单路回炉；无七节覆盖率/同构性检查 |

### 1.2 结构根因：白盒 md 双链路漂移

总纲 spec（2026-08-26-report-generation-agent §2/§3）已定「JSON 为主，md 从 JSON
确定性导出」「前端与 md 同源，无第二次转换」，落地时**只有 combined 轨用了
导出器**（`report_markdown_exporter.export_report_markdown`，orchestrator.py
L104-124 / scan_manager.py L2958-2974）。白盒 md 仍是独立链路：

```
现状（白盒）：
  queue SSOT ─→ render_findings（findings_renderer 七节卡 → findings.md）
            └→ assemble_report ─→ comprehensive md（速查表 + evidence/findings/analysis
                                  三级回退拼接）+ report_data.json 初版
               → run_agent(report)（report-executive 改写 md：摘要+清理）
               → verify_report_vuln_blocks（### ID 自愈重建）
               → inject_attack_chains / inject_gitnexus_track_status（md 注入）
               → generate_poc_report（poc_collection.md，独立 HttpRequestSpec 源）
               → run_report_polish（report_data.json 终版 + 摘要 + 浅 QA）
```

md 内容 = md 链路渲染 + agent 改写 + 两处注入 + web 端点拼接；web 内容 =
report_data.json。两链路各自演进（本次速查表/params/PoC 三处漂移即实证），
用户感知为「HTML 渲染报告和真实报告对不上」。

## 2. 目标 / 非目标

**目标**
- 白盒 md 全面切 `export_report_markdown(report_data)`：comprehensive md 与
  poc_collection.md 均从 report_data 导出，前端与 md 永远同构（真单源）。
- 数据补齐（builder 确定性）：速查表 schema + params 回填 + auth 卡
  problem_points 兜底 + by_type.key_findings。
- 前端补齐：速查表/类型汇总/执行摘要强化 + 整卡折叠（对齐 md 路径交互）。
- QA review 增强：七节覆盖率逐卡校验 + 同构校验（md 卡数=JSON 卡数）+ 回炉
  扩展 + 报告页逐卡缺口显式呈现。
- 黑盒 md 同批切导出（T7 已结构化，复用导出器）。

**非目标**
- 不动双轨检测/判定主干、merge 语义、`externally_exploitable` 铁律。
- 不做人工 review 编辑工作流（qa 字段与呈现预留 per-card 位，编辑器另批立项）。
- legacy md 路径（`MarkdownView` + 解析族）保留服务无 report_data.json 的旧扫描，
  不再投入开发。
- 不动 DeliverablesTab / dataflow 页。
- 多接口拆卡（另批 spec，seven-sections §8 已确认）。

## 3. 目标架构与 workflow 时序

```
白盒（目标）：
  queue SSOT（富化写回不变：endpoints/problem_points/report_poc/narrative）
    → write_structured_poc（不变，POC 写回）
    → assemble_report（改造：builder 组装 report_data.json 初版，含 §5 数据补齐；
       evidence/findings 中间交付物改从 report_data 单点渲染函数导出；不再产 md）
    → run_report_polish（摘要 + QA 增强 + 回炉 → 终版 report_data.json）
    → export_report_markdown（新 activity，polish 之后）：
        ├→ comprehensive_security_assessment_report.md
        │    （执行摘要 + 类型汇总 + 速查表 + 七节卡×N + 攻击链 + GN 判定状态）
        └→ exploitable_poc_collection.md（从 report_data.poc 导出，PoC 单源）
    → 前端 ReportView 纯渲染（同一 report_data.json）
```

### 3.1 退役/收窄清单

| 现状组件 | 去向 |
|---|---|
| `report_assembler` 的 md 拼装（速查表 + 三级回退） | 导出器接管 |
| `render_findings` activity 独立步骤 | 并入 assemble_report（渲染函数单点化后同源导出 evidence/findings） |
| report-executive agent 的 **md 改写**（run_agent(report)） | 退役——摘要进 report_data（run_report_polish 已承担），润色价值并入 polish prompt |
| `verify_report_vuln_blocks` md 自愈 | 退役——导出确定性后无需自愈；同构校验（§6）归入 export activity 内部 |
| `inject_attack_chains` / `inject_gitnexus_track_status`（md 注入） | 退役——attack_chains/confidence/merge_source 已在 report_data schema，导出器渲染 |
| `generate_poc_report` 的 md 渲染 | 退役（export activity 产 poc_collection.md）；POC agent 生成写回职责在 write_structured_poc，不变 |
| web `/report` 端点 poc_collection 拼接（`report_for` L252-256） | 删——md 内已含 POC 节 |

## 4. 渲染函数单点化

- `findings_renderer` 的七节渲染函数族（`render_vuln_card` /
  `_poc_section_lines` / `_issue_section_lines` / `_entry_section_lines` /
  `_tech_detail_lines`，1c27ec13 成果）**平移改吃 `ReportVulnerability`**
  （其字段为 queue Finding 组装超集，problem_points/endpoints/poc/narrative/
  dataflow_steps 全覆盖），收进导出器（`report_markdown_exporter` 吸收，或
  findings_renderer 转为其内部模块——实现时按文件大小定，单点原则不变）。
- 导出器扩展为三产物：comprehensive md（节序：执行摘要→类型汇总→速查表→
  分类七节卡→攻击链→GN 判定注记）、poc_collection.md、（assemble 内）evidence/
  findings 分项交付物。同一份卡渲染函数。
- md 视觉基准：对齐 101619 现状（用户已认可七节形态）；速查表/类型汇总/
  执行摘要沿用现 comprehensive md 措辞。

## 5. 数据补齐（builder，确定性）

- **速查表**：`models/report_data.py` 新增
  `QuickReferenceRow{id,title,params,endpoints,severity,verification,confidence}`，
  `ReportData.quick_reference: list[QuickReferenceRow]`；builder 从 vulnerabilities
  + `affected_parameters` 确定性产。前端与 md 都只渲染不派生（守「渲染层纯渲染」）。
- **params 回填**：`parse_endpoint_string` 扩展——params ← `affected_parameters`
  （去重、带 path/query 标注）；行号链（route_registered_at/source/sink）←
  affected_entries 兜底。目标：确定性路径产出与富化路径字段同构。
- **auth 卡 problem_points 兜底**：位置 ← queue 定位字段（file:line），snippet ←
  `code_snippet` 服务确定性提取，description 缺则仅渲染位置+片段。
- **by_type.key_findings**：确定性产（每类 top severity 卡标题，≤3 条），
  摘要 agent 可覆写。

## 6. QA review 增强（自动 + 显式）

- **七节覆盖率 checks**（run_report_polish 扩展，逐卡产缺口清单）：
  `problem_points_present`（taint 卡）/ `poc_complete`（curl+raw_http）/
  `params_present` / `narrative_complete`（cause/impact/remediation 三段）/
  `endpoint_rows_have_locations`（行号链）。
- **同构校验**（export activity 内，确定性）：导出后 md `### ID` 卡数 =
  report_data 卡数；速查表行数 = 卡数。失败 → qa.checks 记录，不静默。
- **回炉扩展**：缺 problem_points → 接口富化 agent；缺 POC → POC agent；
  narrative 缺段 → 深富化。写回 queue → 重建 report_data → 复检一次（沿用
  现有 rework 循环模式，单轮）。
- **前端呈现**：qa 横幅升级为逐卡缺口清单（「XSS-GN-01 缺 burp PoC」粒度），
  per-card review status 字段预留（人工编辑另批）。
- 失败语义不变：QA/回炉失败保产物 + `qa.passed=false` 显式呈现。

## 7. 前端补齐（ReportView / VulnerabilityCard）

- **速查表节**：渲染 `quick_reference`，行锚点跳转单卡；**类型汇总节**：
  by_type（count/severity_range/key_findings）；**执行摘要节**强化：
  risk_level / top_risks（锚点）/ remediation_order。
- **整卡折叠**：每卡 chevron + 批量收起/展开按钮（默认展开），目录联动，
  键盘可达（对齐 dataflow 折叠交互基线与原 md 路径习惯）。
- POC 双 tab / 接口 params：数据单源补齐后自然修复，前端逻辑不动。
- i18n zh/en 新 key（速查表/汇总/折叠/qa 缺口文案）。

## 8. 测试与验证

- **core（builder/exporter）**：quick_reference 组装、params 确定性回填、
  auth 卡 problem_points 兜底、key_findings；导出器三产物快照测（对齐 101619
  七节视觉基准）；渲染函数迁移后 findings_renderer 既有 87 测随迁改吃
  ReportVulnerability。
- **whitebox（workflow/activities）**：新时序断言（polish 先于 export；
  render_findings/run_agent(report)/inject_* 已退位）；QA 七节覆盖率 +
  回炉路径；同构校验失败显式化。
- **前端**：ReportView 补节断言（速查表/汇总/摘要/折叠/批量收起）；qa 逐卡
  缺口渲染；VulnerabilityCard 既有 26 测回归。
- **端到端**：NodeGoat 重扫——md 与 web 卡七节同序同内容；速查表行数=卡数；
  全卡 params/problem_points/burp PoC 可见；折叠可用；qa 缺口清单准确。
- 只跑改动相关测试文件（CLAUDE.md §3 预存挂起陷阱）。

## 9. 风险与边界

- **md 视觉漂移风险**（导出器 vs 现 findings_renderer 拼装差异）：渲染函数
  平移不改内容；速查表/摘要措辞沿用现 md；快照测锁定；验收目检对齐。
- **report-executive 润色价值损失**：其「删 disclaimer/压缩」无分析价值保留
  项，polish prompt 吸收必要清理；摘要能力已由 run_report_polish 承担。
- **旧数据兼容**：旧扫描无 report_data.json → legacy md 路径不变；新 schema
  字段均 optional。
- **黑盒切导出**：黑盒 md 措辞与白盒共用导出器节模板，dynamic_evidence 等
  字段渲染位不变（T7 结构已对齐）。
- **activity 注册**：export activity 需 worker.py + runner.py 双注册表同步
  （b51eb9a4 教训）+ step_intents。

## 10. 开放问题

无——人工 review 编辑工作流已裁为另批；多接口拆卡另批（seven-sections §8）。
