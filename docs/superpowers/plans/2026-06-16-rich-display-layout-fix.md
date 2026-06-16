# Rich 实时展示排版修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 rich（TTY）模式下扫描日志的排版错乱——去掉 phase 重复滚动行、把底部 dashboard 收成单条对齐的状态行、结束时 summary 干净落地。

**Architecture:** 保留现有 `DisplayEvent → DisplayDispatcher → (FileLogRenderer + RichConsoleRenderer + LiveDashboardRenderer)` 分层与不可变 `DashboardState`，只改三处：`RichConsoleRenderer` 加 `show_phase` 开关并由 `WorkflowLogger` 在 rich 模式传 `False`；`LiveDashboardRenderer._render` 由「全宽 grid + 多行 agent 表 + 写死 60 分隔线」改为「`options.max_width` 全宽 dim 分隔线 + 单行状态」；`Live(transient=True)`。改动集中在 `shannon_core`，whitebox/blackbox 经 display_lifecycle 委托自动受益。

**Tech Stack:** Python 3.12、uv workspace、Rich（`Live`/`Spinner`/`Table.grid`/`Group`/`Text`）、pytest + pytest-asyncio（`asyncio_mode=auto`，`--import-mode=importlib`）。

**关联 spec:** [`docs/superpowers/specs/2026-06-16-rich-display-layout-fix-design.md`](../specs/2026-06-16-rich-display-layout-fix-design.md)

> 分支：当前在 `feat/fork-py`（非 main），可直接在此分支实现；如需隔离可用 `using-git-worktrees` 另开 worktree，非必须。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `packages/core/src/shannon_core/display/rich_renderer.py` | 滚动日志行渲染（PHASE/AGENT/TOOL/LLM/ERROR/SUMMARY） | 新增 `show_phase` 开关；`PhaseEvent` 受其门控 |
| `packages/core/src/shannon_core/display/live_dashboard.py` | 底部 live 区域渲染（`__rich_console__`） | 重写 `_render` 为单状态行 + 全宽分隔线；删 `_agent_line` |
| `packages/core/src/shannon_core/audit/workflow_logger.py` | 组装 dispatcher + renderers | 构造 `RichConsoleRenderer` 传 `show_phase=not use_rich` |
| `packages/core/src/shannon_core/audit/display_lifecycle.py` | 建 Console/Live、yield session | `Live(transient=True)` |
| `packages/core/tests/display/test_rich_renderer.py` | rich_renderer 单测 | 新增 `show_phase=False` 抑制用例 |
| `packages/core/tests/display/test_live_dashboard.py` | dashboard 单测 | 重写为单状态行断言 |
| `packages/core/tests/audit/test_display_lifecycle.py`（新建） | lifecycle 接线单测 | 新增 `transient=True` 守卫 |
| `packages/whitebox/tests/test_display_integration.py` | whitebox L2 管线集成 | dashboard 断言去 "Phase: " 前缀；加 phase 不滚动断言 |
| `packages/blackbox/tests/test_display_integration.py` | blackbox L2 管线集成 | 同上 |

`DashboardState`（纯数据状态机）**不改**——字段已满足新状态行。

---

## Task 1: RichConsoleRenderer 加 show_phase 开关

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py:26-28`（`__init__`）、`:34-42`（`render` 的 match）
- Test: `packages/core/tests/display/test_rich_renderer.py`

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/display/test_rich_renderer.py` 末尾追加：

```python
async def test_phase_suppressed_when_show_phase_false():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    renderer = RichConsoleRenderer(console, show_phase=False)
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="reconnaissance", event="start"))
    out = console.export_text()
    assert "PHASE" not in out
    assert "reconnaissance" not in out


async def test_phase_rendered_by_default():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    renderer = RichConsoleRenderer(console)  # show_phase defaults to True
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="reconnaissance", event="start"))
    out = console.export_text()
    assert "reconnaissance" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py::test_phase_suppressed_when_show_phase_false -v`
Expected: FAIL —— `RichConsoleRenderer(console, show_phase=False)` 报 `unexpected keyword argument`（开关尚未存在）。

- [ ] **Step 3: 最小实现**

修改 `packages/core/src/shannon_core/display/rich_renderer.py` 的 `__init__`（约 26-28 行）：

```python
    def __init__(self, console: Console | None = None, show_phase: bool = True) -> None:
        self._console = console or Console()
        self._show_phase = show_phase
```

修改 `render` 内的 match（约 34-42 行），把 `case PhaseEvent(): self._render_phase(event)` 改为：

```python
            case PhaseEvent():
                if self._show_phase:
                    self._render_phase(event)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS（含新两例与既有全部）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "fix(display): add show_phase switch to RichConsoleRenderer"
```

---

## Task 2: LiveDashboardRenderer 收成单状态行

**Files:**
- Modify: `packages/core/src/shannon_core/display/live_dashboard.py`（imports、`__rich_console__`、`_render`；删除 `_agent_line` 与 `_DONE`/`_FAILED`）
- Test: 重写 `packages/core/tests/display/test_live_dashboard.py`

> 注意：本任务完成后，whitebox/blackbox 的 `test_display_integration.py` 会因 dashboard 不再输出 `"Phase: <name>"` 前缀而转红——这是预期的，**Task 3 立即修复**。两个任务连续执行即可，无需中途保持全绿。

- [ ] **Step 1: 重写失败测试**

用以下内容**整体替换** `packages/core/tests/display/test_live_dashboard.py`：

```python
import io

from rich.console import Console

from shannon_core.display.events import PhaseEvent, AgentEvent
from shannon_core.display.live_dashboard import LiveDashboardRenderer


def _console(width: int = 100) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=width, force_terminal=True,
                   color_system=None, force_interactive=True), buf


async def test_render_folds_event_into_snapshot():
    console, _ = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    assert r.snapshot.current_phase == "recon"


async def test_status_line_shows_phase_counts_cost_and_running_agent():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="vulnerability-analysis", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="injection-vuln",
                              event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "vulnerability-analysis" in out   # phase in status line
    assert "0 done" in out                   # completed count (agent running, not done)
    assert "$0.0000" in out                  # accumulated cost
    assert "injection-vuln" in out           # running agent appended with spinner


async def test_separator_spans_full_console_width():
    console, buf = _console(width=80)
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    console.print(r)
    out = buf.getvalue()
    assert out.count("─") == 80              # rule width tracks options.max_width, not hardcoded


async def test_done_agent_increments_count_and_leaves_status_line():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="end",
                              attempt=1, duration_ms=4500, cost_usd=0.23, success=True))
    console.print(r)
    out = buf.getvalue()
    assert "1 done" in out                   # completed_count incremented
    assert "$0.2300" in out                  # cost accumulated into status line
    assert "auth-vuln" not in out            # done agent no longer "running" -> not in status line
    assert "4.5s" not in out                 # per-agent duration NOT shown in dashboard
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -v`
Expected: FAIL —— `test_separator_spans_full_console_width`（旧分隔线写死 60 ≠ 80）、`test_done_agent_increments_count_and_leaves_status_line`（旧 `_agent_line` 仍打印 `auth-vuln` 与 `4.5s`）。其余两例可能仍过（作为回归守卫）。

- [ ] **Step 3: 实现——整体替换 `live_dashboard.py`**

用以下内容**整体替换** `packages/core/src/shannon_core/display/live_dashboard.py`：

```python
"""LiveDashboardRenderer — bottom status-line renderer for the live scan.

Dual role:
  * dispatcher Renderer: async render(event) folds the event into a new
    immutable DashboardState snapshot via atomic reference swap.
  * Rich renderable: __rich_console__ builds a single compact status line from
    the latest snapshot + live elapsed. Rich's Live refresh thread re-invokes
    __rich_console__ each tick, so the status line animates between events
    (spinner frames, ticking elapsed) without any per-event update call.

The status line carries: phase · completed-count · elapsed · cost, with the
currently-running agent(s) + spinner appended. A full-width dim rule sits
above it to separate it from the scrolling log region. This replaces the former
expand-to-width multi-row agent table (which stretched short tokens into big
gaps) and the hardcoded 60-char separator (which never matched terminal width).

Concurrency: _snapshot is mutated only on the event-loop thread (under the
dispatcher's lock) via atomic assignment; the Live refresh thread reads it.
GIL makes the reference swap atomic, so the refresh thread always sees a
complete snapshot.
"""
from __future__ import annotations

import time

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from shannon_core.display.dashboard_state import DashboardState
from shannon_core.display.events import DisplayEvent
from shannon_core.display.formatters import format_duration


class LiveDashboardRenderer:
    def __init__(self, console: Console) -> None:
        self._console = console
        self._snapshot: DashboardState = DashboardState()
        self._start_monotonic: float = time.monotonic()

    @property
    def snapshot(self) -> DashboardState:
        return self._snapshot

    async def render(self, event: DisplayEvent) -> None:
        self._snapshot = self._snapshot.apply(event)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self._render(options)

    def _render(self, options: ConsoleOptions) -> Group:
        snap = self._snapshot
        elapsed = format_duration(int(time.monotonic() - self._start_monotonic) * 1000)
        running = [r for r in snap.agents.values() if r.status == "running"]

        cells: list = [
            Text(snap.current_phase or "—", style="bold cyan"),
            Text(f" · {snap.completed_count} done", style="green"),
            Text(f" · {elapsed}"),
            Text(f" · ${snap.total_cost:.4f}", style="yellow"),
        ]
        if running:
            cells += [Text("    "), Spinner("dots"),
                      Text(" " + " · ".join(r.name for r in running), style="blue")]

        row = Table.grid()  # expand=False: cells take natural width, no big gaps
        row.add_row(*cells)

        return Group(
            Text("─" * options.max_width, style="dim"),  # spans real terminal width
            row,
        )
```

相对旧文件，删除了 `_agent_line`、模块常量 `_DONE`/`_FAILED`，以及不再使用的 import `AgentRow`、`agent_prefix`；新增 `Group`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -v`
Expected: PASS（4 例全绿）。

- [ ] **Step 5: 提交（core 显示层全绿；CLI 集成测试暂红，见 Task 3）**

```bash
git add packages/core/src/shannon_core/display/live_dashboard.py packages/core/tests/display/test_live_dashboard.py
git commit -m "fix(display): collapse live dashboard to single status line + full-width rule"
```

---

## Task 3: rich 模式抑制 phase 滚动行 + 修复两个 CLI 集成测试

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py:46-49`
- Modify: `packages/whitebox/tests/test_display_integration.py`（dashboard 断言 + phase 不滚动断言）
- Modify: `packages/blackbox/tests/test_display_integration.py`（同上）

- [ ] **Step 1: 更新 whitebox 集成测试断言**

在 `packages/whitebox/tests/test_display_integration.py` 中，把末尾两行 dashboard 断言（约 47-51 行）：

```python
    # Dashboard region (rendered via console.print(dashboard) -> __rich_console__).
    # "Phase:" + "1 done" are dashboard-only tokens the scrolling log never emits, so
    # their presence proves the LiveDashboardRenderer snapshot reached the buffer too.
    assert "Phase: vulnerability-analysis" in out               # dashboard phase line
    assert "1 done" in out                                      # completed_count
```

替换为：

```python
    # Phase is suppressed from the scrolling log in rich mode (it lives in the
    # dashboard status line only). color_system=None would strip "[bold cyan]PHASE[/]"
    # to bare "PHASE"; its absence proves show_phase=False is wired through the
    # WorkflowLogger. The phase name still reaches the buffer via the dashboard.
    assert "PHASE" not in out                                   # no scrolling phase line
    assert "Starting vulnerability-analysis" not in out
    # Dashboard status line carries the phase name + completed count.
    assert "vulnerability-analysis" in out                      # phase in status line
    assert "1 done" in out                                      # completed_count
```

- [ ] **Step 2: 更新 blackbox 集成测试断言**

在 `packages/blackbox/tests/test_display_integration.py` 中，把末尾两行 dashboard 断言（约 56-60 行）：

```python
    assert "Phase: exploitation" in out                               # dashboard phase line
    assert "1 done" in out                                            # completed_count
```

替换为：

```python
    assert "PHASE" not in out                                         # no scrolling phase line
    assert "Starting exploitation" not in out
    assert "exploitation" in out                                      # phase in status line
    assert "1 done" in out                                            # completed_count
```

- [ ] **Step 3: 跑两个集成测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_display_integration.py packages/blackbox/tests/test_display_integration.py -v`
Expected: FAIL —— `assert "PHASE" not in out` 失败：`RichConsoleRenderer` 仍以默认 `show_phase=True` 打印 phase 滚动行（接线尚未传 `show_phase=False`）。

- [ ] **Step 4: 实现接线**

修改 `packages/core/src/shannon_core/audit/workflow_logger.py` 的 `initialize`（约 46-49 行），把：

```python
        renderers: list = [FileLogRenderer(self._stream)]
        if self._console is not None:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(self._console))
```

改为：

```python
        renderers: list = [FileLogRenderer(self._stream)]
        if self._console is not None:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(self._console, show_phase=not self._use_rich))
```

> 语义：rich 模式（`use_rich=True`）→ `show_phase=False`（phase 只进状态行）；非 rich/管道/CI（`use_rich=False`）→ `show_phase=True`（无状态行，phase 仍逐行流式）。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_display_integration.py packages/blackbox/tests/test_display_integration.py packages/core/tests/display -v`
Expected: PASS（两个 CLI 集成测试 + core 显示层全绿）。

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/whitebox/tests/test_display_integration.py packages/blackbox/tests/test_display_integration.py
git commit -m "fix(display): suppress scrolling phase line in rich mode (show_phase=not use_rich)"
```

---

## Task 4: Live(transient=True) —— summary 干净落地

**Files:**
- Modify: `packages/core/src/shannon_core/audit/display_lifecycle.py:24`
- Test: 新建 `packages/core/tests/audit/test_display_lifecycle.py`

- [ ] **Step 1: 写失败测试**

新建 `packages/core/tests/audit/test_display_lifecycle.py`：

```python
from unittest import mock

from shannon_core.audit.display_lifecycle import run_with_display
from shannon_core.models.metrics import SessionMetadata


async def test_rich_mode_constructs_transient_live(tmp_path):
    """Live must be transient so the status line is erased on exit and the
    SummaryEvent (printed above the live region) is the final visible output."""
    meta = SessionMetadata(id="x", web_url=None, output_path=str(tmp_path))
    with mock.patch("rich.live.Live") as live_cls, \
         mock.patch("shannon_core.audit.display_lifecycle.AuditSession") as session_cls:
        session_cls.return_value.initialize = mock.AsyncMock()
        session_cls.return_value.close = mock.AsyncMock()
        async with run_with_display(meta, use_rich=True) as session:
            assert session is session_cls.return_value
    assert live_cls.called
    assert live_cls.call_args.kwargs.get("transient") is True


async def test_non_rich_mode_does_not_construct_live(tmp_path):
    meta = SessionMetadata(id="x", web_url=None, output_path=str(tmp_path))
    with mock.patch("rich.live.Live") as live_cls, \
         mock.patch("shannon_core.audit.display_lifecycle.AuditSession") as session_cls:
        session_cls.return_value.initialize = mock.AsyncMock()
        session_cls.return_value.close = mock.AsyncMock()
        async with run_with_display(meta, use_rich=False):
            pass
    assert not live_cls.called
```

> 用 mock 替换 `AuditSession`（避免真实文件 I/O）与 `rich.live.Live`（`display_lifecycle` 内 `from rich.live import Live` 是函数内局部 import，patch `rich.live.Live` 即可拦截），断言 `transient` kwargs。`run_with_display` 还会真实构造 `Console` 与 `LiveDashboardRenderer`，无副作用。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/audit/test_display_lifecycle.py::test_rich_mode_constructs_transient_live -v`
Expected: FAIL —— `transient` 当前为 `False`，断言 `is True` 不成立。（第二条 `test_non_rich_mode_does_not_construct_live` 应直接通过。）

- [ ] **Step 3: 实现**

修改 `packages/core/src/shannon_core/audit/display_lifecycle.py:24`，把：

```python
        live = Live(dashboard, console=console, transient=False, refresh_per_second=10)
```

改为：

```python
        live = Live(dashboard, console=console, transient=True, refresh_per_second=10)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/audit/test_display_lifecycle.py -v`
Expected: PASS（2 例全绿）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/audit/display_lifecycle.py packages/core/tests/audit/test_display_lifecycle.py
git commit -m "fix(display): Live transient=True so summary lands clean"
```

---

## Task 5: 变更路径全量回归 + 人工冒烟

**Files:** 无（仅验证）

- [ ] **Step 1: 跑全部受影响测试**

Run:
```bash
uv run pytest \
  packages/core/tests/display \
  packages/core/tests/audit \
  packages/whitebox/tests/test_display_integration.py \
  packages/blackbox/tests/test_display_integration.py \
  -v
```
Expected: 全部 PASS。

- [ ] **Step 2: 跑 lint（ruff，line-length 120）**

Run: `uv run ruff check packages/core/src/shannon_core/display packages/core/src/shannon_core/audit`
Expected: 无错误（确认删除 `_agent_line` 后无遗留未使用 import：`agent_prefix`、`AgentRow` 已随重写移除）。

- [ ] **Step 3: 人工冒烟（需要 Temporal + 模型 key）**

Run: `uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat`

观察并核对：
1. 顶部 banner（`Shannon Pentest` Panel）正常。
2. 运行中：tool（`🔧`）/ llm（`💭`）/ agent start·end（`AGENT ▶/✓`）在上方滚动；**没有** `[ts] PHASE ...` 滚动行。
3. 底部钉一条状态行：`<phase> · N done · <elapsed> · $<cost>    ⠋ <running agent>`，上方有一条与终端等宽的 dim `─` 分隔线；分隔线与状态行不再有大片空白、宽度与终端对齐。
4. 结束：状态行被擦除，最终可见为 summary（`Workflow COMPLETED/FAILED` Panel + per-agent 表），其下无残留状态行。
5. 非 TTY（`... | cat`）：回到逐行流式（含 `PHASE` 行），无 dashboard。

> 若本机无 Temporal/key，Step 3 可跳过并在 PR 描述注明；自动化测试（Step 1-2）已覆盖行为契约。冒烟主要核对视觉收尾（transient 擦除、分隔线对齐）。

- [ ] **Step 4: （可选）更新或确认无需更新用户文档**

检查仓库是否有 CLI 输出截图/说明文档引用旧 dashboard 样式；若有则同步。无则跳过。

---

## Self-Review

**1. Spec 覆盖**

- §5.1 去重策略（rich 下抑制 PhaseEvent 滚动）→ Task 1（开关）+ Task 3（接线 `show_phase=not use_rich`）。✓
- §5.2 状态行渲染（`expand=False` + `options.max_width` 分隔线 + 删 `_agent_line`）→ Task 2。✓
- §5.3 `transient=True` + `RichConsoleRenderer(show_phase=not use_rich)` 接线 → Task 3（接线）+ Task 4（transient）。✓
- §5.4 改动文件清单 → 全部 5 任务覆盖（live_dashboard / rich_renderer / workflow_logger / display_lifecycle + 4 类测试）。✓
- §6 假设（错误滚动、并发 agent ` · ` 连接、非 rich 不变、取消兼容）→ 并发 agent 连接在 Task 2 代码内体现；非 rich 行为由 Task 3 接线 `not use_rich` 保证；错误/取消未改既有路径，无需新任务。✓
- §7 测试策略 → Task 1-4 各自 TDD + Task 5 全量回归。✓

**2. 占位符扫描**：无 TBD/TODO；每个代码步均含完整可运行代码；测试断言均具体。✓

**3. 类型/命名一致性**：
- `RichConsoleRenderer(console, show_phase=...)` —— Task 1 定义、Task 3 调用，签名一致。✓
- `_render(self, options: ConsoleOptions) -> Group` 与 `__rich_console__` 的 `yield self._render(options)` —— Task 2 内自洽。✓
- `show_phase=not self._use_rich` —— `WorkflowLogger._use_rich` 在 `__init__`（workflow_logger.py:36）已存在。✓
- `format_duration` 来自 `shannon_core.display.formatters`（已在该模块 import 列表）。✓
- `Group` 自 `rich.console` 导入（Rich 标准导出位置）。✓

无规格遗漏、无占位符、命名一致。
