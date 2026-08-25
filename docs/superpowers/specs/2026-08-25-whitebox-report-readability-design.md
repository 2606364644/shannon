# 白盒漏洞报告可读性与专业性改造（design）

> 2026-08-25 立项。诊断证据：NodeGoat-20260821-074131 白盒报告 vs `/root/shannon/sample-reports/shannon-report-crapi.md`（TS 原始）；根因定位见 §1。

## 1. 背景与问题诊断

白盒报告（`comprehensive_security_assessment_report.md`）与原始 shannon TS 报告质量差距集中在**漏洞卡片层**（黑盒报告已达 5-section 结构、质量达标，不在主战场）。四个根因：

1. **schema 视角错位**：TS 报告条目是面向读者的叙事 schema（title/severity/OWASP/overview/impact/exploitation_steps/proof_of_impact/remediation，`shannon/apps/worker/src/services/report-renderer.ts:109`）；fork 白盒渲染的是判定器内部字段（拼接出现/净化情况/判定/见证载荷，`packages/core/src/supernova_core/services/findings_renderer.py:100-219`）。**缺 Impact 与 Remediation**。
2. **GitNexus 轨条目未人话化直接平铺**：GN 条目标题 = taint 链 dump（`INJ-GN-01: injection：preTax → …:eval:32:23`），卡片 3 行；且候选链存在 source×sink 全配对虚假链（`preTax→eval:33` 实际求值的是 afterTax）。
3. **同单位笛卡尔积拆条**：同接口同 sink 因参数×行号差异拆成 9 条 INJ-GN + 15 条 XSS-GN；与 LLM 轨同洞条目也不合并（merger dedup key 拿整链字符串精确匹配）。
4. **管线内部概念泄漏**：`llm-pass-failed, needs_review`（`chain_verdict.py:419`）、GN 编号体系、`Count: 13（单点卡片…）`、`### Xss` 大小写。
5. **文案风格失控**（黑盒白盒共有）：exploit/vuln prompt 无语言与文风指令 → 中文报告夹整段英文 impact；`CONFIRM/PROVE/SYSTEM FINGERPRINT` 大写标签、单段 300 词嵌套编号、总结腔。
6. **缺数据字段**：卡片无 severity（web 前端 `inferSeverity` 关键词猜）、无 CVSS、无 CWE/OWASP、无验证状态区分（白盒静态判定 vs 黑盒动态验证）。

## 2. 目标 / 非目标

**目标**
- 白盒漏洞卡片达到四要素讲解：**是什么（+成因）、危害、问题代码、修复建议**
- 漏洞单位 = 接口级；同单位多参数/多链收敛为一张卡 + 受影响入口列表
- 执行摘要升级：质量提升 + 漏洞速查表（含接口、参数）
- severity/CVSS/CWE 数据化；验证状态显式
- 全文风格统一：语言跟随报告语言、安全专家文风、术语通俗化

**非目标**
- 不改双轨检测/判定逻辑（builder 虚假配对收紧、chain verdict LLM 稳定性另立项）
- 不改黑盒卡片 5-section 结构（已达标；仅受风格指南约束）
- 不动 web 报告页信息架构（仅数据字段适配）
- 不实现 CVSS 精确计算器（LLM 估分 + 规则兜底档位，向量串可选）

## 3. 漏洞单位与归并规则（已对齐口径）

**漏洞卡片的单位 = `(vuln_class, 归一化接口, sink 函数)`。**

1. **不同接口绝不合并**：同根因不同接口触发 = 不同漏洞，各自独立成卡。
2. **同单位收敛一张卡**（口径 A）：同接口 + 同 sink 函数 + 同 vuln_class 的不同参数/不同行号/多条链 → 一张主卡；参数与链变体收纳进卡内「受影响入口」列表（参数 × sink file:line 对应关系，虚假配对按"sink 行实际参数"过滤标注）。
3. **跨轨去重**：LLM 轨条目与 GN 轨条目同单位 → 合并为一张卡（`merge_source=both`，双轨互证）。merger dedup key 从整链字符串精确匹配改为 **endpoint 归一化（`_normalize_endpoint` 已有）+ sink 函数名**；`severity` 取高者、受影响入口取并集、叙述字段以 LLM 轨为权威。
4. LLM 轨条目天然接近接口级（保持现状粒度，仅补字段）。

## 4. 数据层 schema 扩展

`BaseVulnerability`（`models/queue_schemas.py`）新增：

| 字段 | 类型 | 语义 | 来源 |
|---|---|---|---|
| `severity` | `Severity` 枚举，必填 | critical/high/medium/low | LLM 判定输出；缺失时规则兜底（sink 类别 × 可达性定档） |
| `cvss` | `str \| None` | 向量+分数，如 `AV:N/AC:L/… 9.8` | LLM 估，规则校验数值范围 |
| `cwe_id` / `owasp_category` | `str \| None` | `CWE-95` / `A03:2021-Injection` | LLM 判定输出 |
| `endpoint` | `str \| None` | 归一化 `METHOD /path` | LLM 轨从 path 提取；GN 轨经 `http_route_label()` join entry_points |
| `affected_parameters` | `list[str]` | 受影响参数 | LLM 轨判定；GN 轨 source_param |
| `affected_entries` | `list[AffectedEntry]` | 入口明细：参数、sink file:line、链 ID、轨来源 | 归并层生成 |
| `verification` | `static_analysis \| dynamically_verified` | 验证方式 | 白盒渲染层定 `static_analysis`；黑盒 exploited 条目 `dynamically_verified` |
| `code_snippet` | `str \| None` | sink 行 ±3 行源码片段 | 渲染层从 code_index / 仓库确定性提取，零 LLM 成本 |

合并策略：severity 取高者；affected_entries 并集；title/impact/remediation 以 LLM 轨为权威、GN-only 卡走 §6 模板降级渲染。

## 5. 卡片模板（白盒，四要素）

```markdown
### INJ-VULN-01 服务器端 JS 注入（RCE）：POST /contributions
严重程度：严重 ｜ CWE-95 ｜ 验证：静态分析 ｜ 置信度：高（双轨确认）

**受影响入口**
| 参数 | Sink 位置 | 数据流 |
|---|---|---|
| preTax | contributions.js:32 | req.body.preTax → eval() |
| afterTax | contributions.js:33 | req.body.afterTax → eval() |
| roth | contributions.js:34 | req.body.roth → eval() |

**漏洞说明**
（是什么 + 成因，2-4 句：什么参数经什么路径传到什么危险调用、缺了什么防护）

**危害**
（攻击者能做什么、最坏后果、业务影响，≤3 句，结论先行）

**问题代码**
    // app/routes/contributions.js:32
    preTax = eval(req.body.preTax);   // ← 未校验类型直接执行
（一句指出问题所在；snippet 由渲染层确定性注入）

**修复建议**
（代码级动作：怎么改；一句话说清，不写"建议加强输入校验"式空话）

**技术细节**（折叠区，附录化）
判定依据 / taint 链 / 净化情况 / 验证 payload —— 现有判定字段全部降级到此区。
```

黑盒卡片保持 5-section 结构，仅受 §8 风格指南约束 + 补 `severity`/`cwe_id`/`endpoint` 字段渲染。

## 6. GN-only 卡片降级渲染

无 LLM 叙事的 GN-only 单位：模板自动降级——「漏洞说明」由确定性描述生成（`{vuln_class 中文定名}：{参数} 未经过滤传入 {sink 函数名}（{file}:{line}）`），标注"待复核"（替代 `llm-pass-failed` 内部标签），severity 走规则兜底，危害/修复建议可缺省并明示"静态链路发现，建议人工确认"。

## 7. 速查表与执行摘要

**速查表**（正文第一章，report-executive 之前由渲染层确定性注入；web 前端自然渲染表格）：

```markdown
## 漏洞速查表
| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |
|---|---|---|---|---|---|---|
| INJ-VULN-01 | 服务器端 JS 注入（RCE） | POST /contributions | preTax 等 3 个 | 严重 | 静态分析 | 高（双轨） |
```

**执行摘要**（report-executive prompt 重写指令）：
- 保留现有优点：总体态势、最高危攻击面按风险排序、系统性工程问题
- 新增：关键数字段（各类计数 + 严重度分布，**按归并后单位计数**）；修复路线（P0/P1 分级一句话依据）
- 删除：「供技术领导快速判断」类预设读者表述；内部编号体系（"单点卡片 INJ-VULN-01..04 + INJ-GN-01..09"）
- 类别标题美化：`Xss` → `XSS`、`Ssrf` → `SSRF`（速查表与汇总区统一由渲染层生成，LLM 只写叙事段）

## 8. 报告文案风格指南（进 vuln-*.txt / *-exploit.txt / report-executive.txt）

**语言**（推广 report-executive 现有 LANGUAGE 段）：
- 叙述内容跟随 `SUPERNOVA_AGENT_NARRATION_LANG`（zh 报告全文中文）
- 保留英文原文：漏洞编号、代码、命令、文件路径与行号、HTTP 方法/状态码、技术缩写（XSS/CSRF/JWT/IDOR…）

**文风**（安全专家风格：直接、具体、可信）：
- 结论先行：每段第一句定性，证据随后
- 一段一事：影响段 ≤3 句；枚举用列表不用段内编号
- 禁：全大写强调（CONFIRM/PROVE）、戏剧化表述（undeniable proof）、总结腔收尾句、预设读者的元话语
- 术语通俗化映射（渲染层 + prompt 双侧）：见证载荷→验证 payload、判定→结论、拼接出现→字符串拼接、净化情况→过滤/转义情况、sink→危险函数调用点
- 修复建议代码级具体："将 eval 替换为 Number() 并校验类型"，非"建议对输入进行校验"

**验证状态用语**：静态分析发现（未动态验证）/ 已动态验证。

## 9. 内部概念剥离清单

| 内部概念 | 处置 |
|---|---|
| `llm-pass-failed, needs_review` / `unparseable-llm` | 出正文；GN-only 卡统一"待复核"标注 |
| GN 编号（INJ-GN-01） | 出正文标题；仅存附录技术细节与入口列表链 ID |
| `单点卡片/Count: N` 内部口径 | 汇总区改为按归并单位计数 + 严重度分布 |
| `### Xss` 等大小写 | 速查表/汇总区标题由渲染层生成，LLM 不再手写 |
| `inject_gitnexus_track_status` 注记 | 移至报告尾部附录「轨道覆盖说明」 |

## 10. 渲染层与管线改动

1. `findings_renderer.py`：`render_*_entry` 五函数 → 统一 `render_vuln_card(vuln)`（§5 模板 + §6 降级）；技术细节折叠区保留全部现有字段
2. 归并前置：GN 条目在渲染前按 §3 单位收敛（新 `collapse_gn_entries()`，放 `code_index/` 或 `services/`，merger 调用后执行）；merger key 归一化
3. `report_assembler.py`：速查表注入（assemble 阶段，report-executive 之前）
4. `code_snippet` 提取：sink file:line ±3 行（读仓库文件，GitNexus 已有 repo 路径；无索引时缺省不渲染）
5. `report-executive.txt`：摘要指令按 §7 重写；cleanup 规则补速查表不可删约束
6. vuln/exploit prompt：补语言+文风+新字段（severity/cvss/cwe/impact/remediation）输出指令；`add_exploit` 工具 schema 同步
7. web 前端：`inferSeverity` 启发式换真数据（卡片 header 读 severity 字段）；速查表/入口表格样式适配

## 11. 测试策略（TDD）

- schema：新字段 roundtrip + 缺省规则（severity 兜底）
- 归并：跨轨同单位合并（severity 取高/入口并集）；GN 笛卡尔积收敛（9→1）；不同接口不合并；虚假配对过滤
- 渲染：卡片快照（四要素齐、内部标签零出现、语言断言）；速查表行数=归并单位数；GN-only 降级卡
- prompt 锁定：风格指南片段存在性（防回归删除）；vuln prompt 不含确定性产物 include（守既有铁律）
- 前端：inferSeverity 移除后药丸取数断言

## 12. 任务分解蓝图（供 plan 细化）

| # | 任务 | 层 |
|---|---|---|
| T1 | schema 字段扩展 + severity 兜底规则 | 数据 |
| T2 | merger key 归一化 + `collapse_gn_entries` 收敛 + 虚假配对过滤 | 归并 |
| T3 | `render_vuln_card` 统一模板 + 降级渲染 + 技术细节折叠 | 渲染 |
| T4 | 速查表注入 + 类别标题渲染层化 + 内部概念剥离 | 渲染/摘要 |
| T5 | `code_snippet` 确定性提取 | 渲染 |
| T6 | 风格指南进三类 prompt + add_exploit schema 同步 | prompt |
| T7 | report-executive 摘要指令重写 | 摘要 |
| T8 | web 前端 severity/速查表适配 | 前端 |
| T9 | 全链回归（白盒渲染 fixture 端到端 + 前端 vitest） | 回归 |
