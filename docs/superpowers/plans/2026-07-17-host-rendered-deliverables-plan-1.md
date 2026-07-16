# Host-Rendered Deliverables — Plan 1（collector 框架 + 双引擎桥 + pre-recon 端到端）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 恢复 TS「host 渲染」产物架构——pre-recon agent 调 7 个 `set_*` 结构化工具，host 用确定性 renderer 渲染 `pre_recon_deliverable.md`，消除「agent 失忆漏 Write → Missing deliverable」的架构性根因。

**Architecture:** 新建声明式 collector 框架（`SectionSchema` + `CollectorBase`）+ 双引擎工具桥（同一份 JSON Schema dict 在 openai 侧构造 `FunctionTool`、在 claude 侧构造 in-process `SdkMcpTool` MCP server）。executor 在 agent 跑完后、validate 前，用 `collector.get_all()` + renderer 渲染并写 md（host 必渲染，skipped section 渲染 placeholder 不 fail）。本计划只接 pre-recon；recon/vuln/exploit/report 留 Plan 2-5。

**Tech Stack:** Python 3.13 · pydantic · openai-agents（`agents.FunctionTool`）· claude-agent-sdk（`create_sdk_mcp_server` / `SdkMcpTool`）· pytest（TDD）。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`

## Global Constraints

- **CLAUDE.md §1 双轨独立性**：本计划只动「md 产物怎么来」（collector+renderer），**不碰**双轨判定/合并/LLM 轨 prompt 的 source 派生。renderer 是纯函数，不引确定性层产物。
- **CLAUDE.md §2 双引擎可互换**：双引擎工具桥保证一套 `SectionSchema` 双引擎都生成工具，流程一致；改 agent/tool 行为后用 `scripts/validate_*_probe.py` 探针实测。
- **TS 1:1 对齐**：7 个 `set_*` 工具名、schema 字段、renderer section 顺序与 heading、placeholder/N/A 文案均移植 `upstream/main`（`apps/worker/src/collectors/pre-recon-collector.ts` / `services/pre-recon-renderer.ts` / `services/agent-execution.ts`）。
- **queue.json 通道不动**：vuln `{vt}_exploitation_queue.json` 继续走 `result.structured_output`（executor.py:147-154）；本计划只加 md 的 collector+renderer 通道。pre-recon 无 queue（`get_queue_filename(PRE_RECON)` 返 None）。
- **测试纪律**：只跑改动相关测试文件（CLAUDE.md §3：全套 pytest 有预存 hang/失败）。命令一律 `uv run pytest <文件>::<测试> -x`，前端无关。
- **诊断不去除**：`_enrich_missing_deliverable_error`（executor.py:186-232）**保留不动**——Plan 1 只治愈 pre-recon，recon/vuln/exploit 仍自己 Write md，仍可能 Missing deliverable；诊断对这些 agent 仍有价值。移除归 Plan 5（全 agent 治愈后）。

## File Structure

新增两个包（greenfield，目录不存在）：

```
packages/core/src/shannon_core/collectors/
├── __init__.py        # 导出 + make_collector(agent_name) 分发
├── base.py            # SectionSchema + CollectorBase + DuplicateCallError
├── bridge.py          # 双引擎工具桥:build_openai_tools / build_claude_mcp_server
└── pre_recon.py       # 7 个 SectionSchema(JSON Schema) + PreReconCollector

packages/core/src/shannon_core/renderers/
├── __init__.py        # 导出 + render_deliverable(agent_name, data) 分发
└── pre_recon.py       # render_pre_recon(data) -> md + SCOPE_AND_BOUNDARIES 常量
```

修改（既有文件）：

- `packages/core/src/shannon_core/agents/runner.py` — `run_claude_prompt` 加 `collector` 形参，透传 `provider.call`
- `packages/core/src/shannon_core/agents/providers.py` — `BaseProvider.call` 抽象签名加 `collector`
- `packages/core/src/shannon_core/agents/providers_anthropic.py` — `call` 加 `collector`；`_build_options` 加 `mcp_server`/`allowed_tools`；从 collector 经 bridge 构造 MCP server 注入
- `packages/core/src/shannon_core/agents/providers_openai.py` — `call` 加 `collector`；`build_agent` 加 `extra_tools`；从 collector 经 bridge 构造 function tools 注入
- `packages/core/src/shannon_core/agents/executor.py` — `execute` 内：建 collector → 传 run_claude_prompt → run 后用 renderer 写 md（validate 前）
- `prompts/pre-recon-code.txt` + `prompts/pipeline-testing/pre-recon-code.txt` — 删 Write 指令、加 `<deliverable_tools>` 块

测试：

```
packages/core/tests/collectors/__init__.py
packages/core/tests/collectors/test_base.py
packages/core/tests/collectors/test_bridge.py
packages/core/tests/collectors/test_pre_recon.py
packages/core/tests/renderers/__init__.py
packages/core/tests/renderers/test_pre_recon.py
packages/core/tests/agents/test_providers_collector_injection.py   # 新增
packages/core/tests/test_executor_collector_render.py              # 新增
scripts/validate_glm_mcp_tool_probe.py                             # 新增(GLM 真机探针)
```

---

## Task 1: CollectorBase + SectionSchema（声明式 collector 核心）

**Files:**
- Create: `packages/core/src/shannon_core/collectors/__init__.py`
- Create: `packages/core/src/shannon_core/collectors/base.py`
- Create: `packages/core/tests/collectors/__init__.py`
- Test: `packages/core/tests/collectors/test_base.py`

**Interfaces:**
- Produces: `SectionSchema`（frozen dataclass：`tool_name:str` / `section_key:str` / `description:str` / `json_schema:dict`）；`DuplicateCallError(Exception)`；`CollectorBase`（方法 `set_section(tool_name, payload)` / `get_all() -> dict` / `get_call_status() -> dict[str,str]` / `section_schemas` 属性 / `tool_names() -> list[str]`）。后续所有 task 消费这些。

- [ ] **Step 1: 写失败测试**

`packages/core/tests/collectors/__init__.py`（空文件，0 字节）。

`packages/core/tests/collectors/test_base.py`：
```python
from shannon_core.collectors.base import (
    CollectorBase,
    DuplicateCallError,
    SectionSchema,
)


def _schema(tool="set_alpha", key="alpha"):
    return SectionSchema(
        tool_name=tool,
        section_key=key,
        description="alpha tool",
        json_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
    )


def test_set_section_stores_payload_keyed_by_section_key():
    c = CollectorBase([_schema()])
    c.set_section("set_alpha", {"x": "v"})
    assert c.get_all() == {"alpha": {"x": "v"}}


def test_set_section_is_write_once_duplicate_raises():
    c = CollectorBase([_schema()])
    c.set_section("set_alpha", {"x": "first"})
    try:
        c.set_section("set_alpha", {"x": "second"})
    except DuplicateCallError:
        pass
    else:
        raise AssertionError("expected DuplicateCallError on second call")
    assert c.get_all() == {"alpha": {"x": "first"}}   # first call wins


def test_skipped_section_omitted_from_get_all():
    c = CollectorBase([_schema(), _schema("set_beta", "beta")])
    c.set_section("set_alpha", {"x": "v"})
    assert c.get_all() == {"alpha": {"x": "v"}}      # beta absent = skipped


def test_get_call_status_reports_called_or_skipped():
    c = CollectorBase([_schema(), _schema("set_beta", "beta")])
    c.set_section("set_alpha", {"x": "v"})
    assert c.get_call_status() == {"set_alpha": "called", "set_beta": "skipped"}


def test_tool_names_and_section_schemas_preserve_declaration_order():
    a, b = _schema(), _schema("set_beta", "beta")
    c = CollectorBase([a, b])
    assert c.tool_names() == ["set_alpha", "set_beta"]
    assert c.section_schemas == [a, b]


def test_set_section_rejects_unknown_tool():
    c = CollectorBase([_schema()])
    try:
        c.set_section("set_nope", {"x": "v"})
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown tool")


def test_get_all_returns_copy_not_internal_state():
    c = CollectorBase([_schema()])
    c.set_section("set_alpha", {"x": "v"})
    out = c.get_all()
    out["alpha"]["x"] = "mutated"
    assert c.get_all() == {"alpha": {"x": "v"}}   # internal untouched
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/collectors/test_base.py -x`
Expected: FAIL — `ModuleNotFoundError: shannon_core.collectors.base`

- [ ] **Step 3: 写最小实现**

`packages/core/src/shannon_core/collectors/__init__.py`：
```python
from shannon_core.collectors.base import (
    CollectorBase,
    DuplicateCallError,
    SectionSchema,
)

__all__ = ["CollectorBase", "DuplicateCallError", "SectionSchema", "make_collector"]


def make_collector(agent_name) -> "CollectorBase | None":
    """按 agent 分发 collector。Plan 1 仅 pre-recon；其余返 None（无 collector 通道）。"""
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        from shannon_core.collectors.pre_recon import PreReconCollector

        return PreReconCollector()
    return None
```

> `pre_recon.PreReconCollector` 在 Task 3 创建；Task 1 的 `make_collector` 用函数体内 lazy import，测试不触发该 import。

`packages/core/src/shannon_core/collectors/base.py`：
```python
"""声明式 collector 框架（host 渲染产物架构，对齐 TS collectors/）。

agent 调一组 set_* 结构化工具 → CollectorBase 收集 payload → renderer 确定性渲染 md。
write-once（重复调 DuplicateError，首次生效，对齐 TS pre-recon-collector.ts:445-451）。
skipped section 不在 get_all() 里，由 renderer 补 placeholder（不 fail activity）。
"""
from __future__ import annotations

from dataclasses import dataclass


class DuplicateCallError(Exception):
    """set_* 工具被调用超过一次（write-once）。对齐 TS DuplicateError：首次生效，重复 no-op。"""


@dataclass(frozen=True)
class SectionSchema:
    """一个 deliverable section 的声明式定义。

    Attributes:
        tool_name: 模型见到的工具名（如 "set_executive_summary"）。
        section_key: payload bag 里的键（如 "executive_summary"）。
        description: 工具描述，喂给模型当 tool description。
        json_schema: 完整 JSON Schema dict（type=object, properties...）。
            openai 侧作 FunctionTool.params_json_schema，claude 侧作 SdkMcpTool.input_schema（原样透传）。
    """

    tool_name: str
    section_key: str
    description: str
    json_schema: dict


class CollectorBase:
    """per-agent-run 的 payload 收集器（非全局，对齐 TS per-agent collector 实例）。"""

    def __init__(self, section_schemas: list[SectionSchema]):
        self._schemas: list[SectionSchema] = list(section_schemas)
        self._by_key: dict[str, SectionSchema] = {s.section_key: s for s in self._schemas}
        self._tool_to_key: dict[str, str] = {s.tool_name: s.section_key for s in self._schemas}
        self._payloads: dict[str, dict] = {}        # section_key -> payload；absent = skipped
        self._called_tools: list[str] = []          # tool_name 调用顺序（诊断用）

    @property
    def section_schemas(self) -> list[SectionSchema]:
        return list(self._schemas)

    def tool_names(self) -> list[str]:
        return [s.tool_name for s in self._schemas]

    def set_section(self, tool_name: str, payload: dict) -> None:
        """write-once 写入一个 section 的 payload。重复调抛 DuplicateCallError（首次生效）。"""
        key = self._tool_to_key.get(tool_name)
        if key is None and tool_name in self._by_key:   # 也容忍直接传 section_key
            key = tool_name
        if key is None or key not in self._by_key:
            raise KeyError(f"unknown section tool/key: {tool_name!r}")
        if key in self._payloads:
            raise DuplicateCallError(
                f"{tool_name} has already been called. Each set_* tool may only be called once per run."
            )
        self._payloads[key] = dict(payload or {})
        self._called_tools.append(tool_name)

    def get_all(self) -> dict:
        """返回 payload bag（深拷贝；skipped section 不含键，renderer 补 placeholder）。"""
        return {key: dict(val) for key, val in self._payloads.items()}

    def get_call_status(self) -> dict[str, str]:
        """每个 tool_name -> 'called' | 'skipped'（诊断/日志，对齐 TS getCallStatus）。"""
        return {
            s.tool_name: ("called" if s.section_key in self._payloads else "skipped")
            for s in self._schemas
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/collectors/test_base.py -x`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/collectors/__init__.py \
        packages/core/src/shannon_core/collectors/base.py \
        packages/core/tests/collectors/__init__.py \
        packages/core/tests/collectors/test_base.py
git commit -m "feat(collectors): CollectorBase + SectionSchema 声明式收集器(write-once/skipped)"
```

---

## Task 2: 双引擎工具桥（bridge.py）

**Files:**
- Create: `packages/core/src/shannon_core/collectors/bridge.py`
- Test: `packages/core/tests/collectors/test_bridge.py`

**Interfaces:**
- Consumes: Task 1 的 `CollectorBase` / `SectionSchema` / `DuplicateCallError`
- Produces: `build_openai_tools(collector) -> list[FunctionTool]`（每 section 一个 FunctionTool，`strict_json_schema=False`，`on_invoke_tool=(ctx,json_str)` 解析 JSON 写 collector，DuplicateError 返错误串不 raise）；`build_claude_mcp_server(collector, server_name="shannon-collector") -> McpSdkServerConfig`（每 section 一个 `SdkMcpTool`，`input_schema=<完整 JSON Schema dict>` 原样透传，handler `(args:dict)->{content,is_error}`）。Task 5 的两个 provider 消费这两个函数。

> **关键技术事实（已核查 SDK 源码）：**
> - openai `agents.FunctionTool` 是 dataclass：`FunctionTool(name, description, params_json_schema, on_invoke_tool, strict_json_schema=False)`。`on_invoke_tool(ctx, input_json_str)` 直接收 JSON 字符串——**不走 `@function_tool` 装饰器的签名推断**，故动态 schema 可直接喂 `params_json_schema`。`strict_json_schema=False` 跳过 `ensure_strict_json_schema`（`.venv/.../agents/tool.py:507-510`；我们的 schema 多 optional/嵌套，strict 会 reject）。
> - claude `create_sdk_mcp_server(name, tools=[SdkMcpTool])`；`_build_schema`（`.venv/.../claude_agent_sdk/__init__.py:403-421`）检测到 dict 含 `"type"`(str)+`"properties"` 时**原样透传为完整 JSON Schema**。handler 收 `args: dict`，返 `{"content":[{"type":"text","text":...}], "is_error": bool}`。

- [ ] **Step 1: 写失败测试**

`packages/core/tests/collectors/test_bridge.py`：
```python
import json

import pytest

from shannon_core.collectors.base import CollectorBase, SectionSchema
from shannon_core.collectors.bridge import build_claude_mcp_server, build_openai_tools

SCHEMA = SectionSchema(
    tool_name="set_alpha",
    section_key="alpha",
    description="alpha tool",
    json_schema={
        "type": "object",
        "properties": {"x": {"type": "string", "minLength": 1}},
        "required": ["x"],
    },
)


def _collector():
    return CollectorBase([SCHEMA])


# ---------- openai ----------

@pytest.mark.asyncio
async def test_openai_tool_invocation_writes_collector():
    from agents import RunContextWrapper

    collector = _collector()
    (tool,) = build_openai_tools(collector)
    assert tool.name == "set_alpha"
    assert tool.params_json_schema == SCHEMA.json_schema
    assert tool.strict_json_schema is False

    result = await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "v"}))
    assert "recorded" in str(result)
    assert collector.get_all() == {"alpha": {"x": "v"}}


@pytest.mark.asyncio
async def test_openai_tool_duplicate_returns_error_string_not_raise():
    from agents import RunContextWrapper

    collector = _collector()
    (tool,) = build_openai_tools(collector)
    await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "first"}))
    result = await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "second"}))
    assert "DuplicateError" in str(result)          # 返错误串，不 raise、不 fail run
    assert collector.get_all() == {"alpha": {"x": "first"}}


# ---------- claude ----------

@pytest.mark.asyncio
async def test_claude_mcp_server_is_in_process_sdk_config():
    collector = _collector()
    server = build_claude_mcp_server(collector)
    assert server["type"] == "sdk"
    assert server["name"] == "shannon-collector"


@pytest.mark.asyncio
async def test_claude_sdk_tool_input_schema_is_full_json_schema():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _collector()
    sdk_tool = _make_claude_sdk_tool(collector, SCHEMA)
    assert sdk_tool.name == "set_alpha"
    assert sdk_tool.input_schema == SCHEMA.json_schema


@pytest.mark.asyncio
async def test_claude_sdk_tool_handler_writes_collector():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _collector()
    sdk_tool = _make_claude_sdk_tool(collector, SCHEMA)
    res = await sdk_tool.handler({"x": "v"})
    assert res["content"][0]["type"] == "text"
    assert "recorded" in res["content"][0]["text"]
    assert res.get("is_error") is not True
    assert collector.get_all() == {"alpha": {"x": "v"}}


@pytest.mark.asyncio
async def test_claude_sdk_tool_handler_duplicate_is_error_envelope():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _collector()
    sdk_tool = _make_claude_sdk_tool(collector, SCHEMA)
    await sdk_tool.handler({"x": "first"})
    res = await sdk_tool.handler({"x": "second"})
    assert res.get("is_error") is True
    assert "DuplicateError" in res["content"][0]["text"]
    assert collector.get_all() == {"alpha": {"x": "first"}}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/collectors/test_bridge.py -x`
Expected: FAIL — `ModuleNotFoundError: shannon_core.collectors.bridge`

- [ ] **Step 3: 写最小实现**

`packages/core/src/shannon_core/collectors/bridge.py`：
```python
"""双引擎工具桥：一份 SectionSchema 在 openai / claude 各生成一套 set_* 工具。

消除「13 agent × 2 引擎 = 26 套手写工具」——两引擎共享输入都是 JSON Schema dict:
- openai: 直接构造 FunctionTool(params_json_schema=<dict>, strict_json_schema=False,
          on_invoke_tool=(ctx, json_str))。不走 @function_tool 签名推断。
- claude: SdkMcpTool(input_schema=<完整 JSON Schema dict>)，create_sdk_mcp_server 的
          _build_schema 检测到 type+properties 原样透传（claude_agent_sdk/__init__.py:403-421）。

两引擎工具闭包捕获 collector，重复调 DuplicateCallError：
- openai 返错误串（不 raise，不 fail run，对齐 TS retryable=false 结构化结果）
- claude 返 {"is_error": True} 信封
首次调用生效。
"""
from __future__ import annotations

import json

from shannon_core.collectors.base import CollectorBase, DuplicateCallError, SectionSchema


def build_openai_tools(collector: CollectorBase):
    """每个 SectionSchema -> 一个 openai-agents FunctionTool（闭包捕获 collector）。"""
    from agents import FunctionTool

    return [_make_openai_function_tool(collector, s) for s in collector.section_schemas]


def _make_openai_function_tool(collector: CollectorBase, schema: SectionSchema):
    tool_name = schema.tool_name

    async def _on_invoke(ctx, input_json: str) -> str:
        # ctx 不用（collector 经闭包）；input_json 是模型输出的 JSON 串
        try:
            payload = json.loads(input_json) if input_json else {}
        except json.JSONDecodeError:
            payload = {}
        try:
            collector.set_section(tool_name, payload)
        except DuplicateCallError:
            return f"{tool_name}: DuplicateError — already called; first call wins"
        return f"{tool_name}: recorded"

    return FunctionTool(
        name=tool_name,
        description=schema.description,
        params_json_schema=schema.json_schema,
        on_invoke_tool=_on_invoke,
        strict_json_schema=False,
    )


def build_claude_mcp_server(
    collector: CollectorBase, server_name: str = "shannon-collector"
):
    """每个 SectionSchema -> 一个 SdkMcpTool，打包成 in-process MCP server（无子进程/IPC）。"""
    from claude_agent_sdk import create_sdk_mcp_server

    tools = [_make_claude_sdk_tool(collector, s) for s in collector.section_schemas]
    return create_sdk_mcp_server(name=server_name, tools=tools)


def _make_claude_sdk_tool(collector: CollectorBase, schema: SectionSchema):
    from claude_agent_sdk import SdkMcpTool

    tool_name = schema.tool_name

    async def _handler(args: dict) -> dict:
        try:
            collector.set_section(tool_name, args or {})
        except DuplicateCallError:
            return {
                "content": [
                    {"type": "text", "text": f"{tool_name}: DuplicateError — already called; first call wins"}
                ],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": f"{tool_name}: recorded"}]}

    return SdkMcpTool(
        name=tool_name,
        description=schema.description,
        input_schema=schema.json_schema,
        handler=_handler,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/collectors/test_bridge.py -x`
Expected: PASS（7 passed）

> 若 `RunContextWrapper(context=None)` 构造报错，改用 `types.SimpleNamespace()`（handler 不读 ctx）。`SdkMcpTool` 须可裸构造（它就是 dataclass）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/collectors/bridge.py packages/core/tests/collectors/test_bridge.py
git commit -m "feat(collectors): 双引擎工具桥 build_openai_tools/build_claude_mcp_server"
```

---

## Task 3: pre-recon 7 个 SectionSchema + PreReconCollector

**Files:**
- Create: `packages/core/src/shannon_core/collectors/pre_recon.py`
- Test: `packages/core/tests/collectors/test_pre_recon.py`

**Interfaces:**
- Consumes: Task 1 `CollectorBase` / `SectionSchema`；Task 1 `__init__.make_collector` lazy import `PreReconCollector`
- Produces: `PreReconCollector(CollectorBase)`（无参构造，自带 7 个 SectionSchema）；`PRE_RECON_SECTIONS: list[SectionSchema]`。Task 4 renderer 消费相同 section_key；Task 5+ executor 经 `make_collector(PRE_RECON)` 拿到它。

> 7 工具名（对齐 TS `PRE_RECON_ONE_SHOT_TOOLS`）：`set_executive_summary` / `set_application_intelligence` / `set_auth_deep_dive` / `set_codebase_indexing` / `set_critical_file_paths` / `set_xss_sinks` / `set_ssrf_sinks`。section_key = tool_name 去 `set_` 前缀。字段全部对齐 TS TypeBox（**XSS=applicable+5 数组 / SSRF=applicable+13 数组 / attack_surface=4 字段 / critical_file_paths=9 类 / infrastructure=4 字段**——勿漏）。

- [ ] **Step 1: 写失败测试**

`packages/core/tests/collectors/test_pre_recon.py`：
```python
from shannon_core.collectors.pre_recon import PRE_RECON_SECTIONS, PreReconCollector

EXPECTED_TOOLS = [
    "set_executive_summary",
    "set_application_intelligence",
    "set_auth_deep_dive",
    "set_codebase_indexing",
    "set_critical_file_paths",
    "set_xss_sinks",
    "set_ssrf_sinks",
]


def test_pre_recon_has_seven_tools_in_ts_order():
    assert [s.tool_name for s in PRE_RECON_SECTIONS] == EXPECTED_TOOLS


def test_section_key_is_tool_name_without_set_prefix():
    assert [s.section_key for s in PRE_RECON_SECTIONS] == [
        "executive_summary", "application_intelligence", "auth_deep_dive",
        "codebase_indexing", "critical_file_paths", "xss_sinks", "ssrf_sinks",
    ]


def test_every_schema_is_valid_json_schema_object():
    for s in PRE_RECON_SECTIONS:
        assert s.json_schema["type"] == "object"
        assert "properties" in s.json_schema


def test_executive_summary_schema():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_executive_summary")
    assert s.json_schema["properties"] == {"text": {"type": "string", "minLength": 1}}
    assert s.json_schema["required"] == ["text"]


def test_application_intelligence_has_four_nested_groups():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_application_intelligence")
    props = s.json_schema["properties"]
    assert set(props) == {"architecture", "data_security", "attack_surface", "infrastructure"}
    # attack_surface 必须有 4 字段（旧草稿漏了 2 个）
    attack = props["attack_surface"]["properties"]
    assert {"external_entry_points", "internal_service_communication",
            "input_validation_patterns", "background_processing"} <= set(attack)


def test_xss_sinks_schema_has_applicable_and_five_sink_arrays():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_xss_sinks")
    props = s.json_schema["properties"]
    assert props["applicable"] == {"type": "boolean"}
    for ctx_name in ["html_body", "html_attribute", "javascript", "css", "url"]:
        assert props[ctx_name]["type"] == "array"


def test_ssrf_sinks_schema_has_thirteen_sink_arrays():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_ssrf_sinks")
    props = s.json_schema["properties"]
    assert props["applicable"] == {"type": "boolean"}
    expected = [
        "http_clients", "raw_sockets", "url_openers", "redirect_handlers",
        "headless_browsers", "media_processors", "link_preview", "webhook_testers",
        "sso_oidc_discovery", "importers", "package_installers",
        "monitoring_and_health", "cloud_metadata",
    ]
    for k in expected:
        assert props[k]["type"] == "array"
    assert len(expected) == 13


def test_critical_file_paths_has_nine_categories():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_critical_file_paths")
    props = s.json_schema["properties"]
    assert set(props) == {
        "configuration", "authentication_and_authorization", "api_and_routing",
        "data_models_and_db", "dependency_manifests", "sensitive_data_and_secrets",
        "middleware_and_input_validation", "logging_and_monitoring", "infrastructure_and_deployment",
    }


def test_pre_recon_collector_instance_has_seven_sections():
    c = PreReconCollector()
    assert c.tool_names() == EXPECTED_TOOLS
    assert c.get_call_status() == {t: "skipped" for t in EXPECTED_TOOLS}


def test_sink_ref_items_schema_has_location_and_sink_function():
    s = next(s for s in PRE_RECON_SECTIONS if s.tool_name == "set_xss_sinks")
    item = s.json_schema["properties"]["html_body"]["items"]
    assert item["type"] == "object"
    assert "location" in item["properties"]
    assert "sink_function" in item["properties"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/collectors/test_pre_recon.py -x`
Expected: FAIL — `ModuleNotFoundError: shannon_core.collectors.pre_recon`

- [ ] **Step 3: 写最小实现**

`packages/core/src/shannon_core/collectors/pre_recon.py`：
```python
"""pre-recon 的 7 个 set_* section schema（对齐 TS pre-recon-collector.ts）。

字段名/类型移植 TS TypeBox 定义；JSON Schema dict 直接喂双引擎桥（openai
params_json_schema / claude input_schema）。application_intelligence 是复合工具，
喂 renderer 的 section 2/4/5/6 四个 section。
"""
from __future__ import annotations

import copy

from shannon_core.collectors.base import CollectorBase, SectionSchema


def _str_field(desc: str, min_length: int = 1) -> dict:
    return {"type": "string", "minLength": min_length, "description": desc}


# SinkRef（XSS/SSRF 数组元素，对齐 TS SinkRefSchema）
SINK_REF: dict = {
    "type": "object",
    "properties": {
        "location": _str_field(
            "File path with line number (e.g. 'templates/render.js:34') or richer prose. "
            "Must let a downstream agent find the exact location."
        ),
        "sink_function": _str_field("The sink function or property name (e.g. 'innerHTML', 'eval')."),
        "notes": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Optional context — render-context, attribute, scope hints. Omit when not needed.",
        },
    },
    "required": ["location", "sink_function"],
}


def _sink_array(desc: str) -> dict:
    return {"type": "array", "items": copy.deepcopy(SINK_REF), "description": desc}


def _str_array(desc: str = "") -> dict:
    s = {"type": "array", "items": {"type": "string", "minLength": 1}}
    if desc:
        s["description"] = desc
    return s


def _obj(props: dict, required: list[str], desc: str = "") -> dict:
    schema: dict = {"type": "object", "properties": props, "required": required}
    if desc:
        schema["description"] = desc
    return schema


# --- 各 section schema（移植 TS）---

EXECUTIVE_SUMMARY = _obj({"text": _str_field("2-3 paragraph overview of the app's security posture.")}, ["text"])

APPLICATION_INTELLIGENCE = _obj(
    {
        "architecture": _obj(
            {
                "framework_and_language": _str_field("Framework & language with security implications."),
                "architectural_pattern": _str_field("Architectural pattern with trust boundary analysis."),
                "critical_security_components": _str_field("Focus on auth, authz, data protection."),
            },
            ["framework_and_language", "architectural_pattern", "critical_security_components"],
        ),
        "data_security": _obj(
            {
                "database_security": _str_field("Encryption, access controls, query safety."),
                "data_flow_security": _str_field("Sensitive data paths and protection mechanisms."),
                "multi_tenant_isolation": _str_field("Tenant separation effectiveness."),
            },
            ["database_security", "data_flow_security", "multi_tenant_isolation"],
        ),
        "attack_surface": _obj(
            {
                "external_entry_points": _str_field("Publicly exposed web pages and API endpoints."),
                "internal_service_communication": _str_field("Service-to-service calls and trust."),
                "input_validation_patterns": _str_field("Where/how input is validated."),
                "background_processing": _str_field("Queues, schedulers, webhooks."),
            },
            ["external_entry_points", "internal_service_communication",
             "input_validation_patterns", "background_processing"],
        ),
        "infrastructure": _obj(
            {
                "secrets_management": _str_field("How secrets are stored/loaded."),
                "configuration_security": _str_field("Config hardening, debug flags."),
                "external_dependencies": _str_field("Notable deps with known risk surface."),
                "monitoring_and_logging": _str_field("What is logged; sensitive data leakage."),
            },
            ["secrets_management", "configuration_security", "external_dependencies", "monitoring_and_logging"],
        ),
    },
    ["architecture", "data_security", "attack_surface", "infrastructure"],
    desc="Composite of architecture (Section 2), data security (4), attack surface (5), infrastructure (6).",
)

AUTH_DEEP_DIVE = _obj(
    {
        "authentication_mechanisms": _str_field("Auth mechanisms + exhaustive list of auth endpoints."),
        "session_management": _str_field("Session/token security; cookie flags (HttpOnly/Secure/SameSite) with file:line."),
        "authz_model": _str_field("Authorization model and bypass scenarios."),
        "multi_tenancy": _str_field("Multi-tenancy security implementation."),
        "sso_oauth_oidc": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "SSO/OAuth/OIDC flows; null if none at all.",
        },
    },
    ["authentication_mechanisms", "session_management", "authz_model", "multi_tenancy", "sso_oauth_oidc"],
)

CODEBASE_INDEXING = _obj({"text": _str_field("Directory structure narrative (Section 7).")}, ["text"])

CRITICAL_FILE_PATHS = _obj(
    {k: _str_array() for k in [
        "configuration", "authentication_and_authorization", "api_and_routing",
        "data_models_and_db", "dependency_manifests", "sensitive_data_and_secrets",
        "middleware_and_input_validation", "logging_and_monitoring", "infrastructure_and_deployment",
    ]},
    [],  # 全可选：某类没有就给空数组
)

XSS_SINKS = _obj(
    {
        "applicable": {"type": "boolean", "description": "false only if the app has no web frontend at all."},
        "html_body": _sink_array("Sinks rendered into HTML body context."),
        "html_attribute": _sink_array("Sinks rendered into HTML attribute context."),
        "javascript": _sink_array("Sinks rendered into JavaScript context."),
        "css": _sink_array("Sinks rendered into CSS context."),
        "url": _sink_array("Sinks rendered into URL context."),
    },
    ["applicable", "html_body", "html_attribute", "javascript", "css", "url"],
)

_SSRF_KEYS = [
    "http_clients", "raw_sockets", "url_openers", "redirect_handlers",
    "headless_browsers", "media_processors", "link_preview", "webhook_testers",
    "sso_oidc_discovery", "importers", "package_installers",
    "monitoring_and_health", "cloud_metadata",
]
SSRF_SINKS = _obj(
    {
        "applicable": {"type": "boolean", "description": "false only if the app makes no outbound requests at all."},
        **{k: _sink_array(v) for k, v in {
            "http_clients": "HTTP client sinks.", "raw_sockets": "Raw socket sinks.",
            "url_openers": "URL opener sinks.", "redirect_handlers": "Redirect handler sinks.",
            "headless_browsers": "Headless browser sinks.", "media_processors": "Media processor sinks.",
            "link_preview": "Link preview sinks.", "webhook_testers": "Webhook tester sinks.",
            "sso_oidc_discovery": "SSO/OIDC discovery sinks.", "importers": "Importer sinks.",
            "package_installers": "Package installer sinks.", "monitoring_and_health": "Monitoring/health sinks.",
            "cloud_metadata": "Cloud metadata sinks.",
        }.items()},
    },
    ["applicable"] + _SSRF_KEYS,
)


def _section(tool_name: str, key: str, desc: str, schema: dict) -> SectionSchema:
    return SectionSchema(tool_name=tool_name, section_key=key, description=desc, json_schema=schema)


# 顺序对齐 TS PRE_RECON_ONE_SHOT_TOOLS
PRE_RECON_SECTIONS: list[SectionSchema] = [
    _section("set_executive_summary", "executive_summary",
             "Application's overall security posture (Section 1).", EXECUTIVE_SUMMARY),
    _section("set_application_intelligence", "application_intelligence",
             "Composite of architecture, data security, attack surface, infrastructure (Sections 2,4,5,6).",
             APPLICATION_INTELLIGENCE),
    _section("set_auth_deep_dive", "auth_deep_dive",
             "Authentication & authorization deep dive (Section 3).", AUTH_DEEP_DIVE),
    _section("set_codebase_indexing", "codebase_indexing",
             "Directory structure narrative (Section 7).", CODEBASE_INDEXING),
    _section("set_critical_file_paths", "critical_file_paths",
             "Categorized catalog of critical file paths (Section 8).", CRITICAL_FILE_PATHS),
    _section("set_xss_sinks", "xss_sinks",
             "XSS sinks grouped by render context (Section 9). Set applicable=false only if no web frontend.",
             XSS_SINKS),
    _section("set_ssrf_sinks", "ssrf_sinks",
             "SSRF sinks grouped by sink category (Section 10). Set applicable=false only if no outbound requests.",
             SSRF_SINKS),
]


class PreReconCollector(CollectorBase):
    """pre-recon 的 7-section collector（无参构造，自带 PRE_RECON_SECTIONS）。"""

    def __init__(self) -> None:
        super().__init__(PRE_RECON_SECTIONS)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/collectors/test_pre_recon.py -x`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/collectors/pre_recon.py packages/core/tests/collectors/test_pre_recon.py
git commit -m "feat(collectors): pre-recon 7 set_* SectionSchema(字段全对齐 TS)+PreReconCollector"
```

---

## Task 4: pre-recon renderer（render_pre_recon）

**Files:**
- Create: `packages/core/src/shannon_core/renderers/__init__.py`
- Create: `packages/core/src/shannon_core/renderers/pre_recon.py`
- Create: `packages/core/tests/renderers/__init__.py`
- Test: `packages/core/tests/renderers/test_pre_recon.py`

**Interfaces:**
- Consumes: Task 3 的 section_key（`executive_summary` / `application_intelligence` / `auth_deep_dive` / `codebase_indexing` / `critical_file_paths` / `xss_sinks` / `ssrf_sinks`）
- Produces: `render_pre_recon(data: dict) -> str`（纯函数；data 是 collector.get_all() 的子集，缺键 = skipped → placeholder）；`render_deliverable(agent_name, data) -> str | None`（分发；pre-recon 返 render_pre_recon，其余 None）。Task 7 executor 在 validate 前调 `render_deliverable`。

> **渲染规约（对齐 TS pre-recon-renderer.ts）：**
> - preamble：`# Penetration Test Scope & Boundaries` + Scope 正文 + `---`（正文取自当前 prompt 的 scope 块，host 与 prompt 同源）。
> - 10 section 顺序与 heading（TS 原文）：`## 1. Executive Summary` / `## 2. Architecture & Technology Stack` / `## 3. Authentication & Authorization Deep Dive` / `## 4. Data Security & Storage` / `## 5. Attack Surface Analysis` / `## 6. Infrastructure & Operational Security` / `## 7. Overall Codebase Indexing` / `## 8. Critical File Paths` / `## 9. XSS Sinks and Render Contexts` / `## 10. SSRF Sinks`。
> - `application_intelligence` 喂 section 2/4/5/6；缺则该 4 section 各自 placeholder。
> - skipped placeholder（TS 原文 verbatim）：`_[Section N: not provided — \`set_*\` was not called]_`
> - XSS `applicable=false` → `*(N/A — the application has no web frontend; XSS sink analysis does not apply.)*`；SSRF `applicable=false` → `*(N/A — the application makes no outbound requests; SSRF sink analysis does not apply.)*`
> - 空数组：sink 类 → `*(scanned, no sinks of this kind found)*`；critical path 类 → `*(none identified)*`

- [ ] **Step 1: 写失败测试**

`packages/core/tests/renderers/__init__.py`（空文件）。

`packages/core/tests/renderers/test_pre_recon.py`：
```python
from shannon_core.renderers.pre_recon import render_pre_recon
from shannon_core.renderers import render_deliverable
from shannon_core.models.agents import AgentName


def test_empty_data_renders_preamble_and_all_placeholders():
    md = render_pre_recon({})
    assert md.startswith("# Penetration Test Scope & Boundaries")
    assert "## 1. Executive Summary" in md
    assert "## 10. SSRF Sinks" in md
    for tool in [
        "set_executive_summary", "set_application_intelligence", "set_auth_deep_dive",
        "set_codebase_indexing", "set_critical_file_paths", "set_xss_sinks", "set_ssrf_sinks",
    ]:
        assert f"`{tool}` was not called" in md


def test_executive_summary_section_renders_text():
    md = render_pre_recon({"executive_summary": {"text": "The app is risky."}})
    assert "## 1. Executive Summary" in md
    assert "The app is risky." in md


def test_application_intelligence_feeds_sections_2_4_5_6():
    md = render_pre_recon({
        "application_intelligence": {
            "architecture": {"framework_and_language": "Express + Node", "architectural_pattern": "MVC",
                             "critical_security_components": "passport"},
            "data_security": {"database_security": "parametrized", "data_flow_security": "tls",
                              "multi_tenant_isolation": "n/a"},
            "attack_surface": {"external_entry_points": "/login", "internal_service_communication": "none",
                               "input_validation_patterns": "express-validator", "background_processing": "bull"},
            "infrastructure": {"secrets_management": "env", "configuration_security": "helmet",
                               "external_dependencies": "lodash", "monitoring_and_logging": "winston"},
        }
    })
    assert "## 2. Architecture & Technology Stack" in md and "Express + Node" in md
    assert "## 4. Data Security & Storage" in md and "parametrized" in md
    assert "## 5. Attack Surface Analysis" in md and "/login" in md
    assert "## 6. Infrastructure & Operational Security" in md and "winston" in md


def test_auth_deep_dive_section_renders_with_null_sso():
    md = render_pre_recon({"auth_deep_dive": {
        "authentication_mechanisms": "session", "session_management": "cookie HttpOnly",
        "authz_model": "rbac", "multi_tenancy": "single", "sso_oauth_oidc": None,
    }})
    assert "## 3. Authentication & Authorization Deep Dive" in md
    assert "session" in md


def test_xss_applicable_false_renders_na():
    md = render_pre_recon({"xss_sinks": {"applicable": False}})
    assert "N/A — the application has no web frontend; XSS sink analysis does not apply." in md


def test_xss_with_empty_sink_arrays_renders_scanned_placeholder():
    md = render_pre_recon({"xss_sinks": {
        "applicable": True, "html_body": [], "html_attribute": [],
        "javascript": [], "css": [], "url": [],
    }})
    assert "scanned, no sinks of this kind found" in md


def test_xss_with_sinks_renders_location_and_sink_function():
    md = render_pre_recon({"xss_sinks": {
        "applicable": True,
        "html_body": [{"location": "render.js:34", "sink_function": "innerHTML", "notes": "user input"}],
        "html_attribute": [], "javascript": [], "css": [], "url": [],
    }})
    assert "render.js:34" in md and "innerHTML" in md


def test_ssrf_applicable_false_renders_na():
    md = render_pre_recon({"ssrf_sinks": {"applicable": False}})
    assert "N/A — the application makes no outbound requests; SSRF sink analysis does not apply." in md


def test_critical_file_paths_empty_array_renders_none_identified():
    md = render_pre_recon({"critical_file_paths": {"configuration": [], "api_and_routing": ["routes.js"]}})
    assert "none identified" in md
    assert "routes.js" in md


def test_full_payload_byte_stability():
    md = render_pre_recon({
        "executive_summary": {"text": "OVERVIEW."},
        "application_intelligence": {
            "architecture": {"framework_and_language": "F", "architectural_pattern": "P", "critical_security_components": "C"},
            "data_security": {"database_security": "d1", "data_flow_security": "d2", "multi_tenant_isolation": "d3"},
            "attack_surface": {"external_entry_points": "a1", "internal_service_communication": "a2",
                               "input_validation_patterns": "a3", "background_processing": "a4"},
            "infrastructure": {"secrets_management": "i1", "configuration_security": "i2",
                               "external_dependencies": "i3", "monitoring_and_logging": "i4"},
        },
        "auth_deep_dive": {"authentication_mechanisms": "m", "session_management": "s", "authz_model": "z",
                           "multi_tenancy": "t", "sso_oauth_oidc": None},
        "codebase_indexing": {"text": "INDEX."},
        "critical_file_paths": {"configuration": ["c.js"]},
        "xss_sinks": {"applicable": False},
        "ssrf_sinks": {"applicable": False},
    })
    assert md.count("# Penetration Test Scope & Boundaries") == 1
    assert md.endswith("\n")
    assert "OVERVIEW." in md and "INDEX." in md


def test_render_deliverable_dispatches_pre_recon():
    md = render_deliverable(AgentName.PRE_RECON, {"executive_summary": {"text": "x"}})
    assert md is not None and "## 1. Executive Summary" in md


def test_render_deliverable_returns_none_for_non_pre_recon():
    assert render_deliverable(AgentName.RECON, {}) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/renderers/test_pre_recon.py -x`
Expected: FAIL — `ModuleNotFoundError: shannon_core.renderers.pre_recon`

- [ ] **Step 3: 写最小实现**

`packages/core/src/shannon_core/renderers/__init__.py`：
```python
from shannon_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable"]


def render_deliverable(agent_name, data: dict) -> "str | None":
    """按 agent 分发 renderer。Plan 1 仅 pre-recon；其余返 None（无 collector 通道）。"""
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        return render_pre_recon(data)
    return None
```

`packages/core/src/shannon_core/renderers/pre_recon.py`：
```python
"""pre-recon deliverable renderer（纯函数，对齐 TS services/pre-recon-renderer.ts::renderPreRecon）。

输入 data = collector.get_all() 的子集（缺键 = skipped → placeholder，不 fail）。
输出 md：preamble + 10 section。application_intelligence 喂 section 2/4/5/6。
"""
from __future__ import annotations

SCOPE_AND_BOUNDARIES = """# Penetration Test Scope & Boundaries

**Primary Directive:** Your analysis is strictly limited to the **network-accessible attack surface** of the application. All subsequent tasks must adhere to this scope. Before reporting any finding (e.g., an entry point, a vulnerability sink), you must first verify it meets the "In-Scope" criteria.

### In-Scope: Network-Reachable Components
A component is considered **in-scope** if its execution can be initiated, directly or indirectly, by a network request that the deployed application server is capable of receiving. This includes:
- Publicly exposed web pages and API endpoints.
- Endpoints requiring authentication via the application's standard login mechanisms.
- Any developer utility, debug console, or script that has been mistakenly exposed through a route or is otherwise callable from other in-scope, network-reachable code.

### Out-of-Scope: Locally Executable Only
A component is **out-of-scope** if it **cannot** be invoked through the running application's network interface and requires an execution context completely external to the application's request-response cycle. This includes tools that must be run via:
- A command-line interface (e.g., `go run ./cmd/...`, `python scripts/...`).
- A development environment's internal tooling (e.g., a "run script" button in an IDE).
- CI/CD pipeline scripts or build tools (e.g., Dagger build definitions).
- Database migration scripts, backup tools, or maintenance utilities.
- Local development servers, test harnesses, or debugging utilities.
- Static files or scripts that require manual opening in a browser (not served by the application).
"""


def _placeholder(n: int, tool: str) -> str:
    return f"_[Section {n}: not provided — `{tool}` was not called]_"


def _kv(label: str, value: str) -> str:
    return f"- **{label}:** {value}"


def _section(n: int, title: str, body: str) -> str:
    return f"## {n}. {title}\n\n{body}"


def _render_executive_summary(data) -> str:
    es = data.get("executive_summary")
    if not es:
        return _section(1, "Executive Summary", _placeholder(1, "set_executive_summary"))
    return _section(1, "Executive Summary", es.get("text", "").strip() or _placeholder(1, "set_executive_summary"))


def _render_architecture(ai) -> str:
    if not ai:
        return _section(2, "Architecture & Technology Stack", _placeholder(2, "set_application_intelligence"))
    a = ai.get("architecture", {})
    body = "\n".join([
        _kv("Framework & Language", a.get("framework_and_language", "")),
        _kv("Architectural Pattern", a.get("architectural_pattern", "")),
        _kv("Critical Security Components", a.get("critical_security_components", "")),
    ])
    return _section(2, "Architecture & Technology Stack", body)


def _render_auth(data) -> str:
    ad = data.get("auth_deep_dive")
    if not ad:
        return _section(3, "Authentication & Authorization Deep Dive", _placeholder(3, "set_auth_deep_dive"))
    sso = ad.get("sso_oauth_oidc")
    body = "\n".join([
        _kv("Authentication Mechanisms", ad.get("authentication_mechanisms", "")),
        _kv("Session Management", ad.get("session_management", "")),
        _kv("Authorization Model", ad.get("authz_model", "")),
        _kv("Multi-tenancy", ad.get("multi_tenancy", "")),
        _kv("SSO/OAuth/OIDC", sso if sso else "(none identified)"),
    ])
    return _section(3, "Authentication & Authorization Deep Dive", body)


def _render_data_security(ai) -> str:
    if not ai:
        return _section(4, "Data Security & Storage", _placeholder(4, "set_application_intelligence"))
    d = ai.get("data_security", {})
    body = "\n".join([
        _kv("Database Security", d.get("database_security", "")),
        _kv("Data Flow Security", d.get("data_flow_security", "")),
        _kv("Multi-tenant Data Isolation", d.get("multi_tenant_isolation", "")),
    ])
    return _section(4, "Data Security & Storage", body)


def _render_attack_surface(ai) -> str:
    if not ai:
        return _section(5, "Attack Surface Analysis", _placeholder(5, "set_application_intelligence"))
    a = ai.get("attack_surface", {})
    body = "\n".join([
        _kv("External Entry Points", a.get("external_entry_points", "")),
        _kv("Internal Service Communication", a.get("internal_service_communication", "")),
        _kv("Input Validation Patterns", a.get("input_validation_patterns", "")),
        _kv("Background Processing", a.get("background_processing", "")),
    ])
    return _section(5, "Attack Surface Analysis", body)


def _render_infrastructure(ai) -> str:
    if not ai:
        return _section(6, "Infrastructure & Operational Security", _placeholder(6, "set_application_intelligence"))
    i = ai.get("infrastructure", {})
    body = "\n".join([
        _kv("Secrets Management", i.get("secrets_management", "")),
        _kv("Configuration Security", i.get("configuration_security", "")),
        _kv("External Dependencies", i.get("external_dependencies", "")),
        _kv("Monitoring & Logging", i.get("monitoring_and_logging", "")),
    ])
    return _section(6, "Infrastructure & Operational Security", body)


def _render_codebase_indexing(data) -> str:
    ci = data.get("codebase_indexing")
    if not ci:
        return _section(7, "Overall Codebase Indexing", _placeholder(7, "set_codebase_indexing"))
    return _section(7, "Overall Codebase Indexing", ci.get("text", "").strip() or _placeholder(7, "set_codebase_indexing"))


_PATH_LABELS = [
    ("configuration", "Configuration"),
    ("authentication_and_authorization", "Authentication & Authorization"),
    ("api_and_routing", "API & Routing"),
    ("data_models_and_db", "Data Models & DB"),
    ("dependency_manifests", "Dependency Manifests"),
    ("sensitive_data_and_secrets", "Sensitive Data & Secrets"),
    ("middleware_and_input_validation", "Middleware & Input Validation"),
    ("logging_and_monitoring", "Logging & Monitoring"),
    ("infrastructure_and_deployment", "Infrastructure & Deployment"),
]


def _render_critical_file_paths(data) -> str:
    cfp = data.get("critical_file_paths")
    if not cfp:
        return _section(8, "Critical File Paths", _placeholder(8, "set_critical_file_paths"))
    lines = []
    for key, label in _PATH_LABELS:
        paths = cfp.get(key, [])
        if paths:
            bullets = "\n".join(f"  - {p}" for p in paths)
            lines.append(f"- **{label}:**\n{bullets}")
        else:
            lines.append(f"- **{label}:** *(none identified)*")
    return _section(8, "Critical File Paths", "\n".join(lines))


def _render_sink_list(sinks) -> str:
    if not sinks:
        return "*(scanned, no sinks of this kind found)*"
    return "\n".join(
        f"- `{s.get('sink_function', '?')}` — {s.get('location', '?')}"
        + (f" ({s['notes']})" if s.get("notes") else "")
        for s in sinks
    )


def _render_sinks(n, title, tool, payload, labels, na_text) -> str:
    if not payload:
        return _section(n, title, _placeholder(n, tool))
    if payload.get("applicable") is False:
        return _section(n, title, na_text)
    lines = [f"- **{label}:**\n  {_render_sink_list(payload.get(key, []))}" for key, label in labels]
    return _section(n, title, "\n".join(lines))


_XSS_LABELS = [("html_body", "HTML Body"), ("html_attribute", "HTML Attribute"),
               ("javascript", "JavaScript"), ("css", "CSS"), ("url", "URL")]
_XSS_NA = "*(N/A — the application has no web frontend; XSS sink analysis does not apply.)*"

_SSRF_LABELS = [
    ("http_clients", "HTTP Clients"), ("raw_sockets", "Raw Sockets"), ("url_openers", "URL Openers"),
    ("redirect_handlers", "Redirect Handlers"), ("headless_browsers", "Headless Browsers"),
    ("media_processors", "Media Processors"), ("link_preview", "Link Preview"),
    ("webhook_testers", "Webhook Testers"), ("sso_oidc_discovery", "SSO/OIDC Discovery"),
    ("importers", "Importers"), ("package_installers", "Package Installers"),
    ("monitoring_and_health", "Monitoring & Health"), ("cloud_metadata", "Cloud Metadata"),
]
_SSRF_NA = "*(N/A — the application makes no outbound requests; SSRF sink analysis does not apply.)*"


def render_pre_recon(data: dict) -> str:
    """data = collector.get_all() 子集（缺键=skipped）。返回完整 md（preamble + 10 section）。"""
    ai = data.get("application_intelligence")
    sections = [
        SCOPE_AND_BOUNDARIES,
        "---",
        "",
        _render_executive_summary(data),
        "",
        _render_architecture(ai),
        "",
        _render_auth(data),
        "",
        _render_data_security(ai),
        "",
        _render_attack_surface(ai),
        "",
        _render_infrastructure(ai),
        "",
        _render_codebase_indexing(data),
        "",
        _render_critical_file_paths(data),
        "",
        _render_sinks(9, "XSS Sinks and Render Contexts", "set_xss_sinks",
                      data.get("xss_sinks"), _XSS_LABELS, _XSS_NA),
        "",
        _render_sinks(10, "SSRF Sinks", "set_ssrf_sinks",
                      data.get("ssrf_sinks"), _SSRF_LABELS, _SSRF_NA),
        "",
    ]
    return "\n".join(sections).rstrip() + "\n"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/renderers/test_pre_recon.py -x`
Expected: PASS（12 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/renderers/ packages/core/tests/renderers/
git commit -m "feat(renderers): pre-recon host renderer render_pre_recon(byte-stable+placeholder)"
```

---

## Task 5: 把 `collector` 参数贯通 runner + 双引擎 provider

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py`（`run_claude_prompt` 加 `collector`，透传 provider.call）
- Modify: `packages/core/src/shannon_core/agents/providers.py`（`BaseProvider.call` 抽象签名加 `collector`）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`（`call` 加 `collector`；`_build_options` 加 `mcp_server`/`allowed_tools`；从 collector 经 `build_claude_mcp_server` 构造注入）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（`call` 加 `collector`；`build_agent` 加 `extra_tools`；从 collector 经 `build_openai_tools` 构造注入）
- Test: `packages/core/tests/agents/test_providers_collector_injection.py`

**Interfaces:**
- Consumes: Task 2 `build_openai_tools` / `build_claude_mcp_server`；Task 1 `CollectorBase`
- Produces: `run_claude_prompt(..., collector: CollectorBase | None = None)`；`provider.call(..., collector=None)`；claude `_build_options(..., mcp_server=None, allowed_tools=None)`；openai `build_agent(..., extra_tools=None)`。Task 6（probe）与 Task 7（executor）消费 `collector` 参数。

> 当前全栈无 tools/mcp_servers 参数位（已核查：runner.py:165-173 provider.call 只传 7 kwargs；两引擎 call 签名一致无 tools 位）。本 task 从零打通 4 层。**设计选 engine-agnostic**：只传 `collector`（CollectorBase），provider 各自经 bridge 构造本引擎工具（claude→mcp_server、openai→extra_tools），executor/runner 不感知引擎。两引擎工具闭包捕获 collector，**openai 无需改 ToolContext**（工具在 call 内构造，collector 在作用域内）。

- [ ] **Step 1: 写失败测试**

`packages/core/tests/agents/test_providers_collector_injection.py`：
```python
"""验双引擎从 collector 注入工具的构造缝（不跑真模型）。"""
from shannon_core.collectors.pre_recon import PreReconCollector
from shannon_core.collectors.bridge import build_claude_mcp_server, build_openai_tools


def _collector():
    return PreReconCollector()


# ---------- claude: _build_options 注入 mcp_server + allowed_tools ----------

def test_anthropic_build_options_injects_mcp_server_and_allowed_tools():
    from shannon_core.agents.providers_anthropic import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)   # 不走 __init__（无需 API key）
    collector = _collector()
    mcp = build_claude_mcp_server(collector)
    allowed = collector.tool_names()
    options = provider._build_options(
        cwd="/tmp", model="claude-sonnet-5", mcp_server=mcp, allowed_tools=allowed,
    )
    assert "shannon-collector" in options.mcp_servers
    assert options.mcp_servers["shannon-collector"] is mcp
    assert "set_executive_summary" in options.allowed_tools
    assert len(options.allowed_tools) == 7


def test_anthropic_build_options_without_collector_leaves_mcp_empty():
    from shannon_core.agents.providers_anthropic import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    options = provider._build_options(cwd="/tmp", model="claude-sonnet-5")
    assert not options.mcp_servers
    assert not options.allowed_tools


# ---------- openai: build_agent 注入 extra_tools ----------

def test_openai_build_agent_includes_extra_tools(monkeypatch):
    from shannon_core.agents.providers_openai import OpenAIProvider

    provider = OpenAIProvider.__new__(OpenAIProvider)
    monkeypatch.setattr(provider, "_get_client", lambda: object())
    extra = build_openai_tools(_collector())
    agent = provider.build_agent("glm-4.6", None, extra_tools=extra)
    tool_names = [t.name for t in agent.tools]
    assert "set_executive_summary" in tool_names
    assert "set_ssrf_sinks" in tool_names
    assert "read_file" in tool_names or "bash" in tool_names   # 原有工具仍在


def test_openai_build_agent_without_extra_tools_keeps_builtin_only(monkeypatch):
    from shannon_core.agents.providers_openai import OpenAIProvider

    provider = OpenAIProvider.__new__(OpenAIProvider)
    monkeypatch.setattr(provider, "_get_client", lambda: object())
    agent = provider.build_agent("glm-4.6", None)
    assert not any(t.name.startswith("set_") for t in agent.tools)
```

> 若 `AnthropicProvider.__new__` / `OpenAIProvider.__new__` 构造方式与实际不符（如 `_build_options`/`build_agent` 用了实例属性），先读两 provider 文件对齐夹具（勿改生产代码迁就错误夹具）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/agents/test_providers_collector_injection.py -x`
Expected: FAIL — `_build_options() got an unexpected keyword argument 'mcp_server'` / `build_agent() got an unexpected keyword argument 'extra_tools'`

- [ ] **Step 3: 写实现（4 个文件）**

**(a) `providers.py`** — `BaseProvider.call` 抽象签名加 `collector`（providers.py:66 附近，`max_turns` 后）：
```python
        max_turns: int | None = None,
        collector: "CollectorBase | None" = None,
```
同步 docstring 提一句「collector: 可选的结构化工具收集器，注入 set_* 工具给 agent」。

**(b) `runner.py`** — `run_claude_prompt` 加 `collector`（runner.py:107-119 签名末尾）：
```python
    max_turns: int | None = None,
    collector: "CollectorBase | None" = None,
```
`provider.call(...)` 调用（runner.py:165-173）加 `collector=collector,`。

**(c) `providers_anthropic.py`**：
- `call` 签名（providers_anthropic.py:73-95）加 `collector: "CollectorBase | None" = None`。
- `call` 体内、调 `_build_options` 前，从 collector 构造 MCP：
```python
        mcp_server = None
        allowed_tools = None
        if collector is not None:
            from shannon_core.collectors.bridge import build_claude_mcp_server
            mcp_server = build_claude_mcp_server(collector)
            allowed_tools = collector.tool_names()
```
- `call` 内 `self._build_options(...)` 调用加 `mcp_server=mcp_server, allowed_tools=allowed_tools`。
- `_build_options` 签名（providers_anthropic.py:236-242）加两参，`return options` 前注入：
```python
    def _build_options(
        self,
        cwd: str,
        model: str,
        output_format: dict | None = None,
        max_turns_override: int | None = None,
        mcp_server=None,
        allowed_tools: list[str] | None = None,
    ) -> ClaudeAgentOptions:
        # ...（现有逻辑不动）...
        if mcp_server is not None:
            options.mcp_servers = {"shannon-collector": mcp_server}
        if allowed_tools:
            options.allowed_tools = list(allowed_tools)
        return options
```

**(d) `providers_openai.py`**：
- `call` 签名（providers_openai.py:188-197）加 `collector: "CollectorBase | None" = None`。
- `call` 体内构造 extra_tools，传给 build_agent：
```python
        extra_tools = None
        if collector is not None:
            from shannon_core.collectors.bridge import build_openai_tools
            extra_tools = build_openai_tools(collector)
```
- `call` 内 `self.build_agent(model, output_format)` 改为 `self.build_agent(model, output_format, extra_tools=extra_tools)`。
- `build_agent` 签名（providers_openai.py:103-106）加 `extra_tools: list | None = None`，`tools=build_tools()` 改为 `tools=build_tools() + (extra_tools or [])`：
```python
    def build_agent(self, model: str, output_format: dict | None, extra_tools: list | None = None) -> Agent:
        # ...（chat_model / output_type 不动）...
        return Agent(
            name="shannon-openai-agent",
            instructions=self._instructions(),
            tools=build_tools() + (extra_tools or []),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
            output_type=output_type,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/agents/test_providers_collector_injection.py -x`
Expected: PASS（4 passed）

- [ ] **Step 5: 回归既有 provider 测试**

Run: `uv run pytest packages/core/tests/agents/test_providers.py packages/core/tests/agents/test_dual_engine_alignment.py -x`
Expected: PASS（加可选参数不破坏既有签名/对齐测试）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py \
        packages/core/src/shannon_core/agents/providers.py \
        packages/core/src/shannon_core/agents/providers_anthropic.py \
        packages/core/src/shannon_core/agents/providers_openai.py \
        packages/core/tests/agents/test_providers_collector_injection.py
git commit -m "feat(agents): collector 参数贯通 runner+双引擎 provider(MCP server/extra tools 注入)"
```

---

## Task 6: GLM 真机探针（validate_glm_mcp_tool_probe.py）

**Files:**
- Create: `scripts/validate_glm_mcp_tool_probe.py`

**Interfaces:**
- Consumes: Task 5 的 `run_claude_prompt(collector=...)`；Task 3 `PreReconCollector`；Task 2 桥（经 provider 间接）；Task 4 renderer（打印渲染预览）
- Produces: 真机验证「glm-anthropic 能驱动 SDK MCP set_* 工具 + 传结构化参数 + write-once 多次调用」的 scripts 级探针（非 pytest）。**这是「铺开 pre-recon 前的最大未知」门禁。**

> 对标 `scripts/validate_glm_task_probe.py`。PASS 判据：GLM 发起 ≥1 次 set_* 工具调用（audit logger 录到含 `set_` 的工具名），且 collector 收到结构化 payload。**probe 不 PASS 则停下讨论**（spec §4.4、§8 风险表）——不要继续 Task 7-8。

- [ ] **Step 1: 写探针脚本**

`scripts/validate_glm_mcp_tool_probe.py`：
```python
#!/usr/bin/env python3
"""Minimal GLM-MCP-tool validation — host 渲染架构的决定性 checkpoint。

问题：glm-anthropic（GLM 经 BigModel anthropic 端点 + Claude Code CLI）下，GLM 能否
      驱动 in-process SDK MCP 工具（set_*），传符合 schema 的结构化参数，多次调用（write-once）？

PASS：GLM 发起 ≥1 次 set_* MCP 工具调用，collector.get_all() 非空。
FAIL：GLM 从不调 set_*（无视工具 / 卡住）→ host 渲染架构在 claude 轨受阻，需讨论。

对标 validate_glm_task_probe.py。scripts 级真机验证（非 pytest）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

_PROFILE_CANDIDATES = [
    Path("/root/shannon-py/.env.profiles/glm-anthropic.env"),
    Path(__file__).resolve().parent.parent / ".env.profiles" / "glm-anthropic.env",
]
PROFILE = next((p for p in _PROFILE_CANDIDATES if p.exists()), _PROFILE_CANDIDATES[-1])


def load_profile() -> None:
    if PROFILE.exists():
        for line in PROFILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    os.environ["SHANNON_AI_PROVIDER"] = "anthropic_api"   # claude 轨才走 SDK MCP
    os.environ["CLAUDE_MAX_TURNS"] = "20"


from shannon_core.agents.tool_audit_logger import NullToolAuditLogger


class RecordingLogger(NullToolAuditLogger):
    def __init__(self) -> None:
        self.tools: list[str] = []

    async def log_tool_start(self, tool_name: str, parameters) -> None:
        self.tools.append(tool_name)


async def main() -> None:
    load_profile()
    from shannon_core.collectors.pre_recon import PreReconCollector
    from shannon_core.agents.runner import run_claude_prompt
    from shannon_core.renderers.pre_recon import render_pre_recon

    target = Path(tempfile.mkdtemp(prefix="glm_mcp_probe_"))
    (target / "app.py").write_text(
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/search')\n"
        "def search():\n"
        "    q = request.args.get('q', '')\n"
        "    import sqlite3\n"
        "    cur = sqlite3.connect('db').cursor()\n"
        "    cur.execute(\"SELECT * FROM items WHERE name='%s'\" % q)\n"
        "    return str(cur.fetchall())\n"
    )

    collector = PreReconCollector()
    prompt = (
        "You are a pre-recon agent. Analyze the Flask app in cwd for security posture.\n\n"
        "<deliverable_tools>\n"
        "Emit your findings exclusively via the deliverable tools. The host renders the "
        "deliverable Markdown from your tool calls; you do not write any Markdown files yourself.\n"
        "You must call all seven of the following tools exactly once before terminating:\n"
        "- set_executive_summary\n- set_application_intelligence\n- set_auth_deep_dive\n"
        "- set_codebase_indexing\n- set_critical_file_paths\n- set_xss_sinks\n- set_ssrf_sinks\n"
        "Each tool's full schema is in your tool catalog — read it there.\n"
        "</deliverable_tools>\n\n"
        "Task: call set_executive_summary with a 2-3 paragraph overview, then proceed through the "
        "remaining tools. The SQL injection in /search is relevant to your attack surface analysis."
    )

    logger = RecordingLogger()
    t0 = time.time()
    print(f"[probe] target={target}  provider=anthropic_api (glm-anthropic)")
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="large",
                tool_audit_logger=logger,
                collector=collector,
            ),
            timeout=360,
        )
    except asyncio.TimeoutError:
        print("RESULT: TIMEOUT (>360s)")
        return

    dt = time.time() - t0
    set_calls = [t for t in logger.tools if "set_" in t]
    print("=" * 64)
    print(f"duration={dt:.1f}s  turns={getattr(result, 'turns', None)}  success={result.success}")
    if result.error:
        print(f"ERROR: {result.error}")
    print(f"TOOLS CALLED ({len(logger.tools)}): {logger.tools}")
    print(f"COLLECTED SECTIONS: {list(collector.get_all().keys())}")
    print(f"CALL STATUS: {collector.get_call_status()}")
    rendered = render_pre_recon(collector.get_all())
    print("\n--- RENDERED MD (first 800 chars) ---")
    print(rendered[:800])
    passed = len(set_calls) > 0 and len(collector.get_all()) > 0
    print(f"\n>>> MCP set_* TOOLS USED: {'YES ✅' if set_calls else 'NO ❌'}")
    print(f">>> RESULT: {'PASS ✅' if passed else 'FAIL ❌'}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 静态检查**

Run: `uv run python -c "import ast; ast.parse(open('scripts/validate_glm_mcp_tool_probe.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: 真机运行（需 glm-anthropic profile + 网络）**

Run: `uv run python scripts/validate_glm_mcp_tool_probe.py`
Expected: 终端打印 `RESULT: PASS ✅`，`MCP set_* TOOLS USED: YES`，`COLLECTED SECTIONS` 非空，RENDERED MD 含 `# Penetration Test Scope & Boundaries`。

> **门禁**：若 `FAIL ❌`（GLM 不调 set_*）→ **停下，不要继续 Task 7-8**。回 spec §8 风险表讨论（可能需调 prompt 措辞 / 工具描述 / 或评估 MCP 在 GLM 下的可靠性）。这是「铺开 pre-recon 前」的硬门禁。
>
> 若本机无 glm-anthropic profile（CI Linux 路径），在有 profile 的环境跑；probe 结果记录到 commit message。

- [ ] **Step 4: Commit**

```bash
git add scripts/validate_glm_mcp_tool_probe.py
git commit -m "test(probe): validate_glm_mcp_tool_probe — GLM 驱动 SDK MCP set_* 工具真机门禁"
```

---

## Task 7: executor 接 collector → 渲染写 md（validate 前）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`（`execute` 内：建 collector → 传 run_claude_prompt → run 后 renderer 写 md）
- Test: `packages/core/tests/test_executor_collector_render.py`

**Interfaces:**
- Consumes: Task 1 `make_collector`（`collectors.__init__`）；Task 4 `render_deliverable`（`renderers.__init__`）；Task 5 `run_claude_prompt(collector=...)`
- Produces: executor 对有 collector 的 agent（Plan 1 = pre-recon）在 validate 前 host 渲染 md。对齐 TS agent-execution.ts:295-297 `writeDeliverable`（注：TS 是 validate 后写 + pre-recon validator no-op；PY 选 validate 前写，host 必渲染故 validate 见文件即过，无需把 pre-recon validator 改 no-op）。

- [ ] **Step 1: 写失败测试**

`packages/core/tests/test_executor_collector_render.py`：
```python
"""executor host 渲染：mock run_claude_prompt 模拟 agent 调 set_*，验 md 落盘 + 内容。"""
import asyncio

import pytest

from shannon_core.models.agents import AgentName


@pytest.mark.asyncio
async def test_pre_recon_executor_renders_md_from_collector(monkeypatch, tmp_path):
    from shannon_core.agents import executor as exec_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"

    captured: dict = {}

    class FakeResult:
        success = True
        turns = 3
        cost = 0.0
        cost_currency = "USD"
        text = ""
        model = "glm-5.2"
        structured_output = None
        stop_reason = "end_turn"
        error = None
        retryable = False
        error_code = None

        class _T:
            input_tokens = 10
            output_tokens = 5
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        tokens = _T()

    async def fake_run(**kwargs):
        collector = kwargs.get("collector")
        captured["collector_passed"] = collector is not None
        if collector is not None:
            collector.set_section("set_executive_summary", {"text": "OVERVIEW."})
            collector.set_section("set_xss_sinks", {"applicable": False})
        return FakeResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)

    from shannon_core.git_manager import GitManager

    async def noop(*a, **kw):
        return None

    monkeypatch.setattr(GitManager, "ensure_repository", staticmethod(lambda p: asyncio.sleep(0)))
    monkeypatch.setattr(GitManager, "create_checkpoint", noop)
    monkeypatch.setattr(GitManager, "commit", noop)

    class StubPM:
        def load_sync(self, *a, **kw):
            return "stub prompt"

    ex = exec_mod.AgentExecutor(prompt_manager=StubPM())
    await ex.execute(
        agent_name=AgentName.PRE_RECON,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
    )

    assert captured["collector_passed"] is True
    md_file = deliverables / "pre_recon_deliverable.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert content.startswith("# Penetration Test Scope & Boundaries")
    assert "## 1. Executive Summary" in content
    assert "OVERVIEW." in content
    assert "N/A — the application has no web frontend" in content
    assert "set_auth_deep_dive` was not called" in content   # skipped → placeholder
```

> 若 `GitManager.ensure_repository` patch 方式不对（它是 async staticmethod 还是普通 async？读 `git_manager.py` 对齐后再改夹具，勿改生产代码）。executor 调 `await GitManager.ensure_repository(deliverables)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/test_executor_collector_render.py -x`
Expected: FAIL — collector 未传 / md 未落盘（`md_file.exists()` False）

- [ ] **Step 3: 改 executor.execute**

在 `executor.py`：
- 顶部 import 加：
```python
from shannon_core.collectors import make_collector
from shannon_core.renderers import render_deliverable
```
- `execute` 方法体内，`await GitManager.create_checkpoint(deliverables, agent_name)` 之后、`result = await run_claude_prompt(...)` 之前，建 collector：
```python
        collector = make_collector(agent_name)
```
- 把 `collector=collector` 加进 `run_claude_prompt(...)` 调用（executor.py:108-118 的 kwargs）。
- 在 queue 写盘块（executor.py:147-154）之后、`validate_deliverable`（executor.py:156-167）之前，插入 host 渲染写盘（受 `not skip_artifact_postprocess` 保护）：
```python
        if not skip_artifact_postprocess and collector is not None:
            md = render_deliverable(agent_name, collector.get_all())
            if md is not None:
                (deliverables / defn.deliverable_filename).write_text(md, encoding="utf-8")
```

> 这样：agent 跑完 →（queue 写盘）→ host 渲染写 md → validate（见文件即过）→ commit。`_enrich_missing_deliverable_error` 保持不动（pre-recon 不再触发；其它 agent 仍可能触发，诊断仍有价值）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/test_executor_collector_render.py -x`
Expected: PASS（1 passed）

- [ ] **Step 5: 回归既有 executor 测试**

Run: `uv run pytest packages/core/tests/test_executor_artifact_postprocess.py packages/core/tests/test_executor_missing_deliverable_diagnostics.py packages/core/tests/test_executor_template.py -x`
Expected: PASS（collector 改动不破坏既有 artifact postprocess / 诊断 / 模板流程）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor_collector_render.py
git commit -m "feat(executor): pre-recon host 渲染——collector→renderer→写 md(validate 前)"
```

---

## Task 8: pre-recon prompt 改造（删 Write、加 deliverable_tools）

**Files:**
- Modify: `prompts/pre-recon-code.txt`
- Modify: `prompts/pipeline-testing/pre-recon-code.txt`（若与主文件同构则同步改；先 diff 确认）

**Interfaces:**
- Consumes: 无（纯 prompt 文本改）
- Produces: pre-recon prompt 不再要求 agent 自己 Write md，改为「MUST emit via set_* tools，host renders」——对齐 TS prompt（`upstream/main:apps/worker/prompts/pre-recon-code.txt:24/138/181-194`）。

> 改 3 处（行号基于当前 `prompts/pre-recon-code.txt`）：
> 1. **line 26**（`<critical>` 里）：删「MUST save ... using the Write tool」，换 TS 式 MANDATORY emit-tools。
> 2. **line 138-143**（Phase 3 CHUNKED WRITING）：删整块（含 line 143 的 WARNING），换「Emit findings via tools」。
> 3. **新增 `<deliverable_tools>` 块**：插在 Phase 3 段落之后、`</task_agent_strategy>`（约 line 167）之前，用 TS 原文。

- [ ] **Step 1: 先看 pipeline-testing 变体是否同构**

Run: `diff prompts/pre-recon-code.txt prompts/pipeline-testing/pre-recon-code.txt | head -60`

确认 pipeline-testing 变体是否含 line 26 / 138-143 同款 Write 指令。若含，下方改动两文件都做；若变体结构差异大，仅改主文件并在 commit message 注明变体未改的原因。

- [ ] **Step 2: 改 line 26（critical 块）**

把（当前 line 26 整行）：
```
- **MANDATORY:** You MUST save your complete analysis report to `{{DELIVERABLES_PATH}}/pre_recon_deliverable.md` using the Write tool.
```
替换为（对齐 TS line 24）：
```
- **MANDATORY:** You MUST emit your complete analysis by calling all seven `set_*` tools listed in `<deliverable_tools>` before terminating. The host renders the deliverable Markdown from those calls — there is no Markdown for you to write yourself.
```

- [ ] **Step 3: 删 line 138-143（CHUNKED WRITING 整块）**

删除以下整段（含其后的 WARNING 行，即当前 line 138-143）：
```
- **CHUNKED WRITING (MANDATORY):**
  1. Use the **Write** tool to create `{{DELIVERABLES_PATH}}/pre_recon_deliverable.md` with the title and first major section
  2. Use the **Edit** tool to append each remaining section — match the last few lines of the file, then replace with those lines plus the new section content
  3. Repeat step 2 for all remaining sections
  4. Confirm `{{DELIVERABLES_PATH}}/pre_recon_deliverable.md` is complete with all sections
- **WARNING:** Do NOT write the entire report in a single tool call — exceeds 32K output token limit. Split into multiple Write/Edit operations.
```
原位替换为（对齐 TS line 138）：
```
- **Emit findings via tools:** Call every tool listed in `<deliverable_tools>` exactly once. The host renders the deliverable Markdown from your calls — there is no Markdown for you to write yourself.
```

- [ ] **Step 4: 插入 `<deliverable_tools>` 块**

在 `</task_agent_strategy>`（约 line 167）之前插入（TS 原文 verbatim，`upstream/main:apps/worker/prompts/pre-recon-code.txt:180-194`）：
```
<deliverable_tools>
**Emit your findings exclusively via the deliverable tools.** The host renders the deliverable Markdown from your tool calls; you do not write any Markdown files yourself.

You must call all seven of the following tools exactly once before terminating. Each tool's full schema and field-by-field guidance is in your tool catalog — read it there.

- `set_executive_summary` — application's overall security posture (Section 1).
- `set_application_intelligence` — composite of architecture, data security, attack surface, and infrastructure (Sections 2, 4, 5, 6).
- `set_auth_deep_dive` — authentication & authorization deep dive (Section 3).
- `set_codebase_indexing` — directory structure narrative (Section 7).
- `set_critical_file_paths` — categorized catalog of critical file paths (Section 8).
- `set_xss_sinks` — XSS sinks grouped by render context (Section 9). Set `applicable: false` only if the application has no web frontend at all.
- `set_ssrf_sinks` — SSRF sinks grouped by sink category (Section 10). Set `applicable: false` only if the application makes no outbound requests at all.

Each `set_*` tool is one-shot. Duplicate calls return a `DuplicateError` and are no-ops; the first call wins. Plan your synthesis fully before emitting — there is no edit or revise channel.
</deliverable_tools>
```

> 保留 prompt 里 section heading 指引（`Please structure your report using the exact following Markdown headings` 段及之后的 `## 1.`...`## 10.` 描述）——这些是「填工具字段的指引」，host renderer 产出的 md 用相同 heading，二者一致。`{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}`/`{{REPO_PATH}}` 变量保留（renderer 不依赖 prompt 变量，但 prompt 别处仍引用这些路径作上下文）。

- [ ] **Step 5: pipeline-testing 变体同步（若 Step 1 确认同构）**

对 `prompts/pipeline-testing/pre-recon-code.txt` 做相同 3 处改动。

- [ ] **Step 6: 回归 prompt 相关测试**

Run: `uv run pytest packages/core/tests/test_prompt_manager.py packages/core/tests/prompts/test_deliverables_path_interpolation.py -x`
Expected: PASS（prompt 仍能加载、`{{DELIVERABLES_PATH}}` 插值仍工作）

- [ ] **Step 7: Commit**

```bash
git add prompts/pre-recon-code.txt prompts/pipeline-testing/pre-recon-code.txt
git commit -m "feat(prompts): pre-recon 改 host 渲染——删 Write 指令、加 deliverable_tools 块(对齐 TS)"
```

---

## Task 9: 端到端验证 + 收尾笔记

**Files:**
- 无新增；仅运行验证 + 更新 memory。

**Interfaces:**
- 消费 Task 1-8 全部产物。

- [ ] **Step 1: 跑本计划全部新增/改动测试（相关子集）**

Run:
```bash
uv run pytest \
  packages/core/tests/collectors/ \
  packages/core/tests/renderers/ \
  packages/core/tests/agents/test_providers_collector_injection.py \
  packages/core/tests/test_executor_collector_render.py \
  -x -q
```
Expected: 全 PASS

- [ ] **Step 2: 回归 collectors/renderers 无副作用的核心测试**

Run:
```bash
uv run pytest \
  packages/core/tests/test_validators.py \
  packages/core/tests/test_agents.py \
  packages/core/tests/agents/test_dual_engine_alignment.py \
  -x -q
```
Expected: PASS

- [ ] **Step 3: 真机端到端（在有 glm-anthropic 的环境）**

重跑 Task 6 探针确认仍 PASS：
Run: `uv run python scripts/validate_glm_mcp_tool_probe.py`
Expected: `RESULT: PASS ✅`，渲染 md 含全部 section（或 skipped placeholder），不再有「Missing deliverable」。

> 若条件允许，跑一次真实 pre-recon（`SHANNON_AI_PROVIDER=anthropic_api` 对小靶场如 NodeGoat 子集），确认 `pre_recon_deliverable.md` 由 host 渲染落盘、扫描不再卡 Missing deliverable。这是治本目标的最终验收。

- [ ] **Step 4: 更新 memory**

更新 `pre-recon-md-deliverable-glm-forget-write.md`：标注「治本 Plan 1 已实现（collector 框架 + 双引擎桥 + pre-recon 端到端），pre-recon host 渲染已通；recon/vuln/exploit/report 留 Plan 2-5；诊断 `_enrich_missing_deliverable_error` 保留至 Plan 5」。在 MEMORY.md 该行追加状态。

- [ ] **Step 5: 收尾**

memory 在 repo 外（`~/.claude/.../memory/`），单独记录；repo 内 plan 文件本身已随各 task 提交。

---

## Self-Review（plan 作者自查记录）

**1. Spec coverage**（对照 spec §1-§9）：
- §2 治本目标（恢复 host 渲染）：Task 1-8 ✅
- §3.1 TS collector/renderer/writeDeliverable：Task 1/3/4/7 ✅
- §3.2 7 set_* schema：Task 3 ✅（字段全对齐 TS：XSS applicable+5 数组、SSRF applicable+13 数组、attack_surface 4 字段、critical_file_paths 9 类、infrastructure 4 字段——修正了旧草稿的缺漏）
- §3.3 双引擎工具注入：Task 2/5 ✅（已核查 SDK 源码坐实 `FunctionTool.params_json_schema`+`strict_json_schema=False` + `SdkMcpTool.input_schema` 经 `_build_schema` 原样透传）
- §3.4 queue.json 不动：Global Constraints + executor 改动仅加 md 通道 ✅
- §4.1 声明式 collector 框架：Task 1-3 ✅（base/bridge/pre_recon）
- §4.2 数据流（render→validate，host 必渲染）：Task 7 ✅
- §4.3 prompt 改造：Task 8 ✅
- §4.4 GLM probe：Task 6 ✅（门禁）
- §4.5 诊断去留：Global Constraints 明确**不在 Plan 1 移除**（归 Plan 5）✅
- §5 不变量：Global Constraints 四条 ✅
- §7 测试策略：renderer/collector/桥/executor 单测 + GLM probe 全覆盖 ✅

**2. Placeholder 扫描**：无 TBD/TODO；所有代码步骤含完整可运行代码（含完整 SCOPE_AND_BOUNDARIES 原文、完整 7 schema、完整 renderer）。

**3. Type 一致性**：`SectionSchema`（tool_name/section_key/description/json_schema）跨 Task 1/2/3 一致；`CollectorBase.set_section/get_all/get_call_status/tool_names/section_schemas` 跨 Task 1/2/5/7 一致；`make_collector`/`render_deliverable` 分发签名跨 Task 1/4/7 一致；7 个 section_key 跨 Task 3/4 一致；`build_openai_tools`/`build_claude_mcp_server` 跨 Task 2/5 一致；`run_claude_prompt(collector=)`/`provider.call(collector=)`/`_build_options(mcp_server=,allowed_tools=)`/`build_agent(extra_tools=)` 跨 Task 5/6/7 一致。

**4. 已知执行期风险（task 内 TDD 会暴露）**：
- `GitManager.ensure_repository` 在 Task 7 测试里的 patch 方式（async staticmethod?）→ 夹具对齐真实签名。
- `AnthropicProvider.__new__` / `OpenAIProvider.__new__` 构造缝在 Task 5 测试里是否够用 → 若用了实例属性，改为读两 provider 文件对齐夹具。
- GLM 真机驱动 MCP 工具的可靠性（Task 6 门禁）→ 不 PASS 则停，回 spec §8 讨论。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-host-rendered-deliverables-plan-1.md`（旧草稿备份在同目录 `.draft-v1.bak.md`）。两个执行选项：

1. **Subagent-Driven（推荐）** — 每 task 派新 subagent，task 间 review，迭代快。
2. **Inline Execution** — 本会话内用 executing-plans 批量执行 + checkpoint review。

选哪个？
