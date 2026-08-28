# Prompt 通用语言约束（_output-language-core）设计

- 日期：2026-08-28
- 状态：📐 设计完成、**未实施**（留待后续排期；本文档先行沉淀决策）
- 前置：`specs/2026-06-27-display-ux-polish-design.md`（narration directive 的出处，Part B）
- 关联：`packages/core/src/supernova_core/i18n.py`、`prompts/manager.py`、`prompts/shared/_output-language.{zh,en}.txt`

## 1. 背景与问题

语言开关 `SUPERNOVA_AGENT_NARRATION_LANG`（默认 `zh`，`en`/`english` 切英文）目前只有一半 prompt 受强约束：

- **11 个核心漏洞 prompt**（`vuln-{auth,authz,injection,ssrf,xss}` ×5、`{auth,authz,injection,ssrf,xss}-exploit` ×5、`attack-chain`）经 lang-aware `@include` 引入 `_output-language.{zh,en}.txt` 双语 partial，产物叙述语言被 prompt 内嵌整段规范**强锁定**。
- **13 个辅助 prompt 完全没有 prompt 级语言约束**，只靠 `narration_directive()`（system prompt append）弱兜底。directive 是 append 在 system prompt 末尾的一小段指令，要跟几千词的英文 prompt 主体竞争，GLM 经常跟随主体语言——**这是设计上"可接受"的尾巴**（display-ux-polish spec §112 原文："best-effort：GLM 偶尔蹦英文可接受"）。

用户现场观察到的「英文 prompt + LANG=zh 时 Agent 问答/产物还是英文」即源于此。

### 用户认可的目标设定

> 所有叙述散文用 X 语（随 `SUPERNOVA_AGENT_NARRATION_LANG` 变更，目前支持中英），技术标识/受控词/结构标题保留英文。

## 2. 现状盘点（2026-08-28 实测）

三层约束、强弱不一：

| 层 | 覆盖范围 | 强度 |
|---|---|---|
| `_output-language` 双语 partial | 仅 11 个漏洞 prompt | 强（prompt 体内嵌整段语言规范） |
| `narration_directive()`（system prompt append） | 除 openai subagent 外的所有调用 | 弱（best-effort） |
| 无 | openai 引擎的 `task` subagent（`providers_openai.py:230-240` 故意不注入，防中文渗进父 agent 消费的代码数据） | 零 |

13 个无语言约束的 prompt：

| prompt | 主体语言 | 场景 |
|---|---|---|
| `pre-recon-code.txt` | EN | pre-recon |
| `recon-static.txt` | EN | recon |
| `authz_gitnexus_explore.txt` | EN | authz GitNexus 轨深判（自主探索） |
| `authz_gitnexus_judge.txt` | EN | authz GitNexus 轨深判（吃候选） |
| `poc-agent.txt` | EN | POC 验证 |
| `gn_finding_enrichment.txt` | EN | GitNexus finding 补全 |
| `report_summary.txt` | EN | 报告摘要 |
| `blackbox-endpoint-verify.txt` | EN | 黑盒端点验证 |
| `cross-repo-adjudication.txt` | EN | 跨仓裁决 |
| `cross-repo-correlation.txt` | EN | 跨仓关联 |
| `validate-authentication.txt` | EN | auth 验证 |
| `endpoint_enrichment.txt` | zh（弱） | 端点补全 |
| `report-executive.txt` | zh | 执行报告（LANG=en 时目前恒中文，无切换能力） |

（`prompts/pipeline-testing/` 下的同名文件是测试 fixture，不在此列。）

其中 11 个英文主体 prompt 在 LANG=zh 时问答+产物叙述大概率英文；2 个中文主体 prompt 在 LANG=en 时无法切英文（不对称）。

## 3. 设计决策

### 3.1 方案对比（已定：A）

**关键技术事实（已核实，`prompts/manager.py:95-121`）**：`@include(path)` 是单趟 `re.sub`，partial 内容里再写 `@include` 不会被二次解析——**嵌套继承不可行**（除非改 manager 支持递归）。

- **方案 A（选定）**：新建独立通用 partial `_output-language-core.{zh,en}.txt`，13 个 prompt `@include(shared/_output-language-core.txt)`；现有漏洞版 `_output-language` 与 11 个已配 prompt **完全不动**，manager 不动。
- **方案 B（否决）**：改 manager 支持递归 `@include`，把 `_output-language` 重构为「core 通用段 + 漏洞增强段」两层，25 个 prompt 统一。单一来源无重复，但要动所有 prompt 的加载路径 + 重排 11 个已验证 prompt，回归面大；为省 ~20 行静态文案重复不值。

### 3.2 重复面与兜底

方案 A 下，`_output-language-core` 与现有 `_output-language` 重复的是两段（每语言 ~10 行，共 ~20 行）：

- **① 总纲**（1 句）：「全部叙述性、描述性文字用 X 语撰写，覆盖全部产物」
- **③ 技术标识保留清单**（~7 行）：漏洞编号 / 代码·命令·文件路径·行号 / HTTP 方法与状态码 / URL·header·JSON 字段·cookie 名 / 技术缩写（SSRF、XSS、IDOR…）

漏洞版特有的 **② 漏洞场景字段清单**（exploitation_queue 字段、dataflow_steps label、add_exploit 工具参数、deliverable 文件）与 **④ 报告标题英文词表** 不进 core、不重复。

漂移风险（以后往 ③ 加一条要同步 4 个文件）用**一致性测试**锁死：断言 `_output-language.{zh,en}.txt` 文本包含 core 版的 ①+③ 段（逐字符串包含），漂移即红。技术标识清单本身极稳定，重复是静态低频变动的。

### 3.3 core partial 内容骨架

`_output-language-core.zh.txt`（en 版对称）：

```text
<output_language>
全部叙述性、描述性散文用简体中文撰写——覆盖你的推理口述（narration）、
每轮总结、markdown 正文段落、JSON 产物中供人读的字段值、工具调用的叙述性参数。

以下技术标识必须保留英文原文，不得翻译：
- 漏洞/finding 编号（如 AUTH-VULN-01、INJ-VULN-02）
- 代码、命令、文件路径与行号（如 server/app/controller/image.js:102）
- HTTP 方法与状态码（如 GET /api/fileProxyGet、HTTP 302）
- URL、请求头名、JSON 字段名、cookie 名
- 技术缩写（SSRF、SSTI、XSS、CSRF、RBAC、HSTS、IDOR、OAuth、JWT、PKCE）
- 受控词汇字段的"值"（枚举值按 prompt 给定的英文枚举填写）
</output_language>
```

注意与漏洞版的措辞差异：第二段比漏洞版多一条「受控词汇字段的值」（从 narration directive 的 DIRECTIVE_ZH 借来）——辅助 prompt（poc-agent 的 pocs JSON、queue 类产物）同样有枚举值字段，通用版需要这条；漏洞版保持原文不动。

## 4. 实施要点（留给后续）

1. 新建 `prompts/shared/_output-language-core.{zh,en}.txt`（§3.3 骨架）。
2. 13 个 prompt 各加一行 `@include(shared/_output-language-core.txt)`——lang-aware fallback 自动按 `current_lang()` 解析成 `.zh.txt`/`.en.txt`，写法与现有 11 个 prompt 对齐（include 位置跟随现有 `@include(shared/...)` 的惯例处，无则放 prompt 头部约束区）。
3. **不碰**：`prompts/manager.py`（加载逻辑零改动）、现有 11 个漏洞 prompt 及 `_output-language.{zh,en}.txt`、`narration.py` directive（两层约束方向一致、无害重叠；openai subagent 故意不注入的现状保持——防中文渗进代码数据）。
4. 测试（TDD）：
   - 防回退：断言 13 个 prompt 都 include 了 `_output-language-core`（参照 `test_static_dataflow_hints_decoupling.py` 的锁定模式）；core 双语文件都在且含技术标识保留段。
   - 一致性：`_output-language.{zh,en}.txt` 包含 core 版 ①+③ 段文本（§3.2）。
   - 合规锚点：语言 partial 是语言指令而非确定性产物，不踩 CLAUDE.md §1「确定性产物不喂 LLM 轨 prompt」铁律（display-ux-polish spec 已有先例背书）；现有 decoupling 测试保持绿。
5. 生效语义：per-workspace env，扫描执行期读取——改配置只对新扫描生效，已产出的报告语言不变。

## 5. 边界与语义保持

- `SUPERNOVA_AGENT_NARRATION_LANG=off/none/disable` 时 directive 不注入，但 `current_lang()` 回落 `zh` → core partial 仍注入中文约束。这是现状行为（11 个漏洞 prompt 同样如此），本设计**不改**；若未来需要「off = prompt 级也完全关」，需另行设计（如 lang-aware fallback 支持 off 返回空）。
- 本设计不做「UI 语言联动扫描语言」（用户 2026-08-28 明确先不做）。
- 13 个 prompt **全补**（含中文主体的 `report-executive`、`endpoint_enrichment`）——为 LANG=en 时能对称切英文。
