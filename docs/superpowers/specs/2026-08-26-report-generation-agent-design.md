# 报告生成 Agent 化 + 三轨统一结构化 SSOT（design）

> 2026-08-26 立项。承接 2026-08-26-vuln-card-consolidation（卡片信息归并，已上线）之后的报告层重构。
> 触发：用户验收 `NodeGoat-20260826-041323` 白盒报告后的四点反馈：①渲染层做了太多合并逻辑、希望生成层做更多/渲染层仅渲染；②受影响入口只给文件地址、无接口无行号；③XSS-VULN-01 无接口列表而 XSS-GN-01 才有（两轨呈现不一致）；④希望用 Agent 多步生成更好报告（漏洞信息/接口列表/POC 质量）。
> 用户已定四项决策：**JSON 为主（md 从 JSON 导出）**、**完整 agent 群（可派发 subagent 并行）**、**POC 静态增强（完整可复现请求）**、**三轨全改（白盒/黑盒/融合）**、**增量插入现有步骤（不新增独立 phase）**。

## 1. 背景与问题诊断

### 1.1 现状链路与职责错位

白盒报告链路（`packages/whitebox`）：

```
LLM 轨 vuln agent ──→ {vc}_llm_queue.json ─┐
                                           ├→ merge(verdict OR + track_parity 配对 + 双轨折叠) → {vc}_exploitation_queue.json（SSOT）
GN 轨 sink 链 → chain_verdict 轻量 LLM ───┘          ↓
                                          run_gn_finding_enrichment（deep 档多轮富化 GN-only）
                                                   ↓
             render_findings（findings.md 卡片）→ assemble_report（comprehensive_security_assessment_report.md）
                                                   ↓
             report agent（report-executive，仅润色）→ verify_report_vuln_blocks（自愈）→ poc_generator
```

黑盒：exploit agent 读白盒 queue **做真实动态利用** → exploit verdicts（实测证据）→ 确定性拼报告。
融合：**在 web 服务内确定性生成**（`scan_manager.py:2938 _generate_combined_report` → `combined_report_renderer.py:370`），按 `### ID:` 正则向黑盒报告注入白盒两行，机械无 LLM。

**核心职责错位：报告 API 只回 markdown 纯文本**（`scans.py:418`，`response_class=PlainTextResponse`），结构化 SSOT（`*_exploitation_queue.json`，含 endpoints/dataflow_steps/行号）存在但报告页不用（仅 DeliverablesTab 用）。信息走两次有损转换：

```
结构化 JSON ──确定性渲染──→ markdown（丢结构）──前端再解析──→ ParsedVulnBlock（猜回来）
```

前端为此维护整套"非渲染"逻辑（本次全数拆除，见 §7.2）：severity 关键词推断（`vuln-block.ts:22-116`）、速查表行反向合成卡（`:247-292`）、PoC 章节切出按 ID 归并回卡（`MarkdownView.tsx:460-511`）、卡内 PoC 行去重（`:824-835`）、GN 无 title 拼 file:line 副标题（`:402-417`）、执行摘要 ID 斜杠展开（`:81-95`）、零计数类型补卡（`report-stats.ts:146-164`）。

### 1.2 本次验收实证（NodeGoat-20260826-041323，workspaces/__legacy__/scans/）

1. **接口与入口表两张皮**：`findings_renderer._entry_section_lines`（`findings_renderer.py:322-362`）接口是卡内 kv 行 `- **接口:** POST /memos`，入口表是 `参数|Sink 位置|链 ID` 三列——无接口列、无路由注册行号；GN 卡 `endpoints=null` → 接口行缺失，只剩裸 `contributions.js:21`。而 sink→HTTP 路由映射数据管线已有（`intermediate/entry_points.json`、`route_chains.json`、merge 时 endpoint 回填 `activities.py:1845`），未进卡片。
2. **两轨同类信息不同字段不同呈现**：GN 入口表由 `gn_collapse` 折叠合成（11 行 参数×sink×链ID）；LLM 卡 XSS-VULN-01 入口表仅 1 行弱信息（`memo | memos.js:11 | 空链ID`），其接口信息只落在表外 kv 行——同一报告同类信息两种呈现。
3. **chain_verdict 15 条全失败静默放行**（`xss_chain_verdicts.json` 每条 `llm chain-verdict pass failed`），confidence=low 原始链全量进队。
4. **run_gn_finding_enrichment 未执行**：activity 在 worker 未注册（3 次 `NotFoundError`——代码 12:10 提交、12:13 开扫，worker 进程未重启），静默降级。
5. **跨轨同洞未归并**：XSS-VULN-01 与 XSS-GN-13（memo→memos.js render）/GN-14（symbol→research.js render）同洞，LLM 卡 sink 记 `memos.html:31`、GN 链 sink 在 `memos.js:27` → 严格 key 失配 → 报告重复卡。
6. **report agent 只做润色**：本次 60s 花在删 5 个 disclaimer + 结构校验（workflow.log Turn 39-65），无分析性工作。
7. **白盒 POC 为静态模板**：`poc_generator` 确定性 curl 模板 + LLM gap-fill；witness_payload 是 payload 字符串，无完整请求/预期响应。

## 2. 目标 / 非目标

**目标**
- 三轨统一 `report_data.json` 报告 SSOT；md 从 JSON 确定性导出（下载/存档，一份逻辑）
- 渲染层纯渲染：web API 回 JSON，前端删全部解析/推断/归并/补全逻辑
- 报告生成 agent 群（五步，增量插入现有步骤，可派发并行 subagent）：归并终审 / 卡片富化 / POC 增强 / 执行摘要 / QA 审核
- 接口一体表：method/path/参数/认证/路由注册行号/source 位置/sink 位置
- POC 静态增强：完整可复现 HTTP 请求 + 前置条件 + 预期响应特征 + witness payload
- 融合报告从 web 服务迁入管线（agent 生成，交叉验证三态）
- 黑盒产物结构化（同 schema，`verification=dynamic` 带实测证据）

**非目标**
- 不动双轨检测/判定主干（builder 候选链、chain_verdict verdict 语义、LLM 轨 vuln agent prompt 方法论——铁律：确定性产物仍不喂 LLM 轨 prompt）
- 不做报告独立重跑 workflow（增量插入模式，报告随扫描生成；历史 scan 走 md 回退）
- 不引入白盒动态验证（发真实请求）——POC 静态增强口径
- 不动 DeliverablesTab / dataflow 页（已吃 JSON）
- 不改速查表/卡片 md 视觉结构（md 由 JSON 导出后视觉对齐现状基准）

## 3. 总体架构与数据流

```
白盒：
  merge ──→ ①归并终审 agent（track_parity LLM 配对位升级）
        ──→ ②卡片富化 agent 群（run_gn_finding_enrichment 扩展：GN-only 深富化 + 全卡接口表富化，per-class 并行 subagent）
        ──→ ③POC 增强 agent 群（generate_poc_report 内，per-vuln 并行 subagent）
        ──→ assemble 演进：确定性组装 report_data.json + JSON→md 导出
        ──→ ④执行摘要 agent（report-executive 升级，吃 report_data）
        ──→ ⑤QA agent + 确定性 schema 校验（verify_report_vuln_blocks 位演进）
黑盒：  verdicts（已富）→ 确定性组装 report_data.json → ④摘要（复用）
融合：  combined orchestrator 黑盒完成后新增融合 agent → combined/run-K/report_data.json
        （web 删 _generate_combined_report，改读管线产物）
```

信息流单次单向：`queue(SSOT) → agent 富化写回 → 确定性组装 report_data.json → 摘要/QA agent → {前端纯渲染, md 导出}`。**md 导出在 ④⑤ 之后**（摘要与 QA 结果属于 report_data，一并导出）；前端与 md 同源，无第二次转换。

agent 富化产物**写回 `{vc}_exploitation_queue.json` 扩展字段**（endpoints 结构化/poc 结构化/merged_from），report_data.json 组装与 md 导出均为确定性步骤——LLM 失败时确定性字段照常组装，报告永远完整（每步降级见 §5.6）。

## 4. report_data.json schema

落点：`deliverables/{whitebox|blackbox}/report_data.json`；融合 `deliverables/combined/run-K/report_data.json`。`schema_version: 1` 起步。

```jsonc
{
  "schema_version": 1,
  "scan": {"id", "track", "repo", "date", "duration_ms", "cost", "currency", "model"},
  "executive_summary": {                    // ④ agent 产物
    "narrative": "md 文本（攻击面叙事）",
    "risk_level": "极高",
    "top_risks": [{"vuln_id", "reason", "priority": "P0|P1"}],
    "remediation_order": "修复优先级叙事"
  },
  "stats": {                                // 确定性聚合；替代前端 report-stats 推断/零计数补全
    "by_type": {"xss": {"count", "severity_range", "key_findings"}},   // key_findings 可 LLM 补
    "by_severity": {"critical": n, "high": n, ...}
  },
  "vulnerabilities": [{
    "id": "XSS-VULN-01",
    "merged_from": ["XSS-GN-13"],           // ①归并终审产物；跨轨同洞合并，被并卡不再独立出现
    "merge_source": "both|llm-only|gitnexus-only",
    "type": "xss", "vulnerability_type": "Stored",
    "title": "...", "severity": "high",     // severity 由数据带出，前端不再推断
    "confidence": "high|needs_review",      // both=high；单轨=needs_review；chain_verdict 失败="unadjudicated"（§5.7）
    "cvss": "...", "cwe_id": "CWE-79", "owasp_category": "...",
    "externally_exploitable": true,          // 铁律：可达性标签，agent 不可覆写
    "authentication_required": true,
    "narrative": {"cause": "...", "impact": "...", "remediation": "..."},   // md 文本
    "endpoints": [{                          // ★接口一体表（②富化目标形态）
      "method": "POST", "path": "/memos", "role": "write|trigger|read",
      "auth": "isLoggedIn|public|isAdmin",
      "params": ["memo"],
      "route_registered_at": "app/routes/index.js:66",   // 路由注册行号（素材包派生）
      "source_location": "app/routes/memos.js:13",
      "sink_location": "app/views/memos.html:31"
    }],
    "affected_entries": [{"parameter", "sink_location", "chain_id", "track"}],   // 保留（gn_collapse/llm_collapse 产物）
    "dataflow_steps": [{"label", "file", "line", "protection"}],
    "poc": {                                 // ③ agent 产物；黑盒为实测转结构化
      "request": {"method", "url", "headers", "body"},
      "preconditions": "需登录（connect.sid）",
      "expected_response": {"indicator": "响应含未转义 payload/onerror 触发", "success_criteria": "..."},
      "witness_payload": "<img src=x onerror=...>",
      "curl": "...", "raw_http": "..."       // 由 request 确定性生成（导出/复制用）
    },
    "evidence": {
      "verification": "static|dynamic",
      "dynamic_evidence": "黑盒实测输出（uid=1000 回显等）；白盒为 null",
      "verdict": "vulnerable",
      "code_snippet": "...",                 // 确定性提取
      "notes": "..."
    },
    "attack_chain_refs": ["chain-1"]
  }],
  "attack_chains": [{"id", "steps", "narrative"}],
  "qa": {"passed": true, "checks": [{"check", "failed_ids": []}], "reworked_ids": []}   // ⑤产物
}
```

融合版新增（每漏洞卡）：`cross_verification: "verified|untested|failed-to-verify"`（白盒发现 ↔ 黑盒实测三态）+ 顶层 `verification_gaps: [{vuln_id, "reason"}]`（白盒发现黑盒未覆盖清单）+ 融合叙事字段。

## 5. 白盒 agent 群五步

每步独立降级（non-fatal 包裹），LLM 失败回退确定性产物；沿用 `_ENRICHABLE_FIELDS` 白名单 + 保护字段模式（`externally_exploitable`/`verdict`/`flow_id`/`merge_source` 等绝不覆写）扩展到新步骤。

### 5.1 ①归并终审（插入 merge activity 内，track_parity LLM 配对位）

- 现状：`enhance_track_parity`（`services/track_parity.py:133`）内 build_pairing_prompt 每类一次双列摘要配对。
- 升级：同位置换深度 agent（1 次/类，或 taint 三类合 1 次），输入全部双轨卡摘要（id/type/title/sink/endpoint/参数），输出：`merged_from` 归并决策（LLM 卡为主体，GN 卡挂靠）+ 疑似重复标记。幻觉 ID 整对丢弃（沿用 `parse_pairing_response` 防御）。
- 降级：回退现行 track_parity 确定性 key 配对。

### 5.2 ②卡片富化（run_gn_finding_enrichment 扩展，per-class 并行 subagent）

- 现有：deep 档 GN-only 多轮富化（`activities.py:1158`，产 title/notes/impact/remediation/dataflow_steps/witness_payload）保留。
- 新增：**全卡接口表富化**——per-class 并行 subagent，素材包 = 卡片 + `entry_points.json` + `route_chains.json` + 相关源码上下文（agent 可 grep/read），产 §4 endpoints 结构化（含 route_registered_at/source_location/sink_location 行号链）。GN 卡优先（llm 卡 endpoints 已有则补行号字段）。
- 写回：endpoints 结构化字段入 SSOT queue（schema 扩展）。
- 降级：确定性回填——`extract_endpoint(path)` + merge 时 endpoint 回填结果组装（无行号但接口在）。

### 5.3 ③POC 增强（generate_poc_report 内，per-vuln 并行 subagent）

- 现状：`poc_generator` 确定性 curl 模板 + `llm_fill_gaps`（`poc_generator.py:970/1047`）缺口补全。
- 升级：per-vuln 并行 subagent，产 §4 poc 结构化：完整 request（method/url/headers/body）+ preconditions + expected_response（判定依据）+ witness_payload；curl/raw_http 由 request 确定性生成。
- 降级：回退现行确定性模板（现状产物）。

### 5.4 ④执行摘要（report-executive agent 升级）

- 现状：对拼好的 md 报告加摘要+清理。
- 升级：吃 report_data.json（组装后），产 executive_summary（攻击面叙事 + top_risks + P0/P1 修复优先级）+ stats.by_type.key_findings。
- 降级：确定性摘要（计数 + top severity 排序）。

### 5.5 ⑤QA 审核（verify_report_vuln_blocks 位演进）

- agent 校验：每卡必填清单（title/severity/endpoints≥1/poc.request/narrative 三段/行号格式 `file:line`）+ 一致性（merged_from 引用有效、severity 与叙事不矛盾）；不合格卡**回炉一次**（把缺字段清单喂回富化 agent）。
- 确定性校验：schema 校验（pydantic）+ md 导出后 `### ID` 节数 = JSON 卡数（自愈保留）。
- 产物：`qa` 字段；失败保产物 + `qa.passed=false` 显式呈现（不静默）。

### 5.6 降级矩阵

| 步骤 | LLM 失败时 |
|---|---|
| ①归并终审 | 现行 track_parity 确定性配对 |
| ②卡片富化 | 确定性 endpoint 回填（无行号） |
| ③POC 增强 | 现行 curl 模板产物 |
| ④执行摘要 | 确定性摘要 |
| ⑤QA | 保产物 + qa.passed=false |

### 5.7 配套即时性修复（随本工程落地）

- **chain_verdict 失败显式化**：`llm-pass-failed` 链进队时 confidence 标 `unadjudicated`，卡片呈现"未判定（判定通道失败）"，与 needs_review 区分；不静默混入"待复核"。
- **activity 未注册 fail-fast**：worker 收到未注册 activity 的 NotFoundError 属部署不一致（本次 3 次静默降级根因），workflow 层应显式告警事件 + 卡片/状态呈现"富化步骤未执行（worker 需重启）"，而非无痕跳过。

## 6. 黑盒与融合

### 6.1 黑盒（改动最小：数据已富，只差结构化）

- exploit verdicts 之后确定性组装 `blackbox/report_data.json`（同 §4 schema）：`evidence.verification="dynamic"`、`dynamic_evidence`=实测输出；poc.request=实际发出的请求、expected_response=实测观察（从 verdicts/evidence md 转结构化）。
- ④执行摘要复用（黑盒管线 report agent 位升级同一 prompt）。
- 黑盒 md 改为从 report_data.json 导出。

### 6.2 融合（web 服务迁入管线）

- `combined` orchestrator 黑盒完成后新增**融合 agent**（1 次深度）：输入白盒 + 黑盒 report_data.json，输出 `combined/run-K/report_data.json`：
  - 每卡 `cross_verification` 三态（verified/untested/failed-to-verify，按 id/endpoint 匹配 + agent 判定）；
  - 融合叙事：白盒根因（代码位置/缺失防护）× 黑盒实测证据合一；
  - 顶层 `verification_gaps` 清单。
- web 侧删 `_generate_combined_report`（`scan_manager.py:2938`）与 `combined_report_renderer.py`（逻辑迁管线）；web 只读产物。
- 降级：融合 agent 失败 → 确定性交叉表（id/endpoint 匹配，无叙事）。

## 7. web API 与前端纯渲染

### 7.1 API（scans.py）

- 新增 `GET /api/workspaces/{ws}/scans/{id}/report-data?track=whitebox|blackbox|combined`：JSON 返回 report_data.json；combined 走 blackbox-runs 维度同构端点。
- 现有 `GET .../report`（`scans.py:418`）语义收窄为 **md 下载/预览**（返回导出 md）；`report_for()` 的 PoC 拼接（`:243-247`）删除——拼接归生成层。

### 7.2 前端（删解析层，纯渲染）

- 新增 `ReportView` 组件族吃 JSON：`ExecutiveSummary`（叙事 + top_risks 锚点）、`StatsRow`（吃 stats 字段）、`VulnerabilityCard`（严重度/双轨/merged_from 徽章、narrative 三段、**endpoints 一体表**、POC 块（完整请求 + 复制 curl）、dataflow 折叠区、QA 标记）。
- **删除**：`lib/vuln-block.ts` severity 推断与卡解析（`baseSeverity`/`inferSeverity`/`parseVulnBlock`/`parseTableRowToBlock`）、`lib/report-sections.ts` PoC 归并（`splitPocSection`/`parsePocEntries`/`stripCardPocLines`）、`MarkdownView.tsx` 报告页路径上的 `extractVulnIds`/`parseStructure`/`vulnPreview`/`groupSegments`/`pocById` 归并、`report-stats.ts` 零计数补全，及对应测试。
- `MarkdownView` 组件保留（交付物页 md 预览仍用），报告页不再经由它。
- 旧 scan 回退：无 report_data.json 时走现有 md 渲染路径（保留最小降级分支，不再投入开发）。

## 8. 铁律与约束（对齐 CLAUDE.md）

- `externally_exploitable` 是可达性标签，agent 富化不可覆写（白名单 + 保护字段模式）。
- 双轨独立性：报告 agent 群吃**合并后 SSOT + 确定性素材包**（entry_points/route_chains 用于接口映射），属 GitNexus 轨下游报告层；LLM 轨 vuln agent prompt 仍不吃确定性 hints（§1 禁令不触碰，`test_static_dataflow_hints_decoupling.py` 锁定不变量继续绿）。
- verdict OR 合并语义不变（`run_merge_dual_track_queues`）；归并终审只做"呈现层同洞合并"（merged_from），不改变双轨判定结果。
- 成本核算沿用 per-profile 计费（agent 步骤计入 session metrics phases）。

## 9. 测试与验证

- **后端**：report_data schema 契约测试（pydantic，schema_version）；每 agent 步骤 mock-LLM-失败 → 降级产物完整（表 §5.6）；归并终审（合成双轨重复卡 → merged_from 正确、幻觉 ID 丢弃）；JSON→md 导出快照；QA 回炉路径；融合交叉验证表用例。
- **前端**：VulnerabilityCard 字段渲染用例（endpoints 表列齐/POC 复制/徽章）；旧 scan 回退分支；删除逻辑的测试清理。
- **端到端**：NodeGoat 重跑（**worker 重启后**）验证 GN 深富化真实执行、XSS-VULN-01/GN-13 归并消重、接口表带行号、POC 可复制复现。
- 只跑改动相关测试文件（预存挂起陷阱，CLAUDE.md §3）。

## 10. 任务切分（供 implementation plan）

- **T1** report_data schema（pydantic）+ 白盒确定性组装 + JSON→md 导出（assemble 演进）
- **T2** ①归并终审 agent（track_parity 位升级 + unadjudicated 显式化）
- **T3** ②卡片富化扩展（GN-only 保留 + 全卡接口表富化 + 素材包组装 + activity 未注册 fail-fast）
- **T4** ③POC 增强 agent 群（结构化 poc 字段 + curl/raw_http 确定性生成）
- **T5** ④执行摘要 + ⑤QA（report-executive 升级 + 回炉 + qa 字段）
- **T6** web API（report-data 端点 + report 收窄）+ 前端 ReportView + 删解析层
- **T7** 黑盒结构化（verdicts → report_data + 摘要复用）
- **T8** 融合迁移（combined orchestrator 融合 agent + web 删 _generate_combined_report）

依赖：T1 → T2/T3/T4（写回字段 schema）→ T5 → T6；T7 独立于 T2-T5（可并行）；T8 依赖 T1/T7。
