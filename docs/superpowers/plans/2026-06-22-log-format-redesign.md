# 日志格式重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 Rich 终端 PHASE / STEP / AGENT 三类日志行的格式——分隔线动态对齐、STEP 文字统一中文 intent + ○/✓/✗ 符号、AGENT end 行补时间戳与符号、状态符号收口到单一常量来源。

**Architecture:** 新建 `display/symbols.py` 作为状态符号单一来源；`display/formatters.py` 新增 `pad_rule()` 用 rich `cell_len` 按显示宽度对齐分隔线；改动集中在 `rich_renderer.py` 的 `_render_phase`/`_render_step`/`_render_agent`/`_render_summary` 四个方法 + `file_renderer.py::_summary` 的符号 import。不改 `DisplayEvent` 数据结构、dispatcher、`workflow.log` 正文、emoji 行。

**Tech Stack:** Python 3.13、rich>=13.7.0（`rich.cells.cell_len` 已确认可用）、pytest（async 测试 auto mode，无 decorator）。

## Global Constraints

- **Python 3.13**（cpython-313），包结构 `packages/core/src/shannon_core/...`。
- **rich>=13.7.0**，`from rich.cells import cell_len` 可用（实测 `cell_len("预检")==4`）。
- **pytest async**：renderer 测试用 `async def test_` 无 decorator；纯函数测试用普通 `def test_`。
- **测试范围**：只跑 display 相关子集，**严禁跑全套**（全套 hang 在 Temporal/网络慢测试）。命令固定用 `-k` 或指定路径限定。
- **不得改动**：`DisplayEvent` 数据结构、`DisplayDispatcher`、`workflow.log` 文件日志正文、🔧/💭/✅/🔄 emoji 行。
- **符号单一来源**：所有 STEP/AGENT/summary 状态符号必须从 `display/symbols.py` import，`rich_renderer.py` / `file_renderer.py` 内不得残留散落符号字面量。
- **不改 file_renderer 正文**：`_step` 保持现状（slug 给 grep），只改 `_summary` 的 mark 改 import。

---

## File Structure

| 文件 | 职责 | 本计划动作 |
|---|---|---|
| `packages/core/src/shannon_core/display/symbols.py` | 状态符号单一来源 | **新建** |
| `packages/core/src/shannon_core/display/formatters.py` | 格式化工具 | 新增 `PHASE_RULE_WIDTH` + `pad_rule()` |
| `packages/core/src/shannon_core/display/rich_renderer.py` | Rich 终端渲染 | 改 `_render_phase`/`_render_step`/`_render_agent`/`_render_summary` |
| `packages/core/src/shannon_core/display/file_renderer.py` | workflow.log 渲染 | 改 `_summary` 的 mark import |
| `packages/core/tests/display/test_symbols.py` | 符号常量测试 | **新建** |
| `packages/core/tests/display/test_formatters.py` | formatter 测试 | 追加 `pad_rule` 测试 |
| `packages/core/tests/display/test_rich_renderer.py` | renderer 测试 | 更新 + 追加三类行测试 |

---

## Task 1: 新建符号常量表 symbols.py

**Files:**
- Create: `packages/core/src/shannon_core/display/symbols.py`
- Test: `packages/core/tests/display/test_symbols.py`

**Interfaces:**
- Consumes: 无
- Produces: 常量 `STEP_PENDING`、`STEP_DONE`、`STEP_FAIL`、`AGENT_START`、`AGENT_DONE`、`AGENT_FAIL`、`SUMMARY_OK`、`SUMMARY_FAIL`（均为 `str`）。Task 3/4/5/6 import 这些名字。

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/display/test_symbols.py`:

```python
from shannon_core.display.symbols import (
    STEP_PENDING, STEP_DONE, STEP_FAIL,
    AGENT_START, AGENT_DONE, AGENT_FAIL,
    SUMMARY_OK, SUMMARY_FAIL,
)


def test_step_symbols():
    assert STEP_PENDING == "○"
    assert STEP_DONE == "✓"
    assert STEP_FAIL == "✗"


def test_agent_symbols():
    assert AGENT_START == "▶"
    assert AGENT_DONE == "✓"
    assert AGENT_FAIL == "✗"


def test_summary_symbols():
    assert SUMMARY_OK == "✓"
    assert SUMMARY_FAIL == "✗"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/display/test_symbols.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.display.symbols'`

- [ ] **Step 3: 写实现**

Create `packages/core/src/shannon_core/display/symbols.py`:

```python
"""Display status symbols — single source of truth for all renderers.

STEP / AGENT / summary 的状态符号集中在此，避免字面量散落在
rich_renderer.py / file_renderer.py 多处导致不一致。
"""
from __future__ import annotations

STEP_PENDING = "○"
STEP_DONE = "✓"
STEP_FAIL = "✗"

AGENT_START = "▶"
AGENT_DONE = "✓"
AGENT_FAIL = "✗"

SUMMARY_OK = "✓"
SUMMARY_FAIL = "✗"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && python -m pytest tests/display/test_symbols.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/symbols.py packages/core/tests/display/test_symbols.py
git commit -m "feat(display): 新建 symbols.py 状态符号单一来源"
```

---

## Task 2: 分隔线对齐 pad_rule()

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`（文件末尾追加）
- Test: `packages/core/tests/display/test_formatters.py`（文件末尾追加）

**Interfaces:**
- Consumes: `rich.cells.cell_len`
- Produces: `PHASE_RULE_WIDTH: int = 36`；`pad_rule(text: str, col: int = PHASE_RULE_WIDTH) -> str`。Task 3 import `pad_rule`。

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/display/test_formatters.py` 末尾追加：

```python
from shannon_core.display.formatters import pad_rule, PHASE_RULE_WIDTH


def test_pad_rule_constant_exists():
    assert PHASE_RULE_WIDTH == 36


def test_pad_rule_ascii():
    # cell_len("Starting setup") == 14 -> 36 - 14 = 22 个 ─
    result = pad_rule("Starting setup")
    assert result.startswith("Starting setup ")
    assert result.count("─") == 22


def test_pad_rule_cjk_counts_double_width():
    # cell_len("预检") == 4（中文双宽）-> 36 - 4 = 32 个 ─
    assert pad_rule("预检").count("─") == 32


def test_pad_rule_overflow_floors_at_two():
    # 文字超长时兜底至少 2 个 ─
    assert pad_rule("a" * 40).count("─") == 2


def test_pad_rule_same_col_aligns_right_edge():
    # 同一 col 调用，显示宽度恒定 -> 右端对齐
    from rich.cells import cell_len
    a = pad_rule("Starting setup")
    b = pad_rule("Completed pre-recon")
    assert cell_len(a) == cell_len(b)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/display/test_formatters.py -v -k pad_rule`
Expected: FAIL — `ImportError: cannot import name 'pad_rule'`

- [ ] **Step 3: 写实现**

在 `packages/core/src/shannon_core/display/formatters.py` 顶部 import 区追加（紧跟现有 `from urllib.parse import urlparse` 之后）：

```python
from rich.cells import cell_len
```

在文件末尾追加：

```python
PHASE_RULE_WIDTH = 36  # PHASE 行 text + 分隔线的目标显示列宽


def pad_rule(text: str, col: int = PHASE_RULE_WIDTH) -> str:
    """在 text 右侧填充 ─，使同一 col 下所有调用的显示宽度恒定（右端对齐）。

    用 cell_len 按显示宽度计算（中文 intent 算 2 列）。文字超长时兜底至少 2 个 ─。
    """
    width = cell_len(text)
    n = max(2, col - width)
    return f"{text} {'─' * n}"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && python -m pytest tests/display/test_formatters.py -v -k pad_rule`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py
git commit -m "feat(display): 新增 pad_rule 按 cell 宽度对齐分隔线"
```

---

## Task 3: PHASE 行分隔线对齐

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（`_render_phase` 方法，当前在 `:92-97`）
- Test: `packages/core/tests/display/test_rich_renderer.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `pad_rule`
- Produces: PHASE 行输出 `[{timestamp}] PHASE  {verb} {phase} {─对齐}`

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/display/test_rich_renderer.py` 末尾追加：

```python
async def test_phase_rule_right_edges_align_across_phases():
    from rich.cells import cell_len
    renderer, _ = _renderer_with_capture()
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="setup", event="start"))
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="pre-recon", event="start"))
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="pre-recon", event="complete"))
    out = renderer._console.export_text()
    lines = [ln for ln in out.splitlines() if "PHASE" in ln]
    assert len(lines) == 3
    # 三行右端对齐：显示宽度相等
    widths = {cell_len(ln) for ln in lines}
    assert len(widths) == 1, f"phase 行未对齐: {lines}"
    # 横线存在且非固定 20
    assert all("─" in ln for ln in lines)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py -v -k phase_rule`
Expected: FAIL — 当前 `_render_phase` 用固定 `'─' * 20`，三行显示宽度不等，`len(widths) == 1` 断言失败。

- [ ] **Step 3: 写实现**

修改 `packages/core/src/shannon_core/display/rich_renderer.py`：

(a) 在文件顶部 `from shannon_core.display.formatters import (...)` 块中加入 `pad_rule`。修改后的 import 块：

```python
from shannon_core.display.formatters import (
    agent_prefix, format_duration, format_error_block, humanize_tool_call,
    first_nonempty_line, pad_rule,
)
```

(b) 替换 `_render_phase` 方法整体（当前 `:92-97`）：

```python
    def _render_phase(self, e) -> None:
        verb = "Starting" if e.event == "start" else "Completed"
        body = pad_rule(f"{verb} {e.phase}")
        self._console.print(
            f"[{e.timestamp}] [bold cyan]PHASE[/]  {body}",
            highlight=False,
        )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py -v -k "phase"`
Expected: PASS（含新 `phase_rule_right_edges_align_across_phases` 与既有 `test_phase_start_renders_phase_name`）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): PHASE 行分隔线按 cell 宽度右端对齐"
```

---

## Task 4: STEP 行 ○/✓/✗ + 中文 intent 统一

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（`_render_step` 方法，当前在 `:78-90`）
- Modify: `packages/core/tests/display/test_rich_renderer.py`（更新 2 个既有测试 + 追加 2 个）

**Interfaces:**
- Consumes: Task 1 的 `STEP_PENDING`/`STEP_DONE`/`STEP_FAIL`
- Produces: STEP 三态行
  - start: `[{ts}] STEP  ○ {intent or name}`
  - complete: `[{ts}] STEP  ✓ {intent or name}  {dur}`
  - fail: `[{ts}] STEP  ✗ {intent or name}  — {error}`

- [ ] **Step 1: 写失败测试**

(a) 在 `packages/core/tests/display/test_rich_renderer.py` 末尾追加：

```python
async def test_step_start_uses_pending_circle_symbol():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start",
                                    intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "○" in out
    assert "构建调用图与代码索引" in out
    assert "▸" not in out  # 旧符号退出


async def test_step_complete_uses_done_check_and_intent():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete", duration_ms=12000,
                                    intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "✓" in out
    assert "构建调用图与代码索引" in out
    assert "12.0s" in out
    assert "code-index" not in out  # 英文 slug 退出终端（intent 优先）


async def test_step_fail_uses_cross_and_error():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete",
                                    error="索引构建超时", intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "✗" in out
    assert "✓" not in out  # 失败不再误用 ✓
    assert "构建调用图与代码索引" in out
    assert "索引构建超时" in out
```

(b) 更新既有 `test_step_complete_renders_slug_and_duration`（当前文件 `:173-180`）——无 intent 时 fallback name，符号改 ✓。替换为：

```python
async def test_step_complete_renders_slug_and_duration():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete", duration_ms=12000))
    out = renderer._console.export_text()
    assert "code-index" in out  # 无 intent 时 fallback 到 slug
    assert "12.0s" in out
    assert "✓" in out
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py -v -k "step_start_uses or step_complete_uses or step_fail_uses or step_complete_renders_slug"`
Expected: FAIL — 新测试因 `▸` 仍在、完成行用 `e.name`、失败行用 `✓` 而失败。

- [ ] **Step 3: 写实现**

修改 `packages/core/src/shannon_core/display/rich_renderer.py`：

(a) 在顶部新增 import 行（紧跟现有 formatters import 块之后）：

```python
from shannon_core.display.symbols import (
    AGENT_DONE, AGENT_FAIL, AGENT_START,
    STEP_DONE, STEP_FAIL, STEP_PENDING,
    SUMMARY_OK, SUMMARY_FAIL,
)
```

（`AGENT_*` / `SUMMARY_*` 一并 import，供 Task 5/6 使用，避免重复改 import 块。）

(b) 替换 `_render_step` 方法整体（当前 `:78-90`）：

```python
    def _render_step(self, e) -> None:
        label = e.intent or e.name
        if e.event == "start":
            self._console.print(
                f"[{e.timestamp}] [cyan]STEP[/]  {STEP_PENDING} {label}", highlight=False)
            return
        if e.error:
            self._console.print(
                f"[{e.timestamp}] [cyan]STEP[/]  {STEP_FAIL} {label}  — {e.error}",
                highlight=False)
            return
        suffix = f"  {format_duration(e.duration_ms)}" if e.duration_ms is not None else ""
        self._console.print(
            f"[{e.timestamp}] [cyan]STEP[/]  {STEP_DONE} {label}{suffix}", highlight=False)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py -v -k step`
Expected: PASS（含 4 个 complete/start/fail 测试 + 既有 `test_step_event_renders_step_line`/`test_step_start_renders_intent_when_present`）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): STEP 行 ○/✓/✗ 符号 + 完成态统一中文 intent；修失败态误用 ✓"
```

---

## Task 5: AGENT end 行补时间戳与 ✓/✗ 符号

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（`_render_agent` 方法，当前在 `:105-121`）
- Modify: `packages/core/tests/display/test_rich_renderer.py`（更新 1 个既有测试 + 追加 1 个）

**Interfaces:**
- Consumes: Task 1 的 `AGENT_START`/`AGENT_DONE`/`AGENT_FAIL`（已在 Task 4 import）
- Produces: AGENT 三态行
  - start: `[{ts}] AGENT  ▶ {title} started (attempt N)`
  - end success: `[{ts}] AGENT  ✓ {title} Completed (dur, $cost)`
  - end fail: `[{ts}] AGENT  ✗ {title} failed (dur) — {error}`

- [ ] **Step 1: 写失败测试**

(a) 更新既有 `test_agent_end_completed_shows_metrics`（当前 `:74-82`）——追加时间戳与 ✓ 断言。替换为：

```python
async def test_agent_end_completed_shows_metrics():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    out = renderer._console.export_text()
    assert "Completed" in out
    assert "5.2s" in out
    assert "0.15" in out
    assert "✓" in out       # 成功符号
    assert "AGENT" in out   # 带 AGENT 标签前缀（与 start 行一致）
    assert "[t]" in out     # 补时间戳前缀
```

(b) 在文件末尾追加失败态测试：

```python
async def test_agent_end_failed_shows_cross_timestamp_and_error():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, success=False, error="boom"))
    out = renderer._console.export_text()
    assert "✗" in out
    assert "failed" in out
    assert "boom" in out
    assert "[t]" in out     # 补时间戳前缀
    assert "AGENT" in out
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py -v -k agent`
Expected: FAIL — 当前 end 行无时间戳前缀、无 ✓/✗ 符号。

- [ ] **Step 3: 写实现**

替换 `packages/core/src/shannon_core/display/rich_renderer.py` 的 `_render_agent` 方法整体（当前 `:105-121`）：

```python
    def _render_agent(self, e) -> None:
        title = self._agent_panel_title(e.agent_name)
        if e.event == "start":
            self._console.print(
                f"[{e.timestamp}] [blue]AGENT[/]  {AGENT_START} {title} started (attempt {e.attempt})")
            return
        # end
        if e.success is False:
            dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
            err = f" — {e.error}" if e.error else ""
            self._console.print(
                f"[{e.timestamp}] [blue]AGENT[/]  [red]{AGENT_FAIL} {title} failed ({dur}){err}[/]")
            return
        parts = []
        if e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.cost_usd is not None:
            parts.append(f"${e.cost_usd:.4f}")
        metrics = f" ({', '.join(parts)})" if parts else ""
        self._console.print(
            f"[{e.timestamp}] [blue]AGENT[/]  [green]{AGENT_DONE} {title} Completed{metrics}[/]")
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py -v -k agent`
Expected: PASS（含更新的 `test_agent_end_completed_shows_metrics`、新增 `test_agent_end_failed_...`、既有 `test_agent_start_shows_prefix`）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): AGENT end 行补时间戳前缀与 ✓/✗ 符号"
```

---

## Task 6: summary ✓/✗ 收口到 symbols（rich + file）

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（`_render_summary`，当前在 `:158`）
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`（`_summary`，当前在 `:137`）
- Modify: `packages/core/tests/display/test_rich_renderer.py`（更新 `test_summary_completed_renders_panel`）
- Modify: `packages/core/tests/display/test_file_renderer.py`（追加 summary 断言）

**Interfaces:**
- Consumes: Task 1 的 `SUMMARY_OK`/`SUMMARY_FAIL`（rich_renderer 已在 Task 4 import；file_renderer 需新增 import）

- [ ] **Step 1: 写失败测试**

(a) 更新既有 `test_summary_completed_renders_panel`（当前 `:119-128`）——追加 ✓ 符号断言。替换为：

```python
async def test_summary_completed_renders_panel():
    renderer, _ = _renderer_with_capture()
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165)]))
    out = renderer._console.export_text()
    assert "COMPLETED" in out
    assert "12.4s" in out
    assert "xss-vuln" in out
    assert "✓" in out  # summary 行用 SUMMARY_OK
```

(b) 在 `packages/core/tests/display/test_file_renderer.py` 末尾追加（先确认该文件已有的 summary 测试 import 风格，沿用其 `SummaryEvent`/`AgentMetric` import）：

```python
async def test_file_summary_uses_ok_symbol_for_success():
    from shannon_core.display.events import AgentMetric, SummaryEvent
    from shannon_core.display.file_renderer import FileLogRenderer

    class _Buf:
        def __init__(self):
            self.s = ""

        async def write(self, s):
            self.s += s

    buf = _Buf()
    r = FileLogRenderer(buf)
    await r.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165)]))
    assert "✓ xss-vuln" in buf.s
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py tests/display/test_file_renderer.py -v -k "summary or file_summary"`
Expected: PASS 或基本 PASS（此 task 是收口重构，行为不变；测试主要作为回归网。若既有 file_renderer 测试已有 summary 断言冲突则先修冲突）。若新 file_summary 测试因 `FileLogRenderer` 构造签名不符而 FAIL，核对 `_Buf` 是否满足 `LineWriter` 协议（只需 `async def write(self, s: str)`）。

- [ ] **Step 3: 写实现**

(a) 修改 `packages/core/src/shannon_core/display/rich_renderer.py` 的 `_render_summary` 第 `:158` 行：

```python
                mark = SUMMARY_OK if m.success else SUMMARY_FAIL
```

（原为 `mark = "✓" if m.success else "✗"`）

(b) 修改 `packages/core/src/shannon_core/display/file_renderer.py`：

在顶部 import 区（当前 `:9-11` 的 `from shannon_core.display.formatters import (...)` 之后）追加：

```python
from shannon_core.display.symbols import SUMMARY_FAIL, SUMMARY_OK
```

将 `_summary` 第 `:137` 行：

```python
            mark = SUMMARY_OK if m.success else SUMMARY_FAIL
```

（原为 `mark = "✓" if m.success else "✗"`）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/core && python -m pytest tests/display/test_rich_renderer.py tests/display/test_file_renderer.py -v`
Expected: PASS（全绿）

- [ ] **Step 5: 确认无散落符号字面量**

Run: `cd packages/core/src/shannon_core/display && grep -nE '"(○|✓|✗|▶|▸)"' rich_renderer.py file_renderer.py`
Expected: 无输出（STEP/AGENT/summary 范围符号已全部收口；🔧💭✅ 等 emoji 行不在范围，允许保留）。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_rich_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "refactor(display): summary ✓/✗ 收口到 symbols 单一来源"
```

---

## Task 7: 全量回归 + 验收

**Files:**
- 无代码改动，仅运行验证。

**Interfaces:**
- Consumes: Task 1-6 全部产出

- [ ] **Step 1: display 子集全量回归**

Run: `cd packages/core && python -m pytest tests/display/ -v`
Expected: PASS（所有 display 测试全绿，含新 test_symbols.py、扩充的 test_formatters.py、更新的 test_rich_renderer.py、test_file_renderer.py）

- [ ] **Step 2: 跨包 display 集成回归（白盒/黑盒接线测试）**

Run: `cd packages/whitebox && python -m pytest tests/test_display_integration.py tests/test_activity_display_wiring.py -v`
Run: `cd packages/blackbox && python -m pytest tests/test_display_integration.py tests/test_activity_display_wiring.py -v`
Expected: PASS。若失败，多为断言旧符号 `▸`/旧分隔线的断言——按新格式（○/✓/✗、对齐分隔线）更新对应断言。

- [ ] **Step 3: 验收清单核对**

对照 spec `docs/superpowers/specs/2026-06-22-log-format-redesign-design.md` 第 10 节验收标准逐条核对：
- [ ] PHASE 多行分隔线右端对齐（Task 3 测试覆盖）
- [ ] STEP start/complete/fail = ○/✓/✗，文字为中文 intent，失败后缀 `— <error>`（Task 4）
- [ ] AGENT start 用 ▶，end 带 `[时间戳]` + ✓/✗（Task 5）
- [ ] summary ✓/✗ 与 symbols.py 一致（Task 6）
- [ ] 无散落符号硬编码（Task 6 Step 5 grep 验证）
- [ ] display 相关单测全绿（Step 1）

- [ ] **Step 4: Commit 回归记录（可选）**

若 Step 2 修了跨包断言：

```bash
git add packages/whitebox/tests packages/blackbox/tests
git commit -m "test(display): 同步白盒/黑盒接线测试到新日志格式"
```

- [ ] **Step 5: 人工冒烟（交付用户执行）**

提示用户在真仓库跑一次 start（白盒或黑盒），肉眼确认三类行观感符合 spec 3.2 节范例。此步无自动测试覆盖（CLI 真实路径 `load_env→validate→build` 无自动测试），必须人工确认。

---

## Self-Review

**1. Spec 覆盖：**
- 分隔线长度不一 → Task 2（pad_rule）+ Task 3（PHASE 应用）✓
- STEP 开始中文/完成英文割裂 → Task 4（完成态用 `intent or name`）✓
- 符号 ▸/✓ 不统一 → Task 1（symbols）+ Task 4（STEP）+ Task 5（AGENT）✓
- STEP 失败态误用 ✓（bug）→ Task 4（`_render_step` fail 分支用 STEP_FAIL）✓
- AGENT end 缺时间戳（bug）→ Task 5（end 行加 `[{e.timestamp}]`）✓
- 符号散落收口 → Task 1 + Task 6（summary 收口 + grep 验证）✓
- 范围限定三类行、emoji 不动 → Global Constraints 明示，Task 6 Step 5 grep 只查 STEP/AGENT/summary 符号 ✓
- 无遗漏。

**2. 占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码；测试含具体断言值（如 `count("─") == 22`、`cell_len("预检") == 4`）。✓

**3. 类型/命名一致性：**
- `pad_rule` 签名在 Task 2 定义、Task 3 调用一致（`pad_rule(f"{verb} {e.phase}")`）。✓
- symbols 常量名 `STEP_PENDING/DONE/FAIL`、`AGENT_START/DONE/FAIL`、`SUMMARY_OK/FAIL` 在 Task 1 定义，Task 4/5/6 引用一致。✓
- Task 4 一次性 import 全部 symbols（含 AGENT_*/SUMMARY_*），供 Task 5/6 复用，避免重复改 import 块。✓
- `PHASE_RULE_WIDTH` 在 Task 2 定义为 36，测试与实现一致。✓
