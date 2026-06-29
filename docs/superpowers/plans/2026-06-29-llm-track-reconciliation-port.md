# LLM 轨移植 TS 两个对账机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把原始 TS 项目（`/root/shannon`）的两个机械化对账机制（enumeration-completeness + coverage-reconciliation）以纯 prompt 方式移植到 PY 重构项目（`/root/shannon-py`）的 LLM 轨，降低 false-negative。

**Architecture:** 方案 A 纯 prompt——新增 2 个 `shared/` partial（移植 TS，裁剪 EC-D/E 交叉引用、EC-A 适配 Route Mapper 单 agent 覆盖 5 角度、CR-B 用 PY §四 safe section）+ 接入 `recon.txt`（补 frontend/gateway 枚举角度 + §4.3 对账表 + `@include`）/`vuln-authz.txt`（替换薄弱 `<coverage_requirements>` 为 `@include`）+ 1 个 prompt 内容断言测试。**零业务代码改动**，守 CLAUDE.md §1 双轨铁律（对账纯 LLM 自给，anchor count 不取 GitNexus）。

**Tech Stack:** prompt 工程（`@include(shared/_xxx.txt)` partial 机制）、pytest（prompt 内容断言）。

## Global Constraints

- **铁律（CLAUDE.md §1）**：新 partial 不得出现 `FORBIDDEN_PLACEHOLDERS` 任一（`PRE_RECON_GITNEXUS_TRACK`/`RECON_GITNEXUS_TRACK`/`FRAMEWORK_ENDPOINTS_SUMMARY`/`TAINT_FLOW_SUMMARY`/`CHAIN_AUDIT_INPUT`/`TOTAL_CHAINS` 等）；不得 `@include(shared/_static-dataflow-hints.txt)`；不得引用 `code_index.json`/`parameter_graph`/`SinkCallSite`。仅允许合法用户配置占位符 `{{DELIVERABLES_PATH}}`（对齐 `_cross-route-enumeration.txt:11` 现有用法）。
- **方案 A 零代码**：只改 prompt + 测试，不动 `packages/**/src` 业务代码、不动 harness/`conclusion_trigger` 代码逻辑（`@include` 是 prompt 文本，不是代码）。
- **不动已对齐项**：`vuln-injection.txt` slot 体系、`_cross-route-enumeration.txt`、recon §5、`vuln-*.txt` sink 清单——本次不碰。
- 分支 `feat/fork-py`；commit 信息前缀 `feat(prompts)` 或 `test(prompts)`。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `prompts/shared/_enumeration-completeness.txt` | recon pre-termination 枚举对账（5 角度 anchor-count 对账表 + prefix-family gap + self-check） | Create |
| `prompts/shared/_coverage-reconciliation.txt` | authz pre-termination 覆盖对账（F/C/G 集合 + 数据所有权判向量 + 每端点粒度 + self-check） | Create |
| `prompts/recon.txt` | 接入枚举对账：Route Mapper 补 frontend/gateway 角度 + deliverable §4.3 表格 + `@include` | Modify |
| `prompts/vuln-authz.txt` | 接入覆盖对账：`<coverage_requirements>` → `@include` | Modify |
| `packages/core/tests/prompts/test_reconciliation_partials.py` | prompt 内容断言（partial 存在/结构、`@include`、§4.3、角度关键词） | Create |

`@include` 渲染机制：`recon.txt`/`vuln-authz.txt` 已多处使用 `@include(shared/_xxx.txt)`（如 `recon.txt:44` `@include(shared/_endpoint-security-context.txt)`），新增 partial 照此机制，**无需改渲染代码**。

---

## Task 1: 新增 `_enumeration-completeness.txt`

**Files:**
- Create: `prompts/shared/_enumeration-completeness.txt`
- Test: `packages/core/tests/prompts/test_reconciliation_partials.py`（本 task 创建该文件 + 第一个断言）

**Interfaces:**
- Consumes: 无（纯方法论 partial，仅引用 `{{DELIVERABLES_PATH}}` 合法占位符 + 已有 recon §4.1/§5 交叉引用）。
- Produces: `prompts/shared/_enumeration-completeness.txt`——供 Task 3 `recon.txt` 经 `@include` 消费。

- [ ] **Step 1: 写失败测试（创建测试文件 + 第一条断言）**

创建 `packages/core/tests/prompts/test_reconciliation_partials.py`：

```python
"""LLM 轨移植 TS 两个对账机制的 prompt 内容断言。

Spec: docs/superpowers/specs/2026-06-29-llm-track-reconciliation-port-design.md
守 CLAUDE.md §1: 新 partial 不引入确定性产物（forbidden 由
test_static_dataflow_hints_decoupling.py 的 rglob 自动覆盖，此处只断言结构）。
"""
from pathlib import Path

# parents[4] = repo root（持有 prompts/），对齐 test_static_dataflow_hints_decoupling.py:20
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def test_enumeration_completeness_partial_exists_and_has_core_sections():
    p = PROMPTS_DIR / "shared" / "_enumeration-completeness.txt"
    assert p.exists(), "missing prompts/shared/_enumeration-completeness.txt"
    text = p.read_text()
    # EC-A: 5 角度（含补的 frontend + gateway）
    assert "frontend" in text.lower(), "EC-A 缺 frontend-call 角度"
    assert "gateway" in text.lower(), "EC-A 缺 gateway 角度"
    # EC-B: anchor-count 对账表
    assert "Enumeration Reconciliation" in text, "EC-B 缺对账表标题"
    assert "true-miss" in text, "EC-B 缺 true-miss 分类"
    # EC-C: prefix-family gap
    assert "prefix" in text.lower(), "EC-C 缺 prefix-family gap 检查"
    # EC-D/E 交叉引用（不重复已有机制）
    assert "_cross-route-enumeration" in text, "EC-D 未交叉引用 _cross-route-enumeration.txt"
    # self-check 硬阻
    assert "RECONNAISSANCE COMPLETE" in text, "缺 self-check 硬阻（do NOT announce RECONNAISSANCE COMPLETE）"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_enumeration_completeness_partial_exists_and_has_core_sections -v`
Expected: FAIL — `AssertionError: missing prompts/shared/_enumeration-completeness.txt`（文件尚未创建）。

- [ ] **Step 3: 建 partial（最小实现使其通过）**

创建 `prompts/shared/_enumeration-completeness.txt`，完整内容：

```
<enumeration_completeness>
**Pre-Termination Checklist: Enumeration Completeness Reconciliation**

This checklist MUST be completed AFTER your multi-angle enumeration has been merged
into Section 4 and BEFORE you announce "RECONNAISSANCE COMPLETE". Its purpose is to
catch endpoints that exist in the source code but never made it into your Section 4
inventory — the historical source of downstream false-negatives (an entire path-prefix
family silently skipped, so the authz/injection phase never sees it and cannot test it).

### Step EC-A: Confirm 5-angle enumeration coverage

Your Route Mapper agent (launched in systematic_approach step 3) MUST have covered ALL
FIVE enumeration angles and returned, for each, BOTH the endpoints found AND a source
anchor count (the grep pattern + file used + count of route sources matched in code):

1. **Route-definition layer** — router.get/post/put/delete, framework decorators,
   config-driven route tables (e.g. a paths-to-config object).
2. **Controller-method layer** — handler methods referenced by routes.
3. **Interface-contract layer** — proto http annotations, OpenAPI/swagger specs,
   graphql schema.
4. **Frontend-call layer** — axios/fetch/rpc calls in frontend code, reverse-inferred
   backend endpoints NOT declared in route files.
5. **Gateway layer** — nginx `location`/`proxy_pass`, ingress routes, gateway config.

If any angle was skipped, run the Route Mapper for that angle now and merge. Each
angle's anchor count drives the Section 4.3 reconciliation table below.

### Step EC-B: Anchor-count reconciliation (the mechanical check)

Sum the source anchor counts per angle. Compare each against the deduplicated endpoint
count you wrote to Section 4. Produce the **### 4.3 Enumeration Reconciliation** table
(one row per angle) in `{{DELIVERABLES_PATH}}/recon_deliverable.md`. Every non-zero
delta MUST be classified:
- `dedup` — same endpoint surfaced by another angle (name the duplicate angle).
- `out-of-scope` — not reachable via a web entry point (per scope_boundaries).
- `true-miss` — a real omission; you MUST go back and add it to Section 4 (and to
  Section 4.1 if it shares a handler) before terminating.

### Step EC-C: No silent prefix-family gaps

Scan Section 4 for path-prefix families declared in code (e.g. every route under
`/asset-analysis/*`, `/account/*`, `/order/*`). If a family has N members in code but
fewer appear in Section 4, each missing member is a `true-miss` — add it.

### Step EC-D: Shared-handler groups complete (cross-reference)

Handled by `shared/_cross-route-enumeration.txt` + Section 4.1 (Shared Controller Route
Groups): every route sharing a handler is listed one row per route, with pre-auth
(`**none**`) variants flagged. Confirm Section 4.1 is populated; no route packed into a
shared cell, no group member missing from its table.

### Step EC-E: Parameter completeness (cross-reference)

Handled by Section 5 (Parameter Completeness Verification): every Section 4 endpoint
lists ALL client-controllable parameters; template-rendering endpoints have their
hidden-parameter cross-check completed. Confirm Section 5 is populated.

### Step EC-F: Source/sink handoff

Section 9 injection sources enumerated. Sources are fully covered once endpoints and
their parameters are complete — verify no endpoint's input parameters were dropped.
Confirm Section 9 hands the injection-side sources to the downstream injection agent.

**Self-check before terminating:** Is the **### 4.3 Enumeration Reconciliation** table
present with every delta classified and zero `true-miss` remaining? Are Sections 4.1
and 5 populated? If not, do NOT announce "RECONNAISSANCE COMPLETE".
</enumeration_completeness>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_enumeration_completeness_partial_exists_and_has_core_sections -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/shared/_enumeration-completeness.txt packages/core/tests/prompts/test_reconciliation_partials.py
git commit -m "feat(prompts): 新增 _enumeration-completeness.txt（recon 枚举对账，移植 TS）"
```

---

## Task 2: 新增 `_coverage-reconciliation.txt`

**Files:**
- Create: `prompts/shared/_coverage-reconciliation.txt`
- Test: `packages/core/tests/prompts/test_reconciliation_partials.py`（追加第二条断言）

**Interfaces:**
- Consumes: 无（纯方法论；引用 `{{DELIVERABLES_PATH}}/recon_deliverable.md` §4 + authz §四 safe section）。
- Produces: `prompts/shared/_coverage-reconciliation.txt`——供 Task 4 `vuln-authz.txt` 经 `@include` 消费。

- [ ] **Step 1: 写失败测试（追加断言到已有测试文件）**

在 `packages/core/tests/prompts/test_reconciliation_partials.py` 末尾追加：

```python
def test_coverage_reconciliation_partial_exists_and_has_core_sections():
    p = PROMPTS_DIR / "shared" / "_coverage-reconciliation.txt"
    assert p.exists(), "missing prompts/shared/_coverage-reconciliation.txt"
    text = p.read_text()
    # CR-A: USER 端点全集 F
    assert "USER" in text, "CR-A 缺 USER 端点全集 F"
    # CR-C: G = F \ C 覆盖差集
    assert ("F \\ C" in text) or ("G = F" in text), "CR-C 缺 G = F \\ C 差集判定"
    # CR-D: 数据所有权判向量（tenant/region selector 也是向量）
    assert "tenant" in text.lower(), "CR-D 缺 tenant/region selector 向量分类"
    # CR-E: 每端点粒度（禁止全局合并）
    assert "per-endpoint" in text.lower() or "N independent" in text, "CR-E 缺每端点粒度规则"
    # self-check 硬阻
    assert "AUTHORIZATION ANALYSIS COMPLETE" in text, "缺 self-check 硬阻"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_coverage_reconciliation_partial_exists_and_has_core_sections -v`
Expected: FAIL — `missing prompts/shared/_coverage-reconciliation.txt`。

- [ ] **Step 3: 建 partial（最小实现）**

创建 `prompts/shared/_coverage-reconciliation.txt`，完整内容：

```
<coverage_reconciliation>
**Pre-Termination Checklist: Full USER-Endpoint Coverage Reconciliation**

This checklist MUST be completed AFTER you finish analyzing every Section 8 candidate and
BEFORE you emit your deliverable or announce "AUTHORIZATION ANALYSIS COMPLETE". Its
purpose is to catch endpoints that Recon listed in Section 4 but that never made it into
a verdict — the historical source of authorization false-negatives (e.g. an endpoint whose
only vector is a tenant/region selector like `brokerage`, not a classic object id, so
Recon did not flag it as a Section 8 candidate).

### Step CR-A: Build the USER endpoint universe (F)

Open `{{DELIVERABLES_PATH}}/recon_deliverable.md` Section 4 (API Endpoint Inventory).
Collect every endpoint whose Required Role is `user` (authenticated, non-admin) into
set **F**. Record each as `METHOD /path`. Include endpoints even when their
Client-Controlled Parameters column is `None` or lists only non-obvious selectors.

### Step CR-B: Collect already-judged endpoints (C)

Gather the set of endpoints you have already produced a verdict for:
- every `endpoint` field in your exploitation queue (vulnerable verdicts), AND
- every endpoint recorded in Section 四「已分析并确认安全的向量」 of your
  `{{DELIVERABLES_PATH}}/authz_analysis_deliverable.md` (safe verdicts).

Call this set **C**.

### Step CR-C: Compute the coverage gap and judge each

Compute **G = F \ C** (USER endpoints with no verdict yet). For EACH endpoint in G you
MUST produce a verdict — there is no "skip". For each, trace from the endpoint to its
side effect and decide:

- **Vulnerable** → add a finding to the exploitation queue (follow the per-endpoint
  granularity rule in Step CR-E).
- **Safe** → record it explicitly in Section 四 (已分析并确认安全的向量) with the
  guard that protects it.

A non-empty G at termination = INCOMPLETE analysis.

### Step CR-D: Judge by data ownership, not by parameter presence

The vector test is NOT "does this endpoint have a client-controllable parameter" — many
parameters are benign. The test is:

> Does any client-controllable parameter, when its value is changed, cause the endpoint to
> RETURN or MUTATE data that does not belong to the caller — another user's object, or
> another tenant's/region's data — without a sufficient ownership/tenant-binding guard?

- Parameters that only affect presentation of the caller's OWN data are NOT vectors:
  pagination (`page`, `per_page`, `offset`, `limit`), sort (`order_by`, `sort`),
  filtering the caller's own records (`type`, `status`, `date_start`, `date_end`),
  locale/UI (`lang`, `site`, `channel`). Verdict: safe, unless another parameter on the
  same endpoint is a vector.
- Parameters that select WHOSE data is touched ARE vectors: object identifiers
  (`coupon_id`, `account_id`, `order_id`, `id`) AND tenant/region/identity selectors
  (`brokerage`, `market`, `region`, `tenant_id`, `org_id`) when the server forwards the
  caller's identity unboundedly. Verdict: vulnerable unless a sufficient guard dominates
  all paths.

When a parameter's role is ambiguous, trace the code to the sink rather than guessing.

### Step CR-E: Per-endpoint granularity (no global hand-waves)

Do NOT collapse a whole class of endpoints into a single "any /api/* with brokerage"
finding. If a tenant/region selector (or any shared vector) affects N endpoints, produce
N independent findings — one per endpoint — each with its own `endpoint`,
`vulnerable_code_location`, and `minimal_witness`. The shared root cause may be cited in
`reason`/`notes`, but every affected endpoint must appear as its own queue entry so the
exploitation phase can verify each independently.

**Self-check before terminating:** Is G empty? Is every USER endpoint from Section 4 either
in the exploitation queue or in Section 四 (已分析并确认安全的向量)? If not, do not announce
"AUTHORIZATION ANALYSIS COMPLETE".
</coverage_reconciliation>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_coverage_reconciliation_partial_exists_and_has_core_sections -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/shared/_coverage-reconciliation.txt packages/core/tests/prompts/test_reconciliation_partials.py
git commit -m "feat(prompts): 新增 _coverage-reconciliation.txt（authz 覆盖对账，移植 TS）"
```

---

## Task 3: 接入 `recon.txt`（Route Mapper 补角度 + §4.3 表格 + @include）

**Files:**
- Modify: `prompts/recon.txt`（三处：Route Mapper Agent 指令 `:143`、deliverable §4.2 后插 §4.3 `:276`/`:297` 之间、`<conclusion_trigger>` `:470` 前）
- Test: `packages/core/tests/prompts/test_reconciliation_partials.py`（追加 3 条断言）

**Interfaces:**
- Consumes: Task 1 的 `_enumeration-completeness.txt`。
- Produces: `recon.txt` 在 pre-termination 时 `@include` 并执行枚举对账，产出 §4.3 表格。

- [ ] **Step 1: 写失败测试（追加 3 条断言）**

在 `packages/core/tests/prompts/test_reconciliation_partials.py` 末尾追加：

```python
def test_recon_includes_enumeration_completeness():
    text = (PROMPTS_DIR / "recon.txt").read_text()
    assert "@include(shared/_enumeration-completeness.txt)" in text, \
        "recon.txt 未 @include _enumeration-completeness.txt"


def test_recon_route_mapper_has_frontend_and_gateway_angles():
    text = (PROMPTS_DIR / "recon.txt").read_text()
    # 用新增的特定措辞断言（recon.txt §2 原有 "Frontend:"，用泛 "frontend" 会假绿）
    assert "frontend-call layer" in text, "Route Mapper 缺 frontend-call 枚举角度"
    assert "gateway layer" in text, "Route Mapper 缺 gateway 枚举角度"
    assert "Enumeration angles" in text, "Route Mapper 缺 5 角度枚举指令"


def test_recon_deliverable_has_section_43():
    text = (PROMPTS_DIR / "recon.txt").read_text()
    assert "4.3 Enumeration Reconciliation" in text, \
        "recon deliverable 缺 §4.3 Enumeration Reconciliation 表格结构"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_recon_includes_enumeration_completeness packages/core/tests/prompts/test_reconciliation_partials.py::test_recon_route_mapper_has_frontend_and_gateway_angles packages/core/tests/prompts/test_reconciliation_partials.py::test_recon_deliverable_has_section_43 -v`
Expected: 3 FAIL（`@include` 未加、角度未补、§4.3 未加）。

- [ ] **Step 3: 改 `recon.txt` —— Route Mapper Agent 补 5 角度 + anchor count**

用 Edit，把 Route Mapper Agent 指令的结尾扩展。`old_string`（`recon.txt` 现有结尾片段，唯一）：

```
Include the router definition file:line range (e.g., router.js:40-42) for each group so downstream agents can cross-reference."
```

`new_string`：

```
Include the router definition file:line range (e.g., router.js:40-42) for each group so downstream agents can cross-reference. **Enumeration angles (cover ALL FIVE; for each return a source anchor count = grep pattern + file + count of route sources matched):** (1) route-definition layer — router verbs / framework decorators / config-driven route tables; (2) controller-method layer — handler methods referenced by routes; (3) interface-contract layer — proto http annotations, OpenAPI/swagger specs, graphql schema; (4) frontend-call layer — search frontend code for axios/fetch/rpc calls and reverse-infer backend endpoints NOT declared in route files; (5) gateway layer — scan nginx `location`/`proxy_pass`, ingress routes, gateway config for additional endpoints. The per-angle anchor counts drive the Section 4.3 Enumeration Reconciliation table."
```

- [ ] **Step 4: 改 `recon.txt` —— deliverable 新增 §4.3 表格结构**

用 Edit，在 §5 标题前插入 §4.3。`old_string`（`recon.txt` 现有，唯一）：

```
## 5. Potential Input Vectors for Vulnerability Analysis
```

`new_string`：

```
## 4.3 Enumeration Reconciliation

Produce this table AFTER merging all five enumeration angles into Section 4 (see `shared/_enumeration-completeness.txt`). One row per angle. Every non-zero Delta MUST be classified `dedup` / `out-of-scope` / `true-miss`; every `true-miss` MUST be added back to Section 4 (and Section 4.1 if it shares a handler) before announcing completion.

| Angle | Anchor Count | Reported (deduped) | Delta | Explanation (dedup / out-of-scope / true-miss) |
|---|---|---|---|---|
| Route-definition | | | | |
| Controller-method | | | | |
| Interface-contract | | | | |
| Frontend-call | | | | |
| Gateway | | | | |

**Self-check:** zero `true-miss` remaining, else do NOT announce "RECONNAISSANCE COMPLETE".

## 5. Potential Input Vectors for Vulnerability Analysis
```

- [ ] **Step 5: 改 `recon.txt` —— `@include` 放 conclusion_trigger 前**

用 Edit。`old_string`（`recon.txt` 中开标签 `<conclusion_trigger>`，唯一——注意闭标签是 `</conclusion_trigger>` 不匹配）：

```
<conclusion_trigger>
```

`new_string`：

```
@include(shared/_enumeration-completeness.txt)

<conclusion_trigger>
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_recon_includes_enumeration_completeness packages/core/tests/prompts/test_reconciliation_partials.py::test_recon_route_mapper_has_frontend_and_gateway_angles packages/core/tests/prompts/test_reconciliation_partials.py::test_recon_deliverable_has_section_43 -v`
Expected: 3 PASS。

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add prompts/recon.txt packages/core/tests/prompts/test_reconciliation_partials.py
git commit -m "feat(prompts): recon.txt 接入枚举对账（Route Mapper 补 frontend/gateway 角度 + §4.3 表格 + @include）"
```

---

## Task 4: 接入 `vuln-authz.txt`（替换 `<coverage_requirements>` 为 @include）

**Files:**
- Modify: `prompts/vuln-authz.txt`（替换 `:314-317` 的 `<coverage_requirements>` 块）
- Test: `packages/core/tests/prompts/test_reconciliation_partials.py`（追加 2 条断言）

**Interfaces:**
- Consumes: Task 2 的 `_coverage-reconciliation.txt`。
- Produces: `vuln-authz.txt` 在 pre-termination 时 `@include` 并执行 F/C/G 覆盖对账。

- [ ] **Step 1: 写失败测试（追加 2 条断言）**

在 `packages/core/tests/prompts/test_reconciliation_partials.py` 末尾追加：

```python
def test_authz_includes_coverage_reconciliation():
    text = (PROMPTS_DIR / "vuln-authz.txt").read_text()
    assert "@include(shared/_coverage-reconciliation.txt)" in text, \
        "vuln-authz.txt 未 @include _coverage-reconciliation.txt"


def test_authz_weak_coverage_requirements_replaced():
    """薄弱的 <coverage_requirements> 一句话应被对账 partial 取代。"""
    text = (PROMPTS_DIR / "vuln-authz.txt").read_text()
    # 原文是 <coverage_requirements> Test all endpoints ... </coverage_requirements>
    # 替换后该块不应再以独立 <coverage_requirements> 标签形式存在
    assert "@include(shared/_coverage-reconciliation.txt)" in text
    # 确认没有残留的空洞 coverage_requirements 块（允许文字提及 coverage）
    assert "<coverage_requirements>\n- Test **all**" not in text, \
        "薄弱的 <coverage_requirements> 块未被替换"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_authz_includes_coverage_reconciliation packages/core/tests/prompts/test_reconciliation_partials.py::test_authz_weak_coverage_requirements_replaced -v`
Expected: 2 FAIL（`@include` 未加、薄弱块仍在）。

- [ ] **Step 3: 改 `vuln-authz.txt` —— 替换 `<coverage_requirements>` 块**

用 Edit。`old_string`（`vuln-authz.txt:314-317` 现有，唯一）：

```
<coverage_requirements>
- Test **all** endpoints from recon section 8
- Include both REST and GraphQL endpoints
</coverage_requirements>
```

`new_string`：

```
@include(shared/_coverage-reconciliation.txt)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py::test_authz_includes_coverage_reconciliation packages/core/tests/prompts/test_reconciliation_partials.py::test_authz_weak_coverage_requirements_replaced -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-authz.txt packages/core/tests/prompts/test_reconciliation_partials.py
git commit -m "feat(prompts): vuln-authz.txt 用覆盖对账 partial 替换薄弱 <coverage_requirements>"
```

---

## Task 5: 解耦铁律回归 + 全套断言验证

**Files:**
- Test: `packages/core/tests/prompts/test_reconciliation_partials.py`（全套）+ 现有 `test_static_dataflow_hints_decoupling.py`

**Interfaces:**
- Consumes: Task 1-4 全部产物。
- Produces: 验证新 partial 不破坏 CLAUDE.md §1 铁律（不触发 forbidden、不 include 确定性产物），全套断言绿。

- [ ] **Step 1: 跑解耦铁律测试（确认新 partial 不触发 forbidden）**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v`
Expected: PASS（全部）。`test_no_prompt_includes_static_dataflow_hints` + `test_no_llm_track_prompt_has_forbidden_placeholders` 用 rglob 自动覆盖新 partial；新 partial 内容不含 forbidden token / 不 include static-dataflow-hints，故自动合规。

若 FAIL：检查 `_enumeration-completeness.txt` / `_coverage-reconciliation.txt` 是否误写 `{{PRE_RECON_GITNEXUS_TRACK}}` 等 forbidden 占位符或误 `@include(shared/_static-dataflow-hints.txt)`，移除后重跑。

- [ ] **Step 2: 跑新增 prompt 内容断言全套**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/prompts/test_reconciliation_partials.py -v`
Expected: 7 PASS（Task 1×1 + Task 2×1 + Task 3×3 + Task 4×2）。

- [ ] **Step 3: 确认未误改业务代码（方案 A 零代码约束）**

Run: `cd /root/shannon-py && git diff --stat feat/fork-py~5..HEAD -- 'packages/**/src/**/*.py'`
Expected: 空输出（本 plan 5 个 commit 不应触及 `packages/**/src` 业务代码；只动 `prompts/` + `packages/core/tests/prompts/`）。

若有业务代码改动：回退该改动（本任务纯 prompt + 测试）。

- [ ] **Step 4: 验收总结（无需额外 commit）**

确认上述 3 步全绿。本 task 是验证型，不产生新代码改动，故无 commit。若 Step 1/3 发现问题并修复，则 commit：

```bash
cd /root/shannon-py
git add -A
git commit -m "test(prompts): 解耦铁律回归 + 对账 partial 全套断言验证"
```

---

## 真机冒烟（plan 范围外，交付后人工）

- recon 真机跑后，检查 `recon_deliverable.md` 含 §4.3 Enumeration Reconciliation 表格，且零 `true-miss`（或已回补）。
- authz 真机跑后，检查 `G = ∅`（每个 USER 端点在 exploitation queue 或 §四）。
- 双引擎（claude-agent-sdk / openai-agents）跑同一份改后 prompt，行为一致。
