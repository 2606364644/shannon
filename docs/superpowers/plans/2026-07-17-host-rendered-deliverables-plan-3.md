# Host-Rendered Deliverables — Plan 3（vuln agent，5 class 共用）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Plan 1 框架已落地。本 plan 是增量：5 个 vuln agent（injection/xss/auth/ssrf/authz）接入 host 渲染。

**Goal:** 5 个 vuln agent 调 `set_*` 结构化工具，host collector 收集 + `render_vuln_deliverable(vuln_class, data)` 渲染 `{vt}_analysis_deliverable.md`（skipped→placeholder 不 fail）。**vuln 同时走两通道**：analysis md（collector，本 plan）+ exploitation_queue.json（structured_output，已有不动）。

**Architecture:** TS vuln 是「5 class 共用一个 collector + renderer，按 vulnClass branching」：4 个 set_* 里 `set_strategic_intelligence` 是 per-class（5 schema），其余 3 个 shared。PY 复用 Plan 1 框架：`make_vuln_sections(vuln_class)` 按 class 选 strategic_intelligence model → registry 给 5 个 vuln agent 各注册一个 `CollectorSpec` → `render_vuln_deliverable(vuln_class, data)` 按映射表渲染。

**Tech Stack:** pydantic、pytest（同 Plan 1/2）。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 3 = vuln）

## Global Constraints

- **依赖 Plan 1 接口（已落地）**：`CollectorBase`/`SectionSpec`/`bridge`/`CollectorSpec`/`get_collector_spec`/`renderers/_helpers`（`placeholder`/`render_table`）/`executor.execute(collector_spec=...)`。
- **双通道并存**：vuln agent 的 `{vt}_exploitation_queue.json` 继续走 `executor.py:147-154` structured_output（**不动**）；本 plan 只加 `{vt}_analysis_deliverable.md` 的 collector+renderer 通道。executor 同时处理两者（Plan 1 已设计并存）。
- **§1 双轨独立**：renderer 纯函数；vuln 的 source 是 LLM 自身分析（recon + 自 grep），不引 GitNexus 确定性层。
- **§2 双引擎**：复用 bridge。
- **TS 对齐**：4 个 set_* 的 schema、5 张 per-class 映射表、prompt 文案 1:1 移植 `upstream/main:apps/worker/src/collectors/vuln-collector.ts` + `services/vuln-renderer.ts`。
- **5 class 全覆盖**：injection / xss / auth / ssrf / authz。
- **TDD + 测试陷阱**：每 task 先失败测试；只跑改动子集（memory `pytest-whitebox-hang`）。
- **诊断暂不移除**：`_enrich_missing_deliverable_error` 保留到 Plan 5。

## File Structure

- Create: `packages/core/src/shannon_core/collectors/vuln.py`（3 shared model + 5 per-class strategic_intel model + `make_vuln_sections(vuln_class)` + `make_vuln_collector(vuln_class)`）
- Create: `packages/core/src/shannon_core/renderers/vuln.py`（`render_vuln_deliverable` + 4 张 per-class 映射表）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（`get_collector_spec` 加 5 个 vuln 分支）
- Modify: `prompts/vuln-injection.txt`、`vuln-xss.txt`、`vuln-auth.txt`、`vuln-ssrf.txt`、`vuln-authz.txt`（Write→set_*）

---

### Task 1: vuln shared + per-class section models

**Files:**
- Create: `packages/core/src/shannon_core/collectors/vuln.py`
- Test: `packages/core/tests/collectors/test_vuln_models.py`

**Interfaces:**
- Produces: 3 shared model（`VulnFindingsSummary`/`VulnSafeVectors`/`VulnBlindSpots`）+ 5 per-class model（`InjectionStrategicIntel` 等）+ `VULN_CLASSES: list[str]` + `make_vuln_sections(vuln_class) -> list[SectionSpec]` + `make_vuln_collector(vuln_class) -> CollectorBase`。

**TS 对照：** `upstream/main:apps/worker/src/collectors/vuln-collector.ts`（shared line 40-152；per-class line 155-285）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_vuln_models.py
import pytest
from shannon_core.collectors.vuln import (
    VULN_CLASSES, make_vuln_sections, make_vuln_collector,
    InjectionStrategicIntel, XssStrategicIntel,
)


def test_five_vuln_classes():
    assert set(VULN_CLASSES) == {"injection", "xss", "auth", "ssrf", "authz"}


def test_each_class_has_four_set_tools():
    for vc in VULN_CLASSES:
        tools = {s.tool_name for s in make_vuln_sections(vc)}
        assert tools == {"set_findings_summary", "set_strategic_intelligence",
                         "set_safe_vectors", "set_blind_spots"}, vc


def test_strategic_intel_model_differs_per_class():
    inj = make_vuln_sections("injection")
    xss = make_vuln_sections("xss")
    inj_intel = next(s for s in inj if s.tool_name == "set_strategic_intelligence")
    xss_intel = next(s for s in xss if s.tool_name == "set_strategic_intelligence")
    assert inj_intel.model_cls is InjectionStrategicIntel
    assert xss_intel.model_cls is XssStrategicIntel
    assert inj_intel.model_cls is not xss_intel.model_cls


def test_make_collector_per_class():
    for vc in VULN_CLASSES:
        c = make_vuln_collector(vc)
        assert set(c.get_all().keys()) == {
            "findings_summary", "strategic_intelligence", "safe_vectors", "blind_spots"}
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_vuln_models.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/collectors/vuln.py
"""vuln 5 class 共用 collector:3 shared + 1 per-class strategic_intelligence。

移植 TS apps/worker/src/collectors/vuln-collector.ts。
set_findings_summary / set_safe_vectors / set_blind_spots 是 shared;
set_strategic_intelligence 按 vuln_class 选 5 个 schema 之一。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .base import CollectorBase, SectionSpec

VULN_CLASSES: list[str] = ["injection", "xss", "auth", "ssrf", "authz"]


# ── shared: set_findings_summary (§1 + §2) ─────────────────────────────
class _Pattern(BaseModel):
    name: str
    description: str
    implication: str
    representative_finding_ids: list[str] = Field(..., min_length=1,
        description="exhibit 此 pattern 的 finding ID(须与 exploitation queue ID 一致)。")


class VulnFindingsSummary(BaseModel):
    key_outcome: str = Field(..., description="headline 结果(§1)。")
    patterns: list[_Pattern] = Field(default_factory=list,
        description="dominant patterns(§2)。空数组→renderer 渲 No dominant patterns。")


# ── shared: set_safe_vectors (§4) ──────────────────────────────────────
class _SafeVector(BaseModel):
    subject: str
    location: str
    defense_mechanism: str
    render_context: str | None = Field(None,
        description="XSS-only:HTML_BODY/HTML_ATTRIBUTE/JAVASCRIPT_STRING/URL_PARAM/CSS_VALUE。")


class VulnSafeVectors(BaseModel):
    vectors: list[_SafeVector] = Field(default_factory=list)


# ── shared: set_blind_spots (§5) ───────────────────────────────────────
class _BlindSpotItem(BaseModel):
    heading: str
    description: str


class VulnBlindSpots(BaseModel):
    items: list[_BlindSpotItem] = Field(default_factory=list)


# ── per-class: set_strategic_intelligence (§3) ─────────────────────────
# 完整字段对照 TS vuln-collector.ts:155-285 补齐(下方为已核字段 + 待补标注)。
class InjectionStrategicIntel(BaseModel):
    """对照 TS InjectionStrategicIntelSchema(line 155)。"""
    defensive_evasion_waf: str
    error_based_potential: str
    confirmed_database_technology: str
    # 若 TS 还有字段(line 18 之后),对照补齐


class XssStrategicIntel(BaseModel):
    """对照 TS XssStrategicIntelSchema(line 179)。"""
    csp_analysis: str
    cookie_security: str
    # 对照 TS line 36 之后补齐


class AuthStrategicIntel(BaseModel):
    """对照 TS AuthStrategicIntelSchema(line 197)。"""
    authentication_method: str
    session_token_details: str
    password_policy: str
    # 对照 TS line 59 之后补齐


class SsrfStrategicIntel(BaseModel):
    """对照 TS SsrfStrategicIntelSchema(line 220)。"""
    http_client_library: str
    request_architecture: str
    internal_services: str
    # 对照 TS line 82 之后补齐


class AuthzStrategicIntel(BaseModel):
    """对照 TS AuthzStrategicIntelSchema(line 243)。"""
    session_management_architecture: str
    role_permission_model: str
    resource_access_patterns: str
    workflow_implementation: str
    # 对照 TS line 114 之后补齐


_STRATEGIC_INTEL_MODELS = {
    "injection": InjectionStrategicIntel,
    "xss": XssStrategicIntel,
    "auth": AuthStrategicIntel,
    "ssrf": SsrfStrategicIntel,
    "authz": AuthzStrategicIntel,
}

_VULN_SECTIONS_ORDER = ["findings_summary", "strategic_intelligence", "safe_vectors", "blind_spots"]


def make_vuln_sections(vuln_class: str) -> list[SectionSpec]:
    if vuln_class not in _STRATEGIC_INTEL_MODELS:
        raise ValueError(f"unknown vuln class: {vuln_class}")
    return [
        SectionSpec("findings_summary", "set_findings_summary", VulnFindingsSummary,
                    "headline 结果 + dominant patterns(§1/§2)。"),
        SectionSpec("strategic_intelligence", "set_strategic_intelligence",
                    _STRATEGIC_INTEL_MODELS[vuln_class],
                    f"{vuln_class} 战略情报(§3)。"),
        SectionSpec("safe_vectors", "set_safe_vectors", VulnSafeVectors,
                    "已确认安全的向量(§4)。"),
        SectionSpec("blind_spots", "set_blind_spots", VulnBlindSpots,
                    "分析盲点/约束(§5)。"),
    ]


def make_vuln_collector(vuln_class: str) -> CollectorBase:
    return CollectorBase(known_sections=list(_VULN_SECTIONS_ORDER))
```

> **per-class 字段完整移植**：执行时逐个对照 TS `vuln-collector.ts:155-285` 的 5 个 schema 补齐**所有**字段（上方为 grep 已核字段，部分 class TS 可能还有更多字段）。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_vuln_models.py -q` → 4 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/vuln.py packages/core/tests/collectors/test_vuln_models.py && git commit -m "feat(collectors): vuln 5 class 共用 — 3 shared + per-class strategic_intel model"`

---

### Task 2: `render_vuln_deliverable` + 4 张 per-class 映射表

**Files:**
- Create: `packages/core/src/shannon_core/renderers/vuln.py`
- Test: `packages/core/tests/renderers/test_vuln.py`

**Interfaces:**
- Produces: `render_vuln_deliverable(vuln_class: str, data: dict) -> str`。

**TS 对照：** `upstream/main:apps/worker/src/services/vuln-renderer.ts`（`TITLES` line 40 / `SECTION_FOUR_HEADING` line 48 / `STRATEGIC_INTEL_SUBHEADERS` line 56 / `SECTION_FOUR_COLUMNS` line 94 / `renderVulnDeliverable` line 217）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/renderers/test_vuln.py
from shannon_core.renderers.vuln import render_vuln_deliverable
from shannon_core.renderers._helpers import placeholder


def test_all_missing_renders_placeholders_per_class():
    for vc in ["injection", "xss", "auth", "ssrf", "authz"]:
        md = render_vuln_deliverable(vc, {})
        assert placeholder("Section 1", "set_findings_summary") in md, vc
        assert placeholder("Section 3", "set_strategic_intelligence") in md, vc


def test_findings_summary_renders_outcome_and_patterns():
    md = render_vuln_deliverable("auth", {"findings_summary": {
        "key_outcome": "weak pwd policy",
        "patterns": [{"name": "Weak Session", "description": "d", "implication": "i",
                      "representative_finding_ids": ["AUTH-VULN-1"]}]}})
    assert "## 1. Executive Summary" in md and "weak pwd policy" in md
    assert "## 2. Dominant Vulnerability Patterns" in md and "Weak Session" in md


def test_xss_safe_vectors_has_render_context_column():
    md = render_vuln_deliverable("xss", {"safe_vectors": {"vectors": [
        {"subject": "q", "location": "s.js:1", "defense_mechanism": "escape",
         "render_context": "HTML_BODY"}]}})
    assert "Render Context" in md and "HTML_BODY" in md


def test_non_xss_safe_vectors_no_render_context_column():
    md = render_vuln_deliverable("injection", {"safe_vectors": {"vectors": [
        {"subject": "q", "location": "s.js:1", "defense_mechanism": "prepared"}]}})
    assert "Render Context" not in md


def test_strategic_intel_uses_per_class_subheaders():
    md = render_vuln_deliverable("ssrf", {"strategic_intelligence": {
        "http_client_library": "axios", "request_architecture": "server",
        "internal_services": "none"}})
    # subheader 由 STRATEGIC_INTEL_SUBHEADERS 映射(对照 TS)
    assert "## 3. Strategic Intelligence" in md and "axios" in md
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/renderers/test_vuln.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/renderers/vuln.py
"""移植 TS vuln-renderer.ts::renderVulnDeliverable。5 class 共用,按 vuln_class branching。

4 张 per-class 映射表 + 5 个 render 函数。纯函数,不引确定性层(守 §1)。
"""
from __future__ import annotations

from ._helpers import placeholder, render_table

# ── per-class 映射表(对照 TS vuln-renderer.ts:40-105 逐字移植) ──────────
TITLES = {
    "injection": "Injection Analysis Report",
    "xss": "Cross-Site Scripting (XSS) Analysis Report",
    "auth": "Authentication Analysis Report",
    "ssrf": "SSRF Analysis Report",
    "authz": "Authorization (Authz) Analysis Report",
    # 完整 title 对照 TS TITLES(line 40)
}

SECTION_FOUR_HEADING = {
    "injection": "4. Parameters Analyzed and Confirmed Secure",
    "xss": "4. Vectors Analyzed and Confirmed Secure",
    "auth": "4. Components Analyzed and Confirmed Secure",
    "ssrf": "4. Components Analyzed and Confirmed Secure",
    "authz": "4. Endpoints Analyzed and Confirmed Secure",
    # 对照 TS SECTION_FOUR_HEADING(line 48)
}

# [field_name, friendly_header] per class — 把 strategic_intel schema 字段映射到 §3 子标题
STRATEGIC_INTEL_SUBHEADERS = {
    "injection": [("defensive_evasion_waf", "Defensive/Evasion (WAF)"),
                  ("error_based_potential", "Error-Based Potential"),
                  ("confirmed_database_technology", "Confirmed Database Technology")],
    "xss": [("csp_analysis", "CSP Analysis"), ("cookie_security", "Cookie Security")],
    "auth": [("authentication_method", "Authentication Method"),
             ("session_token_details", "Session Token Details"),
             ("password_policy", "Password Policy")],
    "ssrf": [("http_client_library", "HTTP Client Library"),
             ("request_architecture", "Request Architecture"),
             ("internal_services", "Internal Services")],
    "authz": [("session_management_architecture", "Session Management Architecture"),
              ("role_permission_model", "Role/Permission Model"),
              ("resource_access_patterns", "Resource Access Patterns"),
              ("workflow_implementation", "Workflow Implementation")],
    # 完整 subheader 对照 TS STRATEGIC_INTEL_SUBHEADERS(line 56)
}

# §4 列形状:XSS 多 Render Context 列;subject/location 列名 per class
SECTION_FOUR_COLUMNS = {
    "injection": {"subject": "Parameter", "location": "File Location", "include_render_context": False},
    "xss": {"subject": "Source", "location": "Endpoint/File Location", "include_render_context": True},
    "auth": {"subject": "Component", "location": "Location", "include_render_context": False},
    "ssrf": {"subject": "Component", "location": "Location", "include_render_context": False},
    "authz": {"subject": "Endpoint", "location": "Guard Location", "include_render_context": False},
    # 对照 TS SECTION_FOUR_COLUMNS(line 94)
}


def _exec(summary: dict | None) -> str:
    if not summary:
        return f"## 1. Executive Summary\n\n{placeholder('Section 1', 'set_findings_summary')}"
    return f"## 1. Executive Summary\n\n{summary.get('key_outcome', '')}"


def _patterns(summary: dict | None) -> str:
    head = "## 2. Dominant Vulnerability Patterns"
    if not summary:
        return f"{head}\n\n{placeholder('Section 2', 'set_findings_summary')}"
    patterns = summary.get("patterns") or []
    if not patterns:
        return f"{head}\n\n*No dominant patterns identified.*"
    blocks = []
    for i, p in enumerate(patterns, 1):
        blocks.append("\n".join([
            f"### Pattern {i}: {p.get('name', '')}", "",
            p.get("description", ""), "",
            f"**Implication:** {p.get('implication', '')}", "",
            f"**Findings:** {', '.join(p.get('representative_finding_ids', []))}",
        ]))
    return f"{head}\n\n" + "\n\n".join(blocks)


def _strategic_intel(vuln_class: str, intel: dict | None) -> str:
    head = "## 3. Strategic Intelligence for Exploitation"
    if not intel:
        return f"{head}\n\n{placeholder('Section 3', 'set_strategic_intelligence')}"
    subheaders = STRATEGIC_INTEL_SUBHEADERS.get(vuln_class, [])
    blocks = []
    for field_name, header in subheaders:
        val = intel.get(field_name)
        if val is not None:
            blocks.append(f"### {header}\n\n{val}")
    return f"{head}\n\n" + "\n\n".join(blocks) if blocks else f"{head}\n\n{placeholder('Section 3', 'set_strategic_intelligence')}"


def _safe_vectors(vuln_class: str, data: dict | None) -> str:
    cols = SECTION_FOUR_COLUMNS[vuln_class]
    head = f"## {SECTION_FOUR_HEADING[vuln_class]}"
    if not data:
        return f"{head}\n\n{placeholder('Section 4', 'set_safe_vectors')}"
    vecs = data.get("vectors") or []
    if not vecs:
        return f"{head}\n\n*No vectors confirmed secure during analysis.*"
    headers = [cols["subject"], cols["location"], "Defense Mechanism"]
    if cols["include_render_context"]:
        headers.append("Render Context")
    rows = []
    for v in vecs:
        row = [v.get("subject", ""), v.get("location", ""), v.get("defense_mechanism", "")]
        if cols["include_render_context"]:
            row.append(v.get("render_context") or "")
        rows.append(row)
    return f"{head}\n\n{render_table(headers, rows)}"


def _blind_spots(data: dict | None) -> str:
    head = "## 5. Analysis Blind Spots"
    if not data:
        return f"{head}\n\n{placeholder('Section 5', 'set_blind_spots')}"
    items = data.get("items") or []
    if not items:
        return f"{head}\n\n*No analysis constraints or blind spots identified.*"
    blocks = [f"### {it.get('heading', '')}\n\n{it.get('description', '')}" for it in items]
    return f"{head}\n\n" + "\n\n".join(blocks)


def render_vuln_deliverable(vuln_class: str, data: dict) -> str:
    summary = data.get("findings_summary")
    parts = [
        f"# {TITLES[vuln_class]}", "",
        _exec(summary), "",
        _patterns(summary), "",
        _strategic_intel(vuln_class, data.get("strategic_intelligence")), "",
        _safe_vectors(vuln_class, data.get("safe_vectors")), "",
        _blind_spots(data.get("blind_spots")), "",
    ]
    return "\n".join(parts).rstrip() + "\n"
```

> **4 张映射表完整值**：执行时对照 TS `vuln-renderer.ts:40-105` 逐字核对 TITLES / SECTION_FOUR_HEADING / STRATEGIC_INTEL_SUBHEADERS / SECTION_FOUR_COLUMNS（上方为 TS grep 已核 + 合理推断，title/heading 完整文本以 TS 为准）。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/renderers/test_vuln.py -q` → 5 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/renderers/vuln.py packages/core/tests/renderers/test_vuln.py && git commit -m "feat(renderers): render_vuln_deliverable — 5 class 共用 + per-class 映射表"`

---

### Task 3: registry 注册 5 个 vuln agent

**Files:**
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`
- Test: `packages/core/tests/collectors/test_registry.py`（Plan 2 已建，加 vuln 用例）

**Interfaces:**
- Produces: `get_collector_spec(AgentName.INJECTION_VULN/XSS_VULN/AUTH_VULN/SSRF_VULN/AUTHZ_VULN)` 各返回 `CollectorSpec`（`make_vuln_collector(vc)` + `make_vuln_sections(vc)` + `lambda d: render_vuln_deliverable(vc, d)`）。

- [ ] **Step 1: Add failing test**

```python
# 追加到 packages/core/tests/collectors/test_registry.py
import pytest
from shannon_core.collectors import get_collector_spec
from shannon_core.models.agents import AgentName


@pytest.mark.parametrize("agent,vc", [
    (AgentName.INJECTION_VULN, "injection"), (AgentName.XSS_VULN, "xss"),
    (AgentName.AUTH_VULN, "auth"), (AgentName.SSRF_VULN, "ssrf"),
    (AgentName.AUTHZ_VULN, "authz"),
])
def test_vuln_agents_registered(agent, vc):
    spec = get_collector_spec(agent)
    assert spec is not None
    assert len(spec.sections) == 4
    # render 按 class branching
    md = spec.render({"findings_summary": {"key_outcome": "x", "patterns": []}})
    assert vc in md.lower() or "Analysis Report" in md  # title 含 class


def test_vuln_strategic_intel_model_matches_class():
    from shannon_core.collectors.vuln import InjectionStrategicIntel, AuthzStrategicIntel
    inj = next(s for s in get_collector_spec(AgentName.INJECTION_VULN).sections
               if s.tool_name == "set_strategic_intelligence")
    az = next(s for s in get_collector_spec(AgentName.AUTHZ_VULN).sections
              if s.tool_name == "set_strategic_intelligence")
    assert inj.model_cls is InjectionStrategicIntel
    assert az.model_cls is AuthzStrategicIntel
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_registry.py -q` → FAIL（vuln 返 None）。

- [ ] **Step 3: Implement**

`collectors/__init__.py` 加 vuln 分支（用 `get_vuln_type` 从 AgentName 推 class，或显式映射）：

```python
    # 5 个 vuln agent 共用 vuln collector/renderer,按 class branching
    _VULN_AGENT_CLASS = {
        A.INJECTION_VULN: "injection", A.XSS_VULN: "xss", A.AUTH_VULN: "auth",
        A.SSRF_VULN: "ssrf", A.AUTHZ_VULN: "authz",
    }
    if agent_name in _VULN_AGENT_CLASS:
        vc = _VULN_AGENT_CLASS[agent_name]
        from .vuln import make_vuln_collector, make_vuln_sections
        from ..renderers.vuln import render_vuln_deliverable
        return CollectorSpec(
            make_collector=lambda vc=vc: make_vuln_collector(vc),
            sections=make_vuln_sections(vc),
            render=lambda data, vc=vc: render_vuln_deliverable(vc, data),
        )
```

> `make_collector` / `render` 用默认参数捕获 `vc`（避免闭包晚绑定）。`CollectorSpec.make_collector` 是 callable，每次 execute 调一次产新 collector。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_registry.py -q` → passed（含 Plan 2 的 recon 用例）。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/__init__.py packages/core/tests/collectors/test_registry.py && git commit -m "feat(collectors): registry 注册 5 个 vuln agent(共用 vuln collector/renderer)"`

---

### Task 4: 5 个 vuln prompt 改造（Write→set_*）

**Files:**
- Modify: `prompts/vuln-injection.txt`、`vuln-xss.txt`、`vuln-auth.txt`、`vuln-ssrf.txt`、`vuln-authz.txt`

**TS 对照：** `upstream/main:apps/worker/prompts/vuln-*.txt`（deliverable_tools 块 + strategic_intelligence 的 per-class 字段指引）。

- [ ] **Step 1: 改 5 个 prompt**

每个 `vuln-{class}.txt`：
- 把「MUST save ... `{vt}_analysis_deliverable.md` using the Write tool」改为：

```
- **MANDATORY:** You MUST emit your analysis by calling all four `set_*` tools (`set_findings_summary`, `set_strategic_intelligence`, `set_safe_vectors`, `set_blind_spots`) before terminating. The host renders the analysis deliverable Markdown from those calls — there is no Markdown for you to write yourself.
```

- 加 `<deliverable_tools>` 块，4 个工具 + 字段指引（指引已在 collector pydantic Field description）。`set_strategic_intelligence` 的字段按该 class（injection→defensive_evasion_waf 等）。
- **保留** `{vt}_exploitation_queue.json` 的 structured_output 指示（queue 走另一通道，不动）。

- [ ] **Step 2: 校验**

`cd packages/core && uv run pytest tests/prompts/ -q`（插值测试 + grep 断言无残留 `{vt}_analysis_deliverable.md ... Write tool`）。

- [ ] **Step 3: Commit**

`git add prompts/vuln-injection.txt prompts/vuln-xss.txt prompts/vuln-auth.txt prompts/vuln-ssrf.txt prompts/vuln-authz.txt && git commit -m "feat(prompts): 5 vuln prompt 改 set_* 工具,删 agent Write analysis md(对齐 TS)"`

---

### Task 5: 端到端 + 双通道验证 + GLM 冒烟

**Files:**
- Test: `packages/core/tests/test_executor_vuln_render.py`

**关键验证**：vuln agent 同一 run 既要 collector（analysis md）又要 structured_output（queue.json）——双通道并存。

- [ ] **Step 1: Write end-to-end test**

```python
# packages/core/tests/test_executor_vuln_render.py
import asyncio
from shannon_core.agents import executor as exec_mod
from shannon_core.collectors import get_collector_spec
from shannon_core.models.agents import AgentName


def test_vuln_renders_analysis_md_and_queue_both_written(tmp_path, monkeypatch):
    """双通道:analysis md(collector+renderer)+ exploitation_queue.json(structured_output)。"""
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    spec = get_collector_spec(AgentName.INJECTION_VULN)
    queue_payload = {"verdicts": [{"vulnerability_id": "INJ-1", "status": "false_positive",
                                   "reason": "r", "evidence": "e"}]}

    class _R:
        success = True; turns = 1; cost = 0.0; cost_currency = "USD"
        error = None; retryable = True; model = "stub"
        structured_output = queue_payload
        class tokens:
            input_tokens = 0; output_tokens = 0
            cache_read_input_tokens = 0; cache_creation_input_tokens = 0
    _R.text = "done"

    async def fake_run(**kw):
        kw["collector"].set_section("findings_summary",
            {"key_outcome": "sqli found", "patterns": []})
        return _R()
    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit", lambda *a, **k: asyncio.sleep(0))
    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager); pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    ax = exec_mod.AgentExecutor(pm)

    asyncio.run(ax.execute(
        agent_name=AgentName.INJECTION_VULN, repo_path=str(deliverables),
        deliverables_path=str(deliverables), collector_spec=spec,
        structured_output_schema={"type": "object"}))
    # 双通道:analysis md + queue.json 都落盘
    assert (deliverables / "injection_analysis_deliverable.md").exists()
    md = (deliverables / "injection_analysis_deliverable.md").read_text()
    assert "## 1. Executive Summary" in md and "sqli found" in md
    assert (deliverables / "injection_exploitation_queue.json").exists()
```

- [ ] **Step 2: Run — verify PASS**

`cd packages/core && uv run pytest tests/test_executor_vuln_render.py tests/collectors/test_vuln_models.py tests/renderers/test_vuln.py -q` → passed。

> **双通道是本 plan 核心验证点**：executor 既写 analysis md（collector+renderer）又写 queue.json（structured_output）。若 Plan 1 的 executor 接线没考虑 collector_spec 与 structured_output 同时存在，此处会暴露——回到 Plan 1 Task 7 调整（两通道独立分支，不互斥）。

- [ ] **Step 3: GLM 真机冒烟（需 glm-anthropic env + 仓库）**

跑一个 vuln agent（如 injection-vuln），确认：
- `injection_analysis_deliverable.md` 由 host 渲染（4 section，skipped→placeholder）
- `injection_exploitation_queue.json` 照常落盘（structured_output 通道未受影响）
- agent 调了 4 个 set_* + 吐 structured_output（双通道同 run）
- workflow.log 无 `Missing deliverable: injection_analysis_deliverable.md`

- [ ] **Step 4: Commit（若有修复）+ 记 memory**

记录 Plan 3 落地 + 双通道验证结果到 memory [[pre-recon-md-deliverable-glm-forget-write]]。

---

## Self-Review

**Spec coverage:** §6 Plan 3（vuln）→ Task 1-5 ✓；§3.4 双通道（md via collector + queue via structured_output）→ Task 5 核心验证 ✓；§5 不变量（renderer 纯函数 §1、bridge 复用 §2）✓。

**Placeholder scan:** per-class strategic_intel 字段 + 4 张映射表的完整值标注了 TS 行号（明确移植指令，非 TODO）；映射表 title/heading 给了合理值 + "以 TS 为准"。

**Type consistency:** `make_vuln_sections`/`make_vuln_collector`/`render_vuln_deliverable` 与 Plan 1/2 模式一致；`SectionSpec`/`CollectorSpec` 复用；`_VULN_AGENT_CLASS` 映射覆盖 5 个 vuln AgentName。

**双通道风险：** Task 5 是双通道核心验证。若 executor（Plan 1 Task 7）的 collector_spec 分支与 structured_output 落盘分支互斥（if/elif），vuln 会只产一个——Task 5 暴露后回 Plan 1 调整为两个独立 if。

**已知执行期风险：**
- per-class strategic_intel 字段完整性（5 class × 多字段）→ Task 1 标了 TS 行号，执行时勿漏（§3 subheader 依赖）。
- 4 张映射表的 title/heading 文本以 TS 为准（上方为推断）→ Task 2 标注对照。
- 5 个 vuln prompt 改造量大但同构 → Task 4 可批量。
