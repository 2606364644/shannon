# 双轨解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 拆除 shannon-py 双轨中"确定性层产物 → LLM 轨 prompt"的过程层注入（CLAUDE.md §1 铁律），让 LLM 轨（pre-recon / recon / vuln-* / audit-tier1）回归纯 LLM 自给，合并只发生在产物层。

**Architecture:** 三层拔除每个注入点——(1) LLM 轨 prompt 正文占位符段、(2) `activities.py` 的 `prompt_variables` 注入、(3) renderer 函数本身 + 其连带测试。死占位符/死代码（#4/#5/#7）一并清。反方向 fusion（合规的产物层合并）加 `enable_llm_track` 显式守卫。扩解耦测试锁死防回退。底层确定性 JSON（`code_index.json` / `parameter_graph.json` / `framework_analysis.json`）全部保留，GitNexus 轨继续独立直读。

**Tech Stack:** Python 3.x、pytest、temporalio（workflow/activity）、pydantic。

## Global Constraints

- **铁律（CLAUDE.md §1）**：不得重建任何"确定性产物 → LLM 轨 prompt"桥梁（含 `@include`、正文占位符、`prompt_variables` 注入）。本计划是拆除，不是重构。
- **底层 JSON 不动**：`code_index.json` / `parameter_graph.json` / `framework_analysis.json` 及其消费者（`chain_verdict.py` / `vuln_chain_builders/*` / `authz_gitnexus_track.py`）一行不改。
- **白名单**：`authz_gitnexus_judge` 的 `AUTHZ_GITNEXUS_CANDIDATES`（`activities.py:242-247`）是 GitNexus 轨**内部** LLM 判定，合法，不删、测试不误伤。
- **删除顺序**：每个 renderer 清理任务内，必须**先**断 `activities.py` 调用 → **再**删 renderer `.py` → **再**删其测试文件。顺序颠倒会导致中间态 import 失败。
- **不碰在途改动**：working tree 里 `test_summary_fix_verification.py` 删除 / `scripts/verify_summary_fix.py` 新增与本计划无关，`git add` 只 add 本计划改动的文件。
- **测试范围**：只跑改动相关测试子集（`packages/core/tests/code_index/`、`packages/core/tests/prompts/`、`packages/core/tests/services/`、`packages/core/tests/test_agents.py`、`packages/whitebox/tests/` 相关项），**勿跑全套**（CLAUDE.md §3：全套会 hang 在 Temporal/网络慢测试）。
- **定位用语义边界**：prompt 文件删除步骤给出**要删的原文段**，按内容匹配定位（行号会随编辑漂移），不要按行号硬删。

## File Structure

**Create:**
- `packages/core/src/shannon_core/code_index/patterns.py` —— 收纳从 `recon_gitnexus_track.py` 迁出的 `_OWNERSHIP_PREDICATE_RE`（专放编译后正则常量）。

**Modify:**
- `prompts/pre-recon-code.txt` —— 删 Phase 0 整块（#1 + #5 + 引导文字）。
- `prompts/recon.txt` —— 删 `<parameter_propagation_data>`（#4）、`<recon_gitnexus_track>`（#2）、`<framework_endpoints_deterministic>`（#3）三段 + 修一句悬空引用。
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` —— 删 `prompt_variables` 的 3 个确定性 key 注入。
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` —— 两个 fusion 调用包进 `if input.enable_llm_track:` 守卫。
- `packages/core/src/shannon_core/models/agents.py` —— 删 `AUDIT_TIER1` 枚举值 + AGENTS 条目 + BROWSER_SESSION_MAPPING + AGENT_PHASE_MAP（#7）。
- `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py` —— 改 `_OWNERSHIP_PREDICATE_RE` 的 import 来源（迁后）。
- `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` —— 扩解耦测试（黑名单 + 注入禁止 + 白名单）。
- `packages/core/tests/test_agents.py` —— 删 `test_audit_tier1_agent_registered`。

**Delete:**
- `packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py`（#1 renderer）
- `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`（#2 renderer，迁出正则后）
- `packages/core/src/shannon_core/services/framework_endpoint_renderer.py`（#3 renderer）
- `packages/core/src/shannon_core/code_index/audit_input_builder.py`（#7 死代码 renderer）
- `prompts/audit-tier1.txt`（#7 死 prompt）
- `packages/core/tests/code_index/test_pre_recon_gitnexus_track.py`
- `packages/core/tests/code_index/test_recon_track_integration.py`
- `packages/core/tests/code_index/test_recon_build_track.py`
- `packages/core/tests/services/test_framework_endpoint_renderer.py`
- `packages/core/tests/code_index/test_audit_input_builder.py`

---

### Task 1: pre-recon 清理（#1 PRE_RECON_GITNEXUS_TRACK + #5 元数据 + Phase 0 引导）

**Files:**
- Modify: `prompts/pre-recon-code.txt`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:126-133`
- Delete: `packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py`
- Delete: `packages/core/tests/code_index/test_pre_recon_gitnexus_track.py`
- Delete: `packages/core/tests/code_index/test_pre_recon_track_integration.py`
- Test: `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces: 解耦测试新增 `test_no_llm_track_prompt_has_forbidden_placeholders` 断言（后续任务累积扩黑名单）；`activities.py` 的 PRE_RECON 分支不再 import `pre_recon_gitnexus_track`。

- [ ] **Step 1: 写失败测试（pre-recon 黑名单 + 注入禁止）**

在 `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 末尾追加：

```python
# CLAUDE.md §1 铁律：LLM 轨 prompt 正文不得出现确定性 track 占位符（过程层注入）。
# 这些占位符由 renderer 把确定性 JSON 加工成 markdown 喂给 LLM 轨，破坏独立性。
FORBIDDEN_PLACEHOLDERS = {
    "PRE_RECON_GITNEXUS_TRACK",      # #1 pre-recon ← 本任务
    "RECON_GITNEXUS_TRACK",          # #2 recon
    "FRAMEWORK_ENDPOINTS_SUMMARY",   # #3 recon
    "TAINT_FLOW_SUMMARY",            # #4 recon（死占位符）
    "CHAIN_AUDIT_INPUT",             # #7 audit-tier1（死占位符）
    "VULN_CLASSES_TESTED",           # #7 audit-tier1（死占位符）
    # #5 pre-recon Phase 0 元数据占位符群
    "TOTAL_CHAINS", "AVG_CHAIN_DEPTH", "MAX_CHAIN_DEPTH", "UNRESOLVED_COUNT",
    "TOTAL_FILES", "INDEXED_SOURCE_FILES", "TEMPLATE_FILE_COUNT",
    "SCHEMA_FILE_COUNT", "CONFIG_FILE_COUNT", "DEGRADATION_WARNING_OR_NONE",
}

# 白名单：GitNexus 轨内部 LLM 判定（authz_gitnexus_judge）合法消费确定性 IDOR 候选，
# 属轨内判定（等同 chain_verdict），不是"确定性→LLM 轨"跨轨注入。
WHITELISTED_PLACEHOLDERS = {"AUTHZ_GITNEXUS_CANDIDATES"}


def test_no_llm_track_prompt_has_forbidden_placeholders():
    offenders = []
    for p in PROMPTS_DIR.rglob("*.txt"):
        text = p.read_text()
        for token in FORBIDDEN_PLACEHOLDERS:
            if "{{" + token + "}}" in text:
                offenders.append(f"{p.relative_to(PROMPTS_DIR)}: {{{{{token}}}}}")
    assert not offenders, (
        f"CLAUDE.md §1 violation — LLM-track prompts still embed deterministic "
        f"track placeholders (process-layer coupling): {sorted(offenders)}"
    )
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py::test_no_llm_track_prompt_has_forbidden_placeholders -v`
Expected: FAIL，offenders 含 `pre-recon-code.txt: {{PRE_RECON_GITNEXUS_TRACK}}`、`{{TOTAL_CHAINS}}` 等。

- [ ] **Step 3: 删 prompts/pre-recon-code.txt 的 Phase 0 整块**

删掉以下整块（从 `## Phase 0: Code Index Review` 标题到 `</pre_recon_gitnexus_track>` 闭合标签，含中间引导文字与 `<pre_recon_gitnexus_track>` 段）：

```
## Phase 0: Code Index Review (MUST complete before Phase 1)

Review the deterministic code index data provided below:

1. Read `code_index.json` and review the call chain statistics and file coverage data
2. Note any coverage gaps or degradation warnings
3. Use this understanding to inform your Phase 1 and Phase 2 analysis

Entry point adjudication is handled automatically downstream — you do NOT need to write entry_points.json.

<pre_recon_gitnexus_track>
{{PRE_RECON_GITNEXUS_TRACK}}

**填充规则**：上表为 GitNexus + AST 确定性检测的 entry points / sinks / 模板转义。
- 这些是**确定性下限**：你的 Sink Hunter / Entry Point Mapper 必须**覆盖**这些（逐条确认 network-reachable / render context）。
- **下限非上限**：确定性未列出的 sink/entry/模板不代表不存在——仍须独立探索（glob 模板、grep 危险 API、变体目录），尤其未覆盖语言/动态调用。
- 模板转义段的 unescaped 项是**高危**，优先分析。
</pre_recon_gitnexus_track>
```

删完后，在 `## Phase 1: Discovery Agents (Launch in Parallel)` 标题**正下方**补回保留指令（entry point adjudication 是行为指令，不依赖确定性产物）：

```
> Entry point adjudication is handled automatically downstream — you do NOT need to write entry_points.json.
```

- [ ] **Step 4: 删 prompts/pre-recon-code.txt 的 `<phase0_data>` 段**

删掉以下整块（#5 元数据占位符群所在段）：

```
<phase0_data>
## Phase 0 Coverage Data (from code_index.json + file_manifest.json)

### Call Chain Statistics
- Total chains: {{TOTAL_CHAINS}}
- Average depth: {{AVG_CHAIN_DEPTH}}
- Max depth: {{MAX_CHAIN_DEPTH}}
- Unresolved calls: {{UNRESOLVED_COUNT}}

### File Coverage
- Source files (indexed): {{INDEXED_SOURCE_FILES}}
- Template files: {{TEMPLATE_FILE_COUNT}}
- Config files: {{CONFIG_FILE_COUNT}}
- Schema files: {{SCHEMA_FILE_COUNT}}
- Total: {{TOTAL_FILES}}

### Degradation Status
{{DEGRADATION_WARNING_OR_NONE}}

Use this data to cross-reference your findings. If Phase 0 detected entry points or chains that you don't mention in your analysis, explain why they were excluded.
</phase0_data>
```

- [ ] **Step 5: 删 activities.py 的 PRE_RECON prompt_variables 注入**

`packages/whitebox/src/shannon_whitebox/pipeline/activities.py`，把 PRE_RECON 分支：

```python
        if agent_name == AgentName.PRE_RECON:
                    from shannon_core.code_index.pre_recon_gitnexus_track import build_pre_recon_gitnexus_track

                    prompt_variables = prompt_variables or {}
                    prompt_variables["pre_recon_gitnexus_track"] = build_pre_recon_gitnexus_track(
                        repo,
                        deliverables,
                    )
```

改为（只保留 dict 初始化，去掉确定性产物 import 与注入）：

```python
        if agent_name == AgentName.PRE_RECON:
                    prompt_variables = prompt_variables or {}
```

- [ ] **Step 6: 删 renderer 与其测试**

```bash
git rm packages/core/src/shannon_core/code_index/pre_recon_gitnexus_track.py
git rm packages/core/tests/code_index/test_pre_recon_gitnexus_track.py
git rm packages/core/tests/code_index/test_pre_recon_track_integration.py
```

- [ ] **Step 7: 跑测试确认 pre-recon 相关断言转绿（recon 占位符仍红，预期）**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: `test_no_llm_track_prompt_has_forbidden_placeholders` 仍 FAIL（因 recon.txt 还有 #2/#3/#4 占位符），但 offenders **不再含** `pre-recon-code.txt` 任何项。`test_no_prompt_includes_static_dataflow_hints`（旧 @include 断言）仍 PASS。

- [ ] **Step 8: 确认无残余 import 断链 + 轻量回归**

Run: `grep -rn "pre_recon_gitnexus_track\|build_pre_recon_gitnexus_track\|render_pre_recon_gitnexus_track" packages/ --include="*.py"`
Expected: 无命中（activities.py 的 import 已在 Step 5 删，renderer 已在 Step 6 删）。若有命中——处理残余引用后再继续。

Run: `pytest packages/whitebox/tests/ -k "pre_recon or prerecon" -v`
Expected: PASS（whitebox pre-recon 流程不依赖被删 renderer）。

- [ ] **Step 9: Commit**

```bash
git add prompts/pre-recon-code.txt packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py
git commit -m "feat(decouple): remove PRE_RECON_GITNEXUS_TRACK + Phase0 metadata injection (#1,#5)"
```

---

### Task 2: recon #2 RECON_GITNEXUS_TRACK 清理 + _OWNERSHIP_PREDICATE_RE 迁移

**Files:**
- Create: `packages/core/src/shannon_core/code_index/patterns.py`
- Modify: `prompts/recon.txt`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:122-124`
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py:70`
- Delete: `packages/core/src/shannon_core/code_index/recon_gitnexus_track.py`
- Delete: `packages/core/tests/code_index/test_recon_track_integration.py`
- Delete: `packages/core/tests/code_index/test_recon_build_track.py`

**Interfaces:**
- Consumes: Task 1 的解耦测试框架（`FORBIDDEN_PLACEHOLDERS` 已含 `RECON_GITNEXUS_TRACK`）。
- Produces: `patterns.py` 暴露 `OWNERSHIP_PREDICATE_RE`（去前导下划线，作为公共常量），供 `authz_gitnexus_track.py` import。

- [ ] **Step 1: 建 patterns.py，迁入正则**

创建 `packages/core/src/shannon_core/code_index/patterns.py`：

```python
"""Compiled regex patterns shared across code_index modules.

Lives here (not in recon_gitnexus_track.py) so it survives the removal of the
recon GitNexus track renderer — authz_gitnexus_track still consumes it.
"""
import re

# Detects ownership/authorization predicates in handler source code (e.g.
# `where user_id = req.user.id`, `findByOwnerId`). Used by authz candidate
# detection (GitNexus-track internal), NOT fed to the LLM track.
OWNERSHIP_PREDICATE_RE = re.compile(
    r"(?is)("
    r"where\s*[:(][^}\n;]{0,240}\b(user_?id|owner_?id|owner|creator_?id|author_?id)\b"
    r"[^}\n;]{0,240}(req\.user|ctx\.state\.user|currentUser|user\.id|userId)"
    r"|\.where\s*\(\s*['\"]?(user_?id|owner_?id|owner|creator_?id|author_?id)['\"]?\s*[,=]"
    r"|\bfindBy(Owner|OwnerId|UserId|CreatorId|AuthorId)\b"
    r"|\b(owner|currentUser|req\.user|ctx\.state\.user)\s*\.\s*id\b"
    r"|\b(user_?id|owner_?id)\s*=\s*(req|ctx|currentUser)"
    r")"
)
```

- [ ] **Step 2: authz_gitnexus_track.py 改 import 来源**

`packages/core/src/shannon_core/code_index/authz_gitnexus_track.py:70`，把：

```python
    from shannon_core.code_index.recon_gitnexus_track import _OWNERSHIP_PREDICATE_RE
```

改为：

```python
    from shannon_core.code_index.patterns import OWNERSHIP_PREDICATE_RE
```

同文件下一行（原 `return _OWNERSHIP_PREDICATE_RE.search(...)`）相应改为 `OWNERSHIP_PREDICATE_RE.search(...)`。

- [ ] **Step 3: 写迁移正确性测试（先红）**

`packages/core/tests/code_index/test_patterns.py`（新建）：

```python
from shannon_core.code_index.patterns import OWNERSHIP_PREDICATE_RE


def test_ownership_predicate_matches_user_id_where():
    src = "const item = await Model.where('user_id', req.user.id)"
    assert OWNERSHIP_PREDICATE_RE.search(src) is not None


def test_ownership_predicate_matches_find_by_owner():
    src = "await repo.findByOwnerId(ctx.state.user.id)"
    assert OWNERSHIP_PREDICATE_RE.search(src) is not None


def test_ownership_predicate_no_false_positive_on_plain_code():
    src = "function add(a, b) { return a + b; }"
    assert OWNERSHIP_PREDICATE_RE.search(src) is None
```

- [ ] **Step 4: 跑迁移测试确认 PASS（验证正则搬对）**

Run: `pytest packages/core/tests/code_index/test_patterns.py -v`
Expected: 3 PASS。

- [ ] **Step 5: 删 prompts/recon.txt 的 `<recon_gitnexus_track>` 段**

删掉 `### 4.1 Shared Controller Route Groups` 标题下的整段（含占位符与填充规则）：

```
<recon_gitnexus_track>
{{RECON_GITNEXUS_TRACK}}

**填充规则**：上方「§4.1 Shared Route Groups」表为 GitNexus 调用图反推的确定性路由组（同 handler 多路由）。你的 §4.1 表须据此填充这些组 + **独立探索其他共享 handler 的路由组**（下限非上限）。**Auth 冲突取「无」**：若确定性轨标某路由 **none** auth 或未清晰认证，合并取 none（pre-auth variant）。
</recon_gitnexus_track>
```

保留 `### 4.1 Shared Controller Route Groups` 标题与其后的正常指引文字（"When multiple routes map to the same handler function..."）。

- [ ] **Step 6: 删 activities.py 的 recon_gitnexus_track 注入**

`activities.py` RECON 分支内，删掉这两段：

```python
                    from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track

                    prompt_variables["recon_gitnexus_track"] = build_recon_gitnexus_track(str(deliverables))
```

- [ ] **Step 7: 删 renderer 与其测试**

```bash
git rm packages/core/src/shannon_core/code_index/recon_gitnexus_track.py
git rm packages/core/tests/code_index/test_recon_track_integration.py
git rm packages/core/tests/code_index/test_recon_build_track.py
```

- [ ] **Step 8: 跑测试（#2 断言转绿，#3/#4 仍红，预期）**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v && pytest packages/core/tests/code_index/test_patterns.py -v && pytest packages/core/tests/code_index/ -k "authz" -v`
Expected: 解耦测试仍 FAIL（recon.txt 还有 #3 `FRAMEWORK_ENDPOINTS_SUMMARY`、#4 `TAINT_FLOW_SUMMARY`），但 offenders 不再含 `RECON_GITNEXUS_TRACK`。patterns 测试 PASS。authz 测试 PASS（正则迁移无回归）。

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/code_index/patterns.py packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_patterns.py prompts/recon.txt packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat(decouple): remove RECON_GITNEXUS_TRACK injection; migrate OWNERSHIP_PREDICATE_RE to patterns.py (#2)"
```

---

### Task 3: recon #3 FRAMEWORK_ENDPOINTS_SUMMARY 清理

**Files:**
- Modify: `prompts/recon.txt`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:112-120`
- Delete: `packages/core/src/shannon_core/services/framework_endpoint_renderer.py`
- Delete: `packages/core/tests/services/test_framework_endpoint_renderer.py`

**Interfaces:**
- Consumes: Task 1 解耦测试框架（`FORBIDDEN_PLACEHOLDERS` 已含 `FRAMEWORK_ENDPOINTS_SUMMARY`）。
- Produces: 无新接口；RECON 分支不再 import `framework_endpoint_renderer`。

- [ ] **Step 1: 删 prompts/recon.txt 的 `<framework_endpoints_deterministic>` 段**

删掉 `## 4.2 Endpoint Security Context` 标题下的整段：

```
<framework_endpoints_deterministic>
{{FRAMEWORK_ENDPOINTS_SUMMARY}}

**填充规则**：上表为 framework-analyzer 确定性检测的框架自动生成端点。§4.2 表中这些端点的 **Framework Origin** 列据此填充（finale-rest/epilogue auto-generated）；其 Auth/Middleware/Ownership Check 仍须你独立核实。
**下限非上限**：确定性未列出的端点不代表无框架端点——仍须独立检查路由定义中的框架使用。
</framework_endpoints_deterministic>
```

- [ ] **Step 2: 修 recon.txt 悬空引用句**

删掉 `</framework_endpoints_deterministic>` 之后那句对已删 track 的引用（把整句删除，因其指向的 `<recon_gitnexus_track>` 与 `<framework_endpoints_deterministic>` 都已不存在）：

```
**§4.2 确定性 Auth/Middleware/Ownership**：见上方 `<recon_gitnexus_track>` 区的「§4.2 Endpoint Security Context」表（GitNexus 源码扫描）。Auth/Middleware/Ownership 据此填充 + 独立核实其他端点。**字段冲突取危险侧**：Auth 任一 none→none；Ownership 任一 none→none；Framework Origin 任一 auto-generated→auto-generated（见 `{{FRAMEWORK_ENDPOINTS_SUMMARY}}`）。Ownership = `guarded` 为候选，须语义确认。
```

删后该处直接衔接 `For every endpoint in Section 4, you MUST also provide an Endpoint Security Context entry ...`。

- [ ] **Step 3: 删 activities.py 的 framework_endpoints_summary 注入**

`activities.py` RECON 分支内，删掉整段（含 import、读 framework_analysis.json、`_to_endpoint` 列表构造、赋值）：

```python
                    from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints

                    data = json.loads(framework_analysis_path.read_text())
                    endpoints = [
                        _to_endpoint(endpoint)
                        for endpoint in data.get("inferred_endpoints", [])
                        if isinstance(endpoint, dict)
                    ]
                    prompt_variables["framework_endpoints_summary"] = render_framework_endpoints(endpoints)
```

同时删掉 `framework_analysis_path = deliverables / "framework_analysis.json"` 与 `if framework_analysis_path.exists():` 这两行（它们只服务于已删注入）。改后 RECON 分支仅剩：

```python
        if agent_name == AgentName.RECON:
            prompt_variables = {}
```

> 若 `_to_endpoint` 函数此时变为无引用死代码——`grep -rn "_to_endpoint" packages/ --include="*.py"` 确认仅本处定义、无调用方——一并删除其定义。

- [ ] **Step 4: 确认 framework_endpoint_renderer 无其它消费者**

Run: `grep -rn "framework_endpoint_renderer\|render_framework_endpoints" packages/ --include="*.py"`
Expected: 只剩 `test_framework_endpoint_renderer.py`（即将删）。若还有别的生产引用——停，评估是否真死代码。

- [ ] **Step 5: 删 renderer 与其测试**

```bash
git rm packages/core/src/shannon_core/services/framework_endpoint_renderer.py
git rm packages/core/tests/services/test_framework_endpoint_renderer.py
```

- [ ] **Step 6: 跑测试（#3 断言转绿，仅 #4/#7 仍红）**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v && pytest packages/core/tests/services/ -v`
Expected: 解耦测试 offenders 不再含 `FRAMEWORK_ENDPOINTS_SUMMARY`（仍 FAIL 于 `TAINT_FLOW_SUMMARY` / `CHAIN_AUDIT_INPUT`）。services 测试无 `ImportError`。

- [ ] **Step 7: Commit**

```bash
git add prompts/recon.txt packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat(decouple): remove FRAMEWORK_ENDPOINTS_SUMMARY injection + renderer (#3)"
```

---

### Task 4: #4 TAINT_FLOW_SUMMARY + #7 audit-tier1 死代码清理

**Files:**
- Modify: `prompts/recon.txt`（删 `<parameter_propagation_data>` 段，#4）
- Modify: `packages/core/src/shannon_core/models/agents.py`（删 #7 agent 注册四点）
- Delete: `packages/core/src/shannon_core/code_index/audit_input_builder.py`
- Delete: `prompts/audit-tier1.txt`
- Delete: `packages/core/tests/code_index/test_audit_input_builder.py`
- Modify: `packages/core/tests/test_agents.py`（删 `test_audit_tier1_agent_registered`）

**Interfaces:**
- Consumes: Task 1 解耦测试框架。
- Produces: 无；`AgentName` 枚举与 AGENTS 字典不再含 `AUDIT_TIER1`。

- [ ] **Step 1: 删 prompts/recon.txt 的 `<parameter_propagation_data>` 段（#4）**

删掉整段（含 `## Parameter Propagation Graph` 标题、引导文字、`{{TAINT_FLOW_SUMMARY}}`）：

```
<parameter_propagation_data>
## Parameter Propagation Graph (from parameter_graph.json)

For each endpoint, the parameter propagation graph provides:
- Taint source: Where user input enters (query param, body field, path param, header, cookie)
- Propagation path: How input flows through function calls
- Sink: Where the input reaches a security-sensitive operation (SQL, exec, render, etc.)

{{TAINT_FLOW_SUMMARY}}

When documenting injection sources in Section 9, use the taint flow data to trace the complete path from entry parameter to sink. Include the transformation chain (if any) between source and sink.
</parameter_propagation_data>
```

- [ ] **Step 2: 删 audit-tier1 agent 注册（agents.py 四处）**

`packages/core/src/shannon_core/models/agents.py`：

(a) 删枚举值（`AgentName` 内）：
```python
    AUDIT_TIER1 = "audit-tier1"
```

(b) 删 AGENTS 字典条目：
```python
    AgentName.AUDIT_TIER1: AgentDefinition(
        name=AgentName.AUDIT_TIER1,
        display_name="Tier 1 Combined Audit",
        prerequisites=[AgentName.RECON],
        prompt_template="audit-tier1",
        deliverable_filename=None,  # Findings collected, no separate deliverable
        model_tier="small",
    ),
```

(c) 删 BROWSER_SESSION_MAPPING 行：
```python
BROWSER_SESSION_MAPPING[AgentName.AUDIT_TIER1.value] = "agent16"
```

(d) 删 AGENT_PHASE_MAP 行：
```python
    "audit-tier1": "vulnerability-analysis",
```

- [ ] **Step 3: 删 audit_input_builder.py + audit-tier1.txt + 测试**

```bash
git rm packages/core/src/shannon_core/code_index/audit_input_builder.py
git rm prompts/audit-tier1.txt
git rm packages/core/tests/code_index/test_audit_input_builder.py
```

- [ ] **Step 4: 删 test_agents.py 的 audit-tier1 注册测试**

`packages/core/tests/test_agents.py:131-136`，删整个 `test_audit_tier1_agent_registered` 函数。

- [ ] **Step 5: 确认无残余引用**

Run: `grep -rn "AUDIT_TIER1\|audit-tier1\|audit_tier1\|audit_input_builder\|build_chain_audit_input\|build_tier1_audit_input\|CHAIN_AUDIT_INPUT\|VULN_CLASSES_TESTED" packages/ prompts/ --include="*.py" --include="*.txt"`
Expected: 只剩文档（`docs/`）命中，无 `packages/` 或 `prompts/` 命中。若有 `packages/` 残留——停，处理后再继续。

- [ ] **Step 6: 跑测试（全黑名单转绿）**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v && pytest packages/core/tests/test_agents.py -v`
Expected: `test_no_llm_track_prompt_has_forbidden_placeholders` **PASS**（所有 FORBIDDEN_PLACEHOLDERS 已清）。`test_agents.py` 无 `AttributeError: AUDIT_TIER1`。

- [ ] **Step 7: Commit**

```bash
git add prompts/recon.txt packages/core/src/shannon_core/models/agents.py packages/core/tests/test_agents.py
git commit -m "feat(decouple): remove TAINT_FLOW_SUMMARY placeholder + audit-tier1 dead code (#4,#7)"
```

---

### Task 5: 反方向 fusion 加 enable_llm_track 显式守卫

> Spec §5.3 原写"透传到 fusion 函数"；实现采用更优方案——在 `workflows.py` 调用点外层守卫（与 `workflows.py:296` 现有 vuln-agent 守卫模式一致，零签名变更，无 temporal ActivityInput 序列化风险）。

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:166-178`
- Test: `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`（加静态守卫断言）

**Interfaces:**
- Consumes: `PipelineInput.enable_llm_track`（`pipeline/shared.py:20`，已存在）。
- Produces: 无新接口。

- [ ] **Step 1: 写守卫存在性测试（静态 grep，先红）**

在 `test_static_dataflow_hints_decoupling.py` 顶部 import 区加：

```python
REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS_PY = REPO_ROOT / "packages/whitebox/src/shannon_whitebox/pipeline/workflows.py"
```

末尾追加：

```python
def test_fusion_guarded_by_enable_llm_track():
    """反方向 fusion（run_merge_sink_reports / run_entry_point_fusion）必须受
    enable_llm_track 显式守卫，而非靠 PRE_RECON 不产出文件间接降级。"""
    text = WORKFLOWS_PY.read_text()
    # 找到两个 fusion activity 调用，确认它们都在 if input.enable_llm_track: 块内。
    for fusion_activity in ("run_merge_sink_reports", "run_entry_point_fusion"):
        assert fusion_activity in text, f"{fusion_activity} 调用点消失？"
    # 守卫模式：两个 fusion 调用前应有 if input.enable_llm_track:
    assert "if input.enable_llm_track:" in text, (
        "workflows.py 缺 enable_llm_track 守卫（CLAUDE.md §1：fusion 需显式守卫）"
    )
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py::test_fusion_guarded_by_enable_llm_track -v`
Expected: FAIL（当前 fusion 调用未在 `if input.enable_llm_track:` 块内——`:296` 的守卫只包 vuln agents）。

> 注：此静态断言较弱（只验守卫存在），真机行为（enable_llm_track=0 时 fusion 不跑）靠 Task 7 冒烟。这是 workflow 编排逻辑，单元测成本高，静态锁 + 冒烟是务实选择。

- [ ] **Step 3: workflows.py 加守卫**

`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`，把两个 fusion 调用（原 166-178 段）：

```python
                # Merge deterministic sinks with LLM-discovered sinks
                await workflow.execute_activity(
                    activities.run_merge_sink_reports, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry_for("standard"),
                )

                # Entry point fusion: merge deterministic + LLM discoveries
                await workflow.execute_activity(
                    activities.run_entry_point_fusion, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry_for("standard"),
                )
```

包进 `if input.enable_llm_track:` 守卫：

```python
                if input.enable_llm_track:
                    # Merge deterministic sinks with LLM-discovered sinks (needs LLM deliverable)
                    await workflow.execute_activity(
                        activities.run_merge_sink_reports, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )

                    # Entry point fusion: merge deterministic + LLM discoveries (needs LLM deliverable)
                    await workflow.execute_activity(
                        activities.run_entry_point_fusion, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )
```

> `input` 在该作用域是 `PipelineInput`（`enable_llm_track` 字段可见，与 `:296` 同源）。两个 fusion 都依赖 LLM pre-recon 产出的 `pre_recon_deliverable.md`，关 LLM 轨时跳过整个 activity 比让函数内部读空文件降级更干净。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: `test_fusion_guarded_by_enable_llm_track` PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py
git commit -m "feat(decouple): guard reverse fusion with enable_llm_track (explicit skip)"
```

---

### Task 6: 解耦测试 finalize（prompt_variables 注入禁止 + #6 白名单 + 全量回归）

**Files:**
- Test: `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`

**Interfaces:**
- Consumes: Task 1-5 已删全部确定性→LLM 注入。
- Produces: 完整解耦测试套件（黑名单 prompt 占位符 + `prompt_variables` 注入禁止 + fusion 守卫 + `@include` 锁 + #6 白名单）。

- [ ] **Step 1: 写 prompt_variables 注入禁止断言（应直接绿）**

在 `test_static_dataflow_hints_decoupling.py` 顶部 import 区加：

```python
ACTIVITIES_PY = REPO_ROOT / "packages/whitebox/src/shannon_whitebox/pipeline/activities.py"
```

末尾追加：

```python
def test_no_prompt_variables_inject_deterministic_track():
    """activities.py 不得给 LLM 轨 agent 的 prompt_variables 注入确定性 track 产物。
    白名单：authz_gitnexus_candidates（GitNexus 轨内部 IDOR 判定，合法）。"""
    text = ACTIVITIES_PY.read_text()
    forbidden_keys = (
        "pre_recon_gitnexus_track",
        "recon_gitnexus_track",
        "framework_endpoints_summary",
        "taint_flow_summary",
        "chain_audit_input",
    )
    offenders = [k for k in forbidden_keys if f'prompt_variables["{k}"]' in text or f'"{k}":' in text]
    assert not offenders, (
        f"CLAUDE.md §1 violation — activities.py still injects deterministic "
        f"track into LLM-track prompt_variables: {offenders}"
    )
    # 白名单：authz_gitnexus_candidates 必须保留（轨内合法）
    assert "authz_gitnexus_candidates" in text, (
        "authz_gitnexus_candidates 白名单被误删（authz GitNexus 轨内判定需要它）"
    )
```

- [ ] **Step 2: 跑全解耦测试套件**

Run: `pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: 全部 PASS：
- `test_no_prompt_includes_static_dataflow_hints`（@include 锁）
- `test_no_llm_track_prompt_has_forbidden_placeholders`（黑名单）
- `test_fusion_guarded_by_enable_llm_track`（fusion 守卫）
- `test_no_prompt_variables_inject_deterministic_track`（注入禁止 + #6 白名单）

- [ ] **Step 3: 全量回归（改动相关子集）**

Run: `pytest packages/core/tests/code_index/ packages/core/tests/prompts/ packages/core/tests/services/ packages/core/tests/test_agents.py -v`
Expected: 全 PASS，无 `ImportError` / `AttributeError`。若某测试因 renderer 删除失败——确认它是否在 File Structure 的 Delete 清单里（应在；不在则评估）。

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py
git commit -m "test(decouple): finalize dual-track decoupling suite (injection ban + #6 whitelist)"
```

---

### Task 7: 人工冒烟验证（manual）

> 自动化测试锁住了"结构正确"（占位符/注入/守卫都在位），但 recon/pre-recon 拆确定性摘要后**deliverable 是否仍正常产出**需真机确认。这是 spec §9 风险③的验证。

**Files:** 无（运行验证）

- [ ] **Step 1: 用 glm-anthropic 引擎跑一次 pre-recon + recon（小仓或 fixture 仓）**

确认：`pre_recon_deliverable.md` 与 `recon_deliverable.md` 正常产出，内容不含字面 `{{...}}` 残留（验证 `manager.py` warning 消失）。

- [ ] **Step 2: 验证 enable_llm_track=0 降级**

设 `SHANNON_LLM_TRACK_ENABLED=0` 跑：确认 fusion activity 不被调度（日志无 `run_merge_sink_reports` / `run_entry_point_fusion`），code_index.json 保持纯确定性（无 `source="llm_pre_recon"` / `rule_id="llm-sink-hunter"` 项）。

- [ ] **Step 3: 验证 GitNexus 轨独立兜底**

确认 `chain_verdict` / `authz_gitnexus_judge` 仍能直读 `code_index.json` / `parameter_graph.json` / `framework_analysis.json`（底层 JSON 未受影响）。

- [ ] **Step 4: 记录冒烟结果到 memory**

冒烟通过后，在 memory 写一条 `dual-track-decoupling-status`（参考 `injection-recall-port-status` 格式）：标注实现完成、解耦测试全绿、冒烟已验、待 merge。

---

## Self-Review（写计划后自检）

**Spec 覆盖：**
- spec §5.1 #1 → Task 1 ✓；#2 → Task 2 ✓；#3 → Task 3 ✓；#4 → Task 4 ✓；#5 → Task 1 ✓
- spec §5.2 #7 audit-tier1 → Task 4 ✓
- spec §5.3 fusion 守卫 → Task 5 ✓（采用 workflows.py 外层方案，优于 spec 原方案，已注明）
- spec §5.4 解耦测试 → Task 1（框架）+ Task 6（finalize）✓，含 #6 白名单
- spec §6 降级 → Task 5（守卫）+ Task 7（冒烟）✓
- spec §9 风险③（deliverable 产出）→ Task 7 冒烟 ✓

**无占位符：** 每步均含真实代码/命令/原文段。

**类型/命名一致：** `OWNERSHIP_PREDICATE_RE`（Task 2 patterns.py，去前导下划线）与 Task 2 Step 2 import 一致；`FORBIDDEN_PLACEHOLDERS` 跨 Task 1/6 引用一致；`enable_llm_track` 字段名与 `shared.py:20` 一致。
