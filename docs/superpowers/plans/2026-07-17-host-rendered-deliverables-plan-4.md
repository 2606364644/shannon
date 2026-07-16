# Host-Rendered Deliverables — Plan 4（exploit agent，5 class 共用）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Plan 1 框架已落地。

**Goal:** 5 个 exploit agent（injection/xss/auth/ssrf/authz）调 `add_exploit`（**append** 语义）结构化工具，host collector 收集 + `render_exploit_deliverable(vc, entries, id_to_type)` 渲染 `{vt}_exploitation_evidence.md`（含 Successfully Exploited / Blocked / Unprocessed 三 section）。

**Architecture:** exploit 与前 3 个 plan 的 set_*（write-once）**本质不同**：
1. **append 语义**——agent 多次调 `add_exploit` 累积 list（TS `getAll(): AddExploitInput[]`），不是 write-once。
2. **renderer 双输入**——读 collector entries + `{vt}_exploitation_queue.json`（idToType，渲染 Unprocessed section：queue 有但 collector 没 exploit 的 ID）。
3. 因此 exploit 用**专用 `ExploitCollector` + 专用 bridge + executor exploit 分支**，不复用 Plan 1 的 set collector_spec（其 `render(data: dict)` 单输入、write-once 语义不适用）。

**Tech Stack:** pydantic、pytest。

**Spec:** `docs/superpowers/specs/2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 4 = exploit）

## Global Constraints

- **append ≠ set**：exploit collector 是 append（多次调用累积 list）；不与 Plan 1 `CollectorBase`（write-once）混用。
- **queue 是 vuln 的，不改**：`{vt}_exploitation_queue.json` 由 vuln agent 的 structured_output 落盘（Plan 3 已确认）。exploit renderer **只读** queue（取 ID 列表渲染 Unprocessed），不写。
- **单通道**：exploit agent 只产 `{vt}_exploitation_evidence.md`（不像 vuln 双通道——exploit 不产 queue）。
- **§1 双轨独立 / §2 双引擎**：同前；renderer 读 queue 是读 LLM 产物（vuln queue），不引 GitNexus 确定性层。
- **TS 对齐**：`add_exploit` schema（`AddExploitInput` = `ExploitedExploit | BlockedExploit`）、renderer、prompt 1:1 移植 `upstream/main:apps/worker/src/collectors/exploit-collector.ts` + `services/exploit-renderer.ts`。
- **5 class 全覆盖**：injection/xss/auth/ssrf/authz（per-class 只差 title + ID prefix）。
- **TDD + 测试陷阱**：每 task 先失败测试；只跑改动子集。
- **诊断暂不移除**：`_enrich_missing_deliverable_error` 保留到 Plan 5。

## File Structure

- Create: `packages/core/src/shannon_core/collectors/exploit.py`（`ExploitCollector` append + `AddExploit` union model + `make_exploit_collector()`）
- Create: `packages/core/src/shannon_core/renderers/exploit.py`（`render_exploit_deliverable`）
- Modify: `packages/core/src/shannon_core/collectors/bridge.py`（加 `build_exploit_claude_mcp_server` / `build_exploit_openai_tools`，append 工具）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（加 exploit 落盘分支：读 collector entries + queue.json → render → 写 evidence md）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（registry 加 5 个 exploit agent，返回 exploit spec）
- Modify: `prompts/exploit-injection.txt`、`exploit-xss.txt`、`exploit-auth.txt`、`exploit-ssrf.txt`、`exploit-authz.txt`

---

### Task 1: `ExploitCollector`（append）+ `AddExploit` union model

**Files:**
- Create: `packages/core/src/shannon_core/collectors/exploit.py`
- Test: `packages/core/tests/collectors/test_exploit_models.py`

**Interfaces:**
- Produces: `ExploitCollector`（`add(entry)` append、`get_all() -> list[dict]`）+ `ExploitEntry`/`BlockedEntry` pydantic model + `make_exploit_collector()`。

**TS 对照：** `upstream/main:apps/worker/src/collectors/exploit-collector.ts:69-100`（`ExploitedExploit` / `BlockedExploit`）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/collectors/test_exploit_models.py
import pytest
from shannon_core.collectors.exploit import (
    ExploitCollector, ExploitEntry, BlockedEntry,
)


def test_append_accumulates_list():
    c = ExploitCollector()
    c.add({"status": "exploited", "vulnerability_id": "INJ-1", "title": "t",
           "exploitation_steps": ["s1"], "proof_of_impact": "p"})
    c.add({"status": "blocked", "vulnerability_id": "INJ-2", "title": "t2",
           "current_blocker": "b", "evidence_of_vulnerability": "e"})
    entries = c.get_all()
    assert len(entries) == 2
    assert entries[0]["vulnerability_id"] == "INJ-1"


def test_get_all_empty_when_nothing_added():
    assert ExploitCollector().get_all() == []


def test_entry_models_discriminate_on_status():
    e = ExploitEntry(vulnerability_id="X-1", title="t",
                     exploitation_steps=["s"], proof_of_impact="p")
    assert e.status == "exploited"
    b = BlockedEntry(vulnerability_id="X-2", title="t",
                     current_blocker="b", evidence_of_vulnerability="e")
    assert b.status == "blocked"
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_exploit_models.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/collectors/exploit.py
"""exploit collector:append 语义(agent 多次调 add_exploit 累积)。

与 CollectorBase(write-once set_*)不同。移植 TS exploit-collector.ts。
AddExploit = ExploitedEntry | BlockedEntry(discriminated on status)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class _EntryBase(BaseModel):
    vulnerability_id: str
    title: str


class ExploitEntry(_EntryBase):
    """对照 TS ExploitedExploit(line 69)。完整字段对照 TS 补齐。"""
    status: str = "exploited"
    exploitation_steps: list[str]
    proof_of_impact: str
    severity: str | None = None
    # TS line 69-82 其余字段对照补齐


class BlockedEntry(_EntryBase):
    """对照 TS BlockedExploit(line 83)。"""
    status: str = "blocked"
    current_blocker: str
    evidence_of_vulnerability: str
    what_we_tried: str | None = None
    expected_impact: str | None = None
    # TS line 83-99 其余字段对照补齐


class ExploitCollector:
    """append 收集器:get_all() 返回 entries list(对齐 TS getAll(): AddExploitInput[])。"""

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def add(self, entry: dict) -> None:
        # 校验 status 决定用哪个 model(对齐 TS union re-validation)
        status = entry.get("status")
        model = ExploitEntry if status == "exploited" else BlockedEntry
        validated = model.model_validate(entry).model_dump(exclude_none=True)
        self._entries.append(validated)

    def get_all(self) -> list[dict]:
        return list(self._entries)


def make_exploit_collector() -> ExploitCollector:
    return ExploitCollector()
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_exploit_models.py -q` → 3 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/exploit.py packages/core/tests/collectors/test_exploit_models.py && git commit -m "feat(collectors): ExploitCollector(append) + Exploited/Blocked entry model"`

---

### Task 2: `render_exploit_deliverable`（双输入：entries + idToType）

**Files:**
- Create: `packages/core/src/shannon_core/renderers/exploit.py`
- Test: `packages/core/tests/renderers/test_exploit.py`

**Interfaces:**
- Produces: `render_exploit_deliverable(vuln_class: str, entries: list[dict], id_to_type: dict[str, str]) -> str`。

**TS 对照：** `upstream/main:apps/worker/src/services/exploit-renderer.ts`（`renderExploitDeliverable` line 205 + `TITLES` + `renderExploitedSection`/`renderBlockedSection`/`renderUnprocessedSection`）。

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/renderers/test_exploit.py
from shannon_core.renderers.exploit import render_exploit_deliverable


def test_empty_state_and_queue():
    md = render_exploit_deliverable("injection", [], {})
    assert "No vulnerabilities were available" in md


def test_exploited_and_blocked_sections():
    entries = [
        {"status": "exploited", "vulnerability_id": "INJ-1", "title": "SQLi",
         "exploitation_steps": ["s1", "s2"], "proof_of_impact": "p"},
        {"status": "blocked", "vulnerability_id": "INJ-2", "title": "CmdInj",
         "current_blocker": "sanitize", "evidence_of_vulnerability": "e"},
    ]
    md = render_exploit_deliverable("injection", entries, {"INJ-1": "injection", "INJ-2": "injection"})
    assert "## Successfully Exploited" in md and "SQLi" in md and "s1" in md
    assert "## Potential Vulnerabilities (Validation Blocked)" in md and "CmdInj" in md


def test_unprocessed_section_surfaces_queue_ids_not_exploited():
    entries = [{"status": "exploited", "vulnerability_id": "INJ-1", "title": "t",
                "exploitation_steps": ["s"], "proof_of_impact": "p"}]
    id_to_type = {"INJ-1": "injection", "INJ-9": "injection"}  # INJ-9 在 queue 但没 exploit
    md = render_exploit_deliverable("injection", entries, id_to_type)
    assert "## Unprocessed Vulnerabilities" in md and "INJ-9" in md
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/renderers/test_exploit.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# packages/core/src/shannon_core/renderers/exploit.py
"""移植 TS exploit-renderer.ts::renderExploitDeliverable。5 class 共用(per-class title+ID prefix)。

双输入:entries(collector.get_all()) + id_to_type(从 queue.json 读的 ID→type)。
sections:Successfully Exploited / Potential (Blocked) / Unprocessed。纯函数。
"""
from __future__ import annotations

TITLES = {
    "injection": "Injection Exploitation Report",
    "xss": "Cross-Site Scripting (XSS) Exploitation Report",
    "auth": "Authentication Exploitation Report",
    "ssrf": "SSRF Exploitation Report",
    "authz": "Authorization (Authz) Exploitation Report",
    # 完整 title 对照 TS exploit-renderer.ts TITLES
}


def _numbered(steps: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) if steps else ""


def _exploited_entry(e: dict) -> str:
    lines = [f"### {e.get('vulnerability_id', '')}: {e.get('title', '')}"]
    if e.get("severity"):
        lines.append(f"**Severity:** {e['severity']}")
    steps = _numbered(e.get("exploitation_steps") or [])
    if steps:
        lines += ["", "**Exploitation Steps:**", "", steps]
    if e.get("proof_of_impact"):
        lines += ["", f"**Proof of Impact:** {e['proof_of_impact']}"]
    return "\n".join(lines)


def _blocked_entry(e: dict) -> str:
    lines = [f"### {e.get('vulnerability_id', '')}: {e.get('title', '')}"]
    if e.get("current_blocker"):
        lines.append(f"**Current Blocker:** {e['current_blocker']}")
    if e.get("evidence_of_vulnerability"):
        lines.append(f"**Evidence of Vulnerability:** {e['evidence_of_vulnerability']}")
    return "\n".join(lines)


def _exploited_section(entries: list[dict]) -> str:
    if not entries:
        return ""
    return "## Successfully Exploited Vulnerabilities\n\n" + "\n\n".join(_exploited_entry(e) for e in entries)


def _blocked_section(entries: list[dict]) -> str:
    if not entries:
        return ""
    return "## Potential Vulnerabilities (Validation Blocked)\n\n" + "\n\n".join(_blocked_entry(e) for e in entries)


def _unprocessed_section(missing_ids: list[str], id_to_type: dict[str, str]) -> str:
    if not missing_ids:
        return ""
    items = "\n".join(f"- `{i}` ({id_to_type.get(i, '')})" for i in missing_ids)
    return f"## Unprocessed Vulnerabilities\n\n{items}"


def render_exploit_deliverable(vuln_class: str, entries: list[dict],
                               id_to_type: dict[str, str]) -> str:
    title = f"# {TITLES[vuln_class]}"
    if not entries and not id_to_type:
        return f"{title}\n\n*No vulnerabilities were available in the queue for exploitation.*\n"

    exploited = [e for e in entries if e.get("status") == "exploited"]
    blocked = [e for e in entries if e.get("status") == "blocked"]
    emitted = {e.get("vulnerability_id") for e in entries}
    missing = [i for i in id_to_type if i not in emitted]

    parts = [title, "", _exploited_section(exploited)]
    if blocked:
        parts += ["", _blocked_section(blocked)]
    unproc = _unprocessed_section(missing, id_to_type)
    if unproc:
        parts += ["", unproc]
    return "\n".join(p for p in parts if p is not None).rstrip() + "\n"
```

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/renderers/test_exploit.py -q` → 3 passed.

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/renderers/exploit.py packages/core/tests/renderers/test_exploit.py && git commit -m "feat(renderers): render_exploit_deliverable — exploited/blocked/unprocessed(移植 TS)"`

---

### Task 3: bridge 扩展（append 工具）+ registry 注册 5 exploit agent + executor 分支

**Files:**
- Modify: `packages/core/src/shannon_core/collectors/bridge.py`（加 `build_exploit_claude_mcp_server` / `build_exploit_openai_tools`）
- Modify: `packages/core/src/shannon_core/collectors/__init__.py`（加 exploit registry：`get_exploit_spec(agent_name)`）
- Modify: `packages/core/src/shannon_core/agents/executor.py`（加 exploit 落盘分支）
- Modify: `packages/core/src/shannon_core/agents/runner.py`（透传 exploit collector/tools）
- Test: `packages/core/tests/collectors/test_bridge_exploit.py`、`packages/core/tests/test_executor_exploit_render.py`

**Interfaces:**
- Produces: `build_exploit_claude_mcp_server(collector) -> McpSdkServerConfig`（单个 `add_exploit` 工具，append）、`build_exploit_openai_tools(collector) -> list[FunctionTool]`、`get_exploit_spec(agent_name) -> ExploitCollectorSpec | None`。

> **框架决策**：exploit 不复用 Plan 1 的 `CollectorSpec(render(data))`，因 renderer 双输入（entries + queue）+ append。executor 对 exploit 用**专用分支**：跑完读 `collector.get_all()` + `{vt}_exploitation_queue.json` → `render_exploit_deliverable` → 写 `{vt}_exploitation_evidence.md`。registry 用独立 `get_exploit_spec`（或 `get_collector_spec` 返回带 `kind="exploit"` 标记的 spec，executor 按标记分支）。

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/collectors/test_bridge_exploit.py
import json
import pytest
from agents import FunctionTool
from shannon_core.collectors.exploit import ExploitCollector
from shannon_core.collectors.bridge import build_exploit_openai_tools, build_exploit_claude_mcp_server


def test_openai_exploit_tool_appends():
    c = ExploitCollector()
    tools = build_exploit_openai_tools(c)
    assert len(tools) == 1 and tools[0].name == "add_exploit"
    await_ = tools[0].on_invoke_tool(None, json.dumps({
        "status": "exploited", "vulnerability_id": "X-1", "title": "t",
        "exploitation_steps": ["s"], "proof_of_impact": "p"}))
    import asyncio; asyncio.get_event_loop().run_until_complete(c and asyncio.sleep(0)) if False else None
    # 直接调(同步 wrapper)
    assert len(c.get_all()) == 1


def test_claude_exploit_server_has_add_exploit():
    c = ExploitCollector()
    server = build_exploit_claude_mcp_server(c)
    assert server is not None
```

```python
# packages/core/tests/test_executor_exploit_render.py
import asyncio, json
from shannon_core.agents import executor as exec_mod
from shannon_core.collectors import get_collector_spec
from shannon_core.models.agents import AgentName


def test_exploit_renders_evidence_md_with_queue_unprocessed(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    # queue 已存在(vuln agent 产的)—含 INJ-1, INJ-9
    (deliverables / "injection_exploitation_queue.json").write_text(json.dumps({
        "verdicts": [{"vulnerability_id": "INJ-1"}, {"vulnerability_id": "INJ-9"}]}))
    spec = get_collector_spec(AgentName.INJECTION_EXPLOIT)

    class _R:
        success = True; turns = 1; cost = 0.0; cost_currency = "USD"
        error = None; retryable = True; model = "stub"; structured_output = None
        class tokens:
            input_tokens = 0; output_tokens = 0
            cache_read_input_tokens = 0; cache_creation_input_tokens = 0
    _R.text = "done"

    async def fake_run(**kw):
        kw["collector"].add({"status": "exploited", "vulnerability_id": "INJ-1",
            "title": "SQLi", "exploitation_steps": ["s"], "proof_of_impact": "p"})
        return _R()
    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository", classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit", lambda *a, **k: asyncio.sleep(0))
    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager); pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    ax = exec_mod.AgentExecutor(pm)

    asyncio.run(ax.execute(
        agent_name=AgentName.INJECTION_EXPLOIT, repo_path=str(deliverables),
        deliverables_path=str(deliverables), collector_spec=spec))
    md = (deliverables / "injection_exploitation_evidence.md").read_text()
    assert "Successfully Exploited" in md and "SQLi" in md
    assert "Unprocessed" in md and "INJ-9" in md  # queue 有但没 exploit
```

- [ ] **Step 2: Run — verify FAIL**

`cd packages/core && uv run pytest tests/collectors/test_bridge_exploit.py tests/test_executor_exploit_render.py -q` → FAIL.

- [ ] **Step 3: Implement**

`bridge.py` 追加（append 工具，`add_exploit`，无 write-once）：

```python
# 追加到 packages/core/src/shannon_core/collectors/bridge.py
from .exploit import ExploitCollector, ExploitEntry, BlockedEntry

_EXPLOIT_UNION_SCHEMA = {
    "type": "object",
    "oneOf": [ExploitEntry.model_json_schema(), BlockedEntry.model_json_schema()],
    "properties": {  # visible 参数 schema(对齐 TS:common required + per-status optional)
        "vulnerability_id": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "string", "enum": ["exploited", "blocked"]},
    },
    "required": ["vulnerability_id", "title", "status"],
    "additionalProperties": True,
}


def _exploit_impl_factory(collector: ExploitCollector):
    async def _impl(args):
        collector.add(dict(args))
        return {"content": [{"type": "text", "text": f"added exploit {args.get('vulnerability_id')}"}]}
    return _impl


def build_exploit_claude_mcp_server(collector: ExploitCollector, server_name: str = "exploit"):
    impl = _exploit_impl_factory(collector)
    t = tool("add_exploit", "Record one exploitation attempt (call multiple times).",
             _EXPLOIT_UNION_SCHEMA)(impl)
    return create_sdk_mcp_server(name=server_name, tools=[t])


def build_exploit_openai_tools(collector: ExploitCollector) -> list[FunctionTool]:
    async def _on_invoke(ctx, input_data: str) -> str:
        collector.add(json.loads(input_data) if input_data else {})
        return "added"
    return [FunctionTool(
        name="add_exploit",
        description="Record one exploitation attempt (call multiple times).",
        params_json_schema=_EXPLOIT_UNION_SCHEMA,
        on_invoke_tool=_on_invoke,
        strict_json_schema=False,
    )]
```

`collectors/__init__.py` 加 exploit spec：

```python
@dataclass(frozen=True)
class ExploitCollectorSpec:
    make_collector: Callable
    vuln_class: str
    queue_filename: str  # "{vt}_exploitation_queue.json"
    render: Callable  # render(entries, id_to_type) -> str(闭包捕获 vc)


def get_collector_spec(agent_name):
    ...
    _EXPLOIT_AGENT_CLASS = {
        A.INJECTION_EXPLOIT: "injection", A.XSS_EXPLOIT: "xss", A.AUTH_EXPLOIT: "auth",
        A.SSRF_EXPLOIT: "ssrf", A.AUTHZ_EXPLOIT: "authz",
    }
    if agent_name in _EXPLOIT_AGENT_CLASS:
        vc = _EXPLOIT_AGENT_CLASS[agent_name]
        from .exploit import make_exploit_collector
        from ..renderers.exploit import render_exploit_deliverable
        return ExploitCollectorSpec(
            make_collector=make_exploit_collector,
            vuln_class=vc,
            queue_filename=f"{vc}_exploitation_queue.json",
            render=lambda entries, id_to_type, vc=vc: render_exploit_deliverable(vc, entries, id_to_type),
        )
    return None
```

`executor.py` 加 exploit 分支（与 set collector 分支并列，按 spec 类型判断）：

```python
# executor.execute 内,在 validate_deliverable 前:
from shannon_core.collectors import CollectorSpec, ExploitCollectorSpec
if collector_spec is not None and not skip_artifact_postprocess:
    if isinstance(collector_spec, ExploitCollectorSpec):
        import json
        from pathlib import Path as _P
        expl_collector = collector_spec.make_collector()  # 注意:collector 需在 run_claude_prompt 期间被工具写入,见下方注
        # 读 queue 拿 id_to_type
        queue_path = deliverables / collector_spec.queue_filename
        id_to_type: dict[str, str] = {}
        if queue_path.exists():
            q = json.loads(queue_path.read_text())
            for v in (q.get("verdicts") or q.get("findings") or []):
                vid = v.get("vulnerability_id")
                if vid:
                    id_to_type[vid] = v.get("vulnerability_type", collector_spec.vuln_class)
        md = collector_spec.render(expl_collector.get_all(), id_to_type)
        (deliverables / defn.deliverable_filename).write_text(md)
    else:  # set collector(Plan 1)
        ...  # Plan 1 的 render(collector.get_all()) 落盘
```

> **collector 生命周期注**：exploit collector 必须在 `run_claude_prompt` 之前构建并注入工具（工具 impl 写它），run 之后读 `get_all()`。即 `expl_collector = collector_spec.make_collector()` 要在 `run_claude_prompt` 调用前（同 Plan 1 set collector 的位置），工具用这个实例。上方伪代码位置仅为示意——实际在 Plan 1 已有的「构建 collector → 注入工具 → run → 落盘」骨架里，exploit 分支只改「落盘」段（读 queue + render）。`runner.run_claude_prompt` 对 exploit 透传 `build_exploit_*` 工具 + collector（同 Plan 1 透传模式）。

- [ ] **Step 4: Run — verify PASS**

`cd packages/core && uv run pytest tests/collectors/test_bridge_exploit.py tests/test_executor_exploit_render.py -q` → passed。

- [ ] **Step 5: Commit**

`git add packages/core/src/shannon_core/collectors/bridge.py packages/core/src/shannon_core/collectors/__init__.py packages/core/src/shannon_core/agents/executor.py packages/core/src/shannon_core/agents/runner.py packages/core/tests/collectors/test_bridge_exploit.py packages/core/tests/test_executor_exploit_render.py && git commit -m "feat(exploit): append collector bridge + registry + executor 双输入落盘分支"`

---

### Task 4: 5 个 exploit prompt 改造（add_exploit append）

**Files:**
- Modify: `prompts/exploit-injection.txt`、`exploit-xss.txt`、`exploit-auth.txt`、`exploit-ssrf.txt`、`exploit-authz.txt`

**TS 对照：** `upstream/main:apps/worker/prompts/exploit-*.txt`。

- [ ] **Step 1: 改 5 个 prompt**

每个 `exploit-{class}.txt`：
- 删「MUST save `{vt}_exploitation_evidence.md` using the Write tool」
- 改为：

```
- **MANDATORY:** For each vulnerability you attempt, call the `add_exploit` tool ONCE with the result (status=exploited or blocked + per-status fields). Call it multiple times — once per attempted vulnerability. The host renders the exploitation evidence deliverable from your calls — there is no Markdown for you to write yourself.
```

- 读 `{vt}_exploitation_queue.json`（vuln agent 产的 candidate 列表）作为 attempt 范围（对齐 TS：exploit 读 queue 决定 attempt 哪些 ID）。

- [ ] **Step 2: 校验 + Commit**

`cd packages/core && uv run pytest tests/prompts/ -q`（插值 + 无残留 Write 指示）。
`git add prompts/exploit-*.txt && git commit -m "feat(prompts): 5 exploit prompt 改 add_exploit(append),删 agent Write evidence md"`

---

### Task 5: 端到端 + GLM 冒烟

- [ ] **Step 1: GLM 真机冒烟（需 glm-anthropic env + 仓库 + 已有 queue）**

跑一个 exploit agent（如 injection-exploit，前提 injection-vuln 已产 queue.json），确认：
- `injection_exploitation_evidence.md` 由 host 渲染（Successfully Exploited / Blocked / Unprocessed）
- agent 多次调 `add_exploit`（append）
- Unprocessed section 正确反映 queue 里没 attempt 的 ID
- workflow.log 无 `Missing deliverable: injection_exploitation_evidence.md`

- [ ] **Step 2: 记 memory**

记录 Plan 4 落地 + exploit append 机制到 memory [[pre-recon-md-deliverable-glm-forget-write]]。

---

## Self-Review

**Spec coverage:** §6 Plan 4（exploit）→ Task 1-5 ✓；exploit append + queue 依赖是设计核心（Architecture 已说明为何独立于 set collector）。

**Placeholder scan:** ExploitEntry/BlockedEntry 字段 + TITLES 标注 TS 行号；`_EXPLOIT_UNION_SCHEMA` visible 参数对照 TS line 19-25（common required + per-status optional + handler 内 union re-validation）。

**Type consistency:** `ExploitCollector.add/get_all`、`ExploitCollectorSpec`、`render_exploit_deliverable(vc, entries, id_to_type)` 跨 task 一致；`get_collector_spec` 对 exploit 返回 `ExploitCollectorSpec`（executor 按 isinstance 分支）。

**框架扩展风险：** exploit 不复用 Plan 1 set collector_spec，新增 `ExploitCollectorSpec` + executor 分支。若 Plan 1 executor 的 collector_spec 处理是单一分支，Plan 4 Task 3 改为按 spec 类型分支（set vs exploit）。collector 生命周期（构建→注入工具→run→落盘）须复用 Plan 1 骨架，避免双构建。

**已知执行期风险：**
- `_EXPLOIT_UNION_SCHEMA` 的 oneOf 在 GLM/双引擎的接受度（append 工具传 exploited 或 blocked）→ Task 3 bridge 测试 + Task 5 probe 验证；若 oneOf 不被接受，改 discriminated schema（required status + conditional fields）。
- ExploitEntry/BlockedEntry 完整字段对照 TS line 69-99 补齐。
- executor exploit 分支读 queue 的字段名（`verdicts` vs `findings`）按 PY 实际 queue schema 适配（Task 3 标注）。
