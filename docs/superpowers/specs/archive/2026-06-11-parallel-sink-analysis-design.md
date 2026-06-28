# Parallel Sink Analysis Design Spec

> 重构 Shannon-py 的 Sink 识别从"确定性先行 → LLM 后行"改为"确定性 ∥ LLM 并行"，并恢复原始项目的模板分析方法论。

**日期**：2026-06-11

---

## 1. 背景与动机

当前管线中 `run_code_index`（确定性 AST 分析）和 `run_agent(PRE_RECON)`（LLM 分析）是**串行**的：code_index 先跑完，产物作为 hint 注入 PRE_RECON prompt。

问题：
1. **模板 Sink 检测双层落空** — 确定性层不扫描模板文件内容；PRE_RECON prompt 删了原始项目的强制两步模板分析方法论
2. **串行浪费** — 确定性分析和 LLM 分析没有数据依赖（原始项目本来就没有确定性层），可以并行
3. **差距** — 原始项目的模板分析方法论（glob 枚举 → 逐文件转义模式区分 + 变体验证 + Coverage Audit 表）是重构版最大的单项能力缺失

详见 `docs/sink-gap-analysis-v2.md` SK-1。

## 2. 目标

1. `code_index`（确定性）和 `PRE_RECON`（LLM）**并行执行**
2. PRE_RECON 中恢复原始项目的**模板分析方法论**
3. 两路产物**简单合并去重**

## 3. 设计

### 3.1 管线并行改造

**文件**：`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

**当前**（`workflows.py:74-122`，串行）：
```python
# Step 1: code_index 先跑
code_index_result = await workflow.execute_activity(
    activities.run_code_index, act_input,
    start_to_close_timeout=timedelta(minutes=10),
)
# ... save_adjudication, rebuild_call_chains ...
# Step 2: PRE_RECON 后跑
pre_recon_input = ActivityInput(...)
metrics = await workflow.execute_activity(
    activities.run_agent, pre_recon_input,
    start_to_close_timeout=timedelta(hours=2),
)
```

**改为**（并行）：
```python
# 并行启动 code_index 和 PRE_RECON
pre_recon_input = ActivityInput(**{**act_input.__dict__,
                                    "workspace_name": AgentName.PRE_RECON.value})
code_index_task = workflow.execute_activity(
    activities.run_code_index, act_input,
    start_to_close_timeout=timedelta(minutes=10),
)
pre_recon_task = workflow.execute_activity(
    activities.run_agent, pre_recon_input,
    start_to_close_timeout=timedelta(hours=2),
    retry_policy=PRODUCTION_RETRY,
)
code_index_result, pre_recon_metrics = await asyncio.gather(
    code_index_task, pre_recon_task,
)
```

**下游调整**：
- `run_save_adjudication`、`run_rebuild_call_chains`、`run_render_dataflow_hints`、`run_agent(RECON)` 等步骤仍在 gather 之后串行执行，顺序不变
- PRE_RECON 不再依赖 `_static-dataflow-hints.txt`。删除 `pre-recon-code.txt` 中对 `{{STATIC_DATAFLOW_HINTS}}` 的引用。原始项目的 Sink Hunter 本来就没有确定性 hint，不影响能力

### 3.2 恢复模板分析方法论

**文件**：`prompts/pre-recon-code.txt`

从原始项目 `apps/worker/prompts/pre-recon-code.txt:129-136,276-284` 恢复以下三块内容：

#### 3.2.1 强制两步流程

插入到重构版 Sink Hunter Agent 描述区域，替换当前的单句泛泛指令：

```
**Step 1 — Template File Inventory (glob enumeration):**
"Enumerate ALL template and view files in the project using glob patterns.
 Cover common template extensions: html, ejs, hbs, pug, jsx, tsx, vue, svelte,
 php, erb, jinja2, tmpl, and any additional template extensions discovered
 during analysis. Organize the inventory as a directory tree showing every
 template file path. If no template files exist, explicitly report
 'no template files found' and skip Step 2 for templates."

**Step 2 — Per-File Sink Analysis with Escaping Mode Distinction:**
"For EACH template/view file discovered in Step 1, independently analyze it
 for dangerous sinks. For server-side template engines, distinguish between
 escaping modes: escaped directives (e.g., EJS `<%= %>`, Jinja2 `{{ }}`)
 vs unescaped directives (e.g., EJS `<%- %>`, Jinja2 `{{|safe}}`).
 Flag bare unescaped output without JSON.stringify wrappers as highest-risk.
 Also analyze non-template sinks: XSS sinks (innerHTML, document.write),
 SQL injection points, command injection (exec, system),
 file inclusion/path traversal (fopen, include, require, readFile),
 and deserialization sinks (pickle, unserialize, readObject).
 Provide exact file locations with line numbers."

**Cross-Variant Verification (MANDATORY):**
"When template files exist in variant directories (brands, locales, themes,
 sub-applications), you MUST check for equivalent template files across ALL
 variant directories. For example, if `views/brandA/header/variables.html`
 is found, you MUST verify whether `views/brandB/header/variables.html`
 exists and analyze it independently. NEVER assume variant templates are
 identical — each file MUST be analyzed separately with its own sink report."
```

#### 3.2.2 Coverage Audit 表

插入到 Sink Hunter 输出格式区域：

```
**Template Coverage Audit (MANDATORY):**
Before listing individual sinks, include a "Template Coverage Audit" table
listing every template/view file discovered during Step 1 of the Sink Hunter
Agent, with its sink count and analysis status. Format:

| Template File | Sink Count | Escaping Modes | Analysis Status |
|---|---|---|---|
| `views/brandA/header/variables.html` | 3 | 1 unescaped, 2 escaped | Analyzed |
| `views/brandB/header/variables.html` | 2 | 2 unescaped | Analyzed |
| `views/brandC/header/variables.html` | - | - | NOT ANALYZED |

Any file with "NOT ANALYZED" status indicates a coverage gap that MUST be
resolved before finalizing the report. The table MUST include ALL template
files from the Step 1 inventory, not just those with sinks.
```

#### 3.2.3 保留非模板 sink 指令

当前重构版的泛泛指令 `:142` 仍保留，用于非模板类 sink（SQL、命令注入、反序列化等）的兜底检测。

### 3.3 结果合并

**策略**：简单去重，按 `(file_path, line)` 匹配。

#### 3.3.1 产物格式

| 来源 | 产物格式 | 内容 |
|---|---|---|
| 确定性层 | `list[SinkCallSite]` | Pydantic 模型：file_path, line, rule_id, category, slots |
| LLM 层 | 自由文本报告 | Sink 发现列表（含 file:line）+ Coverage Audit 表 |

#### 3.3.2 合并时机

新增合并步骤，在 `asyncio.gather()` 完成后、`run_save_adjudication` 之前：

```
code_index ──┐
             ├─→ merge_sink_reports() ─→ save_adjudication ─→ rebuild_chains ─→ RECON
PRE_RECON ──┘
```

#### 3.3.3 合并逻辑

新增 `merge_sink_reports()` 函数（约 30-50 行），放在 `packages/core/src/shannon_core/code_index/` 包内：

1. 解析 LLM 报告中的 sink 位置（正则匹配 `file_path:line_number` 模式）
2. 与确定性 `SinkCallSite[]` 按 `(file_path, line)` 去重
3. 仅 LLM 发现的 sink 附加到 `SinkCallSite[]`：
   - `id`: `f"llm:{file_path}:{line}"`
   - `rule_id`: `"llm-sink-hunter"`
   - `category`: 从 LLM 报告中 sink 附近的分类关键词推断（"SQL injection" → SQL, "command" → COMMAND, "SSRF" → SSRF, "XSS" → XSS, "template" → TEMPLATE, "file include/path traversal" → FILE, "deserialize" → DESERIALIZATION）；无法推断时 fallback 为 `GENERIC`
   - `needs_review`: `True`（LLM 发现的 sink 默认需确认）
4. Coverage Audit 表作为独立产物保存，传递给下游 RECON 和 vuln agents

**去重规则**：
- 确定性已检测到的 `(file_path, line)` → 保留确定性记录，丢弃 LLM 重复
- 仅 LLM 检测到的 → 新增 `SinkCallSite`，标记 `source=LLM`

## 4. 改动文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 修改 | code_index ∥ PRE_RECON 并行；新增 merge 步骤 |
| `prompts/pre-recon-code.txt` | 修改 | 恢复模板两步流程 + 变体验证 + Coverage Audit 表 |
| `packages/core/src/shannon_core/code_index/sink_merger.py` | 新增 | `merge_sink_reports()` 合并函数 |

## 5. 不在范围内

- **不**新增确定性 SinkRule（SSRF/XSS/XXE/路径穿越的确定性规则扩展见 `sink-gap-analysis-v2.md`，属于独立的后续 spec）
- **不**改变确定性层本身（`sink_detector.py` 不动）
- **不**给 LLM Sink Hunter 增加结构化输出 Schema（保持自由文本报告）
- **不**新增独立 agent（在 PRE_RECON 内恢复 Sink Hunter 子任务即可）

## 6. 测试要点

1. **管线并行**：验证 `workflows.py` 中 gather 正确处理两路异常（一方失败不阻塞另一方）
2. **模板方法论**：用含 `.ejs`/`.jinja2` 模板的项目测试，验证 LLM 产出两步报告 + Coverage Audit 表
3. **合并去重**：构造同时被确定性和 LLM 检测到的 sink，验证去重逻辑
4. **仅 LLM 发现**：构造模板文件内的 XSS（如 `<%- userData %>`），验证 LLM 能发现但确定性不能，且合并后保留
5. **回归**：验证并行改造后下游（save_adjudication → rebuild_chains → RECON → vuln agents）流程不受影响
