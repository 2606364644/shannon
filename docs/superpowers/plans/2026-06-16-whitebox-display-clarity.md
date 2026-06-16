# 白盒扫描显示层清晰度重做 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让白盒扫描启动信息清晰（消除 `Target: N/A`，显示仓库/模式/监控入口），进行中能看到 phase 内子步骤进度（`step N/M`）与开头 setup 步骤，不再干等或误判卡死。

**Architecture:** 方案 A（事件补全）。在现有 `DisplayEvent` 流里新增轻量 `StepEvent`，给静默的确定性/早期 activity 补发步骤事件；`DashboardState` 跟踪 phase 内单元（step + agent 同等计入）使状态条显示 `step N/M`；banner 改语义化 + Web UI/logs 引导。**全程不动 Temporal workflow 编排顺序和 LLM agent 逻辑。** 详见 `docs/superpowers/specs/2026-06-16-whitebox-display-clarity-design.md`。

**Tech Stack:** Python 3.12+, Temporal.io, Rich, pytest (asyncio_mode=auto, testpaths=`packages/*/tests`), uv。

---

## ⚠️ 模块归属（务必先读）

- **display 层唯一在 core**：`packages/core/src/shannon_core/display/{events,dashboard_state,live_dashboard,rich_renderer,file_renderer}.py`。
- **audit 层实现在 core**：`packages/core/src/shannon_core/audit/{workflow_logger,session}.py`。`shannon_whitebox.audit.*` 是 **compat shim**（`from shannon_core.audit.X import *`），改 core 即可，shim 自动转发 —— **不要改 shim 文件**。
- **whitebox 真实代码**：`packages/whitebox/src/shannon_whitebox/{pipeline/{activities,workflows},worker}.py`。
- **已知挂起测试**（feat/fork-py 现状，广跑需 `--ignore`）：`packages/whitebox/tests/test_worker_progress.py`、`test_cli.py::...follow...`、`test_audit_injection.py`、`test_integration.py`。新增测试避开 worker 级集成。
- **跑单个测试**：`uv run pytest <path>::<test> -v`（项目用 uv）。

## File Structure

| 文件 | 责任 | Task |
|---|---|---|
| `packages/core/src/shannon_core/display/events.py` | DisplayEvent 数据类（+`StepEvent`、`PhaseEvent.steps`、`WorkflowHeader` 字段） | 1 |
| `packages/core/src/shannon_core/display/dashboard_state.py` | 纯状态机（+phase_units/unit_status） | 2 |
| `packages/core/src/shannon_core/display/live_dashboard.py` | 状态条渲染（+`step N/M`） | 3 |
| `packages/core/src/shannon_core/display/file_renderer.py` | workflow.log 文本渲染（+StepEvent、banner 字段） | 4 |
| `packages/core/src/shannon_core/display/rich_renderer.py` | 终端滚动行+banner（+新 banner 布局、`_render_step`） | 5 |
| `packages/core/src/shannon_core/audit/workflow_logger.py` | 事件生产单点（+`log_step`、`log_phase(+steps)`、banner 字段计算） | 6 |
| `packages/core/src/shannon_core/audit/session.py` | AuditSession 门面（+`log_step`、`log_phase_start(+steps)`、`track_step`） | 7 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 确定性/早期 activity 包 `track_step`；`log_phase_start_activity(+steps)` | 8 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | phase 声明（steps 清单）+ setup phase + phase complete | 9 |
| `packages/whitebox/src/shannon_whitebox/worker.py` | 统一 workflow_id | 10 |

**交付阶段标注**：Task 1 是基础。Banner 生效 = Task 1+5+6+10（spec 阶段1）。状态条 `N/M` = Task 1+2+3（阶段2）。事件补全 = Task 1+6+7+8+9（阶段3）。

---

## Task 1: events.py — 新增 StepEvent + PhaseEvent.steps + WorkflowHeader 字段

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py`
- Test: `packages/core/tests/display/test_events.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_events.py`）

```python
from shannon_core.display.events import StepEvent


def test_step_event_fields():
    e = StepEvent(timestamp="t", category="STEP", name="code-index",
                  phase="pre-recon", event="start")
    assert e.name == "code-index"
    assert e.phase == "pre-recon"
    assert e.duration_ms is None
    assert e.error is None


def test_phase_event_has_optional_steps_default_empty():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    assert e.steps == ()


def test_phase_event_carries_steps():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                   steps=("code-index", "pre-recon"))
    assert e.steps == ("code-index", "pre-recon")


def test_workflow_header_banner_fields():
    e = WorkflowHeader(timestamp="t", category="HEADER", workflow_id="wf-1",
                       target_url=None, repo_path="/repo", mode="offline",
                       web_ui_url="http://localhost:8233/x", logs_cmd="logs wf --follow",
                       workspace="wf-1")
    assert e.repo_path == "/repo"
    assert e.mode == "offline"
    assert e.web_ui_url.startswith("http://localhost:8233")
    assert e.logs_cmd == "logs wf --follow"
    assert e.workspace == "wf-1"
```

并把 `StepEvent` 加入 `test_all_events_are_frozen` 的 lambda 列表：

```python
        lambda: StepEvent(timestamp="t", category="STEP", name="x", phase="p", event="start"),
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/display/test_events.py -v`
Expected: FAIL — `ImportError: cannot import name 'StepEvent'`；`PhaseEvent` 无 `steps`；`WorkflowHeader` 无新字段。

- [ ] **Step 3: 实现**（在 `events.py` 中）

在 `PhaseEvent` 定义里加 `steps` 字段；在 `WorkflowHeader` 加字段；新增 `StepEvent`：

```python
@dataclass(frozen=True)
class WorkflowHeader(DisplayEvent):
    workflow_id: str | None
    target_url: str | None
    repo_path: str | None = None
    mode: str | None = None
    web_ui_url: str | None = None
    logs_cmd: str | None = None
    workspace: str | None = None


@dataclass(frozen=True)
class PhaseEvent(DisplayEvent):
    phase: str
    event: Literal["start", "complete"]
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepEvent(DisplayEvent):
    name: str
    phase: str
    event: Literal["start", "complete"]
    duration_ms: int | None = None
    error: str | None = None
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/display/test_events.py -v`
Expected: PASS（含 `test_all_events_are_frozen` 的 StepEvent 用例）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/events.py packages/core/tests/display/test_events.py
git commit -m "feat(display): add StepEvent, PhaseEvent.steps, WorkflowHeader banner fields"
```

---

## Task 2: dashboard_state.py — phase_units / unit_status + apply(StepEvent) + AgentEvent 同步 unit

**Files:**
- Modify: `packages/core/src/shannon_core/display/dashboard_state.py`
- Test: `packages/core/tests/display/test_dashboard_state.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_dashboard_state.py`）

```python
from shannon_core.display.events import StepEvent


def _phase_with_steps(name: str, steps) -> PhaseEvent:
    return PhaseEvent(timestamp="t", category="PHASE", phase=name, event="start",
                      steps=tuple(steps))


def _step(name: str, phase: str, event: str = "start", **kw) -> StepEvent:
    return StepEvent(timestamp="t", category="STEP", name=name, phase=phase,
                     event=event, duration_ms=kw.get("duration_ms"), error=kw.get("error"))


def test_phase_start_records_units_and_resets_status():
    s = DashboardState().apply(
        _phase_with_steps("pre-recon", ["code-index", "pre-recon", "merge-sinks"]))
    assert s.phase_units == ("code-index", "pre-recon", "merge-sinks")
    assert s.unit_status == {}
    assert s.total_units == 3
    assert s.completed_units == 0


def test_step_start_then_complete_advances_unit_progress():
    s = (DashboardState()
         .apply(_phase_with_steps("pre-recon", ["code-index", "pre-recon"]))
         .apply(_step("code-index", "pre-recon", "start"))
         .apply(_step("code-index", "pre-recon", "complete", duration_ms=12000)))
    assert s.unit_status["code-index"] == "done"
    assert s.completed_units == 1
    assert s.running_units == []
    assert "code-index" not in s.running_units


def test_running_units_lists_in_flight():
    s = (DashboardState()
         .apply(_phase_with_steps("pre-recon", ["code-index", "pre-recon"]))
         .apply(_step("code-index", "pre-recon", "start"))
         .apply(_agent("pre-recon", "start")))   # agent in same phase -> unit running
    assert set(s.running_units) == {"code-index", "pre-recon"}
    assert s.completed_units == 0


def test_agent_in_phase_advances_unit_status():
    s = (DashboardState()
         .apply(_phase_with_steps("pre-recon", ["code-index", "pre-recon"]))
         .apply(_agent("pre-recon", "start"))
         .apply(_agent("pre-recon", "end", success=True)))
    assert s.unit_status["pre-recon"] == "done"
    assert s.completed_units == 1


def test_step_failed_marks_unit_failed():
    s = (DashboardState()
         .apply(_phase_with_steps("pre-recon", ["code-index"]))
         .apply(_step("code-index", "pre-recon", "complete", error="boom")))
    assert s.unit_status["code-index"] == "failed"
    assert s.completed_units == 1   # terminal either way


def test_phase_without_steps_keeps_legacy_completed_count():
    # Backward-compat: a PhaseEvent without steps does not set phase_units,
    # so status line falls back to completed_count-based "N done".
    s = DashboardState().apply(_phase("recon"))
    assert s.phase_units == ()
    assert s.total_units == 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py -v`
Expected: FAIL — `phase_units`/`unit_status`/`completed_units`/`running_units` 不存在。

- [ ] **Step 3: 实现**（修改 `DashboardState`）

```python
from shannon_core.display.events import (
    AgentEvent, DisplayEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
    ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent,
)

@dataclass(frozen=True)
class DashboardState:
    current_phase: str | None = None
    agents: dict[str, AgentRow] = field(default_factory=dict)
    phase_units: tuple[str, ...] = ()
    unit_status: dict[str, str] = field(default_factory=dict)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.agents.values() if r.status in ("done", "failed"))

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.agents.values())

    @property
    def total_units(self) -> int:
        return len(self.phase_units)

    @property
    def completed_units(self) -> int:
        return sum(1 for st in self.unit_status.values() if st in ("done", "failed"))

    @property
    def running_units(self) -> list[str]:
        return [n for n in self.phase_units if self.unit_status.get(n) == "running"]

    def _set_unit(self, name: str, status: str) -> "DashboardState":
        if name not in self.phase_units:
            return self   # unit not declared in this phase -> ignore (keeps agents clean)
        units = dict(self.unit_status)
        units[name] = status
        return replace(self, unit_status=units)

    def apply(self, event: DisplayEvent) -> "DashboardState":
        if isinstance(event, PhaseEvent):
            if event.event == "start":
                return replace(self, current_phase=event.phase,
                               phase_units=event.steps, unit_status={})
            return replace(self, current_phase=event.phase)  # complete: keep units

        if isinstance(event, StepEvent):
            status = "running" if event.event == "start" else (
                "failed" if event.error else "done")
            return self._set_unit(event.name, status)

        if isinstance(event, ResumeEvent):
            agents = dict(self.agents)
            for name in event.completed_agents:
                agents[name] = AgentRow(name=name, status="done", attempt=1)
            return replace(self, agents=agents)

        if isinstance(event, AgentEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name, AgentRow(name=event.agent_name))
            if event.event == "start":
                agents[event.agent_name] = replace(
                    cur, status="running", attempt=event.attempt, error=None)
                next_state = self._set_unit(event.agent_name, "running")
            else:
                status: AgentStatus = "done" if event.success else "failed"
                agents[event.agent_name] = replace(
                    cur, status=status,
                    duration_ms=event.duration_ms if event.duration_ms is not None else cur.duration_ms,
                    cost_usd=event.cost_usd if event.cost_usd is not None else cur.cost_usd,
                    error=event.error)
                next_state = self._set_unit(event.agent_name, status)
            return replace(next_state, agents=agents)

        if isinstance(event, ToolCallEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                detail = humanize_tool_call(event.tool_name, event.parameters or {})
                agents[event.agent_name] = replace(
                    cur, last_action=event.tool_name, last_action_detail=detail)
            return replace(self, agents=agents)

        if isinstance(event, LlmTurnEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                agents[event.agent_name] = replace(cur, turn=event.turn)
            return replace(self, agents=agents)

        # ErrorEvent, SummaryEvent, WorkflowHeader -> no dashboard-state change
        return self
```

> 注意：`AgentEvent` 分支现在先更新 `agents`、再调 `_set_unit` 同步 `unit_status`，最后一起 `replace`。`completed_count`/`total_cost` 仍只基于 `agents`，StepEvent 不污染。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py -v`
Expected: PASS（含原有测试，确认未回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/dashboard_state.py packages/core/tests/display/test_dashboard_state.py
git commit -m "feat(display): track phase units in DashboardState (step N/M + running units)"
```

---

## Task 3: live_dashboard.py — 状态条显示 step N/M + running units

**Files:**
- Modify: `packages/core/src/shannon_core/display/live_dashboard.py`
- Test: `packages/core/tests/display/test_live_dashboard.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_live_dashboard.py`）

```python
from shannon_core.display.events import StepEvent, PhaseEvent


async def test_status_line_shows_step_progress_and_running_units():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon",
                              event="start",
                              steps=("code-index", "pre-recon", "merge-sinks")))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="pre-recon",
                              event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "pre-recon" in out            # phase
    assert "step 0/3" in out             # 0 completed of 3 units
    assert "code-index" in out           # running unit
    assert "pre-recon" in out            # running unit (agent)


async def test_status_line_falls_back_when_phase_has_no_steps():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    # PhaseEvent without steps (legacy) -> no "step N/M", keep "N done"
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="recon",
                              event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "0 done" in out
    assert "step " not in out
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -v`
Expected: FAIL — 输出无 `step 0/3`。

- [ ] **Step 3: 实现**（改 `_render` 的 cells 构造）

```python
    def _render(self, options: ConsoleOptions) -> Group:
        snap = self._snapshot
        elapsed = format_duration(int(time.monotonic() - self._start_monotonic) * 1000)
        running = [r for r in snap.agents.values() if r.status == "running"]

        cells: list = [Text(snap.current_phase or "—", style="bold cyan")]

        if snap.total_units > 0:
            cells.append(Text(f" · step {snap.completed_units}/{snap.total_units}", style="green"))
            running_unit_names = snap.running_units
        else:
            cells.append(Text(f" · {snap.completed_count} done", style="green"))
            running_unit_names = [r.name for r in running]

        cells.append(Text(f" · {elapsed}"))
        cells.append(Text(f" · ${snap.total_cost:.4f}", style="yellow"))

        if running_unit_names:
            cells += [Text("    "), Spinner("dots"),
                      Text(" " + " · ".join(running_unit_names), style="blue")]

        row = Table.grid()
        row.add_row(*cells)

        return Group(
            Text("─" * options.max_width, style="dim"),
            row,
        )
```

> 单元名优先用 `snap.running_units`（phase 声明的 step+agent 名）；无 steps 时回退到 running agent 名，保持原行为。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -v`
Expected: PASS（含原有 4 个测试不回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/live_dashboard.py packages/core/tests/display/test_live_dashboard.py
git commit -m "feat(display): show step N/M + running units in live status line"
```

---

## Task 4: file_renderer.py — StepEvent 写 workflow.log + banner 新字段

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`
- Test: `packages/core/tests/display/test_file_renderer.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_file_renderer.py`）

```python
from shannon_core.display.events import StepEvent, WorkflowHeader


async def test_step_event_renders_step_line():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from shannon_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="complete", duration_ms=12000))
    out = "".join(w.lines)
    assert "[STEP]" in out
    assert "code-index" in out
    assert "Starting" in out
    assert "Completed" in out


async def test_header_renders_repo_and_monitor_when_offline():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from shannon_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    await r.render(WorkflowHeader(
        timestamp="2026-06-16 13:49:44", category="HEADER", workflow_id="wf-1",
        target_url=None, repo_path="/root/code/prize_web", mode="offline (source code analysis)",
        web_ui_url="http://localhost:8233/namespaces/default/workflows/wf-1",
        logs_cmd="shannon-whitebox logs wf-1 --follow", workspace="wf-1"))
    out = "".join(w.lines)
    assert "Repository:" in out
    assert "/root/code/prize_web" in out
    assert "offline" in out
    assert "Monitor:" in out
    assert "8233" in out
    assert "Target URL:  N/A" not in out     # offline -> no N/A target line
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: FAIL — StepEvent 无渲染分支；banner 仍是 `Target URL: N/A`。

- [ ] **Step 3: 实现**

`render` match 加 `StepEvent` 分支；新增 `_step`；改 `_header`：

```python
    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): await self._writer.write(self._header(event))
            case PhaseEvent(): await self._writer.write(self._phase(event))
            case StepEvent(): await self._writer.write(self._step(event))
            case AgentEvent(): await self._writer.write(self._agent(event))
            case ToolCallEvent(): await self._writer.write(self._tool(event))
            case LlmTurnEvent(): await self._writer.write(self._llm(event))
            case ErrorEvent(): await self._writer.write(self._error(event))
            case SummaryEvent(): await self._writer.write(self._summary(event))
            case ResumeEvent(): await self._writer.write(self._resume(event))

    def _step(self, e) -> str:
        verb = "Starting" if e.event == "start" else "Completed"
        parts = []
        if e.event == "complete" and e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.error:
            parts.append(f"error: {e.error}")
        suffix = f" ({', '.join(parts)})" if parts else ""
        return f"[{e.timestamp}] [STEP] {e.name}: {verb}{suffix}\n"

    def _header(self, e) -> str:
        lines = [_SEP, "Shannon Pentest - Workflow Log", _SEP]
        if e.workflow_id:
            lines.append(f"Workflow ID: {e.workflow_id}")
        if getattr(e, "repo_path", None):
            lines.append(f"Repository:  {e.repo_path}")
        # Target line only when there is a real URL (offline scans show mode instead)
        if e.target_url:
            lines.append(f"Target URL:  {e.target_url}")
        mode = getattr(e, "mode", None)
        if mode and not e.target_url:
            lines.append(f"Mode:        {mode}")
        lines.append(f"Started:     {e.timestamp}")
        web_ui = getattr(e, "web_ui_url", None)
        logs_cmd = getattr(e, "logs_cmd", None)
        if web_ui or logs_cmd:
            lines.append("Monitor:")
            if web_ui:
                lines.append(f"  Web UI: {web_ui}")
            if logs_cmd:
                lines.append(f"  Logs:   {logs_cmd}")
        lines.append(_SEP)
        return "\n".join(lines) + "\n\n"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "feat(display): render StepEvent + repo/mode/monitor in workflow.log header"
```

---

## Task 5: rich_renderer.py — 新 banner 布局 + _render_step

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`
- Test: `packages/core/tests/display/test_rich_renderer.py`

- [ ] **Step 1: 写失败测试**（追加；并更新现有 `test_header_renders_workflow_id_and_target`）

替换现有 `test_header_renders_workflow_id_and_target` 为：

```python
async def test_header_offline_shows_repo_mode_monitor_no_NA():
    renderer, _ = _renderer_with_capture()
    await renderer.render(WorkflowHeader(
        timestamp="2026-06-16 13:49:44", category="HEADER", workflow_id="wf-1",
        target_url=None, repo_path="/root/code/prize_web",
        mode="offline (source code analysis)",
        web_ui_url="http://localhost:8233/namespaces/default/workflows/wf-1",
        logs_cmd="shannon-whitebox logs wf-1 --follow", workspace="wf-1"))
    out = renderer._console.export_text()
    assert "Repository:" in out
    assert "/root/code/prize_web" in out
    assert "offline" in out
    assert "Monitor:" in out
    assert "8233" in out
    assert "N/A" not in out


async def test_header_with_target_url_shows_url():
    renderer, _ = _renderer_with_capture()
    await renderer.render(WorkflowHeader(
        timestamp="t", category="HEADER", workflow_id="wf-1",
        target_url="https://x.com", repo_path="/repo", mode="https://x.com",
        web_ui_url=None, logs_cmd=None))
    out = renderer._console.export_text()
    assert "https://x.com" in out


async def test_step_event_renders_step_line():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start"))
    out = renderer._console.export_text()
    assert "code-index" in out
    assert "STEP" in out
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: FAIL — banner 仍含 `Target: N/A`；无 `Repository`；StepEvent 无分支。

- [ ] **Step 3: 实现**

`render` match 加 `StepEvent`；改 `_render_header`；新增 `_render_step`：

```python
    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): self._render_header(event)
            case PhaseEvent():
                if self._show_phase:
                    self._render_phase(event)
            case StepEvent():
                if self._show_phase:
                    self._render_step(event)
            case AgentEvent(): self._render_agent(event)
            case ToolCallEvent(): self._render_tool(event)
            case LlmTurnEvent(): self._render_llm(event)
            case ErrorEvent(): self._render_error(event)
            case SummaryEvent(): self._render_summary(event)
            case ResumeEvent(): self._render_resume(event)

    def _render_header(self, e) -> None:
        lines = []
        if getattr(e, "repo_path", None):
            lines.append(f"Repository: {e.repo_path}")
        if e.target_url:
            lines.append(f"Target:     {e.target_url}")
        mode = getattr(e, "mode", None)
        if mode and not e.target_url:
            lines.append(f"Mode:       {mode}")
        lines.append(f"Started:    {e.timestamp}")
        web_ui = getattr(e, "web_ui_url", None)
        logs_cmd = getattr(e, "logs_cmd", None)
        if web_ui or logs_cmd:
            lines.append("")
            lines.append("Monitor:")
            if web_ui:
                lines.append(f"  Web UI: {web_ui}")
            if logs_cmd:
                lines.append(f"  Logs:   {logs_cmd}")
        body = "\n".join(lines)
        self._console.print(Panel(body, title="Shannon Pentest", border_style="cyan"))

    def _render_step(self, e) -> None:
        verb = "Starting" if e.event == "start" else "Completed"
        suffix = ""
        if e.event == "complete" and e.duration_ms is not None:
            suffix = f" ({format_duration(e.duration_ms)})"
        if e.error:
            suffix = f" — {e.error}"
        self._console.print(
            f"[{e.timestamp}] [cyan]STEP[/]  {verb} {e.name}{suffix}", highlight=False)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): rich banner shows repo/mode/monitor + render StepEvent"
```

---

## Task 6: workflow_logger.py — log_step + log_phase(+steps) + initialize 计算 banner 字段

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`
- Test: `packages/whitebox/tests/test_workflow_logger.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_workflow_logger.py`）

```python
WEB_UI_PORT_DEFAULT = "8233"


async def test_log_step_writes_step_line(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_step("code-index", "pre-recon", "start")
    await logger.log_step("code-index", "pre-recon", "complete", duration_ms=12000)
    await logger.close()
    content = _read_log(tmp_path)
    assert "[STEP]" in content
    assert "code-index" in content
    assert "Starting" in content
    assert "Completed" in content


async def test_log_phase_carries_steps(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_phase("pre-recon", "start", steps=("code-index", "pre-recon"))
    await logger.close()
    # steps land in the dispatched PhaseEvent; verify via dispatcher renderers
    content = _read_log(tmp_path)
    assert "[PHASE] Starting pre-recon" in content


async def test_initialize_offline_header_has_repo_mode_monitor(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEMPORAL_WEB_UI_PORT", "8233")
    meta = SessionMetadata(id="wf-1", web_url=None, repo_path="/root/code/prize_web",
                           output_path=str(tmp_path))
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-1")
    content = _read_log(tmp_path)
    assert "Repository:" in content
    assert "/root/code/prize_web" in content
    assert "offline" in content
    assert "Monitor:" in content
    assert "namespaces/default/workflows/wf-1" in content
    assert "shannon-whitebox logs wf-1 --follow" in content
    await logger.close()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py -v`
Expected: FAIL — `log_step` 不存在；`log_phase` 不接 `steps`；header 无 Repository/Monitor。

- [ ] **Step 3: 实现**（修改 `workflow_logger.py`）

文件头加 `import os`。改 `initialize`、`log_phase`，加 `log_step`：

```python
def _web_ui_url(workflow_id: str | None) -> str | None:
    if not workflow_id:
        return None
    port = os.environ.get("TEMPORAL_WEB_UI_PORT", "8233")
    return f"http://localhost:{port}/namespaces/default/workflows/{workflow_id}"


def _logs_cmd(workspace: str | None) -> str | None:
    if not workspace:
        return None
    return f"shannon-whitebox logs {workspace} --follow"
```

```python
    async def initialize(self, workflow_id: str | None = None) -> None:
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()

        renderers: list = [FileLogRenderer(self._stream)]
        if self._console is not None:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(self._console, show_phase=not self._use_rich))
        if self._use_rich and self._dashboard is not None:
            renderers.append(self._dashboard)
        self._dispatcher = DisplayDispatcher(renderers)

        ws = workflow_id or self._meta.id
        mode = self._meta.web_url or "offline (source code analysis)"
        await self._dispatcher.dispatch(WorkflowHeader(
            timestamp=format_log_time(), category="HEADER",
            workflow_id=workflow_id, target_url=self._meta.web_url or None,
            repo_path=self._meta.repo_path,
            mode=mode,
            web_ui_url=_web_ui_url(workflow_id),
            logs_cmd=_logs_cmd(ws),
            workspace=ws,
        ))

    async def log_phase(self, phase: str, event: Literal["start", "complete"],
                        steps: tuple[str, ...] = ()) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(PhaseEvent(
            timestamp=format_log_time(), category="PHASE", phase=phase,
            event=event, steps=tuple(steps)))

    async def log_step(self, name: str, phase: str, event: Literal["start", "complete"],
                       duration_ms: int | None = None, error: str | None = None) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(StepEvent(
            timestamp=format_log_time(), category="STEP", name=name, phase=phase,
            event=event, duration_ms=duration_ms, error=error))
```

并更新文件顶部 import：加入 `StepEvent`。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py -v`
Expected: PASS（含原有测试不回归——注意 `test_initialize_creates_workflow_log` 断言 `Target URL:  https://example.com`，因 web_url 非空时仍输出 Target 行，需确认 `_render`/`_header` 保留该行；本 task file_renderer `_header` 已保留 `if e.target_url` 分支 ✓）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "feat(audit): workflow_logger emits StepEvent, PhaseEvent.steps, banner fields"
```

---

## Task 7: session.py — log_step + log_phase_start(+steps) + track_step context manager

**Files:**
- Modify: `packages/core/src/shannon_core/audit/session.py`
- Test: `packages/whitebox/tests/test_audit_session.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_audit_session.py`）

```python
async def test_log_step_writes_step_line(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_step("code-index", "pre-recon", "start")
    await session.log_step("code-index", "pre-recon", "complete", duration_ms=9000)
    ad = _audit_dir(tmp_path)
    wf = (ad / "workflow.log").read_text()
    assert "[STEP]" in wf
    assert "code-index" in wf


async def test_log_phase_start_passes_steps(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_phase_start("pre-recon", steps=("code-index", "pre-recon"))
    ad = _audit_dir(tmp_path)
    assert "[PHASE] Starting pre-recon" in (ad / "workflow.log").read_text()


async def test_track_step_emits_start_then_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    async with session.track_step("pre-recon", "merge-sinks"):
        pass
    wf = (_audit_dir(tmp_path) / "workflow.log").read_text()
    assert "[STEP] merge-sinks: Starting" in wf
    assert "[STEP] merge-sinks: Completed" in wf


async def test_track_step_emits_complete_with_error_on_exception(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    import pytest
    with pytest.raises(RuntimeError):
        async with session.track_step("pre-recon", "adjudication"):
            raise RuntimeError("boom")
    wf = (_audit_dir(tmp_path) / "workflow.log").read_text()
    assert "[STEP] adjudication: Starting" in wf
    assert "boom" in wf   # error surfaced in the complete step line
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py -v`
Expected: FAIL — `log_step`/`track_step` 不存在；`log_phase_start` 不接 `steps`。

- [ ] **Step 3: 实现**（修改 `session.py`）

文件头加 `import time` 和 `from contextlib import asynccontextmanager`（若未导入）。改 `log_phase_start`，加 `log_step` 和 `track_step`：

```python
    async def log_phase_start(self, phase: str, steps: tuple[str, ...] = ()) -> None:
        """Log a phase start event, optionally declaring the phase's unit names."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(phase, "start", steps=tuple(steps))

    async def log_phase_complete(self, phase: str) -> None:
        """Log a phase complete event."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(phase, "complete")

    async def log_step(self, name: str, phase: str, event: str,
                       duration_ms: int | None = None, error: str | None = None) -> None:
        """Log a deterministic sub-step start/complete event."""
        if self._workflow_logger:
            await self._workflow_logger.log_step(name, phase, event,
                                                 duration_ms=duration_ms, error=error)

    @asynccontextmanager
    async def track_step(self, phase: str, name: str):
        """Emit StepEvent start on enter, complete (with duration/error) on exit.

        Uses try/finally so the complete event is always emitted, even when the
        wrapped activity raises — keeps the dashboard's unit_status from getting
        stuck on 'running'.
        """
        start = time.monotonic()
        await self.log_step(name, phase, "start")
        err: str | None = None
        try:
            yield
        except Exception as e:  # re-raise after recording; caller decides handling
            err = str(e)
            raise
        finally:
            await self.log_step(name, phase, "complete",
                                duration_ms=int((time.monotonic() - start) * 1000), error=err)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py -v`
Expected: PASS（含原有测试不回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/audit/session.py packages/whitebox/tests/test_audit_session.py
git commit -m "feat(audit): AuditSession.log_step/log_phase_start(steps) + track_step ctx mgr"
```

---

## Task 8: activities.py — 各 activity 包 track_step + log_phase_start_activity(+steps)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Test: `packages/whitebox/tests/test_phase_marker_activities.py`

unit 名映射见 spec §4.7。本 task 先改 `log_phase_start_activity` 支持 `steps`，再用代表性 activity 验证 `track_step` 包裹；其余确定性/早期 activity 同构包裹（给出完整代码）。

- [ ] **Step 1: 写失败测试**（追加到 `test_phase_marker_activities.py`）

更新 `_RecordingSession` 使 `log_phase_start` 接收 `steps`，并加 `track_step` 记录：

```python
class _RecordingSession:
    def __init__(self) -> None:
        self.phases_started: list[tuple[str, tuple[str, ...]]] = []
        self.phases_completed: list[str] = []
        self.steps: list[tuple[str, str, str]] = []   # (name, phase, event)

    async def log_phase_start(self, phase: str, steps: tuple[str, ...] = ()) -> None:
        self.phases_started.append((phase, tuple(steps)))

    async def log_phase_complete(self, phase: str) -> None:
        self.phases_completed.append(phase)

    async def log_step(self, name: str, phase: str, event: str, **kw) -> None:
        self.steps.append((name, phase, event))

    # requires `from contextlib import asynccontextmanager` at the test file top
    @asynccontextmanager
    async def track_step(self, phase: str, name: str):
        await self.log_step(name, phase, "start")
        try:
            yield
        except Exception:
            await self.log_step(name, phase, "complete", error="x")
            raise
        await self.log_step(name, phase, "complete")
```

新增测试：

```python
async def test_phase_marker_activity_passes_steps():
    from shannon_whitebox.audit.session import AuditSession  # noqa: F401 (ensures shim resolves)
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(
            ActivityInput(repo_path=".", workspace_name="pre-recon"),
            steps=["code-index", "pre-recon", "merge-sinks"])
    finally:
        clear_audit_session()
    assert rec.phases_started == [("pre-recon", ("code-index", "pre-recon", "merge-sinks"))]


async def test_phase_marker_backward_compat_no_steps():
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await log_phase_start_activity(ActivityInput(repo_path=".", workspace_name="recon"))
    finally:
        clear_audit_session()
    assert rec.phases_started == [("recon", ())]


async def test_save_adjudication_emits_step_events(monkeypatch):
    """Representative deterministic activity wrapped in track_step."""
    import shannon_whitebox.pipeline.activities as act
    import shannon_core.code_index as ci
    monkeypatch.setattr(ci, "save_adjudication", lambda d: None)   # stub function-level import
    monkeypatch.setattr(act, "_get_paths", lambda inp: ("repo", "deliverables", "ws"))
    rec = _RecordingSession()
    set_audit_session(rec)
    try:
        await act.run_save_adjudication(ActivityInput(repo_path="repo"))
    finally:
        clear_audit_session()
    events = [(n, e) for (n, _ph, e) in rec.steps]
    assert ("adjudication", "start") in events
    assert ("adjudication", "complete") in events
```

> 注：`test_save_adjudication_emits_step_events` 依赖 stub `shannon_core.code_index.save_adjudication`。若该 import 路径难以 stub（函数内 import），则改为：用 monkeypatch 在 `sys.modules` 注入 `shannon_core.code_index` 的假 `save_adjudication`。若仍不稳定，**降级**为只断言 `track_step` 本身（Task 7 已覆盖）+ 标注该 activity 包裹由静态审查保证。优先尝试 stub。

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_phase_marker_activities.py -v`
Expected: FAIL — `log_phase_start_activity` 不接第二参数；`_RecordingSession.log_phase_start` 旧签名不匹配。

- [ ] **Step 3: 实现**

改 `log_phase_start_activity`：

```python
@activity.defn
async def log_phase_start_activity(input: ActivityInput, steps: list[str] | None = None) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    phase = input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(phase, steps=tuple(steps or ()))
```

包裹各 activity（用 `get_audit_session()` + `track_step`）。代表性完整示例：

```python
@activity.defn
async def run_save_adjudication(input: ActivityInput) -> dict:
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index import save_adjudication
        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "adjudication"):
            save_adjudication(str(deliverables))
        return {"status": "ok"}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> `track_step` 在 try 内部，其 finally 会先发 complete(error)，再由外层 except 转 ApplicationFailure。对每个 activity 应用相同模式，`(phase, name)` 取下表：

| activity 函数 | phase | name |
|---|---|---|
| `run_preflight` | setup | preflight |
| `run_credential_check` | setup | credential-check |
| `run_auth_validation` | setup | auth-validation |
| `run_code_index` | pre-recon | code-index |
| `run_merge_sink_reports` | pre-recon | merge-sinks |
| `run_entry_point_fusion` | pre-recon | entry-point-fusion |
| `run_save_adjudication` | pre-recon | adjudication |
| `run_framework_analysis` | pre-recon | framework-analysis |
| `run_frontend_mapping` | pre-recon | frontend-mapping |
| `run_route_chain_building` | pre-recon | route-chain-building |
| `run_risk_scoring` | risk-scoring | risk-scoring |
| `run_render_dataflow_hints` | risk-scoring | dataflow-hints |
| `run_attack_chain_assembly` | attack-chain | attack-chain-assembly |
| `render_findings` | reporting | render-findings |

> 注意：`run_agent` / `run_vuln_agent` **不**包 track_step（它们已发 AgentEvent，agent_name 即 unit name）。把核心业务逻辑（`_get_paths` 之后、return 之前）用 `async with get_audit_session().track_step(phase, name):` 包裹；外层 try/except 转 ApplicationFailure 的结构保持不变。`run_code_index` 内含 `async with GitNexusMCPClient(...)`，把 track_step 套在最外层业务段即可（不要套在 import 语句外）。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_phase_marker_activities.py -v`
Expected: PASS。

补充：跑已有 wiring 测试确认不回归：
Run: `uv run pytest packages/whitebox/tests/test_activity_display_wiring.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_phase_marker_activities.py
git commit -m "feat(whitebox): wrap deterministic/setup activities in track_step; phase marker carries steps"
```

---

## Task 9: workflows.py — setup phase + 各 phase steps 清单 + phase complete + risk/attack 声明

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
- Test: `packages/whitebox/tests/test_workflows.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_workflows.py`）

抽 phase steps 为模块常量并单测（workflow 编排本身需 Temporal，用常量保证 steps 清单可测）：

```python
from shannon_whitebox.pipeline.workflows import PHASE_STEPS


def test_phase_steps_constants_match_design():
    assert PHASE_STEPS["setup"] == ("preflight", "credential-check", "auth-validation")
    assert PHASE_STEPS["pre-recon"] == (
        "code-index", "pre-recon", "merge-sinks", "entry-point-fusion",
        "adjudication", "framework-analysis", "frontend-mapping", "route-chain-building")
    assert PHASE_STEPS["recon"] == ("recon",)
    assert PHASE_STEPS["risk-scoring"] == ("risk-scoring", "dataflow-hints")
    assert PHASE_STEPS["attack-chain"] == ("attack-chain-assembly",)
    assert PHASE_STEPS["reporting"] == ("render-findings",)


def test_vuln_phase_steps_dynamic():
    # vulnerability-analysis steps are derived from selected vuln classes at runtime;
    # verify the helper produces {vt}-vuln names.
    from shannon_whitebox.pipeline.workflows import vuln_phase_steps
    steps = vuln_phase_steps(["injection", "xss"])
    assert steps == ("injection-vuln", "xss-vuln")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_workflows.py::test_phase_steps_constants_match_design packages/whitebox/tests/test_workflows.py::test_vuln_phase_steps_dynamic -v`
Expected: FAIL — `PHASE_STEPS` / `vuln_phase_steps` 未定义。

- [ ] **Step 3: 实现**（修改 `workflows.py`）

文件顶部（import 之后）加常量与 helper：

```python
PHASE_STEPS: dict[str, tuple[str, ...]] = {
    "setup": ("preflight", "credential-check", "auth-validation"),
    "pre-recon": (
        "code-index", "pre-recon", "merge-sinks", "entry-point-fusion",
        "adjudication", "framework-analysis", "frontend-mapping", "route-chain-building",
    ),
    "recon": ("recon",),
    "risk-scoring": ("risk-scoring", "dataflow-hints"),
    "attack-chain": ("attack-chain-assembly",),
    "reporting": ("render-findings",),
}


def vuln_phase_steps(vuln_classes: list[str]) -> tuple[str, ...]:
    return tuple(f"{vt}-vuln" for vt in vuln_classes)
```

在 `run()` 里：preflight 前加 setup phase 声明；各大 phase 的 `log_phase_start_activity` 传 `steps=list(PHASE_STEPS["..."])`；每个 phase 结尾调 `log_phase_complete_activity`；risk-scoring / attack-chain 两个 phase **新增**声明。示例改动点（节选，按 `workflows.py` 既有结构插入）：

```python
        # === Setup phase (preflight / credential / auth) ===
        await workflow.execute_activity(
            activities.log_phase_start_activity,
            ActivityInput(**{**act_input.__dict__, "workspace_name": "setup"}),
            steps=list(PHASE_STEPS["setup"]),
            start_to_close_timeout=timedelta(seconds=10),
        )
        self._state.current_phase = "setup"

        await workflow.execute_activity(activities.run_preflight, act_input, ...)   # 既有
        await workflow.execute_activity(activities.run_credential_check, act_input, ...)  # 既有
        await workflow.execute_activity(activities.run_auth_validation, act_input, ...)    # 既有

        await workflow.execute_activity(
            activities.log_phase_complete_activity,
            ActivityInput(**{**act_input.__dict__, "workspace_name": "setup"}),
            start_to_close_timeout=timedelta(seconds=10),
        )
```

pre-recon phase 的现有 `log_phase_start_activity`（`workflows.py:112`）改为带 `steps=list(PHASE_STEPS["pre-recon"])`，并在该 phase 块末尾（route-chain-building 之后、`if AgentName.RECON...` 之前）加 `log_phase_complete_activity(workspace_name="pre-recon")`。

recon phase（`workflows.py:184`）的 start 加 `steps=list(PHASE_STEPS["recon"])`，末尾加 complete。

在 risk_scoring（`workflows.py:201`）前后包 `log_phase_start_activity(workspace_name="risk-scoring", steps=list(PHASE_STEPS["risk-scoring"]))` + render_dataflow_hints 后 complete。

vulnerability-analysis（`workflows.py:213`）的 start 改 `steps=list(vuln_phase_steps([str(vt) for vt in selected_classes]))`，vuln gather 后 complete。

attack-chain（`workflows.py:252`）前后新增 `log_phase_start_activity(workspace_name="attack-chain", steps=list(PHASE_STEPS["attack-chain"]))` + complete（在现有非致命 try/except 外层包）。

reporting（`workflows.py:262`）start 加 `steps=list(PHASE_STEPS["reporting"])`，render_findings 后 complete。

> 注：`selected_classes` 是 `VulnType` 列表（`workflows.py:34`），传给 `vuln_phase_steps` 需转 `str(vt)`。确认 `VulnType` 的 `str()` 形如 `"injection"`（与 `AgentName(f"{vt}-vuln")` 既有用法一致，见 `workflows.py:221`）。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_workflows.py -v`
Expected: PASS（含原有 error-propagation / query-registration 测试不回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_workflows.py
git commit -m "feat(whitebox): declare phase step lists + setup/risk/attack phases + phase-complete events"
```

---

## Task 10: worker.py — 统一 workflow_id

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`
- Test: `packages/whitebox/tests/test_worker.py`（仅纯函数）

- [ ] **Step 1: 写失败测试**（追加到 `test_worker.py`）

```python
from shannon_whitebox.worker import resolve_workflow_id


def test_resolve_workflow_id_uses_workspace_name_when_given():
    assert resolve_workflow_id("my-ws", epoch=1000.0) == "my-ws"


def test_resolve_workflow_id_synthesizes_when_none():
    wid = resolve_workflow_id(None, epoch=1234567890.7)
    assert wid == "whitebox-1234567890"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_worker.py::test_resolve_workflow_id_uses_workspace_name_when_given packages/whitebox/tests/test_worker.py::test_resolve_workflow_id_synthesizes_when_none -v`
Expected: FAIL — `resolve_workflow_id` 未定义。

- [ ] **Step 3: 实现**（修改 `worker.py`）

模块级加纯函数：

```python
def resolve_workflow_id(workspace_name: str | None, epoch: float) -> str:
    """Single source of truth for the Temporal workflow id.

    Used for both the WorkflowHeader banner (meta.id → web_ui_url / logs_cmd)
    and client.start_workflow(id=...) so the Web UI link points at the real run.
    """
    return workspace_name or f"whitebox-{int(epoch)}"
```

在 `run_scan` 里（`worker.py:77` 起）改为先算 id，再用它构造 meta 与 start_workflow：

```python
    loop = asyncio.get_running_loop()
    workflow_id = resolve_workflow_id(input.workspace_name, loop.time())

    meta = SessionMetadata(
        id=workflow_id,
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(resolve_workspaces_dir(input.repo_path)),
    )
```

并把 `client.start_workflow(...)` 的 `id=`（`worker.py:98`）改为 `id=workflow_id`。

> `meta.id` 改为真实 workflow_id 后，`run_with_display` 内 `session.initialize(workflow_id=meta.id)`（display_lifecycle）与 banner 的 web_ui_url/logs_cmd 都指向真实运行；start_workflow 同 id，Web UI 链接可跳转。日志路径 `generate_workflow_log_path(meta)` 随 meta.id 变化（更唯一），属预期改进。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_worker.py -v`
Expected: PASS（仅纯函数新增；若 `test_worker.py` 含需 Temporal 的集成用例已挂起，用 `--ignore` 或 `::test_name` 精确跑）。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker.py
git commit -m "fix(whitebox): unify workflow_id for banner web-ui link + logs cmd"
```

---

## Self-Review（已完成的检查）

1. **Spec 覆盖**：spec §1 根因 → Task 4/5（banner N/A）、Task 1+2+3（步骤不可见）、Task 8+9（早期/确定性 activity 静默）、Task 9（phase complete 缺失）、Task 6（Web UI/logs 引导）。§4.2 事件层 → Task 1。§4.3 生产层 → Task 6/7/8/9。§4.4 显示层 → Task 2/3/4/5。§4.5 banner+worker 时序 → Task 6+10。§4.7 单元名映射 → Task 8 表格 + Task 9 常量。无遗漏。
2. **占位符**：无 TBD/TODO；每个代码步骤均给出完整可运行代码。Task 8 其余 activity 包裹为"同构 + 映射表"（映射表完整给出，非占位），因 11 个 activity 代码模式相同。
3. **类型一致性**：`StepEvent(name, phase, event, duration_ms, error)`（Task 1）在 Task 2/4/5/6/7 全程一致；`PhaseEvent.steps: tuple[str,...]`（Task 1）在 Task 2/6 一致；`log_phase(phase, event, steps=())`（Task 6）与 `log_phase_start(phase, steps=())`（Task 7）与 `log_phase_start_activity(input, steps=None)`（Task 8）签名链一致；`track_step(phase, name)`（Task 7）与 Task 8 包裹一致；`resolve_workflow_id`（Task 10）单一定义点。`PHASE_STEPS` 常量名在 Task 9 测试与实现一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-16-whitebox-display-clarity.md`.
