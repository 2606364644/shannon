# Host-Rendered Deliverables — Plan 1（框架 + GLM probe + pre-recon 端到端）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 恢复 TS host 渲染架构——pre-recon agent 调 `set_*` 结构化工具，host collector 收集 + 确定性 renderer 渲染 `pre_recon_deliverable.md`（skipped→placeholder 不 fail），消除「agent success 但没 Write md → Missing deliverable」。

**Architecture:** 声明式 collector 框架：每 section 一个 pydantic model（`SectionSchema`）→ 双引擎工具桥（claude `@tool`+`create_sdk_mcp_server` in-process ／ openai 手动 `FunctionTool`）→ `CollectorBase` 收集 → `render_pre_recon(get_all())` 渲染 md → executor 落盘。Plan 1 只做 pre-recon + 框架；recon/vuln/exploit/report 在 Plan 2-5 增量。

**Tech Stack:** pydantic（section model + json schema）、claude_agent_sdk（`tool`/`create_sdk_mcp_server`/`McpSdkServerConfig`）、openai-agents（`FunctionTool` 手动构造）、pytest。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`

## Global Constraints

- **§1 双轨独立**：renderer 是纯函数，不引 GitNexus/确定性层产物；只渲染 collector 收到的 LLM payload。
- **§2 双引擎可互换**：双引擎工具桥保证同一 `SectionSchema` 双引擎都生成工具，流程一致。bridge 单测强制一致。
- **queue.json 通道不动**：vuln `{vt}_exploitation_queue.json` 继续走 `executor.py:147-154` structured_output，本 plan 不碰。
- **TS 对齐**：collector/renderer/prompt 文案 1:1 移植 `upstream/main:apps/worker/`。
- **TDD + 测试陷阱**：每 task 先写失败测试；只跑改动相关 pytest 子集，**勿跑全套**（卡 Temporal/网络，见 memory `pytest-whitebox-hang`）；前端无关。
- **诊断暂不移除**：`_enrich_missing_deliverable_error`（executor.py）保留到 Plan 5（其他 md agent 治本前仍有用）。

## File Structure

- Create: `packages/core/src/shannon_core/collectors/__init__.py`（registry：`get_collector_spec(agent_name)`）
- Create: `packages/core/src/shannon_core/collectors/base.py`（`CollectorBase`）
- Create: `packages/core/src/shannon_core/collectors/pre_recon.py`（7 section model + sections 清单）
- Create: `packages/core/src/shannon_core/collectors/bridge.py`（双引擎工具桥）
- Create: `packages/core/src/shannon_core/renderers/__init__.py`
- Create: `packages/core/src/shannon_core/renderers/_helpers.py`（`placeholder`）
- Create: `packages/core/src/shannon_core/renderers/pre_recon.py`（`render_pre_recon`）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（Agent tools 注入 collector 工具）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`（`_build_options` 接 `mcp_servers`+`allowed_tools`）
- Modify: `packages/core/src/shannon_core/agents/runner.py`（`run_claude_prompt` 透传 collector 工具）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（`execute` 接 collector，跑完 renderer 落盘）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_agent` 构建 collector 并贯穿）
- Modify: `prompts/pre-recon-code.txt`（Write → set_*）
- Create: `scripts/validate_glm_mcp_tool_probe.py`（GLM 真机探针）

---

### Task 1: `CollectorBase`（收集器基类）

**Files:**
- Create: `packages/core/src/shannon_core/collectors/__init__.py`（空 `__init__`，registry 在 Task 8 加）
- Create: `packages/core/src/shannon_core/collectors/base.py`
- Test: `packages/core/tests/collectors/test_base.py`

**Interfaces:**
- Produces: `CollectorBase`，方法 `set_section(name: str, payload: dict) -> None`（write-once，重复抛 `ValueError`）、`get_all() -> dict[str, dict | None]`、`get_call_status() -> dict[str, bool]`。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_base.py
import pytest
from shannon_core.collectors.base import CollectorBase


def test_set_and_get_all():
    c = CollectorBase(known_sections=["exec", "auth"])
    c.set_section("exec", {"text": "summary"})
    assert c.get_all() == {"exec": {"text": "summary"}, "auth": None}


def test_write_once_raises_on_duplicate():
    c = CollectorBase(known_sections=["exec"])
    c.set_section("exec", {"text": "a"})
    with pytest.raises(ValueError, match="already called"):
        c.set_section("exec", {"text": "b"})


def test_call_status_tracks_which_called():
    c = CollectorBase(known_sections=["exec", "auth"])
    c.set_section("exec", {"text": "x"})
    assert c.get_call_status() == {"exec": True, "auth": False}
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_base.py -q`
Expected: FAIL (ModuleNotFoundError / ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/collectors/base.py
"""Collector 基类:per-agent-run 实例(非全局,无并发 race),收集 set_* 工具的 payload。

对齐 TS apps/worker/src/collectors/*.ts:write-once(duplicate→DuplicateError),
skipped section 保持 None(get_all 返回,renderer 补 placeholder,不 fail activity)。
"""
from __future__ import annotations


class CollectorBase:
    def __init__(self, known_sections: list[str]) -> None:
        self._known = list(known_sections)
        self._data: dict[str, dict] = {}

    def set_section(self, name: str, payload: dict) -> None:
        if name not in self._known:
            raise ValueError(f"unknown section: {name}")
        if name in self._data:
            raise ValueError(
                f"{name} has already been called. Each set_* tool may only be called once per run."
            )
        self._data[name] = dict(payload)

    def get_all(self) -> dict[str, dict | None]:
        return {name: self._data.get(name) for name in self._known}

    def get_call_status(self) -> dict[str, bool]:
        return {name: name in self._data for name in self._known}
```

```python
# packages/core/src/shannon_core/collectors/__init__.py
```
（空文件占位，registry 在 Task 8 加。）

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_base.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/__init__.py packages/core/src/shannon_core/collectors/base.py packages/core/tests/collectors/test_base.py && git commit -m "feat(collectors): CollectorBase — write-once section 收集器"`

---

### Task 2: pre-recon 的 7 个 section model + sections 清单

**Files:**
- Create: `packages/core/src/shannon_core/collectors/pre_recon.py`
- Test: `packages/core/tests/collectors/test_pre_recon_models.py`

**Interfaces:**
- Consumes: `CollectorBase`（Task 1）。
- Produces: 7 个 pydantic model + `PRE_RECON_SECTIONS: list[SectionSpec]` + `make_pre_recon_collector() -> CollectorBase`。
- `SectionSpec`（在 base.py 加，见 Step 3）= `(section_name, tool_name, model_cls)`。

**TS 对照（移植源）：** `upstream/main:apps/worker/src/collectors/pre-recon-collector.ts` 的 7 个 `*InputSchema`。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_pre_recon_models.py
from shannon_core.collectors.pre_recon import (
    PRE_RECON_SECTIONS, make_pre_recon_collector, PreReconExecutiveSummary,
)


def test_seven_sections_present():
    names = {s.tool_name for s in PRE_RECON_SECTIONS}
    assert names == {
        "set_executive_summary", "set_application_intelligence", "set_auth_deep_dive",
        "set_codebase_indexing", "set_critical_file_paths", "set_xss_sinks", "set_ssrf_sinks",
    }


def test_make_collector_knows_seven_sections():
    c = make_pre_recon_collector()
    assert set(c.get_all().keys()) == {s.section_name for s in PRE_RECON_SECTIONS}


def test_executive_summary_model_validates():
    m = PreReconExecutiveSummary(text="overview")
    assert m.text == "overview"
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_pre_recon_models.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

先在 `base.py` 追加 `SectionSpec`（dataclass）：

```python
# 追加到 packages/core/src/shannon_core/collectors/base.py
from dataclasses import dataclass
from typing import Type
from pydantic import BaseModel


@dataclass(frozen=True)
class SectionSpec:
    section_name: str      # collector 内部 key(如 "executive_summary")
    tool_name: str         # 暴露给 agent 的工具名(如 "set_executive_summary")
    model_cls: Type[BaseModel]  # 该 section 的 pydantic model
    description: str       # 工具描述(agent 可见)
```

再写 `pre_recon.py`。字段对照 TS 7 schema（`text` 单字段的有 executive_summary / codebase_indexing；嵌套的有 application_intelligence / auth_deep_dive / critical_file_paths / xss_sinks / ssrf_sinks）。完整实现：

```python
# packages/core/src/shannon_core/collectors/pre_recon.py
"""pre-recon 的 7 section pydantic model + sections 清单。

移植 TS apps/worker/src/collectors/pre-recon-collector.ts 的 7 个 *InputSchema。
字段名/语义对齐 TS;pydantic model 的 model_json_schema() 供双引擎工具桥生成工具。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .base import CollectorBase, SectionSpec


class PreReconExecutiveSummary(BaseModel):
    text: str = Field(..., description="2-3 段应用安全态势概览。Section 1。")


class _Architecture(BaseModel):
    framework_and_language: str
    architectural_pattern: str
    critical_security_components: str


class _DataSecurity(BaseModel):
    database_security: str
    data_flow_security: str
    multi_tenant_isolation: str


class _AttackSurface(BaseModel):
    external_entry_points: str
    internal_service_communication: str


class _Infrastructure(BaseModel):
    deployment_platform: str
    secrets_management: str


class PreReconApplicationIntelligence(BaseModel):
    """复合 section,驱动 Section 2/4/5/6(对齐 TS AGENT-TO-SECTION MAPPING)。"""
    architecture: _Architecture
    data_security: _DataSecurity
    attack_surface: _AttackSurface
    infrastructure: _Infrastructure


class PreReconAuthDeepDive(BaseModel):
    authentication_mechanisms: str
    session_management: str
    authz_model: str
    multi_tenancy: str
    sso_oauth_oidc: str | None = Field(
        ..., description="SSO/OAuth/OIDC;无集成则 null。")


class PreReconCodebaseIndexing(BaseModel):
    text: str = Field(..., description="代码库目录结构/组织/工具的段落。Section 7。")


class PreReconCriticalFilePaths(BaseModel):
    configuration: list[str]
    authentication_and_authorization: list[str]
    data_handling: list[str]
    routing_and_middleware: list[str]
    template_rendering: list[str]


class _SinkRef(BaseModel):
    location: str = Field(..., description='文件路径+行号,如 "templates/render.js:34"。')
    sink_function: str
    notes: str | None = None


class PreReconXssSinks(BaseModel):
    sinks: list[_SinkRef]


class PreReconSsrfSinks(BaseModel):
    sinks: list[_SinkRef]


PRE_RECON_SECTIONS: list[SectionSpec] = [
    SectionSpec("executive_summary", "set_executive_summary",
                PreReconExecutiveSummary, "应用整体安全态势(Section 1)。"),
    SectionSpec("application_intelligence", "set_application_intelligence",
                PreReconApplicationIntelligence, "架构/数据/攻击面/基础设施(Section 2/4/5/6)。"),
    SectionSpec("auth_deep_dive", "set_auth_deep_dive",
                PreReconAuthDeepDive, "认证/会话/授权/多租户/SSO(Section 3)。"),
    SectionSpec("codebase_indexing", "set_codebase_indexing",
                PreReconCodebaseIndexing, "代码库结构总览(Section 7)。"),
    SectionSpec("critical_file_paths", "set_critical_file_paths",
                PreReconCriticalFilePaths, "关键文件路径(Section 8)。"),
    SectionSpec("xss_sinks", "set_xss_sinks", PreReconXssSinks, "XSS sink(Section 9)。"),
    SectionSpec("ssrf_sinks", "set_ssrf_sinks", PreReconSsrfSinks, "SSRF sink(Section 10)。"),
]


def make_pre_recon_collector() -> CollectorBase:
    return CollectorBase(known_sections=[s.section_name for s in PRE_RECON_SECTIONS])
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_pre_recon_models.py tests/collectors/test_base.py -q` → all passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/pre_recon.py packages/core/tests/collectors/test_pre_recon_models.py packages/core/src/shannon_core/collectors/base.py && git commit -m "feat(collectors): pre-recon 7 section pydantic models + SectionSpec"`

---

### Task 3: renderer helpers + `render_pre_recon`

**Files:**
- Create: `packages/core/src/shannon_core/renderers/__init__.py`（空）
- Create: `packages/core/src/shannon_core/renderers/_helpers.py`
- Create: `packages/core/src/shannon_core/renderers/pre_recon.py`
- Test: `packages/core/tests/renderers/test_pre_recon.py`

**Interfaces:**
- Consumes: collector `get_all()` 的 dict（section_name → payload dict | None）。
- Produces: `render_pre_recon(data: dict) -> str`（完整 md）。

**TS 对照：** `upstream/main:apps/worker/src/services/pre-recon-renderer.ts`（`renderPreRecon` + 10 个 `render*Section` + `placeholder`）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/renderers/test_pre_recon.py
from shannon_core.renderers.pre_recon import render_pre_recon
from shannon_core.renderers._helpers import placeholder


def test_placeholder_for_missing_section():
    md = render_pre_recon({})  # 全 skipped
    assert "# Penetration Test Scope & Boundaries" in md
    assert placeholder("Section 1", "set_executive_summary") in md
    assert placeholder("Section 3", "set_auth_deep_dive") in md


def test_executive_summary_rendered():
    md = render_pre_recon({"executive_summary": {"text": " posture overview "}})
    assert "## 1. Executive Summary" in md
    assert "posture overview" in md


def test_xss_sinks_rendered_as_list():
    md = render_pre_recon({"xss_sinks": {"sinks": [
        {"location": "v.js:1", "sink_function": "innerHTML", "notes": None}]}})
    assert "## 9" in md
    assert "innerHTML" in md and "v.js:1" in md
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/renderers/test_pre_recon.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/renderers/_helpers.py
def placeholder(section_label: str, tool_name: str) -> str:
    """对齐 TS pre-recon-renderer.ts placeholder():skipped section 渲染占位,不 fail。"""
    return f"[{section_label}: not provided — call `{tool_name}` tool]"
```

```python
# packages/core/src/shannon_core/renderers/__init__.py
```

```python
# packages/core/src/shannon_core/renderers/pre_recon.py
"""移植 TS pre-recon-renderer.ts::renderPreRecon。纯函数,不引确定性层(守 §1)。

section 顺序/skipped→placeholder 对齐 TS。data = collector.get_all()。
"""
from __future__ import annotations

from ._helpers import placeholder

SCOPE_AND_BOUNDARIES = "# Penetration Test Scope & Boundaries\n\n[对齐 TS SCOPE_AND_BOUNDARIES 常量,见 upstream/main:apps/worker/src/services/pre-recon-renderer.ts:37]"


def _sink_list(sinks: list[dict]) -> str:
    if not sinks:
        return placeholder("(no sinks)", "")
    lines = []
    for s in sinks:
        loc = s.get("location", "?")
        fn = s.get("sink_function", "?")
        notes = s.get("notes")
        lines.append(f"- `{loc}` — **{fn}**" + (f" ({notes})" if notes else ""))
    return "\n".join(lines)


def _exec(data: dict | None) -> str:
    if not data:
        return f"## 1. Executive Summary\n\n{placeholder('Section 1', 'set_executive_summary')}"
    return f"## 1. Executive Summary\n\n{data.get('text', '')}"


def _codebase(data: dict | None) -> str:
    if not data:
        return f"## 7. Overall Codebase Indexing\n\n{placeholder('Section 7', 'set_codebase_indexing')}"
    return f"## 7. Overall Codebase Indexing\n\n{data.get('text', '')}"


def _intel_sections(prefix: str, intel: dict | None) -> dict[str, str]:
    """application_intelligence 驱动 Section 2/4/5/6。任一子块缺失→placeholder。"""
    out: dict[str, str] = {}
    # Section 2 architecture / 4 data_security / 5 attack_surface / 6 infrastructure
    subs = {
        "2": ("architecture", "Architecture & Technology Stack"),
        "4": ("data_security", "Data Security & Storage"),
        "5": ("attack_surface", "Attack Surface Analysis"),
        "6": ("infrastructure", "Infrastructure"),
    }
    for sec, (key, label) in subs.items():
        sub = (intel or {}).get(key)
        if not sub:
            out[sec] = f"## {sec}. {label}\n\n{placeholder(f'Section {sec}', 'set_application_intelligence')}"
        else:
            body = "\n".join(f"- **{k}**: {v}" for k, v in sub.items())
            out[sec] = f"## {sec}. {label}\n\n{body}"
    return out


def _auth(data: dict | None) -> str:
    if not data:
        return f"## 3. Authentication & Authorization Deep Dive\n\n{placeholder('Section 3', 'set_auth_deep_dive')}"
    body = "\n".join(f"- **{k}**: {v}" for k, v in data.items())
    return f"## 3. Authentication & Authorization Deep Dive\n\n{body}"


def _critical_paths(data: dict | None) -> str:
    if not data:
        return f"## 8. Critical File Paths\n\n{placeholder('Section 8', 'set_critical_file_paths')}"
    body = "\n".join(f"- **{k}**: {', '.join(v)}" for k, v in data.items())
    return f"## 8. Critical File Paths\n\n{body}"


def _sinks_section(num: int, label: str, tool: str, data: dict | None) -> str:
    if not data:
        return f"## {num}. {label}\n\n{placeholder(f'Section {num}', tool)}"
    return f"## {num}. {label}\n\n{_sink_list(data.get('sinks', []))}"


def render_pre_recon(data: dict) -> str:
    intel = data.get("application_intelligence")
    ix = _intel_sections("", intel)
    parts = [
        SCOPE_AND_BOUNDARIES, "---", "",
        _exec(data.get("executive_summary")), "",
        ix["2"], "",
        _auth(data.get("auth_deep_dive")), "",
        ix["4"], "",
        ix["5"], "",
        ix["6"], "",
        _codebase(data.get("codebase_indexing")), "",
        _critical_paths(data.get("critical_file_paths")), "",
        _sinks_section(9, "XSS Sinks", "set_xss_sinks", data.get("xss_sinks")), "",
        _sinks_section(10, "SSRF Sinks", "set_ssrf_sinks", data.get("ssrf_sinks")), "",
    ]
    return "\n".join(parts).rstrip() + "\n"
```

> **SCOPE_AND_BOUNDARIES** 完整文本：执行时从 `upstream/main:apps/worker/src/services/pre-recon-renderer.ts:37` 的 `SCOPE_AND_BOUNDARIES` 常量逐字移植（含 in-scope/out-of-scope 规则）。上面占位仅标注来源——移植时替换为 TS 原文。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/renderers/test_pre_recon.py -q` → 3 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/renderers packages/core/tests/renderers && git commit -m "feat(renderers): render_pre_recon + placeholder(移植 TS)"`

---

### Task 4: 双引擎工具桥 — claude 侧

**Files:**
- Create: `packages/core/src/shannon_core/collectors/bridge.py`
- Test: `packages/core/tests/collectors/test_bridge_claude.py`

**Interfaces:**
- Consumes: `CollectorBase`、`list[SectionSpec]`。
- Produces: `build_claude_mcp_server(collector, sections, server_name) -> McpSdkServerConfig`。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_bridge_claude.py
import pytest
from claude_agent_sdk import create_sdk_mcp_server
from shannon_core.collectors.base import CollectorBase, SectionSpec
from shannon_core.collectors.pre_recon import PreReconExecutiveSummary
from shannon_core.collectors.bridge import build_claude_mcp_server


def test_build_claude_mcp_server_returns_in_process_config():
    sections = [SectionSpec("executive_summary", "set_executive_summary",
                            PreReconExecutiveSummary, "summary")]
    collector = CollectorBase(known_sections=["executive_summary"])
    server = build_claude_mcp_server(collector, sections, server_name="pre_recon")
    # in-process SDK MCP server(非 stdio)
    assert server is not None
    assert getattr(server, "type", None) != "stdio"


@pytest.mark.asyncio
async def test_claude_tool_writes_collector():
    """模拟 SDK 调工具:impl 应把 payload 写入 collector。"""
    from shannon_core.collectors.bridge import _make_claude_tool_impl
    sections = [SectionSpec("executive_summary", "set_executive_summary",
                            PreReconExecutiveSummary, "summary")]
    collector = CollectorBase(known_sections=["executive_summary"])
    impl = _make_claude_tool_impl(collector, sections[0])
    result = await impl({"text": "overview"})
    assert collector.get_all()["executive_summary"] == {"text": "overview"}
    assert "content" in result  # MCP 工具返回格式
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_bridge_claude.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/collectors/bridge.py
"""双引擎工具桥:同一组 SectionSpec → openai FunctionTool / claude SDK MCP 工具。

§2 双引擎可互换:两边都从 model_cls.model_json_schema() 生成工具,impl 都写同一个 collector。
"""
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from agents import FunctionTool, RunContextWrapper

from .base import CollectorBase, SectionSpec


# ── claude 侧(in-process SDK MCP) ──────────────────────────────────────
def _make_claude_tool_impl(collector: CollectorBase, spec: SectionSpec):
    async def _impl(args: Any) -> dict:
        payload = dict(args) if not isinstance(args, dict) else args
        # pydantic 校验(双引擎一致:openai 侧也走 model_cls)
        validated = spec.model_cls.model_validate(payload).model_dump(exclude_none=True)
        collector.set_section(spec.section_name, validated)
        return {"content": [{"type": "text", "text": f"recorded {spec.section_name}"}]}
    return _impl


def build_claude_mcp_server(
    collector: CollectorBase, sections: list[SectionSpec], server_name: str = "collector",
):
    tools = []
    for spec in sections:
        impl = _make_claude_tool_impl(collector, spec)
        schema = spec.model_cls.model_json_schema()
        t = tool(spec.tool_name, spec.description, schema)(impl)
        tools.append(t)
    return create_sdk_mcp_server(name=server_name, tools=tools)


# ── openai 侧(Task 5 实现 build_openai_tools) ─────────────────────────
def _make_openai_on_invoke(collector: CollectorBase, spec: SectionSpec):
    async def _on_invoke(ctx: RunContextWrapper, input_data: str) -> str:
        payload = json.loads(input_data) if input_data else {}
        validated = spec.model_cls.model_validate(payload).model_dump(exclude_none=True)
        collector.set_section(spec.section_name, validated)
        return f"recorded {spec.section_name}"
    return _on_invoke


def build_openai_tools(collector: CollectorBase, sections: list[SectionSpec]) -> list[FunctionTool]:
    tools = []
    for spec in sections:
        on_invoke = _make_openai_on_invoke(collector, spec)
        tools.append(FunctionTool(
            name=spec.tool_name,
            description=spec.description,
            params_json_schema=spec.model_cls.model_json_schema(),
            on_invoke_tool=on_invoke,
            strict_json_schema=False,  # section 复杂 schema 不强制 strict
        ))
    return tools
```

> claude `tool()` 的 `input_schema` 接受 JSON Schema dict（已核查 `claude_agent_sdk.__init__:170-193`）。`create_sdk_mcp_server` 返回 `McpSdkServerConfig`（in-process，含 instance）。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_bridge_claude.py -q` → 2 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/bridge.py packages/core/tests/collectors/test_bridge_claude.py && git commit -m "feat(collectors): bridge claude 侧 — in-process SDK MCP 工具"`

---

### Task 5: 双引擎工具桥 — openai 侧（已在 Task 4 同文件实现，补测试）

**Files:**
- Test: `packages/core/tests/collectors/test_bridge_openai.py`

**Interfaces:**
- Produces: `build_openai_tools(collector, sections) -> list[FunctionTool]`（已在 bridge.py）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_bridge_openai.py
import json
import pytest
from agents import FunctionTool
from shannon_core.collectors.base import CollectorBase, SectionSpec
from shannon_core.collectors.pre_recon import PreReconExecutiveSummary
from shannon_core.collectors.bridge import build_openai_tools


def test_build_openai_tools_returns_function_tools():
    sections = [SectionSpec("executive_summary", "set_executive_summary",
                            PreReconExecutiveSummary, "summary")]
    collector = CollectorBase(known_sections=["executive_summary"])
    tools = build_openai_tools(collector, sections)
    assert len(tools) == 1 and isinstance(tools[0], FunctionTool)
    assert tools[0].name == "set_executive_summary"
    assert "text" in tools[0].params_json_schema.get("properties", {})


@pytest.mark.asyncio
async def test_openai_tool_on_invoke_writes_collector():
    sections = [SectionSpec("executive_summary", "set_executive_summary",
                            PreReconExecutiveSummary, "summary")]
    collector = CollectorBase(known_sections=["executive_summary"])
    tools = build_openai_tools(collector, sections)
    out = await tools[0].on_invoke_tool(None, json.dumps({"text": "overview"}))
    assert collector.get_all()["executive_summary"] == {"text": "overview"}
    assert "recorded" in out


def test_same_schema_both_engines():
    """§2 不变量:同一 SectionSpec 双引擎生成的 schema 一致。"""
    from shannon_core.collectors.bridge import build_claude_mcp_server
    sections = [SectionSpec("executive_summary", "set_executive_summary",
                            PreReconExecutiveSummary, "summary")]
    collector = CollectorBase(known_sections=["executive_summary"])
    openai_tools = build_openai_tools(collector, sections)
    expected = PreReconExecutiveSummary.model_json_schema()["properties"]
    assert set(openai_tools[0].params_json_schema["properties"]) == set(expected)
```

- [ ] **Step 2: Run — verify PASS（bridge.py 已在 Task 4 含 openai 实现）**

`cd packages/core && uv run pytest tests/collectors/test_bridge_openai.py -q` → 3 passed.

> 若 `FunctionTool` 字段名与 agents SDK 版本不符（如 `on_invoke_tool` vs `on_invoke`），按 `uv run python -c "from agents import FunctionTool; import dataclasses; print([f.name for f in dataclasses.fields(FunctionTool)])"` 实测调整字段名。

- [ ] **Step 3: Commit**

`git add packages/core/tests/collectors/test_bridge_openai.py && git commit -m "test(collectors): bridge openai 侧 + 双引擎 schema 一致性"`

---

### Task 6: provider 接 collector 工具（双引擎）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（`Agent(tools=...)` 合并 collector 工具）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`（`call` + `_build_options` 接 `mcp_servers`/`allowed_tools`）
- Modify: `packages/core/src/shannon_core/agents/runner.py`（`run_claude_prompt` 透传 collector 工具参数）
- Test: `packages/core/tests/agents/test_providers_collector_tools.py`

**Interfaces:**
- Consumes: `build_openai_tools` / `build_claude_mcp_server`（Task 4/5）。
- Produces: `OpenAIProvider.call(..., openai_collector_tools=None)`、`AnthropicProvider._build_options(..., mcp_servers=None, allowed_tools=None)`、`run_claude_prompt(..., claude_mcp_server=None, openai_collector_tools=None)`。

- [ ] **Step 1: Write failing test（claude 侧 _build_options 接 mcp）**

```python
# packages/core/tests/agents/test_providers_collector_tools.py
def test_anthropic_build_options_accepts_mcp_servers():
    from shannon_core.agents.providers_anthropic import AnthropicProvider
    p = AnthropicProvider.__new__(AnthropicProvider)  # 不走 __init__
    # 模拟一个 in-process mcp server config
    fake_server = {"type": "sdk", "instance": object()}
    opts = p._build_options("/tmp", "model", mcp_servers={"pre_recon": fake_server},
                            allowed_tools=["set_executive_summary"])
    assert opts.mcp_servers == {"pre_recon": fake_server}
    assert "set_executive_summary" in opts.allowed_tools


def test_run_claude_prompt_forwards_mcp_server(monkeypatch):
    """run_claude_prompt 把 claude_mcp_server 透传到 provider.call。"""
    import asyncio
    from shannon_core.agents import runner as runner_mod

    captured = {}

    class _FakeProvider:
        async def call(self, **kw):
            captured.update(kw)
            from shannon_core.agents.runner import ClaudeRunResult
            return ClaudeRunResult(success=True, turns=1, text="ok")

    monkeypatch.setattr(runner_mod, "create_provider", lambda cfg: _FakeProvider())
    monkeypatch.setattr(runner_mod, "build_provider_config", lambda **kw: None)

    asyncio.run(runner_mod.run_claude_prompt(
        prompt="p", repo_path="/tmp", claude_mcp_server={"x": 1}))
    assert captured.get("claude_mcp_server") == {"x": 1}
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/agents/test_providers_collector_tools.py -q` → FAIL.

- [ ] **Step 3: Implement**

`providers_anthropic.py`：`call` 加 `claude_mcp_server=None`、`claude_allowed_tools=None` 参数，传给 `_build_options`；`_build_options` 签名加 `mcp_servers=None, allowed_tools=None`，赋值 `options.mcp_servers` / `options.allowed_tools`：

```python
# providers_anthropic.py —— call() 内(约 line 101):
options = self._build_options(
    cwd, model, output_format, max_turns_override=max_turns,
    mcp_servers=claude_mcp_server, allowed_tools=claude_allowed_tools,
)

# _build_options 签名(约 line 236):
def _build_options(self, cwd, model, output_format=None,
                   max_turns_override=None, mcp_servers=None, allowed_tools=None):
    options = ClaudeAgentOptions(model=model, cwd=cwd, permission_mode="bypassPermissions")
    # ... 既有 max_turns / output_format / thinking / env / stderr / system_prompt ...
    if mcp_servers:
        options.mcp_servers = mcp_servers
    if allowed_tools:
        options.allowed_tools = list(allowed_tools)
    return options
```

`runner.py`：`run_claude_prompt` 加 `claude_mcp_server=None, claude_allowed_tools=None, openai_collector_tools=None` 参数，透传 `provider.call(...)`。

`providers_openai.py`：`call` 加 `openai_collector_tools=None`，`Agent(tools=build_tools() + (openai_collector_tools or []))`（约 line 108-111）。子代理（line 131）不动。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/agents/test_providers_collector_tools.py -q` → passed。回归 `tests/test_executor_artifact_postprocess.py` 等既有 provider 测试（只跑改动相关，不跑全套）。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/src/shannon_core/agents/providers_openai.py packages/core/src/shannon_core/agents/runner.py packages/core/tests/agents/test_providers_collector_tools.py && git commit -m "feat(agents): provider 接 collector 工具(claude mcp_servers/openai tools)"`

---

### Task 7: collector registry + executor 落盘

**Files:**
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（registry）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（`execute` 接 collector spec，跑完 renderer 落盘）
- Test: `packages/core/tests/test_executor_collector_render.py`

**Interfaces:**
- Produces: `get_collector_spec(agent_name) -> CollectorSpec | None`；`CollectorSpec = (make_collector, sections, render)`。
- `executor.execute(...)` 加 `collector_spec: CollectorSpec | None = None`；跑完若 collector_spec，调 `render(collector.get_all())` 写 `{deliverable_filename}`，再 validate（一定通过）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/test_executor_collector_render.py
import asyncio
from shannon_core.agents import executor as exec_mod


def _wire(monkeypatch, tmp_path, run_result):
    async def fake_run(**kw):
        return run_result
    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit", lambda *a, **k: asyncio.sleep(0))
    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager); pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return exec_mod.AgentExecutor(pm)


def test_executor_renders_md_from_collector(tmp_path, monkeypatch):
    """agent success 且 collector 收到 payload → executor 用 renderer 写 md。"""
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    # 模拟 agent 调了 set_executive_summary(fake_run 内触发 collector.set_section)
    from shannon_core.collectors import get_collector_spec, set_collector_for_test
    from shannon_core.models.agents import AgentName
    spec = get_collector_spec(AgentName.PRE_RECON)
    set_collector_for_test(spec.make_collector())  # 测试钩子:让 fake_run 能访问 collector

    class _R:
        success = True; turns = 1; cost = 0.0; cost_currency = "USD"
        error = None; retryable = True; model = "stub"; structured_output = None
        class tokens:
            input_tokens = 0; output_tokens = 0
            cache_read_input_tokens = 0; cache_creation_input_tokens = 0
    _R.text = "done"

    async def fake_run(**kw):
        # 模拟 agent 调工具 → collector 收到
        kw["collector"].set_section("executive_summary", {"text": "posture"})
        return _R()
    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    ax = _wire(monkeypatch, tmp_path, _R())

    asyncio.run(ax.execute(
        agent_name=AgentName.PRE_RECON, repo_path=str(deliverables),
        deliverables_path=str(deliverables), collector_spec=spec))
    md = (deliverables / "pre_recon_deliverable.md").read_text()
    assert "## 1. Executive Summary" in md and "posture" in md
    # skipped section → placeholder,不 fail
    assert "Section 3" in md


def test_no_collector_spec_unaffected(tmp_path, monkeypatch):
    """无 collector_spec 的 agent(如 validate-auth)走原路径,不渲染。"""
    # 用现有 skip_artifact_postprocess 路径验证无回归(skip=True 不调 validate)
    assert get_collector_spec is not None  # registry 存在
```

> 注：`set_collector_for_test` 是测试便利钩子；真实链路 collector 由 executor 构建（见 Step 3）。若该钩子设计笨重，可改为 `collector_spec.make_collector()` 由 executor 构建后，fake_run 经由 `run_claude_prompt` 的 `collector` kwarg 拿到——上面 fake_run 已用 `kw["collector"]`。删除 `set_collector_for_test` 用法。

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/test_executor_collector_render.py -q` → FAIL.

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/collectors/__init__.py
"""collector registry:agent_name → CollectorSpec(make_collector, sections, render)。

None = 该 agent 无 collector(validate-auth/cross-repo/attack-chain,deliverable_filename=None)。
"""
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from shannon_core.models.agents import AgentName
    from .base import CollectorBase, SectionSpec


@dataclass(frozen=True)
class CollectorSpec:
    make_collector: Callable
    sections: list  # list[SectionSpec]
    render: Callable[[dict], str]


def get_collector_spec(agent_name: "AgentName"):
    from shannon_core.models.agents import AgentName as A
    if agent_name == A.PRE_RECON:
        from .pre_recon import PRE_RECON_SECTIONS, make_pre_recon_collector
        from ..renderers.pre_recon import render_pre_recon
        return CollectorSpec(make_pre_recon_collector, PRE_RECON_SECTIONS, render_pre_recon)
    return None
```

`executor.py`：`execute` 加 `collector_spec=None`；构建 collector，透传 provider 工具参数；跑完 renderer 落盘。在 `execute` 内（line 65 defn 之后）：

```python
collector = None
if collector_spec is not None:
    collector = collector_spec.make_collector()
```

`run_claude_prompt(...)` 调用（line 108）按引擎透传（engine 探测由 provider 决定；两参数都传，provider 各取所需）：

```python
from shannon_core.collectors.bridge import build_claude_mcp_server, build_openai_tools
claude_server = None; claude_allowed = None; openai_tools = None
if collector is not None:
    claude_server = build_claude_mcp_server(collector, collector_spec.sections, server_name="collector")
    claude_allowed = [s.tool_name for s in collector_spec.sections]
    openai_tools = build_openai_tools(collector, collector_spec.sections)

result = await run_claude_prompt(
    prompt=prompt, repo_path=str(repo), model_tier=defn.model_tier, api_key=api_key,
    deliverables_subdir=...,
    structured_output_schema=structured_output_schema,
    audit_logger=audit_logger, tool_audit_logger=tool_audit_logger, max_turns=max_turns,
    claude_mcp_server=claude_server, claude_allowed_tools=claude_allowed,
    openai_collector_tools=openai_tools, collector=collector,  # collector 回传给测试/诊断
)
```

在 `validate_deliverable` 之前（line 156 之前）加 renderer 落盘：

```python
if collector is not None and collector_spec is not None and not skip_artifact_postprocess:
    from shannon_core.utils.atomic_write import atomic_write_text
    md = collector_spec.render(collector.get_all())
    atomic_write_text(deliverables / defn.deliverable_filename, md)
```

> `atomic_write_text`：若 `utils/atomic_write.py` 无文本版，加一个（仿 `atomic_write_json`，`f.write(text)`）。或直接 `(deliverables / defn.deliverable_filename).write_text(md)`（git 已 checkpoint，原子性由 commit 保证）。

`runner.py` `run_claude_prompt` 加 `collector=None` 透传给 provider（仅诊断/测试用，provider 不消费 collector 对象本身，只消费工具）。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/test_executor_collector_render.py tests/collectors/ tests/renderers/ -q` → passed。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/__init__.py packages/core/src/shannon_core/agents/executor.py packages/core/src/shannon_core/agents/runner.py packages/core/tests/test_executor_collector_render.py && git commit -m "feat(executor): collector registry + renderer 落盘(host 渲染)"`

---

### Task 8: activity `run_agent` 构建 collector 并贯穿

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:208`（`executor.execute` 调用传 `collector_spec`）
- Test: `packages/whitebox/tests/pipeline/test_run_agent_collector.py`

**Interfaces:**
- Consumes: `get_collector_spec`（Task 7）。

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/pipeline/test_run_agent_collector.py
"""pre-recon run_agent 应把 collector_spec 传给 executor(集成层接线测试)。"""
import inspect
from shannon_whitebox.pipeline import activities


def test_run_agent_passes_collector_spec():
    src = inspect.getsource(activities.run_agent)
    assert "get_collector_spec" in src
    assert "collector_spec" in src
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/whitebox && uv run pytest tests/pipeline/test_run_agent_collector.py -q` → FAIL.

- [ ] **Step 3: Implement**

`activities.py` `run_agent`（约 line 192-221），在 `executor.execute` 调用前：

```python
from shannon_core.collectors import get_collector_spec
collector_spec = get_collector_spec(agent_name)
```

`executor.execute(...)` 调用加 `collector_spec=collector_spec`。

- [ ] **Step 4: Run — verify PASS**

`cd packages/whitebox && uv run pytest tests/pipeline/test_run_agent_collector.py -q` → passed。

- [ ] **Step 5: Commit**

`git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_run_agent_collector.py && git commit -m "feat(whitebox): run_agent 接 collector_spec(pre-recon host 渲染接线)"`

---

### Task 9: pre-recon prompt 改造（Write → set_*）

**Files:**
- Modify: `prompts/pre-recon-code.txt`

**TS 对照：** `upstream/main:apps/worker/prompts/pre-recon-code.txt`（line 24/138/180-186 的 `<deliverable_tools>` 块）。

- [ ] **Step 1: 改 prompt（非代码，无单测；靠 Task 11 端到端 + GLM probe 验证）**

把 `prompts/pre-recon-code.txt` 里所有「MUST save ... using the Write tool」「Use the Write tool to create ...」「written via the Write/Edit tool」改为 TS 文案：

```
- **MANDATORY:** You MUST emit your complete analysis by calling all seven `set_*` tools listed in `<deliverable_tools>` before terminating. The host renders the deliverable Markdown from those calls — there is no Markdown for you to write yourself.
```

在 prompt 末尾加 `<deliverable_tools>` 块（对齐 TS line 180-186），列出 7 个工具：`set_executive_summary` / `set_application_intelligence` / `set_auth_deep_dive` / `set_codebase_indexing` / `set_critical_file_paths` / `set_xss_sinks` / `set_ssrf_sinks`，每个标注对应 Section 号 + 字段指引（指引已在 collector 的 pydantic Field description 里，prompt 引用工具目录即可）。

删掉 prompt 里指示 agent 自己 `mkdir schemas/ + copy` 的段落除非 TS 保留（查 TS prompt 确认——TS 仍有 schema copy 段，保留）。

- [ ] **Step 2: 校验 prompt 渲染不报错**

`cd packages/core && uv run pytest tests/prompts/test_deliverables_path_interpolation.py -q`（验证 `{{DELIVERABLES_PATH}}` 插值仍工作）。

- [ ] **Step 3: Commit**

`git add prompts/pre-recon-code.txt && git commit -m "feat(prompts): pre-recon 改 set_* 工具,删 agent Write md(对齐 TS)"`

---

### Task 10: GLM probe（真机验证 GLM 驱动 MCP 工具）

**Files:**
- Create: `scripts/validate_glm_mcp_tool_probe.py`

**对标：** `scripts/validate_glm_task_probe.py`（GLM 驱动 Agent 子代理委派）。

- [ ] **Step 1: 写 probe 脚本**

```python
# scripts/validate_glm_mcp_tool_probe.py
"""验证 GLM(glm-anthropic)能驱动 in-process SDK MCP 工具 + 传结构化参数。

对标 validate_glm_task_probe。成功标准:agent 调 set_executive_summary + set_auth_deep_dive,
collector 收到符合 schema 的 payload(2/2)。
"""
import asyncio
from shannon_core.agents.runner import run_claude_prompt
from shannon_core.collectors.pre_recon import PRE_RECON_SECTIONS, make_pre_recon_collector
from shannon_core.collectors.bridge import build_claude_mcp_server


async def main():
    collector = make_pre_recon_collector()
    server = build_claude_mcp_server(collector, PRE_RECON_SECTIONS, server_name="pre_recon_probe")
    allowed = [s.tool_name for s in PRE_RECON_SECTIONS]
    result = await run_claude_prompt(
        prompt="调用 set_executive_summary(text='测试摘要') 和 set_auth_deep_dive(...) 两个工具后停止。",
        repo_path=".", model_tier="large",
        claude_mcp_server=server, claude_allowed_tools=allowed,
    )
    status = collector.get_call_status()
    print("success:", result.success, "turns:", result.turns)
    print("call_status:", status)
    print("executive_summary:", collector.get_all().get("executive_summary"))
    ok = status.get("executive_summary") and status.get("auth_deep_dive")
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: 真机跑（需 GLM env，profile glm-anthropic）**

`uv run python scripts/validate_glm_mcp_tool_probe.py`
Expected: `PROBE: PASS`（agent 调了两个工具，collector 收到 payload）。

> **决策点：** 若 probe FAIL（GLM 不能可靠驱动 MCP 工具/传参），**停止 Plan 1 后续推广**，回到 spec 第 8 节风险表，讨论回退（如改用 structured_output 路线，或分 section 多次 prompt）。probe 通过才进 Plan 2-5。

- [ ] **Step 3: Commit**

`git add scripts/validate_glm_mcp_tool_probe.py && git commit -m "test(scripts): validate_glm_mcp_tool_probe — GLM 驱动 in-process MCP 工具探针"`

---

### Task 11: 端到端回归 + 移除预存 cost_currency 测试债务（可选）

**Files:**
- Test: `packages/core/tests/test_executor_missing_deliverable_diagnostics.py`（确认 pre-recon 治本后该诊断对 pre-recon 不再触发，但保留给其他 agent）

- [ ] **Step 1: 端到端冒烟（真机，NodeGoat 或小仓）**

跑一次白盒 pre-recon（`SHANNON_AI_PROVIDER=glm-anthropic`），确认：
- `pre_recon_deliverable.md` 由 host 渲染产生（不再依赖 agent Write）
- 即使 agent 漏调部分 set_*，md 仍含 placeholder、不报 Missing deliverable
- workflow.log 无 `Missing deliverable: pre_recon_deliverable.md`

- [ ] **Step 2: 回归测试**

`cd packages/core && uv run pytest tests/collectors/ tests/renderers/ tests/test_executor_collector_render.py tests/test_executor_missing_deliverable_diagnostics.py -q` → all passed。

- [ ] **Step 3: Commit（若有修复）**

记录冒烟结果到 memory [[pre-recon-md-deliverable-glm-forget-write]]（治本 Plan 1 落地）。

---

## Self-Review

**Spec coverage:**
- §4.1 声明式框架 → Task 1/2/4/5 ✓
- §4.2 数据流 → Task 6/7/8 ✓
- §4.3 prompt 改造 → Task 9 ✓
- §4.4 GLM probe → Task 10 ✓
- §4.5 诊断移除 → 推迟 Plan 5（Global Constraints 已声明）✓
- §5 不变量（§1 双轨独立 renderer 纯函数 ✓、§2 双引擎 bridge 一致性测试 Task 5 ✓、queue 通道不动 ✓）
- §6 分阶段 → 本 plan = Plan 1（pre-recon），Plan 2-5 后续 ✓

**Placeholder scan:** Task 3 的 `SCOPE_AND_BOUNDARIES` 标注了 TS 来源行号待移植（非 TODO，是明确的移植指令）；其余无 TBD/TODO。

**Type consistency:** `CollectorBase`/`SectionSpec`/`CollectorSpec` 跨 task 名字一致；`build_claude_mcp_server`/`build_openai_tools`/`get_collector_spec`/`render_pre_recon` 签名一致。

**已知执行期风险（task 内 TDD 会暴露）：**
- agents SDK `FunctionTool` 字段名（`on_invoke_tool`）随版本可能变 → Task 5 给了实测命令。
- claude `tool()` 的 schema 对嵌套 `$ref` 的接受度 → Task 4/10 probe 验证；若 nested model_json_schema 含 `$ref` 致 CLI 拒绝，bridge 里 `model_json_schema(mode='serialization', ref_template=...)` 或 flatten。
