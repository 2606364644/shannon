# Host-Rendered Deliverables — Plan 2（recon agent）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Plan 1（`2026-07-17-host-rendered-deliverables-plan-1.md`）已完成——框架（`CollectorBase`/`SectionSpec`/`bridge`/registry/executor 接线/renderer helpers）已落地。本 plan 是增量：recon agent 接入 host 渲染。

**Goal:** recon agent 调 8 个 `set_*` 结构化工具，host collector 收集 + 确定性 renderer 渲染 `recon_deliverable.md`（skipped→placeholder 不 fail），消除 recon 的「agent success 但没 Write md → Missing deliverable」风险。

**Architecture:** 复用 Plan 1 框架：recon 的 8 个 section pydantic model → `PRE_RECON_SECTIONS` 同模式的 `RECON_SECTIONS` → renderer `render_recon` → registry 注册 `AgentName.RECON` → prompt 改 set_*。

**Tech Stack:** pydantic、pytest（同 Plan 1）。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 2 = recon）

## Global Constraints

- **依赖 Plan 1 接口（已落地，勿改）**：`CollectorBase(known_sections)`、`SectionSpec(section_name, tool_name, model_cls, description)`、`bridge.build_claude_mcp_server`/`build_openai_tools`、`CollectorSpec(make_collector, sections, render)`、`get_collector_spec`、`renderers/_helpers.placeholder`、`executor.execute(collector_spec=...)`。
- **§1 双轨独立**：renderer 纯函数，不引确定性层；recon 的 source 仅 LLM 自身分析（recon prompt 已是 LLM 自给自足，本 plan 只改产物落盘方式）。
- **§2 双引擎**：复用 bridge，recon model 自动双引擎生效。
- **TS 对齐**：collector section / renderer / prompt 1:1 移植 `upstream/main:apps/worker/`。
- **TDD + 测试陷阱**：每 task 先失败测试；只跑改动子集，勿跑全套（memory `pytest-whitebox-hang`）。
- **两个 prompt 都改**：`recon.txt`（在线）+ `recon-static.txt`（离线回退）都要 Write→set_*（`recon-blackbox.txt` 是黑盒，不在本 plan scope）。
- **诊断暂不移除**：`_enrich_missing_deliverable_error` 保留到 Plan 5。

## File Structure

- Create: `packages/core/src/shannon_core/collectors/recon.py`（8 section model + `RECON_SECTIONS` + `make_recon_collector`）
- Create: `packages/core/src/shannon_core/renderers/recon.py`（`render_recon`）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（`get_collector_spec` 加 `RECON` 分支）
- Modify: `packages/core/src/shannon_core/renderers/_helpers.py`（加 `render_table` helper，recon 用）
- Modify: `prompts/recon.txt`、`prompts/recon-static.txt`（Write→set_*）

---

### Task 1: recon 的 8 section pydantic model + sections 清单

**Files:**
- Create: `packages/core/src/shannon_core/collectors/recon.py`
- Test: `packages/core/tests/collectors/test_recon_models.py`

**Interfaces:**
- Consumes: `CollectorBase`、`SectionSpec`（Plan 1）。
- Produces: 8 个 pydantic model + `RECON_SECTIONS: list[SectionSpec]` + `make_recon_collector()`。

**TS 对照：** `upstream/main:apps/worker/src/collectors/recon-collector.ts`（`RECON_ONE_SHOT_TOOLS` line 576-584 + 各 `*InputSchema`）。

**⚠️ add_endpoints 待确认：** TS renderer Section 4（API Endpoint Inventory）引用 `add_endpoints` 工具，但不在 `RECON_ONE_SHOT_TOOLS` 8 个里。执行 Step 1 前先查 TS recon-collector.ts 全文确认 `add_endpoints` 语义：
- 若是 **append**（多次调用累积 endpoints）→ `CollectorBase` 加 `append_section(name, item)` 方法（Plan 1 的 write-once 不兼容），或 recon 用独立 endpoints list。
- 若已并入 `set_network_map` → renderer Section 4 从 network_map payload 取 endpoints。
- 默认假设（下方代码）：endpoints 并入 `set_network_map`（Section 4 = network_map 的一部分）。若 TS 实际是独立 append 工具，Task 1 加 `set_endpoints` + collector append 支持，并相应改 Task 2 renderer。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_recon_models.py
from shannon_core.collectors.recon import RECON_SECTIONS, make_recon_collector


def test_eight_sections_present():
    names = {s.tool_name for s in RECON_SECTIONS}
    assert names == {
        "set_executive_summary", "set_technology_stack", "set_authentication",
        "set_input_vectors", "set_network_map", "set_role_architecture",
        "set_authz_candidates", "set_injection_sources",
    }


def test_make_collector_knows_eight_sections():
    c = make_recon_collector()
    assert len(c.get_all()) == 8


def test_section_names_distinct():
    names = [s.section_name for s in RECON_SECTIONS]
    assert len(names) == len(set(names))
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_recon_models.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/collectors/recon.py
"""recon 的 8 section pydantic model + sections 清单。

移植 TS apps/worker/src/collectors/recon-collector.ts(RECON_ONE_SHOT_TOOLS + *InputSchema)。
字段对照 TS;model_json_schema() 供 bridge 双引擎生成工具。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .base import CollectorBase, SectionSpec


# ── Section 1 ──────────────────────────────────────────────────────────
class ReconExecutiveSummary(BaseModel):
    text: str = Field(..., description="recon 概览,关键攻击面与优先级。Section 1。")


# ── Section 2 ──────────────────────────────────────────────────────────
class _ServiceEntry(BaseModel):
    name: str
    type: str = Field(..., description="服务类型(db/cache/queue/api 等)")
    location: str | None = None


class ReconTechnologyStack(BaseModel):
    """对照 TS TechnologyStackInputSchema(line 41)。完整字段移植 TS。"""
    framework: str
    language: str
    services: list[_ServiceEntry] = Field(default_factory=list)
    # 其余字段对照 TS TechnologyStackInputSchema 补齐


# ── Section 3 ──────────────────────────────────────────────────────────
class _RoleSwitching(BaseModel):
    applicable: bool
    location: str | None = Field(None, description="实现文件/函数,null when applicable=false")


class ReconAuthentication(BaseModel):
    """对照 TS AuthenticationInputSchema(line 56),含 3.1/3.2/3.3 子节。"""
    role_assignment_process: str
    privilege_storage_and_validation: str
    role_switching_impersonation: _RoleSwitching
    # 其余字段对照 TS 补齐


# ── Section 5 ──────────────────────────────────────────────────────────
class ReconInputVectors(BaseModel):
    """对照 TS InputVectorsInputSchema(line 206)。多类输入向量。"""
    api_inputs: str
    file_uploads: str
    # 其余字段对照 TS 补齐


# ── Section 4(并入 network_map) / 6 ────────────────────────────────────
class _Entity(BaseModel):
    name: str
    metadata: dict | None = None


class _Flow(BaseModel):
    name: str
    description: str | None = None


class ReconNetworkMap(BaseModel):
    """对照 TS NetworkMapInputSchema(line 333)。含 entities/flows 表格 + endpoints(Section 4)。"""
    entities: list[_Entity] = Field(default_factory=list)
    flows: list[_Flow] = Field(default_factory=list)
    endpoints: list[dict] = Field(default_factory=list,
        description="API endpoint 清单(Section 4)。对照 TS add_endpoints schema。")


class ReconRoleArchitecture(BaseModel):
    """对照 TS RoleArchitectureInputSchema(line 412)。"""
    role_hierarchy: str
    enforcement_points: str


class ReconAuthzCandidates(BaseModel):
    """对照 TS AuthzCandidatesInputSchema(line 484)。"""
    candidates: list[dict] = Field(default_factory=list)


class ReconInjectionSources(BaseModel):
    """对照 TS InjectionSourcesInputSchema(line 500)。"""
    sources: list[dict] = Field(default_factory=list)


RECON_SECTIONS: list[SectionSpec] = [
    SectionSpec("executive_summary", "set_executive_summary",
                ReconExecutiveSummary, "recon 概览(Section 1)。"),
    SectionSpec("technology_stack", "set_technology_stack",
                ReconTechnologyStack, "技术栈与服务地图(Section 2)。"),
    SectionSpec("authentication", "set_authentication",
                ReconAuthentication, "认证/会话/角色(Section 3)。"),
    SectionSpec("input_vectors", "set_input_vectors",
                ReconInputVectors, "潜在输入向量(Section 5)。"),
    SectionSpec("network_map", "set_network_map",
                ReconNetworkMap, "网络地图/实体/流/endpoints(Section 4/6)。"),
    SectionSpec("role_architecture", "set_role_architecture",
                ReconRoleArchitecture, "角色架构(Section 7)。"),
    SectionSpec("authz_candidates", "set_authz_candidates",
                ReconAuthzCandidates, "authz 候选(Section 8)。"),
    SectionSpec("injection_sources", "set_injection_sources",
                ReconInjectionSources, "注入源(Section 9)。"),
]


def make_recon_collector() -> CollectorBase:
    return CollectorBase(known_sections=[s.section_name for s in RECON_SECTIONS])
```

> **完整字段移植**：上面每个 model 只给了骨架字段。执行时逐个对照 TS `recon-collector.ts` 的对应 `*InputSchema`（行号已标注）补齐**所有**字段 + description（对齐 TS）。不要漏字段——下游 vuln agent 读 recon_deliverable.md 依赖这些。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_recon_models.py -q` → 3 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/recon.py packages/core/tests/collectors/test_recon_models.py && git commit -m "feat(collectors): recon 8 section pydantic models(移植 TS)"`

---

### Task 2: `render_recon` + 表格 helper

**Files:**
- Modify: `packages/core/src/shannon_core/renderers/_helpers.py`（加 `render_table`）
- Create: `packages/core/src/shannon_core/renderers/recon.py`
- Test: `packages/core/tests/renderers/test_recon.py`

**Interfaces:**
- Produces: `render_recon(data: dict) -> str`。

**TS 对照：** `upstream/main:apps/worker/src/services/recon-renderer.ts`（`renderRecon` + HOW_TO_READ_THIS 常量 + 各 render* + `renderTable`/`renderSinkList`）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/renderers/test_recon.py
from shannon_core.renderers.recon import render_recon
from shannon_core.renderers._helpers import placeholder


def test_all_missing_renders_placeholders():
    md = render_recon({})
    assert "HOW TO READ THIS" in md or "## 0" in md
    assert placeholder("Section 1", "set_executive_summary") in md
    assert placeholder("Section 3", "set_authentication") in md


def test_executive_summary_rendered():
    md = render_recon({"executive_summary": {"text": "key findings"}})
    assert "## 1. Executive Summary" in md and "key findings" in md


def test_network_map_endpoints_table():
    md = render_recon({"network_map": {"entities": [{"name": "User"}],
                                       "endpoints": [{"path": "/login", "method": "POST"}],
                                       "flows": []}})
    assert "/login" in md
    assert "User" in md


def test_authentication_role_switching_subsection():
    md = render_recon({"authentication": {
        "role_assignment_process": "rbac", "privilege_storage_and_validation": "session",
        "role_switching_impersonation": {"applicable": True, "location": "admin.js:10"}}})
    assert "### 3.3" in md and "admin.js:10" in md
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/renderers/test_recon.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

`_helpers.py` 追加表格 helper：

```python
# 追加到 packages/core/src/shannon_core/renderers/_helpers.py
def render_table(headers: list[str], rows: list[list[str]]) -> str:
    """对齐 TS recon-renderer.ts renderTable。空表→空串(由调用方决定 placeholder)。"""
    if not rows:
        return ""
    line0 = "| " + " | ".join(headers) + " |"
    line1 = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join([line0, line1] + body)
```

`renderers/recon.py`：

```python
# packages/core/src/shannon_core/renderers/recon.py
"""移植 TS recon-renderer.ts::renderRecon。纯函数,不引确定性层(守 §1)。

section 顺序/skipped→placeholder 对齐 TS。data = collector.get_all()。
"""
from __future__ import annotations

from ._helpers import placeholder, render_table

HOW_TO_READ_THIS = (
    "## 0) HOW TO READ THIS\n\n"
    "[对齐 TS recon-renderer.ts HOW_TO_READ_THIS 常量(line 50),逐字移植]"
)


def _exec(data: dict | None) -> str:
    if not data:
        return f"## 1. Executive Summary\n\n{placeholder('Section 1', 'set_executive_summary')}"
    return f"## 1. Executive Summary\n\n{data.get('text', '')}"


def _tech(data: dict | None) -> str:
    if not data:
        return f"## 2. Technology & Service Map\n\n{placeholder('Section 2', 'set_technology_stack')}"
    lines = [f"## 2. Technology & Service Map", "",
             f"- **Framework**: {data.get('framework', '')}",
             f"- **Language**: {data.get('language', '')}"]
    svcs = data.get("services") or []
    if svcs:
        lines.append("")
        lines.append(render_table(["name", "type", "location"],
                                  [[s.get("name", ""), s.get("type", ""), s.get("location") or ""]
                                   for s in svcs]))
    return "\n".join(lines)


def _auth(data: dict | None) -> str:
    if not data:
        return f"## 3. Authentication & Session Management Flow\n\n{placeholder('Section 3', 'set_authentication')}"
    rs = data.get("role_switching_impersonation") or {}
    rs_block = ""
    if rs.get("applicable"):
        rs_block = f"\n\n### 3.3 Role Switching & Impersonation\n\n{rs.get('location', '')}"
    elif rs.get("applicable") is False:
        rs_block = "\n\n### 3.3 Role Switching & Impersonation\n\n[not applicable]"
    return "\n".join([
        "## 3. Authentication & Session Management Flow", "",
        f"### 3.1 Role Assignment Process\n\n{data.get('role_assignment_process', '')}", "",
        f"### 3.2 Privilege Storage & Validation\n\n{data.get('privilege_storage_and_validation', '')}",
        rs_block,
    ])


def _network(data: dict | None) -> str:
    """Section 4(API endpoints)+ Section 6(entities/flows),均来自 set_network_map。"""
    if not data:
        return (f"## 4. API Endpoint Inventory\n\n{placeholder('Section 4', 'set_network_map')}\n\n"
                f"## 6. Network Map\n\n{placeholder('Section 6', 'set_network_map')}")
    eps = data.get("endpoints") or []
    ep_tbl = render_table(["method", "path", "functionality"],
                          [[e.get("method", ""), e.get("path", ""), e.get("functionality", "")]
                           for e in eps]) if eps else placeholder("Section 4", "set_network_map")
    ents = data.get("entities") or []
    ent_tbl = render_table(["entity"], [[en.get("name", "")] for en in ents]) if ents else ""
    return "\n".join([
        "## 4. API Endpoint Inventory", "", ep_tbl, "",
        "## 6. Network Map", "",
        ent_tbl,
    ])


def _input_vectors(data: dict | None) -> str:
    if not data:
        return f"## 5. Potential Input Vectors for Vulnerability Analysis\n\n{placeholder('Section 5', 'set_input_vectors')}"
    body = "\n".join(f"- **{k}**: {v}" for k, v in data.items())
    return f"## 5. Potential Input Vectors for Vulnerability Analysis\n\n{body}"


def _simple_section(num: int, label: str, tool: str, data: dict | None) -> str:
    if not data:
        return f"## {num}. {label}\n\n{placeholder(f'Section {num}', tool)}"
    body = "\n".join(f"- **{k}**: {v}" for k, v in data.items())
    return f"## {num}. {label}\n\n{body}"


def render_recon(data: dict) -> str:
    parts = [
        HOW_TO_READ_THIS, "",
        _exec(data.get("executive_summary")), "",
        _tech(data.get("technology_stack")), "",
        _auth(data.get("authentication")), "",
        _network(data.get("network_map")), "",
        _input_vectors(data.get("input_vectors")), "",
        _simple_section(7, "Role Architecture", "set_role_architecture",
                        data.get("role_architecture")), "",
        _simple_section(8, "Authz Candidates", "set_authz_candidates",
                        data.get("authz_candidates")), "",
        _simple_section(9, "Injection Sources", "set_injection_sources",
                        data.get("injection_sources")), "",
    ]
    return "\n".join(parts).rstrip() + "\n"
```

> **完整移植**：`HOW_TO_READ_THIS` 常量 + 各 section 的完整字段渲染，执行时对照 TS `recon-renderer.ts` 逐段对齐（行号已标）。上方为结构骨架 + 关键 section 完整实现。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/renderers/test_recon.py -q` → 4 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/renderers/recon.py packages/core/src/shannon_core/renderers/_helpers.py packages/core/tests/renderers/test_recon.py && git commit -m "feat(renderers): render_recon + render_table(移植 TS)"`

---

### Task 3: registry 注册 recon

**Files:**
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（`get_collector_spec` 加 `RECON` 分支）
- Test: `packages/core/tests/collectors/test_registry.py`

**Interfaces:**
- Produces: `get_collector_spec(AgentName.RECON)` 返回 `CollectorSpec(make_recon_collector, RECON_SECTIONS, render_recon)`。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_registry.py
from shannon_core.collectors import get_collector_spec
from shannon_core.models.agents import AgentName


def test_recon_registered():
    spec = get_collector_spec(AgentName.RECON)
    assert spec is not None
    assert len(spec.sections) == 8
    data = spec.make_collector().get_all()
    assert len(data) == 8


def test_unmapped_agent_returns_none():
    from shannon_core.models.agents import AgentName as A
    assert get_collector_spec(A.VALIDATE_AUTH) is None
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_registry.py -q` → FAIL（`get_collector_spec(RECON)` 返 None）。

- [ ] **Step 3: Implement**

`collectors/__init__.py` 的 `get_collector_spec` 加分支（在 PRE_RECON 分支后）：

```python
    if agent_name == A.RECON:
        from .recon import RECON_SECTIONS, make_recon_collector
        from ..renderers.recon import render_recon
        return CollectorSpec(make_recon_collector, RECON_SECTIONS, render_recon)
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_registry.py -q` → 2 passed。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/__init__.py packages/core/tests/collectors/test_registry.py && git commit -m "feat(collectors): registry 注册 recon"` 

---

### Task 4: recon prompt 改造（recon.txt + recon-static.txt）

**Files:**
- Modify: `prompts/recon.txt`
- Modify: `prompts/recon-static.txt`

**TS 对照：** `upstream/main:apps/worker/prompts/recon.txt`（deliverable_tools 块 + "no Markdown for you to write" 文案）。

- [ ] **Step 1: 改 recon.txt**

把 `prompts/recon.txt` 里所有「MUST save ... using the Write tool」「Use the Write tool to create recon_deliverable.md」改为 TS 文案：

```
- **MANDATORY:** You MUST emit your complete analysis by calling all eight `set_*` tools listed in `<deliverable_tools>` before terminating. The host renders the deliverable Markdown from those calls — there is no Markdown for you to write yourself.
```

末尾加 `<deliverable_tools>` 块，列 8 个工具：`set_executive_summary` / `set_technology_stack` / `set_authentication` / `set_input_vectors` / `set_network_map` / `set_role_architecture` / `set_authz_candidates` / `set_injection_sources`，每个标 Section 号 + 引用工具目录字段指引（字段已在 collector pydantic Field description）。

删 `{{DELIVERABLES_PATH}}/recon_deliverable.md` 的 Write 指示；`{{DELIVERABLES_PATH}}` 仍保留（schemas 目录等仍用，对齐 TS）。

- [ ] **Step 2: 改 recon-static.txt（同上）**

`recon-static.txt` 同样 Write→set_*（离线模式同样的 8 工具；recon-static 是 PY 特有，TS 无对应，但产物机制一致——host 渲染）。

- [ ] **Step 3: 校验 prompt 渲染**

`cd packages/core && uv run pytest tests/prompts/ -q`（验证 `{{DELIVERABLES_PATH}}` 插值 + 无残留 Write 指示断言，若 test_deliverables_path_interpolation 有 recon 相关断言则跑）。

- [ ] **Step 4: Commit**

`git add prompts/recon.txt prompts/recon-static.txt && git commit -m "feat(prompts): recon 改 set_* 工具,删 agent Write md(对齐 TS)"`

---

### Task 5: 端到端测试 + GLM 真机冒烟

**Files:**
- Test: `packages/core/tests/test_executor_recon_render.py`（端到端：mock agent 调 set_* → recon md 落盘）

- [ ] **Step 1: Write end-to-end test**

```python
# packages/core/tests/test_executor_recon_render.py
import asyncio
from shannon_core.agents import executor as exec_mod
from shannon_core.collectors import get_collector_spec
from shannon_core.models.agents import AgentName


def test_executor_renders_recon_md_from_collector(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    spec = get_collector_spec(AgentName.RECON)

    class _R:
        success = True; turns = 1; cost = 0.0; cost_currency = "USD"
        error = None; retryable = True; model = "stub"; structured_output = None
        class tokens:
            input_tokens = 0; output_tokens = 0
            cache_read_input_tokens = 0; cache_creation_input_tokens = 0
    _R.text = "done"

    async def fake_run(**kw):
        kw["collector"].set_section("executive_summary", {"text": "recon overview"})
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
        agent_name=AgentName.RECON, repo_path=str(deliverables),
        deliverables_path=str(deliverables), collector_spec=spec))
    md = (deliverables / "recon_deliverable.md").read_text()
    assert "## 1. Executive Summary" in md and "recon overview" in md
    assert "Section 3" in md  # skipped → placeholder
```

- [ ] **Step 2: Run — verify PASS**

`cd packages/core && uv run pytest tests/test_executor_recon_render.py tests/collectors/ tests/renderers/ -q` → passed。

- [ ] **Step 3: GLM 真机冒烟（需 glm-anthropic env + 仓库）**

跑白盒 recon（接 pre-recon 之后），确认：
- `recon_deliverable.md` 由 host 渲染产生
- agent 漏调部分 set_* → placeholder、不报 Missing deliverable
- workflow.log 无 `Missing deliverable: recon_deliverable.md`

> 若 GLM 在 recon 的 8 工具 fan-out 下不可靠（漏调多），记录到 memory，但不阻塞（placeholder 兜底已保证不 fail）。

- [ ] **Step 4: Commit（若有修复）+ 记 memory**

记录 Plan 2 落地到 memory [[pre-recon-md-deliverable-glm-forget-write]]（recon 治本完成）。

---

## Self-Review

**Spec coverage:** §6 Plan 2（recon）→ Task 1-5 ✓；§5 不变量（renderer 纯函数 §1、bridge 复用 §2）✓。

**Placeholder scan:** Task 1/2 的 model 字段与 renderer 完整移植标注了 TS 行号（明确移植指令，非 TODO）；HOW_TO_READ_THIS 同。`add_endpoints` 语义在 Task 1 ⚠️ 标注待查 + 默认假设 + 备选方案。

**Type consistency:** `RECON_SECTIONS`/`make_recon_collector`/`render_recon` 与 Plan 1 的 `PRE_RECON_SECTIONS`/`make_pre_recon_collector`/`render_pre_recon` 模式一致；`SectionSpec`/`CollectorSpec` 复用 Plan 1 定义。

**Plan 1 依赖：** 本 plan 假设 Plan 1 已落地（框架 + executor collector_spec 接线 + registry）。若 Plan 1 接口有调整，Task 3 的 registry 分支与 Task 5 的端到端按实际接口适配。

**已知执行期风险：**
- TS `add_endpoints` 语义（append vs 并入 network_map）→ Task 1 ⚠️ 已标注。
- recon model 字段多（8 section × 多字段），完整移植需逐个对照 TS——Task 1 已标行号，执行时勿漏字段（下游依赖）。
