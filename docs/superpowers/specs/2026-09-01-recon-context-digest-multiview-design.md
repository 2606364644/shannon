# recon-context-digest 修复与多视图摘要（第一期）— 设计 spec

- 日期：2026-09-01
- 状态：设计定稿（经用户逐点确认：方案形态、职责边界、token 账、解析容错、重试/resume 语义）
- 上游背景：CLAUDE.md §1 双轨铁律（LLM 轨 prompt 源只从 recon 派生）；commit `8f32899e`（2026-09-01，digest 共享化——本次改造的直接前置）
- 下游：第二期（注入侧按 agent 路由）为期权，见 §9

## 1. 背景与痛点（研究证据）

### 1.1 P0：`recon_md[:8000]` 截断使摘要覆盖面坍塌

`summarize_recon_context()` 的 LLM 调用输入是 `recon_md[:8000]`（`recon_context_summarizer.py:75`，`a5ce1368` 2026-07-03 引入）。对 15 份真实扫描交付物的统计：

- §4（API Endpoint Inventory）**完整落入 8000 字符窗口：0/15 份**；9 份 §4 起点在 8000 之后（摘要完全看不到端点表），6 份只见开头碎片。
- §8（authz 候选）、§9（injection sources）**从未进入过摘要输入**。
- 实测（NodeGoat 20260901）：digest 仅 **484 字符 / 4 个端点**，原文 §4 表 **31 个端点**、§9 有完整分类注入源。
- **质量倒挂**：`llm-summary` 档信息量低于 `deterministic-extract` 降级档（后者对全文抽 §4+§8），但 `source` 标签显示前者更"高级"，不可观测。
- 现有 5 个 digest 测试全部小 fixture，未锁定截断行为。

### 1.2 P1：五类 vuln agent 的视角无结构保障

recon md 的信息结构本已按类组织（§8 按水平/垂直/上下文；§9 按 command/SQL/LFI·RFI/SSTI/path-traversal/deserialization；§3/§7 为 auth 类服务），但：

- 摘要 prompt 只要求 §4+§8，§9 从不在范围（即使无截断）。
- 单一自由文本摘要下，LLM 通用压缩时 xss/ssrf 视角最易被挤掉——正是"每个 vuln agent 需要的摘要不一样"这一关切。
- §9 的 `set_injection_sources` 分类骨架（6 桶）只对齐 injection agent（recon-static.txt:102 "Drives the vuln-injection agent's todos downstream"）；xss 搭 SSTI 桶便车，ssrf 无桶（出站线索散在 §4 描述列 + §6.3 flows）。

### 1.3 成本定位（非痛点，约束放开）

实测单次摘要调用 ¥0.0052 / 4.2s（deepseek flash medium 档，4750 in / 236 out）。全 scan 成本结构：5 个 vuln agent ≈ 51%、chain-verdict ≈ 30%、摘要调用 ≈ **0.1%**。结论：**"为省 token 压缩摘要输入"的设计约束取消**；摘要输入扩大到全量抽取的边际成本可忽略（详见 §6 权衡）。

## 2. 目标 / 非目标

**目标（第一期）**

1. 修截断：摘要输入 = 确定性抽取 `§3 + §4 + §6.3 + §7 + §8 + §9` 拼接全文，去掉 `[:8000]`。
2. 分节生成：摘要 prompt 要求按六节固定结构输出（endpoints / authz / injection / xss / ssrf / auth），并附"只重组原文线索、禁止推断新增"硬约束——**内容的类覆盖**本期兑现（生成侧强制五类各扫一遍）。
3. digest schema v2：`text`（LLM 原始输出全量保底）+ `sections`（解析派生视图）+ coverage 对账元数据。
4. 解析容错：纯代码逐行解析，永不 hard fail；最坏退化 = 单一文本注入（等价旧行为）。
5. 对账与可升级：`endpoints` 节行数 vs §4 表行数机器对账；覆盖率不足或未分节 → 标 `degraded` → resume 时自动重试升级（复用 `8f32899e` 的机制）。
6. 降级链同构：LLM 失败/输出空的确定性降级也按六节抽取组织（与 LLM 摘要同构，兼容路径同步升级）。
7. 5 个 vuln prompt 的 RECON_CONTEXT 说明措辞更新（去掉具体节号，与实际注入内容一致）。
8. 测试：真实规模 fixture（31 端点级）锁定覆盖率与节完整性；坏输出 fixtures 锁定解析容错。

**非目标（第一期不做）**

- **不做注入侧路由**：所有 vuln agent 拿完整分节摘要（分发差异化 = 第二期期权，§9）。
- 不改 `set_injection_sources` 的 6 桶分类骨架（加 SSRF/XSS 桶 = 改 recon agent 产出契约，观察后再定）。
- §5（input vectors）不进摘要输入（二期评估）。
- 不改摘要输出语言行为（跟随输入语言，`language` 缓存维度保留）。
- 不动 workflow 编排（digest 活动已就位）、不动双轨合并、不动 GitNexus 轨。

## 3. 现状锚点（实现时核对）

| 位置 | 现状 |
|---|---|
| `packages/core/src/supernova_core/agents/recon_context_summarizer.py` | `_SUMMARY_PROMPT`（§4+§8 提取指令）；`summarize_recon_context(recon_md, llm_client, *, fallback_on_error=True)`，输入 `recon_md[:8000]`；`_extract_sections` 匹配 `## 4.`/`## 8.`；`extract_recon_context_sections` 公开降级入口；`RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION = 1` |
| `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` | `run_recon_context_digest` 活动（缓存指纹 = source_hash + prompt_version + language；`require_llm` 判 `source=="llm-summary"`）；`_build_vuln_prompt_variables` 读 digest 注入 `RECON_CONTEXT`，缺失时走确定性抽取兼容路径 |
| `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py` | digest 活动挂在 recon agent 之后、vuln fan-out 之前；`_needs_recon_context_digest`（resume 跳过逻辑） |
| `packages/core/src/supernova_core/renderers/recon.py:35,39` | heading 由渲染器产出：`## {n}. {title}` / `### {num} {title}`——**结构稳定**，确定性抽取可依赖 |
| `prompts/vuln-{injection,xss,ssrf,authz,auth}.txt` | RECON_CONTEXT 段后注释 "(Auto-summarized from ... §4 + §8. ...)"（5 处，措辞需更新） |
| `packages/whitebox/tests/test_recon_context_digest.py` | 5 个测试（缓存命中/失效/降级升级/读注入/兼容路径），全部小 fixture |
| `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` | 双轨铁律锁定测试（本次改造不触碰其不变量，须保持绿） |
| 真实产物 | `workspaces/**/deliverables/whitebox/intermediate/recon_context_digest.json`（schema v1）；`recon_deliverable.md`（15 份统计样本） |

## 4. 设计

### 4.1 输入构造（core：`recon_context_summarizer.py`）

新增纯函数 `build_summarizer_input(recon_md: str) -> tuple[str, dict]`：

- 依次抽取并拼接：`§3`（`## 3.` → `## 4.`）、`§4`（→ `## 5.`）、`§6.3`（`### 6.3` → 下一个 `### ` 或 `## 7.`）、`§7`（→ `## 8.`）、`§8`（→ `## 9.`）、`§9`（→ 文末/下一个 `## `）。任一节缺失静默跳过（§6.3 无出站流时常见空）。
- 返回 `(拼接文本, 元数据)`；元数据含 `source_endpoint_rows`（§4 表行数：§4 文本中以 `|` 开头的行数 − 2，扣除表头与分隔行）。
- 防御上限 100K 字符：超出按原序截尾并置 `input_truncated=true`（真实最大样本 ~45K，纯防御；告警日志）。
- `summarize_recon_context()` 改为接收构造好的输入文本（或内部调用 `build_summarizer_input`），**删除 `[:8000]`**。
- `extract_recon_context_sections()`（降级入口）同步升级为同一抽取集合（六节同构）。

### 4.2 摘要 prompt（六节 + 硬约束）

`_SUMMARY_PROMPT` 重写（`RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION: 1 → 2`，版本指纹自动使旧 digest 失效）。要点：

```
Given the extracted recon sections, produce EXACTLY these six sections, in order,
each starting with its heading on its own line:

## endpoints — EVERY endpoint from the API inventory, one line each, terse format:
   METHOD /path (role, object-id param if any). No prose.
## authz — horizontal / vertical / context candidates (one line each: endpoint, missing
   or weak control, data sensitivity)
## injection — command / SQL / LFI·RFI / path-traversal / deserialization source leads
## xss — template-rendering / reflection / storage sink leads
## ssrf — outbound-request leads (including endpoints whose description mentions
   fetching user-controlled URLs; include network flows if present)
## auth — authentication-flow and role-architecture highlights

HARD CONSTRAINT: Only reorganize leads that are PRESENT in the input text. Do NOT
infer, speculate, or add any lead not explicitly written there. If a section has no
leads, output exactly "(none found)" under that heading. Never omit a heading.
```

- **职责边界**（与用户确认的三层分工）：recon=枚举、摘要=归类（零新增）、vuln agent=验证定性——硬约束是"摘要=图书管理员不是侦探"的落地。
- 六份短清单的压缩替代一份长清单的压缩（任务分解降漏行）；五类视角生成侧强制覆盖。
- 输出语言跟随输入语言（不加语言指令，维持现状）。

### 4.3 digest schema v2 与解析器（core + activities）

`recon_context_digest.json` schema：

```json
{
  "schema_version": 2,
  "source": "llm-summary | deterministic-extract | empty-recon",
  "degraded": false,
  "degraded_reason": "coverage_low | unsectioned | null",
  "source_hash": "…", "summarizer_prompt_version": 2, "language": "zh",
  "input_meta": {"source_endpoint_rows": 31, "input_chars": 15230, "input_truncated": false},
  "coverage": {"digest_endpoint_rows": 31, "coverage_ratio": 1.0},
  "missing_sections": [],
  "text": "<LLM 原始输出全量——解析层任何 bug 都不弄丢它>",
  "sections": {"endpoints": "…", "authz": "…", "injection": "…", "xss": "…", "ssrf": "…", "auth": "…"}
}
```

- `sections` 由纯函数 `parse_sections(raw: str) -> dict[str, str]` 派生：
  - 逐行匹配 `^#{2,3}\s+(.+?)$`，规范化（小写、去空白）后查别名表（`endpoint(s)/api/端点→endpoints` 等少量别名，六节各一组）；
  - 识别不了的段落挂 `_unparsed`（内容不丢弃，第一期全文注入下零信息损失）；
  - 漏节不补造，记录进 digest 顶层 `missing_sections` 列表（观测字段；非空即提示分节不完整，但不单独触发 degraded——degraded 判据以 coverage 与 unsectioned 为准）；
  - `_load_recon_context_digest` 的 `schema_version` 校验同步升为 `2`——存量 v1 digest 自然判 miss 重生成（与 `summarizer_prompt_version` 升版双保险）；
  - **一个节都识别不出 → 整体存为单节**（`sections` 为空 dict + `degraded=true, reason=unsectioned`），注入侧退 `text`。
- **对账**：`digest_endpoint_rows` = `sections["endpoints"]` 非空行数；`coverage_ratio = digest_endpoint_rows / max(source_endpoint_rows, 1)`；`coverage_ratio < 0.8`（模块级常量，不进 env）→ `degraded=true, reason=coverage_low`。
- `degraded` 语义（与用户确认的判据——**内容覆盖，不是格式美观**）：
  - `llm-summary` + 未 degraded → 有效缓存，resume 跳过；
  - `unsectioned` / `coverage_low` → degraded，`_load_recon_context_digest(require_llm=True)` 不认 → **resume 自动重试升级**（复用 `8f32899e` 机制；`require_llm` 判定从 `source=="llm-summary"` 改为 `source=="llm-summary" and not degraded`）。
  - 设计修正说明：讨论中曾说"unsectioned 不重试"，定稿改为 degraded 可升级——理由：未分节意味着五类强制扫描未发生（差异化价值归零），且 resume 重试成本仅几分钱。同一 scan 内不重试（只标 degraded 落盘），升级发生在下次 resume。

### 4.4 注入侧（activities：`_build_vuln_prompt_variables`）

- digest 存在且 `sections` 非空 → `RECON_CONTEXT` = 按固定节序（endpoints → authz → injection → xss → ssrf → auth → `_unparsed`）重组的分节文本；`sections` 为空 → 注入 `text` 全文。
- **第一期不按 agent_name 路由**——五个 agent 拿同一份完整分节摘要（分发差异化是二期）。
- digest 缺失的兼容路径 → `extract_recon_context_sections()`（已升级为六节抽取），维持"不触发 per-agent LLM 调用"的现状性质。
- `run_recon_context_digest` 活动内部：LLM 成功 → `text=raw, sections=parse_sections(raw)` + 对账定 degraded；LLM 失败/输出空 → `source=deterministic-extract`，`sections` 由六节确定性抽取直接构造（天然带节标题，与 LLM 摘要同构），`degraded=false`（确定性抽取是有效终态，非降级残次品——对账仅对 `llm-summary` 生效）。

### 4.5 5 个 vuln prompt 措辞（一行 × 5 文件）

`(Auto-summarized from {{DELIVERABLES_PATH}}/recon_deliverable.md §4 + §8. If empty, …)` → `(Auto-summarized from {{DELIVERABLES_PATH}}/recon_deliverable.md. If empty, the recon deliverable is the source of truth — read it directly.)`——去掉具体节号（注入内容已是六节），`{{RECON_CONTEXT}}` 占位符与全部方法论不动。

### 4.6 重试 / 降级 / resume 全链路（决策树）

```
LLM 调用失败（超时/API 错）
  → Temporal activity 异常 → retry_for("standard") 自动重试（既有）
  → 重试耗尽 → 确定性六节抽取落盘（source=deterministic-extract，非 degraded）
LLM 输出空/白
  → 同上确定性抽取
LLM 输出正常
  → parse_sections 分节 → 对账
      ├─ 分节成功 + coverage ≥ 0.8 → 有效 llm-summary 缓存
      ├─ 分节成功 + coverage < 0.8 → degraded(coverage_low)，resume 可升级
      └─ 完全未分节            → degraded(unsectioned)，text 全文注入，resume 可升级
digest 已存在（resume）
  ├─ 指纹命中且非 degraded → 跳过（含 deterministic-extract 终态）
  └─ degraded 且仍有未完成 vuln agent → 重新生成（升级）
```

## 5. 铁律合规（CLAUDE.md §1）

- 摘要输入**全部**来自 `recon_deliverable.md`（LLM 轨 recon agent 产物）——不引 GitNexus 确定性层任何产物；不新增 `@include` 确定性产物的 partial。
- `test_static_dataflow_hints_decoupling.py` 不变量不触碰，保持绿。
- vuln prompt 只改一行说明措辞，方法论与 sink 清单不动；LLM 轨自给自足性质不变（vuln agent 仍以 recon 原文为 SoT，摘要只是先验）。

## 6. 权衡记录

| 决策 | 取 | 舍 | 理由 |
|---|---|---|---|
| 输入全量抽取 vs 维持 8000 | 覆盖完整 | 摘要调用 token 涨 ~10×（¥0.005→~¥0.05） | 摘要占 scan 成本 0.1%；涨后 ~1%；现状的"便宜"是残缺换来的 |
| 分节生成 vs 单一文本 | 五类覆盖有结构保障、可对账 | 摘要 prompt 略复杂、需解析容错 | 六份短清单压缩漏行率低于一份长清单；最坏退化=旧行为 |
| markdown 节 vs JSON 输出 | 局部失败局部隔离 | — | JSON 一处转义错全挂；与 chain_verdict"自由文本+宽容解析"先例一致 |
| 第一期不路由 | 出错面最小（无路由/无 prompt 大改） | 每 agent 多背 ~几 K 字符他类视图 | 注入开销 <1%；路由是纯注入侧改动，二期免返工 |
| 纯代码解析 vs LLM 校验 | 确定性、可测试、零成本 | — | 不引入二阶 LLM 依赖；坏输出 fixtures 直接单测 |
| unsectioned 判 degraded | resume 可升级 | 讨论中曾议"不重试" | 五类扫描未发生≈差异化价值归零；重试成本几分钱 |

## 7. 测试与验收

1. **core 单测（summarizer）**：`build_summarizer_input` 对真实规模 fixture（31 端点 NodeGoat 副本）断言六节齐全、`source_endpoint_rows=31`、无截断；`parse_sections` 坏输出 fixtures（节名变体/漏节/散文/截断/自创节）逐个断言容错行为；`extract_recon_context_sections` 六节同构。
2. **whitebox 测试（扩展 `test_recon_context_digest.py`）**：
   - LLM 成功路径落 `schema_version=2`、sections 齐全、coverage 达标非 degraded；
   - LLM 输出仅 4 端点（复刻真实残缺）→ `coverage_low` degraded → `require_llm=True` 不认 → resume 升级路径触发；
   - 输出不分节 → `unsectioned` degraded、注入退 `text`；
   - LLM 失败 → `deterministic-extract` 六节 sections、非 degraded、resume 跳过；
   - `summarizer_prompt_version=2` 使 v1 旧 digest 缓存失效；
   - 兼容路径（digest 缺失）注入六节抽取且零 LLM 调用。
3. **真实规模验收**：以脚本/单测方式对 NodeGoat 20260901 的真实 `recon_deliverable.md` 重放 digest 生成（不经 Temporal，直接调 `summarize_recon_context` + 解析落盘逻辑），人工核对六节内容——每行可在原文找到出处（硬约束抽检）、`endpoints` 节 ≥ 31 行、`ssrf` 节含 `/research`（§4 描述列重组的实证）。
4. **既有测试保持绿**：`test_recon_context_digest.py` 旧 5 测试按新 schema 适配断言；`test_static_dataflow_hints_decoupling.py` 原样通过。

## 8. 风险

- **LLM 分节遵从率**：六节+硬约束下仍可能漂移——缓解：别名表+漏节观测+degraded 升级；真实规模验收（§7.3）先行把关。
- **coverage 阈值 0.8 的标定**：过高会把正常摘要误判 degraded（多 resume 重试）；过低失去对账意义——首版 0.8，依据 §7.3 真实重放结果可调（模块常量，改起来零成本）。
- **摘要行数≠端点数的口径差**：LLM 可能把同端点多方法并作一行——coverage 低估。首版接受（宁可多一次 resume 升级，不放过真残缺）。
- **输入 100K 防御上限触发时**（尚无真实样本）截尾可能丢 §9——`input_truncated` 元数据可观测，出现真实样本再设计节优先级。

## 9. 第二期期权（本期不做，不返工承诺）

注入侧按 `agent_name` 路由：`RECON_CONTEXT = endpoints 节 + 本类节`（activities 已知 agent_name，纯注入侧改动，digest schema/管道/测试不动）。**触发条件**：第一期真实扫描观察各节质量——若 xss/ssrf 节持续偏薄（重组质量不足），升级 recon 侧 `set_injection_sources` 分类骨架（加 SSRF/XSS 桶）优先于路由。
