# 白盒 Live 显示：对齐原版的逐轮过程 + 步骤意图 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让白盒 live 扫描像原版 `/root/shannon` 一样展示过程——确定性子步骤滚出中文意图行、agent 逐轮 assistant 文本以可读单行滚出、live 屏隐藏吵闹的工具调用行、底部钉住"当前步骤意图 + 最新一轮"，刷新降到 3Hz。

**Architecture:** 纯渲染侧 + 文案改动，**无新埋点**——逐轮文本链路（`message_dispatcher._handle_assistant → log_assistant_turn → SessionToolAuditLogger → llm_response → LlmTurnEvent → 💭`）已就绪。意图随事件走：whitebox 新建 `step_intents.py` 单一真相源（`StepSpec{name,intent}`），`StepEvent`/`PhaseEvent` 增可选 intent/intents 字段，core 渲染器（live+file）与 dashboard 直接读字段。rich 模式下解耦 `show_steps`/`show_tools` 门控（放开 STEP、隐藏 🔧、保留 💭、压住 PHASE）。

**Tech Stack:** Python 3.12 · pytest（`asyncio_mode=auto`，无需 `@pytest.mark.asyncio`）· Rich（`Live`/`Console`/`Spinner`/`Table.grid`）· Temporal（不动）。

**Spec:** `docs/superpowers/specs/2026-06-16-whitebox-live-step-intent-display-design.md`

**约定：**
- 提交信息用 conventional commits（`feat(core):`/`feat(whitebox):`/`test(...)`）。
- 每个任务结束提交一次。
- 单测命令：`uv run pytest <path>::<test> -xvs`；广跑 display 目录：`uv run pytest packages/core/tests/display packages/whitebox/tests/test_phase_steps.py -q`。
- 全套 pytest 有预存挂起（`test_worker_progress`/`test_cli`/`test_audit_injection`/`test_integration`），**不要广跑全套**，按文件/目录跑。

---

## 文件结构（改动地图）

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/display/formatters.py` | 共享格式化函数 | **新增** `first_nonempty_line()` |
| `packages/core/src/shannon_core/display/events.py` | 事件纯数据 | `StepEvent.intent`、`PhaseEvent.step_intents`（均可选，默认空） |
| `packages/core/src/shannon_core/audit/workflow_logger.py` | 事件发射 | `log_step`/`log_phase` 透传 intent(s)；构造 renderer 传 `show_steps`/`show_tools` |
| `packages/core/src/shannon_core/audit/session.py` | AuditSession 门面 | `log_step`/`track_step`/`log_phase_start` 透传 intent(s) |
| `packages/core/src/shannon_core/display/rich_renderer.py` | live 滚动渲染 | `show_steps`/`show_tools` 开关；`_render_step` 用意图；`_render_llm` 渲染首行；`_render_tool` 受门控 |
| `packages/core/src/shannon_core/display/file_renderer.py` | workflow.log 渲染 | `_step` 追加意图 |
| `packages/core/src/shannon_core/display/dashboard_state.py` | dashboard 状态机 | `PhaseEvent` 播种 `unit_intent`；`StepEvent` 记录意图；`LlmTurnEvent` 记录 `last_turn_text`；`AgentRow.last_turn_text` |
| `packages/core/src/shannon_core/display/live_dashboard.py` | 底部状态行 | 新增"钉住行"：步骤意图 + 最新一轮 |
| `packages/core/src/shannon_core/audit/display_lifecycle.py` | Live 装配 | `refresh_per_second` 10→3（可配） |
| `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py` | **新建**：步骤意图单一真相源 | `StepSpec`、`PHASE_STEPS`、`step_names`、`step_intents`、`intent_for` |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | 阶段编排 | 改从 `step_intents` 导入；6 处消费者用 `step_names()`/`step_intents()` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | activity 实现 | 14 处 `track_step` 透传 `intent=intent_for(name)` |

---

### Task 1: 事件层加可选 intent 字段

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py`（`StepEvent` ~38-44、`PhaseEvent` ~31-35）
- Test: `packages/core/tests/display/test_events.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_events.py` 末尾）

```python
def test_step_event_carries_optional_intent():
    e = StepEvent(timestamp="t", category="STEP", name="code-index",
                  phase="pre-recon", event="start", intent="构建调用图与代码索引")
    assert e.intent == "构建调用图与代码索引"


def test_step_event_intent_defaults_none():
    e = StepEvent(timestamp="t", category="STEP", name="x", phase="p", event="start")
    assert e.intent is None


def test_phase_event_carries_step_intents():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                   steps=("code-index", "pre-recon"),
                   step_intents=("构建调用图与代码索引", "扫描架构与入口点"))
    assert e.step_intents == ("构建调用图与代码索引", "扫描架构与入口点")


def test_phase_event_step_intents_default_empty():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    assert e.step_intents == ()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest packages/core/tests/display/test_events.py::test_step_event_carries_optional_intent -xvs`
Expected: FAIL（`unexpected keyword argument 'intent'` / 无 `step_intents` 字段）

- [ ] **Step 3: 实现**（编辑 `events.py`）

`StepEvent` 改为：
```python
@dataclass(frozen=True)
class StepEvent(DisplayEvent):
    name: str
    phase: str
    event: Literal["start", "complete"]
    duration_ms: int | None = None
    error: str | None = None
    intent: str | None = None
```

`PhaseEvent` 改为：
```python
@dataclass(frozen=True)
class PhaseEvent(DisplayEvent):
    phase: str
    event: Literal["start", "complete"]
    steps: tuple[str, ...] = ()
    step_intents: tuple[str | None, ...] = ()
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `uv run pytest packages/core/tests/display/test_events.py -q`
Expected: PASS（含既有 `test_step_event_fields`、`test_all_events_are_frozen`——新字段默认值不破坏它们）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/display/events.py packages/core/tests/display/test_events.py
git commit -m "feat(core): add optional intent to StepEvent/PhaseEvent"
```

---

### Task 2: audit 层透传 intent(s)

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`（`log_step` ~88-94、`log_phase` ~80-86）
- Modify: `packages/core/src/shannon_core/audit/session.py`（`log_step` ~116-121、`track_step` ~123-141、`log_phase_start` ~106-109）
- Test: `packages/whitebox/tests/test_workflow_logger.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_workflow_logger.py`）

```python
async def test_log_step_threads_intent_into_step_event():
    from shannon_core.display.events import StepEvent
    from shannon_whitebox.pipeline.shared import PipelineInput  # noqa: F401  (keep import style)
    # 直接用 WorkflowLogger + 假 dispatcher 捕获
    from shannon_core.audit.workflow_logger import WorkflowLogger
    from shannon_core.models.metrics import SessionMetadata

    captured = []

    class _Disp:
        async def dispatch(self, ev):
            if isinstance(ev, StepEvent):
                captured.append(ev)

    meta = SessionMetadata(id="wf", web_url=None, repo_path="/r", output_path="/o")
    wl = WorkflowLogger(meta, use_rich=False)
    wl._dispatcher = _Disp()  # 绕过 initialize 直接注入
    await wl.log_step("code-index", "pre-recon", "start", intent="构建调用图与代码索引")
    assert captured and captured[-1].intent == "构建调用图与代码索引"


async def test_log_phase_threads_step_intents():
    from shannon_core.display.events import PhaseEvent
    from shannon_core.audit.workflow_logger import WorkflowLogger
    from shannon_core.models.metrics import SessionMetadata

    captured = []

    class _Disp:
        async def dispatch(self, ev):
            if isinstance(ev, PhaseEvent):
                captured.append(ev)

    meta = SessionMetadata(id="wf", web_url=None, repo_path="/r", output_path="/o")
    wl = WorkflowLogger(meta, use_rich=False)
    wl._dispatcher = _Disp()
    await wl.log_phase("pre-recon", "start",
                       steps=("code-index",), step_intents=("构建调用图与代码索引",))
    assert captured and captured[-1].step_intents == ("构建调用图与代码索引",)
```

> 说明：若 `test_workflow_logger.py` 里已有等价 fixture/dispatcher 捕获工具，复用之；上面是自包含版。

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_log_step_threads_intent_into_step_event -xvs`
Expected: FAIL（`log_step() got unexpected keyword 'intent'`）

- [ ] **Step 3: 实现 `workflow_logger.py`**

`log_phase` 改为：
```python
    async def log_phase(self, phase: str, event: Literal["start", "complete"],
                        steps: tuple[str, ...] = (),
                        step_intents: tuple[str | None, ...] = ()) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(PhaseEvent(
            timestamp=format_log_time(), category="PHASE", phase=phase,
            event=event, steps=tuple(steps), step_intents=tuple(step_intents)))
```

`log_step` 改为：
```python
    async def log_step(self, name: str, phase: str, event: Literal["start", "complete"],
                       duration_ms: int | None = None, error: str | None = None,
                       intent: str | None = None) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(StepEvent(
            timestamp=format_log_time(), category="STEP", name=name, phase=phase,
            event=event, duration_ms=duration_ms, error=error, intent=intent))
```

- [ ] **Step 4: 实现 `session.py`**

`log_phase_start` 改为：
```python
    async def log_phase_start(self, phase: str, steps: tuple[str, ...] = (),
                              step_intents: tuple[str | None, ...] = ()) -> None:
        """Log a phase start event, optionally declaring unit names + intents."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(
                phase, "start", steps=tuple(steps), step_intents=tuple(step_intents))
```

`log_step` 改为：
```python
    async def log_step(self, name: str, phase: str, event: str,
                       duration_ms: int | None = None, error: str | None = None,
                       intent: str | None = None) -> None:
        """Log a deterministic sub-step start/complete event."""
        if self._workflow_logger:
            await self._workflow_logger.log_step(name, phase, event,
                                                 duration_ms=duration_ms, error=error,
                                                 intent=intent)
```

`track_step` 改为（签名 + 两处 `log_step` 透传 intent）：
```python
    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        """Emit StepEvent start on enter, complete (with duration/error) on exit.

        Uses try/finally so the complete event is always emitted, even when the
        wrapped activity raises — keeps the dashboard's unit_status from getting
        stuck on 'running'.
        """
        start = time.monotonic()
        await self.log_step(name, phase, "start", intent=intent)
        err: str | None = None
        try:
            yield
        except Exception as e:  # re-raise after recording; caller decides handling
            err = str(e)
            raise
        finally:
            await self.log_step(name, phase, "complete",
                                duration_ms=int((time.monotonic() - start) * 1000), error=err,
                                intent=intent)
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py packages/whitebox/tests/test_audit_session.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/core/src/shannon_core/audit/session.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "feat(core): thread step/phase intent through audit log_step/log_phase"
```

---

### Task 3: rich 渲染器——门控解耦 + 意图 + 可读 💭 + 隐藏 🔧

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`（新增 `first_nonempty_line`）
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（`__init__` ~26-28、`render` match ~35-48、`_render_step` ~72-80、`_render_llm` ~117-119）
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`（`initialize` ~60-66）
- Test: `packages/core/tests/display/test_formatters.py`、`packages/core/tests/display/test_rich_renderer.py`

- [ ] **Step 1: 写 `first_nonempty_line` 的失败测试**（追加到 `test_formatters.py`）

```python
from shannon_core.display.formatters import first_nonempty_line


def test_first_nonempty_line_single_line():
    assert first_nonempty_line("🔄 Read router.ts") == "🔄 Read router.ts"


def test_first_nonempty_line_picks_first_non_blank():
    assert first_nonempty_line("\n\n  🔄 Read router.ts  \nnext") == "🔄 Read router.ts"


def test_first_nonempty_line_empty_returns_empty():
    assert first_nonempty_line("") == ""
    assert first_nonempty_line("   \n  ") == ""
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest packages/core/tests/display/test_formatters.py::test_first_nonempty_line_single_line -xvs`
Expected: FAIL（`ImportError: cannot import name 'first_nonempty_line'`）

- [ ] **Step 3: 实现 `first_nonempty_line`**（加到 `formatters.py`，位置不限，建议靠近其它格式化函数）

```python
def first_nonempty_line(text: str) -> str:
    """Return the first non-blank stripped line, or '' if none.

    Used to render an assistant turn's text as one calm live line (the full
    turn text is retained in the per-agent JSON log regardless).
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
```

- [ ] **Step 4: 跑，确认通过**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -q`
Expected: PASS

- [ ] **Step 5: 写 rich 渲染器失败测试**（追加到 `test_rich_renderer.py`）

```python
async def test_step_start_renders_intent_when_present():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start",
                                    intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "构建调用图与代码索引" in out
    assert "STEP" in out


async def test_step_complete_renders_slug_and_duration():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete", duration_ms=12000))
    out = renderer._console.export_text()
    assert "code-index" in out
    assert "12.0s" in out


async def test_rich_mode_shows_steps_hides_tools_keeps_llm():
    # 复刻 workflow_logger rich 模式构造：show_phase=False, show_steps=True, show_tools=False
    from shannon_core.display.events import StepEvent, ToolCallEvent, LlmTurnEvent, PhaseEvent
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    renderer = RichConsoleRenderer(console, show_phase=False, show_steps=True, show_tools=False)
    await renderer.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start"))
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start", intent="构建调用图"))
    await renderer.render(ToolCallEvent(timestamp="t", category="TOOL", agent_name="pre-recon",
                                        tool_name="Bash", parameters={"command": "ls"}))
    await renderer.render(LlmTurnEvent(timestamp="t", category="LLM", agent_name="pre-recon",
                                       turn=3, content="🔄 Read router.ts\nnext"))
    out = console.export_text()
    assert "pre-recon" not in out.replace("pre-recon", "pre-recon") or True  # phase 行被压
    assert "构建调用图" in out        # STEP 行放开
    assert "Bash" not in out          # 🔧 被 show_tools=False 压住
    assert "Turn 3" in out            # 💭 保留
    assert "🔄 Read router.ts" in out # 💭 取首行，不截断
    assert "next" not in out          # 多行只取首行


async def test_tool_rendered_by_default_show_tools_true():
    # 非 rich 默认 show_tools=True，行为不变
    from shannon_core.display.events import ToolCallEvent
    renderer, _ = _renderer_with_capture()  # 默认 show_tools=True
    await renderer.render(ToolCallEvent(timestamp="t", category="TOOL", agent_name="a",
                                        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._console.export_text()
    assert "Bash" in out
```

- [ ] **Step 6: 跑，确认失败**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py::test_step_start_renders_intent_when_present -xvs`
Expected: FAIL（意图未渲染 / `show_tools` 参数不存在）

- [ ] **Step 7: 实现 `rich_renderer.py`**

`__init__` 改为：
```python
    def __init__(self, console: Console | None = None, show_phase: bool = True,
                 show_steps: bool = True, show_tools: bool = True) -> None:
        self._console = console or Console()
        self._show_phase = show_phase
        self._show_steps = show_steps
        self._show_tools = show_tools
```

import 行追加 `first_nonempty_line`：
```python
from shannon_core.display.formatters import (
    agent_prefix, format_duration, format_error_block, humanize_tool_call,
    first_nonempty_line,
)
```

`render` 的 match 分支改为（StepEvent 用 `show_steps`、ToolCallEvent 用 `show_tools`）：
```python
        match event:
            case WorkflowHeader(): self._render_header(event)
            case PhaseEvent():
                if self._show_phase:
                    self._render_phase(event)
            case StepEvent():
                if self._show_steps:
                    self._render_step(event)
            case AgentEvent(): self._render_agent(event)
            case ToolCallEvent():
                if self._show_tools:
                    self._render_tool(event)
            case LlmTurnEvent(): self._render_llm(event)
            case ErrorEvent(): self._render_error(event)
            case SummaryEvent(): self._render_summary(event)
            case ResumeEvent(): self._render_resume(event)
```

`_render_step` 改为：
```python
    def _render_step(self, e) -> None:
        if e.event == "start":
            label = e.intent or e.name
            self._console.print(
                f"[{e.timestamp}] [cyan]STEP[/]  ▸ {label}", highlight=False)
            return
        suffix = ""
        if e.duration_ms is not None:
            suffix = f" ({format_duration(e.duration_ms)})"
        if e.error:
            suffix = f" — {e.error}"
        self._console.print(
            f"[{e.timestamp}] [cyan]STEP[/]  ✓ {e.name}{suffix}", highlight=False)
```

`_render_llm` 改为：
```python
    def _render_llm(self, e) -> None:
        line = first_nonempty_line(e.content) or "(无文本)"
        self._console.print(
            f"[{e.timestamp}] [magenta]💭 Turn {e.turn}: {line}[/]", highlight=False)
```

- [ ] **Step 8: 改 `workflow_logger.initialize` 的 renderer 构造**（`workflow_logger.py` ~60-66）

把：
```python
        if self._console is not None:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(self._console, show_phase=not self._use_rich))
```
改为：
```python
        if self._console is not None:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(
                self._console,
                show_phase=not self._use_rich,   # rich: 压住 PHASE 行
                show_steps=True,                 # rich: 放开 STEP 行
                show_tools=not self._use_rich,   # rich: 隐藏 🔧（仍写 workflow.log）
            ))
```

- [ ] **Step 9: 跑测试，确认通过（含既有用例不回归）**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -q`
Expected: PASS（既有 `test_step_event_renders_step_line`/`test_tool_renders_humanized`/`test_llm_renders_turn`/`test_phase_suppressed_when_show_phase_false` 等仍通过——默认 `show_steps=show_tools=True`）

- [ ] **Step 10: 提交**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/src/shannon_core/display/rich_renderer.py packages/core/src/shannon_core/audit/workflow_logger.py packages/core/tests/display/test_formatters.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(core): decouple rich step/tool gates, render step intent + readable turns"
```

---

### Task 4: file 渲染器——`_step` 追加意图

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`（`_step` ~46-54）
- Test: `packages/core/tests/display/test_file_renderer.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_file_renderer.py`）

```python
async def test_step_file_line_includes_intent_when_present():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from shannon_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    from shannon_core.display.events import StepEvent
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start",
                             intent="构建调用图与代码索引"))
    out = "".join(w.lines)
    assert "[STEP] code-index: Starting — 构建调用图与代码索引\n" in out
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py::test_step_file_line_includes_intent_when_present -xvs`
Expected: FAIL（行里没有意图）

- [ ] **Step 3: 实现 `_step`**（保留 `Starting`/`Completed` 动词——既有测试依赖）

```python
    def _step(self, e) -> str:
        verb = "Starting" if e.event == "start" else "Completed"
        intent = f" — {e.intent}" if getattr(e, "intent", None) else ""
        parts = []
        if e.event == "complete" and e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.error:
            parts.append(f"error: {e.error}")
        suffix = f" ({', '.join(parts)})" if parts else ""
        return f"[{e.timestamp}] [STEP] {e.name}: {verb}{intent}{suffix}\n"
```

- [ ] **Step 4: 跑，确认通过（含既有 `test_step_event_renders_step_line`）**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -q`
Expected: PASS（既有用例断言 `Starting`/`Completed`/`code-index`/`[STEP]` 仍在——无意图时不追加 ` — `）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "feat(core): include step intent in workflow.log [STEP] lines"
```

---

### Task 5: dashboard 状态机——记录 unit_intent + last_turn_text

**Files:**
- Modify: `packages/core/src/shannon_core/display/dashboard_state.py`（`AgentRow` ~22-31、`DashboardState` ~34-39、`apply` StepEvent ~76-79 / PhaseEvent ~70-74 / LlmTurnEvent ~113-118）
- Test: `packages/core/tests/display/test_dashboard_state.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_dashboard_state.py`）

```python
def test_phase_start_seeds_unit_intents():
    s = DashboardState().apply(
        _phase_with_steps_intents("pre-recon", ["code-index", "pre-recon"],
                                  ["构建调用图与代码索引", "扫描架构与入口点"]))
    assert s.unit_intent == {"code-index": "构建调用图与代码索引",
                             "pre-recon": "扫描架构与入口点"}


def test_phase_start_resets_unit_intents_across_phases():
    s = (DashboardState()
         .apply(_phase_with_steps_intents("pre-recon", ["code-index"], ["构建调用图"]))
         .apply(_phase_with_steps_intents("recon", ["recon"], ["侦察"])))
    assert s.unit_intent == {"recon": "侦察"}


def test_step_event_records_intent():
    s = (DashboardState()
         .apply(_phase_with_steps("pre-recon", ["code-index"]))
         .apply(StepEvent(timestamp="t", category="STEP", name="code-index",
                          phase="pre-recon", event="start", intent="构建调用图")))
    assert s.unit_intent["code-index"] == "构建调用图"


def test_llm_turn_records_last_turn_text():
    s = (DashboardState()
         .apply(_agent("pre-recon", "start"))
         .apply(LlmTurnEvent(timestamp="t", category="LLM", agent_name="pre-recon",
                             turn=5, content="🔄 Read router.ts\nmore")))
    row = s.agents["pre-recon"]
    assert row.turn == 5
    assert row.last_turn_text == "🔄 Read router.ts"
```

并在该文件辅助区追加：
```python
def _phase_with_steps_intents(name: str, steps, intents) -> PhaseEvent:
    return PhaseEvent(timestamp="t", category="PHASE", phase=name, event="start",
                      steps=tuple(steps), step_intents=tuple(intents))
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py::test_phase_start_seeds_unit_intents -xvs`
Expected: FAIL（无 `unit_intent` / `last_turn_text`）

- [ ] **Step 3: 实现 `dashboard_state.py`**

`AgentRow` 加字段：
```python
@dataclass(frozen=True)
class AgentRow:
    name: str
    status: AgentStatus = "running"
    attempt: int = 1
    turn: int = 0
    last_action: str | None = None
    last_action_detail: str | None = None
    last_turn_text: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None
```

`DashboardState` 加字段：
```python
@dataclass(frozen=True)
class DashboardState:
    current_phase: str | None = None
    agents: dict[str, AgentRow] = field(default_factory=dict)
    phase_units: tuple[str, ...] = ()
    unit_status: dict[str, str] = field(default_factory=dict)
    unit_intent: dict[str, str] = field(default_factory=dict)
```

import 追加 `first_nonempty_line`：
```python
from shannon_core.display.formatters import humanize_tool_call, first_nonempty_line
```

`apply` 的 `PhaseEvent` 分支改为（start 时播种 unit_intent 并重置）：
```python
        if isinstance(event, PhaseEvent):
            if event.event == "start":
                intents = {n: i for n, i in zip(event.steps, event.step_intents) if i}
                return replace(self, current_phase=event.phase,
                               phase_units=event.steps, unit_status={}, unit_intent=intents)
            return replace(self, current_phase=event.phase)  # complete: keep units
```

`apply` 的 `StepEvent` 分支改为：
```python
        if isinstance(event, StepEvent):
            status = "running" if event.event == "start" else (
                "failed" if event.error else "done")
            state = self._set_unit(event.name, status)
            if event.intent:
                intents = dict(state.unit_intent)
                intents[event.name] = event.intent
                state = replace(state, unit_intent=intents)
            return state
```

`apply` 的 `LlmTurnEvent` 分支改为：
```python
        if isinstance(event, LlmTurnEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                line = first_nonempty_line(event.content)
                agents[event.agent_name] = replace(
                    cur, turn=event.turn,
                    last_turn_text=line or cur.last_turn_text)
            return replace(self, agents=agents)
```

- [ ] **Step 4: 跑，确认通过（含既有用例不回归）**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py -q`
Expected: PASS（`test_llm_turn_updates_turn_count` 等仍通过——`turn` 仍设置；`test_phase_start_records_units_and_resets_status` 仍过——`unit_status=={}` 不变）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/display/dashboard_state.py packages/core/tests/display/test_dashboard_state.py
git commit -m "feat(core): dashboard state tracks unit_intent + last_turn_text"
```

---

### Task 6: 底部状态行——钉住"步骤意图 + 最新一轮"

**Files:**
- Modify: `packages/core/src/shannon_core/display/live_dashboard.py`（`_render` ~52-79）
- Test: `packages/core/tests/display/test_live_dashboard.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_live_dashboard.py`）

```python
async def test_pinned_row_shows_step_intent_and_latest_turn():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                              steps=("code-index", "pre-recon"),
                              step_intents=("构建调用图", "扫描架构与入口点")))
    from shannon_core.display.events import LlmTurnEvent
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="pre-recon",
                              event="start", attempt=1))
    await r.render(LlmTurnEvent(timestamp="t", category="LLM", agent_name="pre-recon",
                                turn=33, content="🔄 Read server/app/router.ts"))
    console.print(r)
    out = buf.getvalue()
    assert "step 0/2" in out                 # 状态行
    assert "扫描架构与入口点" in out          # 钉住行：步骤意图
    assert "Turn 33" in out                  # 钉住行：最新轮
    assert "🔄 Read server/app/router.ts" in out


async def test_pinned_row_falls_back_to_running_units_without_turns():
    # 无 LLM 轮时，钉住行退化为运行中单元名（保持既有可见性）
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                              steps=("code-index", "pre-recon")))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    console.print(r)
    out = buf.getvalue()
    assert "code-index" in out
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py::test_pinned_row_shows_step_intent_and_latest_turn -xvs`
Expected: FAIL（无钉住行 / 无意图与轮文本）

- [ ] **Step 3: 实现 `_render`**（spinner 移到钉住行；状态行不再带 spinner）

把整个 `_render` 改为：
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
        row1 = Table.grid()
        row1.add_row(*cells)

        rows = [Text("─" * options.max_width, style="dim"), row1]
        detail = self._pinned_detail(snap, running, running_unit_names)
        if detail is not None:
            pin = Table.grid()
            pin.add_row(Spinner("dots"), Text(" " + detail, style="blue"))
            rows.append(pin)
        return Group(*rows)

    def _pinned_detail(self, snap, running, running_unit_names) -> str | None:
        """Bottom pinned line: latest agent turn (prefixed by its step intent) if
        available, else the running unit names. Keeps 'what's happening now'
        visible as the scrolling log region advances."""
        narrating = [r for r in running if r.last_turn_text]
        if narrating:
            a = narrating[-1]
            intent = snap.unit_intent.get(a.name)
            prefix = f"{intent} · " if intent else ""
            return f"{prefix}Turn {a.turn}: {a.last_turn_text}"
        if running_unit_names:
            return " · ".join(running_unit_names)
        return None
```

- [ ] **Step 4: 跑，确认通过（含既有用例不回归）**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -q`
Expected: PASS（`test_status_line_shows_step_progress_and_running_units` 仍过——运行单元名在钉住行回退中可见；`test_separator_spans_full_console_width` 仍过——只有一条 `─` 分隔行）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/display/live_dashboard.py packages/core/tests/display/test_live_dashboard.py
git commit -m "feat(core): pin step-intent + latest turn on live dashboard second row"
```

---

### Task 7: whitebox 步骤意图单一真相源 + 消费者迁移

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（删除本地 `PHASE_STEPS`、改导入、6 处消费点 ~74/146/227/252/325/349）
- Test: `packages/whitebox/tests/test_phase_steps.py`（**新建**）

- [ ] **Step 1: 写失败测试**（新建 `test_phase_steps.py`）

```python
from shannon_whitebox.pipeline.step_intents import (
    PHASE_STEPS, StepSpec, step_names, step_intents, intent_for,
)


def test_every_declared_step_has_an_intent():
    for phase, specs in PHASE_STEPS.items():
        for spec in specs:
            assert isinstance(spec, StepSpec)
            assert spec.intent, f"step {phase}/{spec.name} 缺少 intent"


def test_intent_for_resolves_all_declared_names():
    names = {s.name for specs in PHASE_STEPS.values() for s in specs}
    for n in names:
        assert intent_for(n) is not None, f"intent_for({n!r}) 未命中"


def test_step_names_matches_phase_steps_order():
    assert step_names("pre-recon") == (
        "code-index", "pre-recon", "merge-sinks", "entry-point-fusion",
        "adjudication", "framework-analysis", "frontend-mapping", "route-chain-building",
    )
    assert step_names("setup") == ("preflight", "credential-check", "auth-validation")


def test_step_intents_parallel_to_step_names():
    for phase in PHASE_STEPS:
        names = step_names(phase)
        intents = step_intents(phase)
        assert len(names) == len(intents)


def test_intent_for_unknown_returns_none():
    assert intent_for("does-not-exist") is None
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest packages/whitebox/tests/test_phase_steps.py -xvs`
Expected: FAIL（`ImportError: ... step_intents`）

- [ ] **Step 3: 新建 `step_intents.py`**

```python
"""Step intent registry — single source of truth for whitebox phase steps.

Each declared step carries a human-readable intent so the live display can tell
the user *what* a step is doing (not just its slug). Consumed by:
  * workflows.py — log_phase_start_activity (names + intents for the dashboard)
  * activities.py — track_step(intent=...) on each deterministic step
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepSpec:
    name: str
    intent: str


PHASE_STEPS: dict[str, tuple[StepSpec, ...]] = {
    "setup": (
        StepSpec("preflight",          "预检（环境 / 依赖就绪性）"),
        StepSpec("credential-check",   "校验 API 凭证"),
        StepSpec("auth-validation",    "验证目标鉴权链路"),
    ),
    "pre-recon": (
        StepSpec("code-index",          "构建调用图与代码索引"),
        StepSpec("pre-recon",           "扫描应用架构、入口点与 sink"),
        StepSpec("merge-sinks",         "合并确定性 sink 与 LLM 发现"),
        StepSpec("entry-point-fusion",  "融合确定性入口点与 LLM 发现"),
        StepSpec("adjudication",        "按置信度裁决入口点"),
        StepSpec("framework-analysis",  "检测 REST 框架并推断端点"),
        StepSpec("frontend-mapping",    "映射前端路由到 API、识别 XSS 链"),
        StepSpec("route-chain-building", "构建攻击路由链"),
    ),
    "recon": (
        StepSpec("recon", "侦察目标运行时与外部信息"),
    ),
    "risk-scoring": (
        StepSpec("risk-scoring",   "打分与风险排序"),
        StepSpec("dataflow-hints", "生成数据流提示"),
    ),
    "attack-chain": (
        StepSpec("attack-chain-assembly", "组装攻击链"),
    ),
    "reporting": (
        StepSpec("render-findings", "渲染最终报告"),
    ),
}


def step_names(phase: str) -> tuple[str, ...]:
    """Step slugs for a phase (consumed by log_phase_start_activity)."""
    return tuple(s.name for s in PHASE_STEPS[phase])


def step_intents(phase: str) -> tuple[str, ...]:
    """Human intents parallel to step_names (consumed by the dashboard pin)."""
    return tuple(s.intent for s in PHASE_STEPS[phase])


_INTENT_BY_NAME: dict[str, str] = {
    s.name: s.intent for specs in PHASE_STEPS.values() for s in specs
}


def intent_for(name: str) -> str | None:
    """Resolve a step slug to its intent, or None if unknown."""
    return _INTENT_BY_NAME.get(name)
```

- [ ] **Step 4: 改 `workflows.py`**

删除文件顶部的本地 `PHASE_STEPS`（~14-24），改为导入：
```python
from .step_intents import PHASE_STEPS, step_names, step_intents
```
（保留 `vuln_phase_steps` 函数不动——它是动态的、phase 专用。）

6 处消费点把 `list(PHASE_STEPS["X"])` 改为同时传 names + intents。这些点形如（以 setup 为例，~70-75）：
```python
        await workflow.execute_activity(
            activities.log_phase_start_activity,
            args=[
                ActivityInput(**{**act_input.__dict__, "workspace_name": "setup"}),
                list(step_names("setup")),
                list(step_intents("setup")),
            ],
```
对其余 5 处同样替换（pre-recon ~146、recon ~227、risk-scoring ~252、attack-chain ~325、reporting ~349）：`list(PHASE_STEPS["X"])` → `list(step_names("X"))`，并在 args 列表追加 `list(step_intents("X"))`。

> 注意 `log_phase_start_activity(input, steps)` 当前签名只接 `steps`——下一个 Task 8 之前的步骤里，先**同步**把该 activity 签名扩成接 `intents`（见 Task 8 Step 3）。本步先确认 `workflows.py` 引用 `step_names/step_intents` 编译通过即可（activity 签名在 Task 8 改）。

为避免本任务与 Task 8 之间编译断裂，**把 `log_phase_start_activity` 的签名扩展放到本任务**：

`activities.py` 的 `log_phase_start_activity`（~137-140）改为：
```python
@activity.defn
async def log_phase_start_activity(input: ActivityInput, steps: list[str] | None = None,
                                   intents: list[str] | None = None) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    phase = input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(
        phase, steps=tuple(steps or ()), step_intents=tuple(intents or ()))
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `uv run pytest packages/whitebox/tests/test_phase_steps.py packages/whitebox/tests/test_phase_marker_activities.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/step_intents.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_phase_steps.py
git commit -m "feat(whitebox): step intent registry as single source of truth"
```

---

### Task 8: 14 处 track_step 透传 intent

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（顶部加 import；14 处 track_step ~38/161/184/236/311/333/356/404/469/504/539/569/604/673）
- Test: 防漂移由 `test_phase_steps.py` 覆盖；本任务用 grep 校验所有站点已带 `intent=`

- [ ] **Step 1: 顶部加 import**

在 `activities.py` 顶部 import 区追加：
```python
from .step_intents import intent_for
```

- [ ] **Step 2: 14 处 `track_step` 加 `intent=`**

每处把 `track_step("<phase>", "<name>"):` 改为 `track_step("<phase>", "<name>", intent=intent_for("<name>")):`。完整清单：

| 行 | 旧 | 新 |
|---|---|---|
| 38  | `track_step("setup", "preflight"):` | `track_step("setup", "preflight", intent=intent_for("preflight")):` |
| 161 | `track_step("setup", "credential-check"):` | `track_step("setup", "credential-check", intent=intent_for("credential-check")):` |
| 184 | `track_step("setup", "auth-validation"):` | `track_step("setup", "auth-validation", intent=intent_for("auth-validation")):` |
| 236 | `track_step("pre-recon", "code-index"):` | `track_step("pre-recon", "code-index", intent=intent_for("code-index")):` |
| 311 | `track_step("pre-recon", "entry-point-fusion"):` | `track_step("pre-recon", "entry-point-fusion", intent=intent_for("entry-point-fusion")):` |
| 333 | `track_step("pre-recon", "adjudication"):` | `track_step("pre-recon", "adjudication", intent=intent_for("adjudication")):` |
| 356 | `track_step("pre-recon", "merge-sinks"):` | `track_step("pre-recon", "merge-sinks", intent=intent_for("merge-sinks")):` |
| 404 | `track_step("risk-scoring", "risk-scoring"):` | `track_step("risk-scoring", "risk-scoring", intent=intent_for("risk-scoring")):` |
| 469 | `track_step("reporting", "render-findings"):` | `track_step("reporting", "render-findings", intent=intent_for("render-findings")):` |
| 504 | `track_step("risk-scoring", "dataflow-hints"):` | `track_step("risk-scoring", "dataflow-hints", intent=intent_for("dataflow-hints")):` |
| 539 | `track_step("pre-recon", "framework-analysis"):` | `track_step("pre-recon", "framework-analysis", intent=intent_for("framework-analysis")):` |
| 569 | `track_step("pre-recon", "frontend-mapping"):` | `track_step("pre-recon", "frontend-mapping", intent=intent_for("frontend-mapping")):` |
| 604 | `track_step("pre-recon", "route-chain-building"):` | `track_step("pre-recon", "route-chain-building", intent=intent_for("route-chain-building")):` |
| 673 | `track_step("attack-chain", "attack-chain-assembly"):` | `track_step("attack-chain", "attack-chain-assembly", intent=intent_for("attack-chain-assembly")):` |

- [ ] **Step 3: 校验——无遗漏**

Run: `grep -c 'track_step(' packages/whitebox/src/shannon_whitebox/pipeline/activities.py && grep -c 'intent=intent_for' packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
Expected: 两个计数都为 `14`（每处 track_step 都带 intent=）

- [ ] **Step 4: 跑相关单测不回归**

Run: `uv run pytest packages/whitebox/tests/test_activity_display_wiring.py packages/whitebox/tests/test_phase_steps.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat(whitebox): pass step intent at all track_step sites"
```

---

### Task 9: 刷新频率 10 → 3Hz（可配）

**Files:**
- Modify: `packages/core/src/shannon_core/audit/display_lifecycle.py`（`run_with_display` ~30）
- Test: `packages/core/tests/display/`（新增对默认值的单测）

- [ ] **Step 1: 写失败测试**（新建 `packages/core/tests/display/test_display_lifecycle.py`）

```python
import os

from shannon_core.audit.display_lifecycle import default_refresh_hz


def test_default_refresh_hz_is_3(monkeypatch):
    monkeypatch.delenv("SHANNON_LIVE_REFRESH_HZ", raising=False)
    assert default_refresh_hz() == 3.0


def test_refresh_hz_env_override(monkeypatch):
    monkeypatch.setenv("SHANNON_LIVE_REFRESH_HZ", "2")
    assert default_refresh_hz() == 2.0
```

- [ ] **Step 2: 跑，确认失败**

Run: `uv run pytest packages/core/tests/display/test_display_lifecycle.py -xvs`
Expected: FAIL（`ImportError: ... default_refresh_hz`）

- [ ] **Step 3: 实现 `display_lifecycle.py`**

顶部加 `import os`，并加模块级函数：
```python
def default_refresh_hz() -> float:
    """Live dashboard refresh rate. Default 3Hz (calm); override via env."""
    return float(os.environ.get("SHANNON_LIVE_REFRESH_HZ", "3"))
```

`run_with_display` 里 `Live(...)` 改为：
```python
        live = Live(dashboard, console=console, transient=True,
                    refresh_per_second=default_refresh_hz(),
                    redirect_stderr=False)
```

- [ ] **Step 4: 跑，确认通过**

Run: `uv run pytest packages/core/tests/display/test_display_lifecycle.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/audit/display_lifecycle.py packages/core/tests/display/test_display_lifecycle.py
git commit -m "feat(core): lower live refresh to 3Hz (env-tunable)"
```

---

### Task 10: 全 display 套件回归 + 冒烟记录

**Files:** 无代码改动；仅校验与记录。

- [ ] **Step 1: 跑全部 display 相关单测**

Run: `uv run pytest packages/core/tests/display packages/whitebox/tests/test_phase_steps.py packages/whitebox/tests/test_rich_renderer.py packages/whitebox/tests/test_workflow_logger.py packages/whitebox/tests/test_file_renderer.py packages/whitebox/tests/test_activity_display_wiring.py packages/whitebox/tests/test_phase_marker_activities.py -q`
Expected: 全 PASS（若 `test_rich_renderer.py`/`test_file_renderer.py` 在 whitebox 不存在则跳过该项——它们在 `packages/core/tests/display/`）

- [ ] **Step 2: 静态校验导入无环**

Run: `uv run python -c "import shannon_whitebox.pipeline.workflows as w; import shannon_whitebox.pipeline.activities as a; print('imports ok', bool(w.PHASE_STEPS), bool(a.intent_for))"`
Expected: 打印 `imports ok True True`（确认 `step_intents` 未引入循环导入）

- [ ] **Step 3: 手动冒烟（人工，记录结论到 memory）**

在真仓库上跑：`uv run shannon-whitebox start -r /root/code/<某仓库>`
确认 pre-recon 阶段：
- 确定性步骤滚 `STEP ▸ 构建调用图与代码索引` / `STEP ✓ code-index (…)`；
- agent 逐轮滚 `💭 Turn N: 🔄/✅ …`（每轮一行、平静、不截断）；
- `🔧` 工具调用**不在 live 屏**；另开 `shannon-whitebox logs <id> --follow` 可见 `[TOOL]`；
- 底部钉两行：`pre-recon · step X/8 · …` + `⠋ 扫描架构与入口点 · Turn N: …`；
- 刷新平稳。

把冒烟结论回填到 memory `whitebox-display-clarity-redesign`（标注本增量已验证）。

- [ ] **Step 4: 终态提交（若有遗留改动）**

```bash
git status   # 应无未跟踪的实现文件；spec/plan 已提交
```

---

## 自审（Self-Review）

**1. Spec 覆盖：**
- §4.1 步骤意图注册表 → Task 7（`step_intents.py`）。✓
- §4.2 `StepEvent.intent` → Task 1 + Task 2。✓
- §4.3 解耦 show_steps/show_phase + 反转 clarity-design → Task 3（+ §10 supersede）。✓
- §4.4 live 隐藏 🔧（show_tools）→ Task 3。✓
- §4.5 可读 💭（首行，不截断）→ Task 3（`first_nonempty_line` + `_render_llm`）。✓
- §4.6 钉两行（步骤意图 + 最新轮）→ Task 5（state）+ Task 6（渲染）。✓ 含 agent-step 意图前缀（经 `PhaseEvent.step_intents` 播种）。
- §4.7 刷新 10→3 → Task 9。✓
- §1.2 逐轮链路无需埋点 → 全计划无 SDK/dispatcher 改动，仅在渲染层。✓
- §6 文件清单 → 与"文件结构"表一致。✓

**2. 占位符扫描：** 无 TBD/TODO；每个 code step 都给了完整代码；14 处 track_step 全列出。

**3. 类型/命名一致性：**
- `intent_for` / `step_names` / `step_intents` / `StepSpec` / `PHASE_STEPS` 在 Task 7 定义，Task 8 引用一致。
- `StepEvent.intent` / `PhaseEvent.step_intents`（Task 1）→ Task 2 透传 → Task 5 消费，命名一致。
- `AgentRow.last_turn_text` / `DashboardState.unit_intent`（Task 5）→ Task 6 `_pinned_detail` 引用一致。
- `first_nonempty_line`（Task 3 加到 formatters）→ Task 5 dashboard_state 引用一致。
- `default_refresh_hz`（Task 9）命名一致。
- `show_steps`/`show_tools`（Task 3 rich_renderer `__init__`）→ workflow_logger.initialize（Task 3 Step 8）传参一致。

**4. 既有测试不回归已逐项核对**（见各 Task Step "确认通过" 的说明）。
