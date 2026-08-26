# 漏洞卡片信息归并与双轨呈现一致性（design）

> 2026-08-26 立项，直接延续 2026-08-25 白盒报告可读性改造（已上线的四要素卡 + 漏洞细节折叠区）。
> 触发：用户验收 NodeGoat-20260825-140612 白盒报告后的 5 点卡片冗余/缺失反馈 + 2 点双轨一致性要求。诊断证据见 §1。

## 1. 背景与问题诊断

上轮改造后卡片结构已立（正文四节叙事 + 漏洞细节折叠），但用户验收发现信息布局仍有七类问题（实证：`workspaces/__legacy__/scans/NodeGoat-20260825-140612/deliverables/whitebox/`）：

**卡片信息冗余/缺失（5 点）：**

1. **PoC 两处**：同一张卡上「漏洞细节」区 `- **PoC:**`（`witness_payload`，authz 为同义的 `- **最小见证:** minimal_witness`，`findings_renderer.py:390/427`）+ 前端并入的「详细 PoC」curl/Burp 块（`exploitable_poc_collection.md` → web report endpoint 拼接 → `report-sections.ts` `parsePocEntries` 并入卡片）。后者是前者的超集。
2. **脆弱位置/数据流/来源详情三行同源**（`findings_renderer.py:406-410`）：脆弱位置 = `source → path` 拼接 = 数据流单行 dump；来源详情 ≈ 数据流前几步散文版。GN 卡实测三行 100% 重复（XSS-GN-01：`preTax → …:render:21:19` 换三种拼法）；LLM 卡约八成重复。
3. **渲染上下文**（`render_context`，`findings_renderer.py:416`）：XSS 专属（HTML_BODY/HTML_ATTRIBUTE/URL_PARAM/…），对 XSS 是决定 payload 形态与转义方案的真实属性，但单独一行只值一个词，信息密度过低。
4. **Sink/位置信息最多散在 4 处**：正文问题点节（依赖 snippet 提取成功，失败整节消失——NodeGoat XSS-VULN-02~08 均无此节）、细节区 `Sink 函数`（`:412`）、auth/ssrf/authz 的 `脆弱代码位置`（`:421`）、GN 入口表「Sink 位置」列（builders 未回填 `sink_location`，恒空）。
5. **接口列表与外部参数缺失**：受影响入口节本应承担，但 a) 接口行仅在 endpoint 不在标题时渲染且仅单个；b) LLM taint 卡 collector 不产 `endpoint`/`affected_parameters` → NodeGoat XSS-VULN-01~09 **整节全缺**；c) 存储型 XSS 双接口（写入口+触发口）无处呈现；d) 接口散落 title/notes/path/`端点`/`来源端点` 四处。

**双轨一致性（2 点）：**

6. **同洞漏合并**：`merge_dual_track_queues` 已做跨轨归并（key = `(vtype, 归一化接口, 归一化 sink)`，both 卡 base 取 LLM + GN entries 并集），但 sink 粒度/称谓不同即配不上——XSS-VULN-01（sink `marked(doc.memo)`，链起 `POST /memos`）vs XSS-GN-13（sink `render`，unit `GET /memos`）：跨接口存储型 XSS 两轨各见半条链，字符串归一（`_normalize_sink_func`）救不了语义差异 → 同洞两张卡。无 endpoint 无 sink 的卡落 `_strict_key` 全字段精确匹配 → 两轨永不命中。
7. **GN-only 卡质量洼地**：15 张 GN XSS 卡 vs 9 张 LLM 卡——标题确定性拼接、危害/修复为占位句（「静态链路发现，建议人工确认」）、无 CVSS/OWASP、元信息行 `置信度：待复核 ｜ 待复核` 重复 bug（`confidence="needs_review"` 被 `_strip_internal` 替换为「待复核」后 `gn_only` 又追加一次）。

## 2. 目标 / 非目标

**目标**
- 卡片信息各归一处：PoC 单一呈现、数据流三行归一、Sink/位置合并进问题点、接口+参数结构化呈现（§3-§5）
- 双轨同洞合并率提升：LLM 辅助配对归并补确定性 key 的语义盲区（§6.1）
- GN-only 卡字段与 LLM 卡同构：轻量 LLM 补全叙事/评级字段，消除占位文案（§6.2）
- 用户看见的报告字段两轨完全一致（渲染基准 schema 统一 + 数据补全）

**非目标**
- 不动双轨检测/判定主干（builder 候选链、chain_verdict verdict 语义、LLM 轨 prompt 方法论）
- 不做 sink 粒度的确定性归一升级（marked vs render 类语义对齐交给 §6.1 LLM 配对）
- 不实现 CVSS 精确计算器（补全 LLM 估分，向量串可选，不确定省略）
- 不改黑盒渲染路径（共用 `render_vuln_card`，缺字段自动省略节）
- 不动速查表/执行摘要（上轮已改造）

## 3. 卡片基准模板（定稿，两轨唯一口径）

```markdown
### XSS-VULN-01 跨站脚本 (XSS)：存储型 XSS：POST /memos 的 memo 经 marked 渲染…
严重程度：高危 ｜ CWE-79 ｜ 验证：静态分析 ｜ 置信度：高（双轨确认）

**漏洞成因（研判依据）**
{notes 叙事；GN-only 无补全时保持确定性一句话}

**危害**
{impact；GN-only 无补全时保持类级兜底/待复核提示}

**问题点**
- 位置：app/views/memos.html:31
- 说明：req.body.memo 未经校验进入 marked() 渲染（HTML body 上下文）
```js
{代码片段，提取失败缺省 fence，位置+说明照常渲染}
```

**受影响入口**
- 接口：POST /memos（写入）、GET /memos（触发）
| 参数 | Sink 位置 | 链 ID |
|---|---|---|
| memo | app/views/memos.html:31 | XSS-GN-13 |

**修复建议**
{remediation；GN-only 无补全时保持类级兜底}

#### 漏洞细节
- **数据流:**
  1. addMemos (app/routes/memos.js:13)
  2. …
- **防护情况:** …（encoding/sanitization/guard_evidence/missing_defense 收拢，现状不变）
- **判定:** vulnerable ｜ **CVSS:** … ｜ **OWASP 分类:** …

{详细 PoC —— curl/Burp 可执行块，前端并入，web 报告页卡内唯一 PoC 呈现}
```

## 4. 渲染层改动（findings_renderer.py）

### 4.1 漏洞细节区收敛（25 个可能 kv 行 → 3 组）

| 现有行 | 处置 | 去向 |
|---|---|---|
| `PoC`（witness_payload）/ `最小见证`（minimal_witness） | `.md` 原文保留，**web 前端有并入详细 PoC 时过滤**（§8；poc_generator 现收录全部 vulnerable 卡，覆盖面≈全量，不能按 ee 后端硬删） | 详细 PoC 块 |
| `脆弱位置`（source→path） | 删 | 数据流分点 |
| `来源详情`（source_detail） | 删（GN 无损；LLM 卡独有信息基本已被 dataflow_steps 覆盖） | 数据流分点 |
| `Sink 函数` / `Sink 调用` | 删 | 问题点·说明 |
| `渲染上下文`（render_context） | 删，拼入问题点·说明句尾（仅 XSS 出现） | 问题点·说明 |
| `脆弱代码位置`（vulnerable_code_location） | 删 | 问题点·位置 |
| `端点` / `来源端点` / `拼接出现` / `利用假设` / `建议利用技术` / `角色上下文` / `副作用` / `原因` 等 | **保留**（判定/防护语义字段照旧全量） | 漏洞细节区 |
| `数据流` 分点、`防护情况` 组、判定三件套 | 保留（现状顺序：数据流 → 防护 → 判定/CVSS/OWASP） | 漏洞细节区 |

### 4.2 问题点节三要素化

固定结构：`位置`（file:line）→ `说明`（一句话）→ fence（snippet，可缺省）。

- **位置**复用 `_card_loc` 回退链：sink_call 解析 → `affected_entries[0].sink_location` → `vulnerable_code_location` → path/sink_call 正则。
- **说明** = 现有 `code_issue_line`（`{param} 未经校验进入 {sink}`）+ XSS 时句尾附 `（{render_context}）`。
- **snippet 提取失败不再整节省略**：位置+说明照常渲染，仅缺 fence（现状 `if snippet:` 导致整节消失是缺陷）。

### 4.3 受影响入口节重构

`_entry_section_lines` 改为：

1. **接口列表行**：`- 接口：POST /memos（写入）、GET /memos（触发）`——数据源 `endpoints`（新字段，§5）→ 兜底 `endpoint`/path 提取/`source_endpoint`；**删除「endpoint 在标题里就不渲染」的跳过条件**（title 是叙事，接口行是结构化数据，不应被叙事掩盖）。
2. **参数表**：现有三列表保留（参数 × Sink 位置 × 链 ID），承载 both 卡多 entry 全量与 GN 追溯；单轨合成路径不变。
3. 参数与接口全无才整节省略（现状 F8 口径不变）。

### 4.4 顺带修复

- 元信息行「待复核 ｜ 待复核」重复：`gn_only` 追加条件改为 conf 显示值已非「待复核」时才追加。
- `_first_sink_location` 与 `_card_loc` 合一（两套回退链漂移风险）。

## 5. 数据层：taint 类 collector 补接口/参数字段

`collectors/vuln.py` + prompt 字段表（`_INJECTION/_XSS/_SSRF_FINDING_PROPS`）append-only 增补（走 2026-08-25 Task 7 同模式，契约测试 `test_vuln_prompt_schema_contract.py` 同步）：

| 字段 | 类型 | 语义 |
|---|---|---|
| `endpoints` | `list[str]` | 该漏洞涉及的全部接口（写入+触发分开列），元素 `METHOD /path`，可带角色注记如 `（写入）` |
| `affected_parameters` | `list[str]` | 外部可控参数名（进 `BaseVulnerability` 现有字段），元素可带来源注记如 `memo (body)` |

- GN 侧无需新字段：接口已在 `path`（`extract_endpoint` 可提）、参数在 source（`extract_param`）；GN builders 回填 `affected_entries[].sink_location`（§7）。
- 旧 queue 无新字段自动降级（渲染层兜底提取路径已覆盖）。

## 6. 双轨一致性

### 6.1 LLM 辅助跨轨配对归并（补确定性 key 语义盲区）

**位置**：`run_merge_dual_track_queues`（`activities.py:919`）内，确定性 `merge_dual_track_queues` 之后、落盘之前。

**流程**：
1. 收集剩余 `llm-only` × `gitnexus-only` 卡（同 class），每卡一行摘要（ID / title / 接口 / sink / 参数）。
2. **每 class 一次** `run_claude_prompt` 单次结构化输出（chain_verdict 同模式，非 agent）：输入双列摘要，输出 `{pairs: [{gn_id, llm_id, confidence: high|medium|low, reason}]}`。
3. 仅 **high** 置信对应用合并：GN finding 并入 LLM 卡（复用 both 分支字段融合——entries 并集 / severity 取高 / evidence_chain 兜底），GN 独立卡移除；medium/low 不动（误合并比漏合并更伤报告可信度，保守优先）。
4. 两侧全空或单侧空 → 跳过调用。

**退化**：LLM 不可用（stub/超时）→ 跳过，维持确定性 merge 结果，不 fail-fast。

### 6.2 GN-only 卡轻量补全（字段与 LLM 卡同构）

**位置**：同 activity，配对归并之后对仍 `gitnexus-only` 的卡逐卡执行。

**输入**（GN 自己的产物，不涉 LLM 轨）：参数、sink 函数、file:line、evidence_chain、vuln class、可预提取的 code snippet、`affected_entries`。

**输出**（单次结构化 JSON，写入 `BaseVulnerability` 现成字段，零 schema 改动）：
`title`（叙事标题）、`notes`（成因 2-3 句）、`impact`（危害）、`remediation`（代码级修复）、`cvss`/`owasp_category`（不确定省略不编造）、`severity` 校准（低置信保持 `effective_severity` 规则兜底）。

**明确不补**：`dataflow_steps`（轻量单次不读码，编造中间节点有风险；保持 evidence_chain 拆点）。

**退化**：LLM 不可用 → 跳过补全，保持现状确定性文案（`det_unfiltered_into` / `gn_static_hint` / 类级兜底）——渲染层兜底路径已存在，报告不阻塞。

**幂等**：配对/补全只写合并版 `intermediate/{vuln}_exploitation_queue.json`；activity 重跑从 `*_llm_queue.json` + `*_gitnexus_queue.json` 重算，无污染。

**合规**（CLAUDE.md 铁律）：本环节是 **GitNexus 轨侧的 LLM 补全**（同 `chain_verdict` 轻量判定、llm-discovered sink 模式——GitNexus 轨已演进为「确定性兜底 + 可选 LLM 补召回」），输入是 GN 自己的产物；**不触碰**「确定性产物不喂 LLM 轨 vuln agent prompt」铁律（LLM 轨 `vuln-*.txt` 不动）。

**成本**：配对 ≤5 次/scan（每 class 1 次）+ 补全 = GN-only 卡数 ×1 次轻量调用（NodeGoat 量级 ≈ 15-20 次）。

## 7. GN builders 回填 sink_location

`vuln_chain_builders/*_builder.py` 组 `affected_entries` 时填 `sink_location`（sink file:line），入口表「Sink 位置」列与 `_card_loc` 位置链不再恒空。（实现点 plan 时核 builders 组 entry 的具体位置。）

## 8. 前端适配（web 报告页）

- **PoC 单一呈现**：`parsePocEntries` 已建 id→md 映射；渲染并入详细 PoC 的卡时**过滤卡内 `- **PoC:**` / `- **最小见证:**` 行**（`.md` 原文/独立下载件不受影响，各自文档完整自洽）。
- 解析锚点不动：标题/元信息行/kv-list 正则（`vuln-block.ts`）零改动；`vuln-block.test.ts` fixture 随渲染 md 同步。
- 速查表/类型卡/inferSeverity 真数据路径（上轮已上线）不动。

## 9. 测试策略（TDD）

- **渲染**：细节区收敛断言（脆弱位置/来源详情/Sink 函数/渲染上下文行不再出现；数据流分点保留）；问题点三要素（snippet 缺省时节不消失）；受影响入口（接口列表行必渲染、多接口、无 endpoints 兜底提取）；PoC 行保留（.md 路径）；「待复核」不重复。
- **配对归并**：高置信对合并（entries 并集/severity 取高/GN 卡移除）；中低置信不合并；LLM 不可用跳过（结果=纯确定性 merge）；空侧跳过。
- **补全**：字段写入 roundtrip；不编造（cvss 省略路径）；不可用退化文案；幂等重跑。
- **collector/契约**：taint 三类 schema 含 `endpoints`/`affected_parameters` + prompt 字段表同步；LLM 轨 prompt 仍无确定性产物 include（守既有铁律测试）。
- **builders**：entries 含 sink_location。
- **前端**：有 PoC entry 时卡内 PoC 行过滤；fixture 快照更新；vitest 全量。
- 回归口径：`test_findings_renderer.py`（44 例）+ readability e2e + merger/models + 前端 vitest；预存红不动（CLAUDE.md §3）。

## 10. 任务分解蓝图（供 plan 细化）

| # | 任务 | 层 |
|---|---|---|
| T1 | 渲染基准：细节区收敛 + 问题点三要素 + 受影响入口重构 + 待复核修复 | 渲染 |
| T2 | taint collector schema/prompt 补 `endpoints`/`affected_parameters` + 契约测试 | 数据 |
| T3 | LLM 辅助配对归并（prompt + 保守应用 + 退化） | 合并 |
| T4 | GN-only 卡补全（prompt + 写回 + 退化 + 幂等） | 合并 |
| T5 | GN builders 回填 `sink_location` | 数据 |
| T6 | 前端 PoC 单一呈现过滤 + fixture 同步 | 前端 |
| T7 | 全链回归（renderer/merger/e2e/vitest + NodeGoat 复扫验收） | 回归 |
