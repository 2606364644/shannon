# LLM 轨弱项修复 Plan 1（recon-static 对账 + vuln 字段 + SharedKnowledge 注入）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 shannon-py LLM 轨相对原始 TS 的三类弱项——白盒纯静态路径 recon-static 缺枚举对账、vuln inj+xss 失去 Section 4.2 可达性指引、vuln prompt 拿不到结构化 recon 前序知识。

**Architecture:** 纯 prompt 改动为主（Track A：recon-static 对账 + vuln 字段），加一个轻量 LLM 摘要注入（Track B'2：把 recon_deliverable.md 摘要成结构化 `{{RECON_CONTEXT}}` 注入 vuln prompt）。不改 workflow 编排、不新建确定性服务。

**Tech Stack:** Python（activities.py 注入 + 新增 summarizer）、prompt .txt（recon-static / vuln-* / shared partial）、`run_claude_prompt` 轻量 LLM 单次调用（参照 chain_verdict 模式）、pytest。

## Global Constraints

- **守 CLAUDE.md §1 双轨铁律**：所有注入 vuln prompt 的内容只能来自 LLM 轨自产（`recon_deliverable.md` / `pre_recon_deliverable.md` / 代码层 `framework_analysis.json`），**绝不引 GitNexus 确定性层产物**（`parameter_graph.json` / `SinkCallSite` / `static_dataflow_hints`）。新 prompt 不得 `@include` 确定性产物。
- **解耦测试**：`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` rglob 自动覆盖所有 `prompts/` 改动——只要文件不写 FORBIDDEN token 即过。
- **白盒纯静态为主用例**：所有设计以 `executor.py:39-40 not web_url → recon-static` 为基准。
- **不动 recon.txt 黑盒路径对账**（已接 `_enumeration-completeness.txt:484`），只补 recon-static。
- **不移植 TS SharedKnowledge store 抽象**：用 PY 通用 `{{KEY}}` 占位符（`manager.py:175-178`）。
- **frequent commits**：每个 task 结束 commit。
- **TDD**：每个 task 先写失败测试再实现。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `prompts/shared/_enumeration-completeness.txt` | 5 角度枚举对账 checklist | 加第 4 类 delta `not-applicable`（Task 1） |
| `prompts/recon-static.txt` | 白盒纯静态 recon 方法论 + 产出契约 | 章节重构 §0-§9 + 5 角度 + Step 3.5 + @include（Task 2-3） |
| `prompts/vuln-injection.txt` | injection vuln 检测 | starting_context 补 §4.2 字段 + framework auto-gen + source-list 警示（Task 4-5） |
| `prompts/vuln-xss.txt` | xss vuln 检测 | starting_context 补 §4.2 字段（Task 4） |
| `prompts/vuln-{ssrf,authz,auth}.txt` | 其余 vuln 检测 | starting_context 加 `{{RECON_CONTEXT}}` 占位符块（Task 7） |
| `packages/core/src/shannon_core/agents/recon_context_summarizer.py` | LLM 摘要 recon md → 结构化（新建） | Task 6 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | vuln activity 注入 prompt_variables | `run_agent` :175-181 加 vuln 分支（Task 7） |
| `packages/core/tests/prompts/test_recon_static_reconciliation.py` | recon-static 对账内容断言（新建） | Task 2-3 |
| `packages/core/tests/agents/test_recon_context_summarizer.py` | summarizer 单测（新建） | Task 6 |
| `packages/core/tests/prompts/test_vuln_recon_context_injection.py` | 注入 + 解耦断言（新建） | Task 7 |

---

## Task 1: `_enumeration-completeness.txt` 加 `not-applicable` delta 分类

**Files:**
- Modify: `prompts/shared/_enumeration-completeness.txt:28-37`
- Test: `packages/core/tests/prompts/test_enumeration_completeness.py`（新建）

**Interfaces:**
- Produces: 共享 partial 新增第 4 类 delta `not-applicable`，供 recon-static（Task 2）与 recon.txt（已 @include）共用。

**Why:** 白盒纯静态下 Angle 4（frontend-call）/Angle 5（gateway）常不适用（target 无前端代码 / 无 gateway 配置）。原 3 类（dedup/out-of-scope/true-miss）逼 LLM 为填表编造或卡住。`not-applicable` 让 LLM 诚实标"target 无此类代码"，但必须附 grep 零结果证据防偷懒。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_enumeration_completeness.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def test_enumeration_completeness_has_not_applicable_delta():
    """EC-B delta 分类必须含 not-applicable（白盒纯静态适配）。"""
    content = (PROMPTS_DIR / "shared" / "_enumeration-completeness.txt").read_text("utf-8")
    assert "`not-applicable`" in content, (
        "EC-B delta 分类缺 not-applicable——白盒纯静态下 Angle 4/5 无从分类"
    )
    # 必须要求 grep 零结果证据（防 LLM 偷懒标 N/A）
    assert "grep" in content.lower() or "zero" in content.lower() or "no match" in content.lower(), (
        "not-applicable 必须附证据（grep 零结果），防偷懒"
    )


def test_enumeration_completeness_keeps_original_three_deltas():
    """原 3 类 delta 必须保留（向后兼容）。"""
    content = (PROMPTS_DIR / "shared" / "_enumeration-completeness.txt").read_text("utf-8")
    for cls in ("`dedup`", "`out-of-scope`", "`true-miss`"):
        assert cls in content, f"原 delta 分类 {cls} 丢失"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_enumeration_completeness.py -v`
Expected: FAIL — `not-applicable` 断言失败（当前只有 3 类）。

- [ ] **Step 3: 实现——加第 4 类 delta**

修改 `prompts/shared/_enumeration-completeness.txt`，在 EC-B 段（line 33-37 的 list）追加第 4 类。把当前：

```text
- `dedup` — same endpoint surfaced by another angle (name the duplicate angle).
- `out-of-scope` — not reachable via a web entry point (per scope_boundaries).
- `true-miss` — a real omission; you MUST go back and add it to Section 4 (and to
  Section 4.1 if it shares a handler) before terminating.
```

改为：

```text
- `dedup` — same endpoint surfaced by another angle (name the duplicate angle).
- `out-of-scope` — not reachable via a web entry point (per scope_boundaries).
- `not-applicable` — the target codebase contains NO source for this angle (e.g. a
  pure-backend service has no frontend-call layer; no nginx/ingress config exists).
  You MUST justify this with a grep zero-result: state the grep pattern you ran and
  that it matched zero files. A bare `not-applicable` with no evidence is forbidden —
  it will be treated as an unclassified delta and blocks termination.
- `true-miss` — a real omission; you MUST go back and add it to Section 4 (and to
  Section 4.1 if it shares a handler) before terminating.
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_enumeration_completeness.py -v`
Expected: PASS（2 用例）。

- [ ] **Step 5: 跑解耦测试确认无回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: PASS（partial 未引确定性 token）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add prompts/shared/_enumeration-completeness.txt packages/core/tests/prompts/test_enumeration_completeness.py
git commit -m "feat(prompt): _enumeration-completeness 加 not-applicable delta 分类（白盒纯静态适配）"
```

---

## Task 2: recon-static.txt 章节重构对齐 §0-§9 + @include 对账 partial

**Files:**
- Modify: `prompts/recon-static.txt`（整体重构 output_format 段）
- Test: `packages/core/tests/prompts/test_recon_static_reconciliation.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `not-applicable` delta。
- Produces: recon-static 产出契约与 recon.txt §0-§9 一致；`@include(shared/_enumeration-completeness.txt)` + `@include(shared/_cross-route-enumeration.txt)` 接入。

**Why:** 当前 recon-static 章节错乱（6.4→6.5→6.6→8→7）、缺 §4.3 对账表、未 @include 对账 partial。下游 vuln agent 自读 deliverable 时静态/动态路径结构不一致。对齐 recon.txt §0-§9 让两者产出同构。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_recon_static_reconciliation.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


def test_recon_static_includes_enumeration_completeness_partial():
    """recon-static 必须 @include _enumeration-completeness.txt（白盒主路径对账）。"""
    content = _read("recon-static.txt")
    assert "@include(shared/_enumeration-completeness.txt)" in content


def test_recon_static_includes_cross_route_enumeration_partial():
    """recon-static 必须 @include _cross-route-enumeration.txt（§4.1 shared route group 对账）。"""
    content = _read("recon-static.txt")
    assert "@include(shared/_cross-route-enumeration.txt)" in content


def test_recon_static_has_section_4_3_reconciliation_table():
    """recon-static 必须产出 §4.3 Enumeration Reconciliation（5 角度对账表）。"""
    content = _read("recon-static.txt")
    assert "4.3 Enumeration Reconciliation" in content or "### 4.3" in content
    # 5 角度都要点名
    for angle in ("Route-definition", "Controller-method", "Interface-contract",
                  "Frontend-call", "Gateway"):
        assert angle in content, f"recon-static 缺枚举角度 {angle}"


def test_recon_static_aligns_section_0_to_9_structure():
    """recon-static 产出契约必须含 §0-§9 骨架（与 recon.txt 一致）。"""
    content = _read("recon-static.txt")
    required = [
        "HOW TO READ",
        "Executive Summary",
        "Technology & Service Map",
        "API Endpoint Inventory",
        "Parameter Completeness",
        "Authorization Vulnerability Candidates",
        "Injection Sources",
    ]
    for sec in required:
        assert sec in content, f"recon-static 缺产出节: {sec}"


def test_recon_static_decoupled_from_deterministic():
    """守铁律：recon-static 不引确定性产物。"""
    content = _read("recon-static.txt")
    forbidden = ["parameter_graph", "SinkCallSite", "static_dataflow_hints", "static-dataflow-hints"]
    for tok in forbidden:
        assert tok not in content, f"recon-static 引确定性产物 token: {tok}"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_recon_static_reconciliation.py -v`
Expected: FAIL（@include / §4.3 / 角度 断言失败）。

- [ ] **Step 3: 实现——重构 recon-static.txt 的 output_format + 加 @include**

在 `<methodology>` 之前（紧跟 `@include(shared/_rules-of-engagement.txt)` 之后）加两行 @include：

```text
@include(shared/_cross-route-enumeration.txt)
@include(shared/_enumeration-completeness.txt)
```

然后把 `<output_format>` 段的 deliverable 结构整体替换为对齐 recon.txt §0-§9 的结构（保留 PY 现有的 §2.1 Endpoint Security Context / §6.4 Guards / §8 Authz Candidates 好内容，重新编号归位）。新 `<output_format>` 关键节如下（写入 `{{DELIVERABLES_PATH}}/recon_deliverable.md`）：

```text
<output_format>
Write your findings to `{{DELIVERABLES_PATH}}/recon_deliverable.md` with these sections:

## 0) HOW TO READ
Static source-code reconnaissance — no live requests. Downstream vuln agents consume §4 (endpoint inventory + §4.2 security context), §5 (input vectors), §8 (authz candidates), §9 (injection sources).

## 1. Executive Summary
- Framework / language / key tech

## 2. Technology & Service Map
- Frontend / Backend / Infra

## 3. Authentication & Session Management Flow
### 3.1 Role Assignment / 3.2 Privilege Storage & Validation / 3.3 Role Switching & Impersonation

## 4. API Endpoint Inventory
| Method | Endpoint Path | Required Role | Object ID Parameters | Authorization Mechanism | Description & Code Pointer |

### 4.1 Shared Controller Route Groups
Per the shared/_cross-route-enumeration.txt checklist: every route sharing a handler
listed one row per route; pre-auth (`**none**` middleware) variants flagged with ⚠️.
Format: `#### Group: <handler> (<file:line>)` + table `| Method | Path | Auth Middleware | Router Line |`.

### 4.2 Endpoint Security Context
For every endpoint in Section 4, document (descriptive — what protections exist, NOT sufficiency):
| Method | Path | Auth | Middleware | Framework Origin | Ownership Check | Notes |
Framework Origin: manual / finale-rest auto-generated / epilogue auto-generated.
When auto-REST frameworks detected (`finale.initialize()` / `epilogue.initialize()` / `finale.resource()`):
enumerate all auto-generated endpoints per model, mark Origin, note ownership is typically
absent by default ("none detected" unless explicit check found). Field conflicts → take the dangerous side.

### 4.3 Enumeration Reconciliation
Produce AFTER merging all five enumeration angles (see shared/_enumeration-completeness.txt).
| Angle | Anchor Count | Reported (deduped) | Delta | Explanation |
Angles: Route-definition / Controller-method / Interface-contract / Frontend-call / Gateway.
Every non-zero Delta MUST be classified `dedup` / `out-of-scope` / `not-applicable` / `true-miss`;
every `true-miss` MUST be added back to Section 4 (and 4.1 if shared handler) before terminating.

## 5. Potential Input Vectors (Parameter Completeness Verification)
For every Section 4 endpoint list ALL client-controllable parameters:
| Endpoint | URL Params | POST Body | Headers | Cookies | Template Variables | Hidden Parameters | Extraction Code Location |
Template-rendering endpoints: complete the hidden-parameter cross-check (undeclared fields
like `[key: string]: unknown` / wildcard / catch-all that may pass through).

## 6. Network & Interaction Map
### 6.1 Entities `| Title | Type | Zone | Tech | Data | Notes |`
### 6.2 Entity Metadata
### 6.3 Flows `| FROM→TO | Channel | Path/Port | Guards | Touches |`
### 6.4 Guards Directory `| Guard Name | Category | Statement |`

## 7. Role & Privilege Architecture
### 7.1 Discovered Roles / 7.2 Privilege Lattice / 7.3 Role Entry Points / 7.4 Role-to-Code Mapping

## 8. Authorization Vulnerability Candidates
### 8.1 Horizontal Privilege Escalation Candidates
`| Priority | Endpoint Pattern | Object ID Param | Ownership Check | Data Type | Reason |`
### 8.2 Vertical Privilege Escalation Candidates
`| Target Role | Endpoint | Functionality | Role Guard | Risk Level |`
### 8.3 Context-Based Authorization Candidates
`| Workflow | Endpoint | Expected Prior State | State Verified? | Bypass Potential |`

## 9. Injection Sources (Command/SQL/LFI·RFI/SSTI/Path Traversal/Deserialization)
file:line list, 5 categories.
</output_format>
```

> 注：上面是 `<output_format>` 段的完整替换。保留 PY 现有 `§2.1 Endpoint Security Context` 的 framework endpoint 检测细节（已融入 §4.2 描述）、`§6.4 Guards`、`§8` 三类表（已重编号）。删掉原错乱的 §6.5/§6.6/§7 Priority 散落节（内容归位到 §7/§8）。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_recon_static_reconciliation.py -v`
Expected: PASS（5 用例）。

- [ ] **Step 5: 跑解耦测试 + PromptManager 渲染冒烟**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v && python -c "from shannon_core.prompts.manager import PromptManager; from pathlib import Path; m=PromptManager(Path('prompts')); t=m.load_sync('recon-static', {'web_url':'','repo_path':'/tmp','deliverables_path':'/tmp/d','scratchpad_path':'/tmp/s'}); assert 'Enumeration Reconciliation' in t and 'not-applicable' in t; print('render OK', len(t), 'chars')"`
Expected: 解耦测试 PASS；渲染冒烟输出 `render OK` + 字符数（无残留 `{{UPPER}}` 占位符报错）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add prompts/recon-static.txt packages/core/tests/prompts/test_recon_static_reconciliation.py
git commit -m "feat(prompt): recon-static 章节重构对齐 §0-§9 + @include 对账 partial（白盒枚举完整性）"
```

---

## Task 3: recon-static.txt 补 5 角度并行枚举方法论 + Step 3.5 强制对账

**Files:**
- Modify: `prompts/recon-static.txt`（`<methodology>` 段）
- Test: `packages/core/tests/prompts/test_recon_static_reconciliation.py`（加用例）

**Interfaces:**
- Consumes: Task 2 的产出节（§4.3 表目标）。
- Produces: recon-static Phase 1 改为 5 角度并行枚举 + Step 3.5 MANDATORY 对账。

**Why:** 当前 Phase 1 是"5 Task Agent 按扫描维度切"（架构/入口/安全模式/sink/dataflow），非对账导向——单角度扫漏了就漏了。TS 的精髓是 5 角度按枚举切（每角度独立产 endpoint 集，再对账 delta），把覆盖完整性变成强制的集合对账。

- [ ] **Step 1: 加失败测试**

在 `test_recon_static_reconciliation.py` 追加：

```python
def test_recon_static_has_five_angle_methodology():
    """recon-static Phase 1 必须是 5 角度枚举（非维度扫描）。"""
    content = _read("recon-static.txt")
    # 5 角度 + anchor count 要求
    assert "anchor count" in content.lower() or "source anchor count" in content.lower()
    for angle in ("Route definitions", "Controller methods", "Interface contracts",
                  "Frontend calls", "Gateway config"):
        assert angle in content, f"recon-static methodology 缺角度: {angle}"


def test_recon_static_has_step_3_5_mandatory_reconciliation():
    """recon-static 必须有 Step 3.5 MANDATORY 对账（产 §4.3 前强制）。"""
    content = _read("recon-static.txt")
    assert "3.5" in content and "MANDATORY" in content.upper(), (
        "recon-static 缺 Step 3.5 强制对账"
    )
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_recon_static_reconciliation.py::test_recon_static_has_five_angle_methodology packages/core/tests/prompts/test_recon_static_reconciliation.py::test_recon_static_has_step_3_5_mandatory_reconciliation -v`
Expected: FAIL（5 角度 + Step 3.5 缺）。

- [ ] **Step 3: 实现——重写 `<methodology>` 段**

把当前 `<methodology>` 的 Phase 1（"Launch parallel Task Agents to scan different aspects" + Task Agent A-E 按维度切）替换为 5 角度枚举。新 `<methodology>`：

```text
<methodology>

### Phase 1: Multi-Angle Source Mapping (5 parallel Task agents)

Launch FIVE Task agents IN PARALLEL in a single message, one per enumeration angle.
Each angle catches different blind spots; merge their results (deduplicated) into Section 4.
Each Task agent MUST return BOTH (a) the endpoints it found
(METHOD /path + file:line + handler) AND (b) a source anchor count — how many route
sources it counted in code, with the grep pattern and file used. This count drives
the Section 4.3 reconciliation; do not omit it.

- **Angle 1 — Route definitions:** router.get/post/put/delete, framework decorators,
  config-driven route tables (e.g. paths-to-config object). Count route-definition entries.
- **Angle 2 — Controller methods:** handler methods referenced by routes. Count handlers
  bound to a route.
- **Angle 3 — Interface contracts:** proto http annotations, OpenAPI/swagger specs,
  graphql schema. Count contract-declared operations.
- **Angle 4 — Frontend calls:** axios/fetch/rpc calls in frontend code, reverse-inferred
  backend endpoints NOT declared in route files. Count distinct backend paths called.
  (If the target is a pure-backend service with no frontend code, this angle yields
  zero — classify `not-applicable` in §4.3 with the grep zero-result evidence.)
- **Angle 5 — Gateway config:** nginx `location`/`proxy_pass`, ingress routes, gateway
  config. Count proxied paths. (If no gateway/ingress config exists in the repo,
  classify `not-applicable` with grep evidence.)

After merging, perform **group detection**: identify routes mapping to the SAME handler
function — these share processing logic; a vulnerability in the handler affects every
route in the group. For each group note whether routes differ in auth middleware, and
include the router definition file:line (feeds Section 4.1).

Also launch IN PARALLEL: Authorization Checker Agent, Input Validator Agent (enumerate
ALL fields from input type definitions — TS interfaces / Zod / Joi / Pydantic / JSON
Schema, incl. wildcard `[key: string]: unknown`), Session Handler Agent.

### Phase 2: Security Pattern Correlation
For each vulnerability class being tested, correlate code patterns with known classes
(injection sinks / XSS contexts / auth boundaries / SSRF entry points / access control).
Populate §3 / §6 / §7.

### Step 3.5: Enumeration Reconciliation (MANDATORY before writing Section 4)
- Sum the source anchor counts returned by the five angles in Phase 1.
- Compare each angle's anchor count against the deduplicated endpoint set you are about
  to write to Section 4.
- Produce the `### 4.3 Enumeration Reconciliation` table (one row per angle: anchor count,
  reported deduped count, delta, explanation).
- Classify every non-zero delta as `dedup` / `out-of-scope` / `not-applicable` / `true-miss`.
  `not-applicable` REQUIRES a grep zero-result evidence (pattern + zero files matched).
  For every `true-miss`, go back and add the missing endpoint to Section 4 (and 4.1 if
  shared handler) before proceeding.
- You may NOT consider Section 4 complete while any delta remains unclassified.

### Phase 3: Attack Surface Documentation
Compile findings into the deliverable per `<output_format>` + the pre-termination
checklist in `shared/_enumeration-completeness.txt` (EC-A through EC-F). Do NOT announce
"RECONNAISSANCE COMPLETE" until §4.3 has zero `true-miss` and §4.1 / §5 are populated.

</methodology>
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_recon_static_reconciliation.py -v`
Expected: PASS（7 用例全过）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/recon-static.txt packages/core/tests/prompts/test_recon_static_reconciliation.py
git commit -m "feat(prompt): recon-static 补 5 角度并行枚举 + Step 3.5 强制对账"
```

---

## Task 4: vuln inj+xss 的 starting_context 补 Section 4.2 字段 + framework auto-gen 提示

**Files:**
- Modify: `prompts/vuln-injection.txt`（starting_context 段）
- Modify: `prompts/vuln-xss.txt`（starting_context 段）
- Test: `packages/core/tests/prompts/test_vuln_starting_context.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 recon-static §4.2 Endpoint Security Context（vuln agent 消费它）。
- Produces: vuln inj+xss 拿到 §4.2 字段指引（可达性 + middleware 验证）。

**Why:** 当前 PY 把 TS 的 §4.2 字段清单（HTTP method / auth 等级 / ownership / framework auto-gen / middleware chain）删成一句套话，vuln agent 失去可达性判断依据——false-negative 直接诱因。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_vuln_starting_context.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


def test_injection_starting_context_has_section_4_2_fields():
    content = _read("vuln-injection.txt")
    assert "Section 4.2" in content or "§4.2" in content
    for field in ("HTTP method", "ownership", "middleware"):
        assert field.lower() in content.lower(), f"injection starting_context 缺字段: {field}"
    # framework auto-gen 提示
    assert "framework auto-generated" in content.lower() or "finale-rest" in content.lower()


def test_xss_starting_context_has_section_4_2_fields():
    content = _read("vuln-xss.txt")
    assert "Section 4.2" in content or "§4.2" in content
    for field in ("HTTP method", "ownership", "middleware"):
        assert field.lower() in content.lower(), f"xss starting_context 缺字段: {field}"
    assert "framework auto-generated" in content.lower() or "finale-rest" in content.lower()


def test_starting_context_decoupled_from_deterministic():
    for name in ("vuln-injection.txt", "vuln-xss.txt"):
        content = _read(name)
        for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
            assert tok not in content, f"{name} 引确定性 token: {tok}"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_vuln_starting_context.py -v`
Expected: FAIL（§4.2 字段 / framework auto-gen 断言失败）。

- [ ] **Step 3: 实现——补 starting_context 字段块**

在 `vuln-injection.txt` 和 `vuln-xss.txt` 的 `<starting_context>`（或等价的 context 引用段，当前指向 `recon_deliverable.md` 路径处）之后，插入同一个字段块。injection 版：

```text
**Endpoint Security Context (recon Section 4.2):** For each endpoint you analyze,
`{{DELIVERABLES_PATH}}/recon_deliverable.md` Section 4.2 provides: HTTP methods,
auth level (anon / user / admin), ownership validation (none detected / yes file:line),
framework origin (manual / finale-rest auto-generated / epilogue auto-generated), and
the middleware chain. Use these to judge each endpoint's reachability and to validate
sinks that sit behind middleware. Pay special attention to endpoints marked as framework
auto-generated that accept user input — trace where that input reaches a sink, since
auto-REST handlers often lack parameterization.
```

xss 版（最后一句改为 render 导向）：

```text
**Endpoint Security Context (recon Section 4.2):** For each endpoint you analyze,
`{{DELIVERABLES_PATH}}/recon_deliverable.md` Section 4.2 provides: HTTP methods,
auth level (anon / user / admin), ownership validation (none detected / yes file:line),
framework origin (manual / finale-rest auto-generated / epilogue auto-generated), and
the middleware chain. Use these to judge each endpoint's reachability and to validate
sinks that sit behind middleware. Pay special attention to endpoints marked as framework
auto-generated that accept user input — trace where that input is rendered in frontend
components or server-side templates.
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_vuln_starting_context.py -v`
Expected: PASS（3 用例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-injection.txt prompts/vuln-xss.txt packages/core/tests/prompts/test_vuln_starting_context.py
git commit -m "feat(prompt): vuln inj+xss starting_context 补 Section 4.2 字段 + framework auto-gen 提示"
```

---

## Task 5: vuln-injection source-list 警示强化

**Files:**
- Modify: `prompts/vuln-injection.txt`（step 1 / source enumeration 段）
- Test: `packages/core/tests/prompts/test_vuln_starting_context.py`（加用例）

**Interfaces:**
- Produces: inj agent 收到"上游列表非穷尽、未枚举 sink 必漏"强信号。

**Why:** 当前压成 "Do NOT rely solely on an upstream target list" 套话，失去 TS 的"仅分析上游列表会漏未枚举 sink"强警示。

- [ ] **Step 1: 加失败测试**

在 `test_vuln_starting_context.py` 追加：

```python
def test_injection_has_strong_source_list_warning():
    """injection 必须有强 source-list 警示（上游列表非穷尽、未枚举 sink 必漏）。"""
    content = _read("vuln-injection.txt")
    # 强信号关键词
    assert "not exhaustive" in content.lower() or "非穷尽" in content or "not complete" in content.lower()
    # 必须指示自己 grep 扩展
    assert "grep" in content.lower()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_vuln_starting_context.py::test_injection_has_strong_source_list_warning -v`
Expected: FAIL（强信号关键词缺）。

- [ ] **Step 3: 实现——替换 source-list 警示句**

在 `vuln-injection.txt` 找到当前的 "Do NOT rely solely on an upstream target list" 句（约 step 1 / source enumeration 段），替换为：

```text
The recon and pre-recon deliverables are a STARTING POINT, NOT an exhaustive sink list.
Analyzing ONLY the endpoints they list will miss sinks that were never enumerated — you
MUST extend the sink search yourself with grep (per the language-specific sink list in
this prompt), covering paths recon did not list. An entire path-prefix family silently
absent from recon is the historical source of false-negatives; do not let it happen here.
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_vuln_starting_context.py -v`
Expected: PASS（4 用例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-injection.txt packages/core/tests/prompts/test_vuln_starting_context.py
git commit -m "feat(prompt): vuln-injection source-list 警示强化（未枚举 sink 必漏）"
```

---

## Task 6: 新增 `recon_context_summarizer`（LLM 摘要 recon md → 结构化）

**Files:**
- Create: `packages/core/src/shannon_core/agents/recon_context_summarizer.py`
- Test: `packages/core/tests/agents/test_recon_context_summarizer.py`（新建）

**Interfaces:**
- Consumes: `run_claude_prompt`（runner.py:106）、`recon_deliverable.md` 内容。
- Produces: `summarize_recon_context(recon_md: str, llm_client: Callable) -> str` —— 结构化摘要文本（注入 `{{RECON_CONTEXT}}`）。

**Why:** recon_deliverable.md 是 LLM 产的自由文本，vuln agent 自读依赖其彻底性。用轻量 LLM 单次摘要（参照 chain_verdict 模式）把 §4 endpoint inventory + §8 authz candidates 提炼成结构化短摘要注入 vuln prompt，减少遗漏。守铁律：输入是 LLM 轨自产 md，非确定性层。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/agents/test_recon_context_summarizer.py
import asyncio
import pytest
from shannon_core.agents.recon_context_summarizer import summarize_recon_context


@pytest.mark.asyncio
async def test_summarize_returns_structured_summary():
    recon_md = """
## 4. API Endpoint Inventory
| Method | Endpoint Path | Required Role | Object ID Parameters |
| GET | /api/orders/{id} | user | order_id |
| DELETE | /api/users/{id} | admin | user_id |

## 8. Authorization Vulnerability Candidates
### 8.1 Horizontal Privilege Escalation Candidates
| Priority | Endpoint Pattern | Object ID Param | Ownership Check |
| High | DELETE /api/orders/{order_id} | order_id | none detected |
"""

    async def fake_llm(prompt: str) -> str:
        # 模拟 LLM 返回结构化摘要
        assert "orders" in prompt and "Horizontal" in prompt, "summarizer prompt 应含 recon 内容"
        return ("- GET /api/orders/{id} (user, object-id=order_id)\n"
                "- DELETE /api/users/{id} (admin, object-id=user_id)\n"
                "IDOR candidate: DELETE /api/orders/{order_id} — no ownership check")

    result = await summarize_recon_context(recon_md, fake_llm)
    assert "orders" in result
    assert "IDOR" in result or "ownership" in result.lower()


@pytest.mark.asyncio
async def test_summarizer_degrades_gracefully_on_llm_failure():
    """LLM 失败时降级为截取 recon md §4/§8 原文（不崩）。"""

    async def failing_llm(prompt: str) -> str:
        raise RuntimeError("LLM unavailable")

    recon_md = "## 4. API Endpoint Inventory\n| GET | /api/x | user | - |\n"
    result = await summarize_recon_context(recon_md, failing_llm)
    # 降级：返回原文 §4 段（非空）
    assert "API Endpoint Inventory" in result or "/api/x" in result


@pytest.mark.asyncio
async def test_summarizer_decoupled_from_deterministic():
    """守铁律：summarizer prompt 不引确定性产物。"""
    from pathlib import Path
    import inspect
    src = inspect.getsource(__import__("shannon_core.agents.recon_context_summarizer", fromlist=["x"]))
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
        assert tok not in src, f"summarizer 引确定性 token: {tok}"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/agents/test_recon_context_summarizer.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.agents.recon_context_summarizer`。

- [ ] **Step 3: 实现——新建 summarizer**

```python
# packages/core/src/shannon_core/agents/recon_context_summarizer.py
"""Lightweight LLM summarizer for recon_deliverable.md → structured {{RECON_CONTEXT}}.

Reads the LLM-track recon deliverable (free-text markdown) and produces a compact
structured summary of §4 endpoint inventory + §8 authz candidates, injected into vuln
prompts so the vuln agent gets structured prior knowledge without re-reading the whole md.

Pattern mirrors chain_verdict.judge_chain_verdict: single run_claude_prompt call, JSON-ish
free-text parse, graceful degradation. Input is LLM-track self-produced md — NOT
deterministic-layer output (CLAUDE.md §1 ironclad rule).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """You are a compact summarizer for a static-recon deliverable.
Given the recon markdown, extract ONLY:
1. Every endpoint from Section 4 (API Endpoint Inventory): METHOD /path (required role,
   object-id parameter if any).
2. Every Section 8 authorization candidate (horizontal/vertical/context): the endpoint,
   the missing or weak control, and the data sensitivity.
Be terse — one line per item. Omit prose. If a section is absent, skip it silently.

Recon markdown:
---
{recon_md}
---

Respond with the extracted lines ONLY (no preamble, no JSON)."""


def _extract_sections(recon_md: str) -> str:
    """Degradation fallback: extract §4 + §8 raw text from markdown."""
    lines = recon_md.splitlines()
    out, capturing = [], False
    for line in lines:
        if line.strip().startswith("## 4.") or line.strip().startswith("## 8."):
            capturing = True
            out.append(line)
        elif line.strip().startswith("## ") and capturing:
            capturing = False
        elif capturing:
            out.append(line)
    return "\n".join(out).strip() or recon_md[:2000]


async def summarize_recon_context(
    recon_md: str,
    llm_client: Callable[[str], Awaitable[str]],
) -> str:
    """Summarize recon md into a structured context string for {{RECON_CONTEXT}}.

    Falls back to raw §4/§8 extraction if the LLM call fails (non-fatal).
    """
    if not recon_md or not recon_md.strip():
        return "(no recon deliverable available)"
    try:
        return await llm_client(_SUMMARY_PROMPT.format(recon_md=recon_md[:8000]))
    except Exception as e:  # noqa: BLE001 — graceful degradation
        logger.warning("recon_context summarizer LLM failed, falling back to raw extract: %s", e)
        return _extract_sections(recon_md)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/agents/test_recon_context_summarizer.py -v`
Expected: PASS（3 用例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/agents/recon_context_summarizer.py packages/core/tests/agents/test_recon_context_summarizer.py
git commit -m "feat(core): recon_context_summarizer 轻量 LLM 摘要 recon md → 结构化注入"
```

---

## Task 7: activities 注入 `{{RECON_CONTEXT}}` + `{{FRAMEWORK_ANALYSIS}}` 到 vuln prompt

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:175-181`（run_agent 的 prompt_variables 构造）+ 新增注入 helper
- Modify: `prompts/vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt` / `vuln-authz.txt` / `vuln-auth.txt`（加占位符块）
- Test: `packages/core/tests/prompts/test_vuln_recon_context_injection.py`（新建）

**Interfaces:**
- Consumes: Task 6 的 `summarize_recon_context`、`framework_analysis.json`、recon_deliverable.md。
- Produces: vuln agent 的 `prompt_variables` 含 `RECON_CONTEXT` + `FRAMEWORK_ANALYSIS`（条件）；vuln prompts 渲染出注入块。

**Why:** vuln agent 当前 `prompt_variables=None`（activities.py:175-181 只对 RECON/PRE_RECON 设）。注入结构化 recon 前序知识让 vuln agent 不靠自读 md 彻底性。`FRAMEWORK_ANALYSIS` 条件注入（白盒样本多空，非空才注入）。

**实现要点**：
- 在 `run_agent`（activities.py:155-229）的 prompt_variables 构造处（:175-181）加 vuln 分支：读 `deliverables/recon_deliverable.md` → `summarize_recon_context` → 塞 `RECON_CONTEXT`；读 `deliverables/framework_analysis.json` → `inferred_endpoints` 非空才塞 `FRAMEWORK_ANALYSIS`。
- `llm_client` 复用 activities.py 现有的 `_make_verdict_llm_client`（:1099-1110）模式注入。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_vuln_recon_context_injection.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def _read(name):
    return (PROMPTS_DIR / name).read_text("utf-8")


VULN_PROMPTS = ["vuln-injection.txt", "vuln-xss.txt", "vuln-ssrf.txt", "vuln-authz.txt", "vuln-auth.txt"]


def test_all_vuln_prompts_have_recon_context_placeholder():
    for name in VULN_PROMPTS:
        content = _read(name)
        assert "{{RECON_CONTEXT}}" in content, f"{name} 缺 {{RECON_CONTEXT}} 占位符"


def test_vuln_prompts_decoupled_from_deterministic():
    for name in VULN_PROMPTS:
        content = _read(name)
        for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
            assert tok not in content, f"{name} 引确定性 token: {tok}"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_vuln_recon_context_injection.py -v`
Expected: FAIL（`{{RECON_CONTEXT}}` 占位符缺）。

- [ ] **Step 3: 实现——vuln prompts 加占位符块**

在每个 vuln prompt（`vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt` / `vuln-authz.txt` / `vuln-auth.txt`）的 `<starting_context>`（或 context 引用段，Task 4 已在 inj+xss 加了 §4.2 字段块）之后，插入：

```text
**Structured Recon Context (prior knowledge):**
{{RECON_CONTEXT}}

(Auto-summarized from `{{DELIVERABLES_PATH}}/recon_deliverable.md` §4 + §8. If empty,
the recon deliverable is the source of truth — read it directly.)
{{FRAMEWORK_ANALYSIS}}
```

> 注：`{{FRAMEWORK_ANALYSIS}}` 由 activities 条件填充（非空才注入 framework 推断的 endpoints，否则注入空串）。

- [ ] **Step 4: 实现——activities.py 加 vuln prompt_variables 注入**

先在 activities.py 顶部 import 区加（找现有 import 段位置）：

```python
from shannon_core.agents.recon_context_summarizer import summarize_recon_context
```

然后在 `run_agent`（activities.py:155-229）的 prompt_variables 构造处。当前（:175-181）：

```python
prompt_variables = None
if agent_name == AgentName.RECON:
    prompt_variables = {}

if agent_name == AgentName.PRE_RECON:
    prompt_variables = prompt_variables or {}
```

改为（在 PRE_RECON 块后追加 vuln 分支）：

```python
prompt_variables = None
if agent_name == AgentName.RECON:
    prompt_variables = {}

if agent_name == AgentName.PRE_RECON:
    prompt_variables = prompt_variables or {}

if _is_vuln_agent(agent_name):
    prompt_variables = await _build_vuln_prompt_variables(input, prompt_variables or {})
```

新增两个 helper（放在 `run_agent` 函数之后、模块级）：

```python
_VULN_AGENT_NAMES = {
    AgentName.INJECTION, AgentName.XSS, AgentName.SSRF,
    AgentName.AUTHZ, AgentName.AUTH,
}


def _is_vuln_agent(agent_name: AgentName) -> bool:
    return agent_name in _VULN_AGENT_NAMES


async def _build_vuln_prompt_variables(
    input: "ActivityInput", base: dict
) -> dict:
    """Inject structured recon prior knowledge into vuln prompt_variables.

    - RECON_CONTEXT: LLM-summarized §4/§8 of recon_deliverable.md (always injected;
      degrades to raw extract if LLM unavailable). Source = LLM-track recon output.
    - FRAMEWORK_ANALYSIS: from framework_analysis.json — injected ONLY when
      inferred_endpoints is non-empty (whitebox samples are often empty).
    Both sources are LLM-track / code-layer pre-recon output — NEVER GitNexus
    deterministic-layer (CLAUDE.md §1 ironclad rule).
    """
    import json
    from shannon_core.git_manager import _repo_and_deliverables  # noqa: reuse existing path resolver if present; else use _get_paths

    repo, deliverables, _ = _get_paths(input)

    # RECON_CONTEXT: summarize recon_deliverable.md §4/§8
    recon_md_path = deliverables / "recon_deliverable.md"
    recon_md = recon_md_path.read_text("utf-8") if recon_md_path.exists() else ""
    llm_client = _make_verdict_llm_client()  # reuse existing factory (activities.py:1099)
    recon_context = await summarize_recon_context(recon_md, llm_client)
    base["RECON_CONTEXT"] = recon_context

    # FRAMEWORK_ANALYSIS: conditional — only when non-empty
    fw_path = deliverables / "framework_analysis.json"
    fw_lines: list[str] = []
    if fw_path.exists():
        try:
            fw = json.loads(fw_path.read_text("utf-8"))
            endpoints = fw.get("inferred_endpoints", []) or []
            if endpoints:
                fw_lines.append("Framework-inferred endpoints (auto-generated, verify each):")
                for ep in endpoints:
                    fw_lines.append(
                        f"- {ep.get('method','?')} {ep.get('path','?')} "
                        f"[source={ep.get('source','?')}, "
                        f"middleware={ep.get('middleware', [])}]"
                    )
        except (json.JSONDecodeError, OSError):
            pass  # non-fatal
    base["FRAMEWORK_ANALYSIS"] = "\n".join(fw_lines) if fw_lines else ""

    return base
```

> 实现者注意：`_get_paths`、`_make_verdict_llm_client`、`AgentName.INJECTION/XSS/SSRF/AUTHZ/AUTH` 的准确名字以 activities.py 现有代码为准（grep 确认；`AgentName` 枚举值见 `models/agents.py`）。若 `_get_paths` 签名不同，按其返回 `(repo, deliverables, scratchpad)` 调整。`_make_verdict_llm_client` 若不存在，参照 chain_verdict 的 llm_client 注入方式（activities.py:1099-1110 的 `_make_verdict_llm_client`）。

- [ ] **Step 5: 跑测试验证通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_vuln_recon_context_injection.py packages/core/tests/agents/test_recon_context_summarizer.py -v`
Expected: PASS。

- [ ] **Step 6: 跑 PromptManager 渲染冒烟（确认占位符被替换）**

Run: `cd /root/shannon-py && python -c "
from shannon_core.prompts.manager import PromptManager
from pathlib import Path
m = PromptManager(Path('prompts'))
t = m.load_sync('vuln-injection', {'web_url':'','repo_path':'/tmp','deliverables_path':'/tmp/d','scratchpad_path':'/tmp/s','RECON_CONTEXT':'TEST_RECON','FRAMEWORK_ANALYSIS':'TEST_FW'})
assert 'TEST_RECON' in t and 'TEST_FW' in t
assert '{{RECON_CONTEXT}}' not in t and '{{FRAMEWORK_ANALYSIS}}' not in t
print('inject render OK')
"`
Expected: 输出 `inject render OK`（占位符全替换）。

- [ ] **Step 7: 跑全套解耦测试 + 相关回归**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py packages/core/tests/prompts/test_vuln_starting_context.py packages/core/tests/prompts/test_recon_static_reconciliation.py packages/core/tests/prompts/test_vuln_recon_context_injection.py -v`
Expected: PASS（守铁律全过）。

- [ ] **Step 8: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-ssrf.txt prompts/vuln-authz.txt prompts/vuln-auth.txt packages/core/tests/prompts/test_vuln_recon_context_injection.py
git commit -m "feat(whitebox): vuln prompt 注入 {{RECON_CONTEXT}} + 条件 {{FRAMEWORK_ANALYSIS}}（SharedKnowledge 接通）"
```

---

## Self-Review（plan 完成后自查）

**1. Spec 覆盖**：
- A1（recon-static 对账）：Task 1（not-applicable）+ Task 2（章节+@include）+ Task 3（5 角度+Step 3.5）✓
- A2（vuln §4.2 字段 + framework auto-gen）：Task 4 ✓
- A3（inj source-list 警示）：Task 5 ✓
- B'2（SharedKnowledge 注入）：Task 6（summarizer）+ Task 7（activities 注入 + 占位符）✓
- 守铁律：每个 prompt task 有解耦断言；Task 7 注入源明确为 LLM 轨产物 ✓
- spec §5.5 framework_analysis 边界：Task 7 注释明确"LLM-track / code-layer pre-recon output, NEVER GitNexus" ✓

**2. Placeholder 扫描**：Task 7 Step 4 的 `_get_paths`/`_make_verdict_llm_client`/`AgentName.*` 已注明"以现有代码为准 grep 确认"——这是实现时核对点，非 placeholder（给出了完整代码骨架 + 备选说明）。其余 task 均含 actual code。✓

**3. 类型/命名一致**：`summarize_recon_context(recon_md, llm_client) -> str`（Task 6 定义）= Task 7 调用签名一致 ✓；`{{RECON_CONTEXT}}`/`{{FRAMEWORK_ANALYSIS}}`（Task 7 prompt）= activities 塞的 key 大写（`base["RECON_CONTEXT"]`，manager.py:175-178 自动 `{{KEY}}`）✓。

**4. B'2 调整记录**：spec §5.2 原列"frameworkAnalysis + endpoint inventory + authz candidates"。实现调研发现 framework_analysis.json 白盒样本全空，故 Task 7 将 `{{FRAMEWORK_ANALYSIS}}` 改为条件注入（非空才注入）、`{{RECON_CONTEXT}}` 为主（LLM 摘要 recon md §4+§8，必非空）。spec 文档后续同步此调整。

---

## 不在本 plan（Plan 2 / follow-up）

- **B'1 双轨 attack chain**（LLM Agent + GitNexus assembler + 合并函数）→ Plan 2。
- **B'3 黑盒按链验证** → follow-up。
- **framework_analyzer 白盒产空根因修复** → follow-up（影响 `{{FRAMEWORK_ANALYSIS}}` 注入价值，但 `{{RECON_CONTEXT}}` 不依赖它）。
