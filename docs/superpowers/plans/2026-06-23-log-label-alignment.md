# 日志标签列对齐 + rich/file 正文统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让终端与 workflow.log 的 PHASE/STEP/AGENT 三类日志行标签列等宽对齐、正文经共享函数逐字统一，且不与 Turn 行的 agent 身份方括号冲突。

**Architecture:** 在 `formatters.py` 新增单一来源的格式层 —— `tag()` 补齐标签列宽度、`step_body/phase_body/agent_body` 产出纯文本正文、`agent_title` 统一 agent 显示名。`rich_renderer` 与 `file_renderer` 的三类行改为调用这批共享函数：终端标签无方括号（避开 agent_prefix 方括号与 Turn 行冲突），file 保留方括号（grep 传统），两者正文逐字一致。

**Tech Stack:** Python 3、Rich（终端渲染）、pytest（async 测试，`asyncio_mode=auto`）、uv 包管理。

## Global Constraints

- **测试只跑改动相关子集**，绝不跑全套/全包（会卡 Temporal/网络慢测试 hang）。命令形如 `uv run pytest packages/core/tests/display/test_xxx.py -v`。
- **涉及完整行 / 对齐断言的测试，时间戳必须用真实格式 `YYYY-MM-DD HH:MM:SS`**，不能用 `"t"` —— Rich 把 `[单字母]` 当 markup tag 吞括号，`[t]` 会被吞掉破坏行结构。多字母标签 `STEP/PHASE/AGENT` 不触发吞括号。
- **agent_prefix 的方括号（`[Injection]/[XSS]/[Auth]/[Agent]`）是 agent 身份标识的既有约定，三 renderer 共享，本次绝不动它**。终端事件标签因此保持无方括号。
- 正文文案含中文（如「预检（环境 / 依赖就绪性）」），保持中文不英化。
- DRY / YAGNI / TDD（先写失败测试）/ 每个任务结束 commit。

## File Structure

| 文件 | 责任 | 本次改动 |
|---|---|---|
| `packages/core/src/shannon_core/display/formatters.py` | 共享格式 helper | **新增** `LABEL_WIDTH`、`tag`、`step_body`、`phase_body`、`agent_title`、`agent_body`；新增 `from ...symbols import` |
| `packages/core/src/shannon_core/display/rich_renderer.py` | 终端渲染 | `_render_step/_render_phase/_render_agent` 改调共享函数 + `tag()`；删 `_agent_panel_title`（改调 `agent_title`） |
| `packages/core/src/shannon_core/display/file_renderer.py` | workflow.log 渲染 | `_step/_phase/_agent` 改调共享函数 + `tag()`；`_prefixed` 改调 `agent_title` |
| `packages/core/src/shannon_core/display/live_dashboard.py` | 底部状态栏 | **不动** |
| `packages/core/src/shannon_core/display/symbols.py` | 状态符号常量 | **不动**（被 formatters 新增 import） |
| `packages/core/tests/display/test_formatters.py` | 格式层单测 | 新增 `tag/step_body/phase_body/agent_title/agent_body` 测试 |
| `packages/core/tests/display/test_rich_renderer.py` | 终端单测 | 新增 PHASE/STEP/AGENT 对齐测试 |
| `packages/core/tests/display/test_file_renderer.py` | file 单测 | 重写 step/agent 断言（正文改符号+意图）；新增对齐测试 |

---

## Task 1: formatters 共享格式层

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`（顶部 import 区 + 文件末尾追加）
- Test: `packages/core/tests/display/test_formatters.py`

**Interfaces:**
- Consumes: `shannon_core.display.symbols.{STEP_PENDING,STEP_DONE,STEP_FAIL,AGENT_START,AGENT_DONE,AGENT_FAIL}`、`shannon_core.display.events.{StepEvent,PhaseEvent,AgentEvent}`、已有的 `agent_prefix`/`format_duration`
- Produces（后续任务依赖的精确签名）:
  - `LABEL_WIDTH: int = 5`
  - `tag(label: str, width: int = LABEL_WIDTH) -> str` —— `tag("STEP") == "STEP "`
  - `step_body(e: StepEvent) -> str` —— start→`"○ {intent或name}"`；complete ok→`"✓ {label}  {dur}"`；error→`"✗ {label}  — {error}"`
  - `phase_body(e: PhaseEvent) -> str` —— `"Starting {phase}"` 或 `"Completed {phase}"`
  - `agent_title(agent_name: str) -> str` —— `"[Prefix] name"` 或未知时 `"name"`
  - `agent_body(e: AgentEvent) -> str` —— start→`"▶ {title} started (attempt {n})"`；fail→`"✗ {title} failed ({dur}) — {error}"`；ok→`"✓ {title} Completed ({dur}, ${cost:.4f})"`

- [ ] **Step 1: 写失败测试 —— tag / LABEL_WIDTH**

在 `tests/display/test_formatters.py` 末尾追加：

```python
from shannon_core.display.formatters import tag, LABEL_WIDTH


def test_tag_pads_short_label_to_width():
    assert tag("STEP") == "STEP "          # 4 -> 5


def test_tag_no_pad_when_already_full_width():
    assert tag("PHASE") == "PHASE"
    assert tag("AGENT") == "AGENT"


def test_tag_all_core_labels_equal_width():
    assert {len(tag(l)) for l in ("PHASE", "STEP", "AGENT")} == {LABEL_WIDTH}
    assert LABEL_WIDTH == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: FAIL —— `ImportError: cannot import name 'tag'`

- [ ] **Step 3: 实现 tag / LABEL_WIDTH**

在 `formatters.py` 顶部现有 import 之后，文件末尾（`pad_rule` 之后）追加：

```python
LABEL_WIDTH = 5  # PHASE/AGENT=5，STEP 补齐到 5，让标签列等宽


def tag(label: str, width: int = LABEL_WIDTH) -> str:
    """补齐到固定宽度的标签内容：tag("STEP") -> "STEP "。

    rich 与 file 共用：终端 [cyan]{tag}[/] 无字面方括号，file [{tag}] 方括号内补齐。
    """
    return label.ljust(width)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS（新增 3 个 tag 测试；其余既有测试不受影响）

- [ ] **Step 5: 写失败测试 —— step_body**

在 `test_formatters.py` 末尾追加：

```python
from shannon_core.display.formatters import step_body
from shannon_core.display.events import StepEvent


def test_step_body_start_uses_pending_and_intent():
    e = StepEvent(timestamp="t", category="STEP", name="code-index", phase="pre-recon",
                  event="start", intent="构建调用图与代码索引")
    assert step_body(e) == "○ 构建调用图与代码索引"


def test_step_body_start_falls_back_to_name_when_no_intent():
    e = StepEvent(timestamp="t", category="STEP", name="code-index", phase="pre-recon",
                  event="start")
    assert step_body(e) == "○ code-index"


def test_step_body_complete_with_duration():
    e = StepEvent(timestamp="t", category="STEP", name="code-index", phase="pre-recon",
                  event="complete", duration_ms=4100, intent="构建调用图与代码索引")
    assert step_body(e) == "✓ 构建调用图与代码索引  4.1s"


def test_step_body_complete_without_duration():
    e = StepEvent(timestamp="t", category="STEP", name="x", phase="p", event="complete")
    assert step_body(e) == "✓ x"


def test_step_body_error_uses_cross_and_error():
    e = StepEvent(timestamp="t", category="STEP", name="x", phase="p",
                  event="complete", error="索引构建超时", intent="构建调用图")
    assert step_body(e) == "✗ 构建调用图  — 索引构建超时"
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_formatters.py::test_step_body_start_uses_pending_and_intent -v`
Expected: FAIL —— `ImportError: cannot import name 'step_body'`

- [ ] **Step 7: 实现 step_body**

在 `formatters.py` 顶部 import 区追加（与现有 `from rich.cells import cell_len` 同区）：

```python
from shannon_core.display.symbols import (
    AGENT_DONE, AGENT_FAIL, AGENT_START,
    STEP_DONE, STEP_FAIL, STEP_PENDING,
)
```

在 `tag` 函数之后追加：

```python
def step_body(e) -> str:
    """STEP 正文：○/✓/✗ + 意图（fallback name）+ duration/error suffix。

    纯文本、无颜色、无标签列、无换行 —— rich 与 file 共用的单一来源。
    """
    label = e.intent or e.name
    if e.event == "start":
        return f"{STEP_PENDING} {label}"
    if e.error:
        return f"{STEP_FAIL} {label}  — {e.error}"
    suffix = f"  {format_duration(e.duration_ms)}" if e.duration_ms is not None else ""
    return f"{STEP_DONE} {label}{suffix}"
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS（含 5 个新 step_body 测试）

- [ ] **Step 9: 写失败测试 —— phase_body / agent_title / agent_body**

在 `test_formatters.py` 末尾追加：

```python
from shannon_core.display.formatters import phase_body, agent_title, agent_body
from shannon_core.display.events import PhaseEvent, AgentEvent


def test_phase_body_start():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="setup", event="start")
    assert phase_body(e) == "Starting setup"


def test_phase_body_complete():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="complete")
    assert phase_body(e) == "Completed pre-recon"


def test_agent_title_known_prefix():
    assert agent_title("injection-vuln") == "[Injection] injection-vuln"
    assert agent_title("xss-vuln") == "[XSS] xss-vuln"


def test_agent_title_unknown_is_bare_name():
    assert agent_title("pre-recon") == "pre-recon"


def test_agent_body_start_with_prefix():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="injection-vuln",
                   event="start", attempt=1)
    assert agent_body(e) == "▶ [Injection] injection-vuln started (attempt 1)"


def test_agent_body_start_unknown_agent():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="pre-recon",
                   event="start", attempt=1)
    assert agent_body(e) == "▶ pre-recon started (attempt 1)"


def test_agent_body_end_completed_with_metrics():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="xss-vuln",
                   event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True)
    assert agent_body(e) == "✓ [XSS] xss-vuln Completed (5.2s, $0.1500)"


def test_agent_body_end_failed():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="xss-vuln",
                   event="end", attempt=1, duration_ms=100, success=False, error="boom")
    assert agent_body(e) == "✗ [XSS] xss-vuln failed (100ms) — boom"
```

- [ ] **Step 10: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_formatters.py::test_phase_body_start -v`
Expected: FAIL —— `ImportError: cannot import name 'phase_body'`

- [ ] **Step 11: 实现 phase_body / agent_title / agent_body**

在 `step_body` 之后追加：

```python
def phase_body(e) -> str:
    """PHASE 正文：verb + phase，如 'Starting setup'。纯文本，rich/file 共用。"""
    verb = "Starting" if e.event == "start" else "Completed"
    return f"{verb} {e.phase}"


def agent_title(agent_name: str) -> str:
    """'[Prefix] name' 或未知 agent 直接 'name'。

    取代 rich 的 _agent_panel_title 与 file 的 _prefixed（两者逻辑相同），统一为单一来源。
    """
    pfx = agent_prefix(agent_name)
    if pfx == "[Agent]":
        return agent_name
    return f"{pfx} {agent_name}"


def agent_body(e) -> str:
    """AGENT 正文：▶/✗/✓ + title + (attempt)/failed/metrics。纯文本，rich/file 共用。"""
    title = agent_title(e.agent_name)
    if e.event == "start":
        return f"{AGENT_START} {title} started (attempt {e.attempt})"
    if e.success is False:
        dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
        err = f" — {e.error}" if e.error else ""
        return f"{AGENT_FAIL} {title} failed ({dur}){err}"
    parts = []
    if e.duration_ms is not None:
        parts.append(format_duration(e.duration_ms))
    if e.cost_usd is not None:
        parts.append(f"${e.cost_usd:.4f}")
    metrics = f" ({', '.join(parts)})" if parts else ""
    return f"{AGENT_DONE} {title} Completed{metrics}"
```

- [ ] **Step 12: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS（全部新增 + 既有测试）

- [ ] **Step 13: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py
git commit -m "feat(display): 新增 tag/step_body/phase_body/agent_title/agent_body 共享格式层"
```

---

## Task 2: rich_renderer 接入共享格式层

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（import 区、`_render_step`、`_render_phase`、`_render_agent`，删 `_agent_panel_title`）
- Test: `packages/core/tests/display/test_rich_renderer.py`（新增对齐测试）

**Interfaces:**
- Consumes: Task 1 的 `tag`、`step_body`、`phase_body`、`agent_body`、`agent_title`
- Produces: 终端 PHASE/STEP/AGENT 行标签列等宽对齐、正文来自共享函数；既有渲染行为（符号、颜色、`pad_rule`）保持

- [ ] **Step 1: 写失败测试 —— PHASE/STEP/AGENT body 同列对齐**

在 `test_rich_renderer.py` 末尾追加（**时间戳用真实格式**，避免 `[t]` 被吞破坏行结构）：

```python
async def test_phase_step_agent_bodies_align_same_column():
    """标签列经 tag() 补齐等宽 -> PHASE/STEP/AGENT 正文起点同列。"""
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    ts = "2026-06-23 00:42:39"
    await renderer.render(PhaseEvent(timestamp=ts, category="PHASE", phase="setup", event="start"))
    await renderer.render(StepEvent(timestamp=ts, category="STEP", name="preflight",
                                    phase="setup", event="start", intent="预检"))
    await renderer.render(AgentEvent(timestamp=ts, category="AGENT", agent_name="pre-recon",
                                     event="start", attempt=1))
    out = renderer._console.export_text()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    phase_line = next(ln for ln in lines if "Starting setup" in ln)
    step_line = next(ln for ln in lines if "预检" in ln)
    agent_line = next(ln for ln in lines if "pre-recon started" in ln)
    # body 起点（标签列之后）三行必须同列
    p = phase_line.index("Starting")
    s = step_line.index("○")
    a = agent_line.index("▶")
    assert p == s == a, f"PHASE/STEP/AGENT 正文未对齐: phase={p} step={s} agent={a}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py::test_phase_step_agent_bodies_align_same_column -v`
Expected: FAIL —— `AssertionError: PHASE/STEP/AGENT 正文未对齐`（STEP 比 PHASE/AGENT 早 1 列）

- [ ] **Step 3: 改 import**

`rich_renderer.py` 顶部 import from `formatters` 改为：

```python
from shannon_core.display.formatters import (
    agent_body, agent_title, format_duration, format_error_block,
    humanize_tool_call, first_nonempty_line, pad_rule, phase_body,
    step_body, tag,
)
```

（去掉 `agent_prefix`，新增 `agent_body/agent_title/phase_body/step_body/tag`。`agent_prefix` 仍被 `_render_llm` 用 —— 保留它：）

实际 import 应为（`_render_llm` 仍直接用 `agent_prefix`，保留）：

```python
from shannon_core.display.formatters import (
    agent_body, agent_prefix, agent_title, format_duration,
    format_error_block, humanize_tool_call, first_nonempty_line,
    pad_rule, phase_body, step_body, tag,
)
```

- [ ] **Step 4: 改 `_render_step`**

替换整个 `_render_step` 方法为：

```python
    def _render_step(self, e) -> None:
        self._console.print(
            f"[{e.timestamp}] [cyan]{tag('STEP')}[/]  {step_body(e)}", highlight=False)
```

- [ ] **Step 5: 改 `_render_phase`**

替换整个 `_render_phase` 方法为：

```python
    def _render_phase(self, e) -> None:
        body = pad_rule(phase_body(e))
        self._console.print(
            f"[{e.timestamp}] [bold cyan]{tag('PHASE')}[/]  {body}",
            highlight=False,
        )
```

- [ ] **Step 6: 改 `_render_agent`，删 `_agent_panel_title`**

删除 `_agent_panel_title` 方法（其逻辑已被 Task 1 的 `agent_title` 取代），把 `_render_agent` 替换为：

```python
    def _render_agent(self, e) -> None:
        body = agent_body(e)
        if e.event == "start":
            self._console.print(
                f"[{e.timestamp}] [blue]{tag('AGENT')}[/]  {body}", highlight=False)
            return
        if e.success is False:
            self._console.print(
                f"[{e.timestamp}] [blue]{tag('AGENT')}[/]  [red]{body}[/]", highlight=False)
            return
        self._console.print(
            f"[{e.timestamp}] [blue]{tag('AGENT')}[/]  [green]{body}[/]", highlight=False)
```

> 注：`body` 含 agent_title 的 `[Injection]` 等方括号，与改动前 `{title}` 插值进 markup 的行为完全一致（Rich 对多字母未知 tag 当字面输出，既有测试 `test_agent_start_shows_prefix` 已验证 `[Injection]` 正常显示）。`highlight=False` 保留。

- [ ] **Step 7: 跑对齐测试 + 既有测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS（新对齐测试 + 全部既有 step/phase/agent 测试 —— 正文格式保持，符号/`pad_rule`/metrics 行为不变）

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "refactor(rich): PHASE/STEP/AGENT 行接入共享格式层 + tag 标签列对齐"
```

---

## Task 3: file_renderer 接入共享格式层

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`（import 区、`_step`、`_phase`、`_agent`、`_prefixed`）
- Test: `packages/core/tests/display/test_file_renderer.py`（重写 step/agent 断言、新增对齐测试）

**Interfaces:**
- Consumes: Task 1 的 `tag`、`step_body`、`phase_body`、`agent_body`、`agent_title`
- Produces: workflow.log 的 `[PHASE]/[STEP ]/[AGENT]` 标签列对齐、正文与终端逐字一致（符号○✓▶ + 中文意图）

> **本任务会改变 file 的 step/agent 行正文格式**（从 `name: verb` 改为符号 + 意图，spec 已确认接受此代价 —— file 丢失 step name 机器可读字段，但 summary block / `COMPLETION_PATTERN` 不受影响）。多个既有断言会因此失效，需重写。

- [ ] **Step 1: 重写 step 断言（写失败测试 —— 期望新格式）**

在 `test_file_renderer.py` 中：

把 `test_step_event_renders_step_line`（约 160-175 行）的断言改为：

```python
    out = "".join(w.lines)
    assert "[STEP ] ○ code-index\n" in out        # start: 符号 + name fallback，标签补齐 [STEP ]
    assert "[STEP ] ✓ code-index  12.0s\n" in out  # complete: 符号 + duration
```

把 `test_step_file_line_includes_intent_when_present`（约 178-190 行）的断言改为：

```python
    out = "".join(w.lines)
    assert "[STEP ] ○ 构建调用图与代码索引\n" in out   # 符号 + intent，不再有 name:verb
```

- [ ] **Step 2: 重写 agent 断言**

把四个 agent 测试的断言改为：

```python
    # test_agent_start_with_prefix
    assert "[AGENT] ▶ [Injection] injection-vuln started (attempt 2)\n" in renderer._writer.text

    # test_agent_start_no_prefix_for_unknown
    assert "[AGENT] ▶ pre-recon started (attempt 1)\n" in renderer._writer.text

    # test_agent_end_completed_with_metrics
    assert "[AGENT] ✓ [XSS] xss-vuln Completed (5.2s, $0.1500)\n" in renderer._writer.text

    # test_agent_end_failed
    assert "[AGENT] ✗ [XSS] xss-vuln failed (100ms) — boom" in renderer._writer.text
```

- [ ] **Step 3: 新增 file 对齐测试**

在 `test_file_renderer.py` 末尾追加：

```python
async def test_phase_step_agent_labels_align_in_file():
    """file [PHASE]/[STEP ]/[AGENT] 标签列等宽 -> 正文起点同列。"""
    from shannon_core.display.events import StepEvent
    renderer = FileLogRenderer(FakeWriter())
    ts = "2026-06-23 00:42:39"
    await renderer.render(PhaseEvent(timestamp=ts, category="PHASE", phase="setup", event="start"))
    await renderer.render(StepEvent(timestamp=ts, category="STEP", name="preflight",
                                    phase="setup", event="start", intent="预检"))
    await renderer.render(AgentEvent(timestamp=ts, category="AGENT", agent_name="pre-recon",
                                     event="start", attempt=1))
    out = renderer._writer.text
    lines = [ln for ln in out.splitlines() if ln.strip()]
    phase_line = next(ln for ln in lines if "[PHASE]" in ln)
    step_line = next(ln for ln in lines if "[STEP ]" in ln)
    agent_line = next(ln for ln in lines if "[AGENT]" in ln)
    # 三行正文起点同列（标签列 [PHASE]/[STEP ]/[AGENT] 均为 7 字符等宽）
    p = phase_line.index("Starting")
    s = step_line.index("○")
    a = agent_line.index("▶")
    assert p == s == a, f"file 标签列未对齐: phase={p} step={s} agent={a}"
```

- [ ] **Step 4: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: FAIL —— step/agent 旧格式仍在（实现未改），新断言不匹配；对齐测试也 fail

- [ ] **Step 5: 改 import**

`file_renderer.py` 顶部 import from `formatters` 改为（去掉不再需要的 `humanize_tool_call` 之外保留；新增共享函数；`_prefixed` 改调 `agent_title` 后 `agent_prefix` 不再直接被 `_prefixed` 用，但 `_tool/_llm` 不在本次范围，仍用 `_prefixed`）：

```python
from shannon_core.display.formatters import (
    agent_body, agent_title, format_duration, format_error_block,
    humanize_tool_call, phase_body, step_body, tag,
)
```

> `agent_prefix` 不再需要直接 import（`_prefixed` 改调 `agent_title`）。

- [ ] **Step 6: `_prefixed` 改调 `agent_title`**

把 `_prefixed` 函数替换为（消除与 `agent_title` 的重复逻辑）：

```python
def _prefixed(agent_name: str) -> str:
    """Return '[Prefix] agentname' or just 'agentname' for unknown agents.

    Delegates to the shared agent_title so file/rich agree on agent display.
    仍供 _tool/_llm 行使用（本次不改这两类行）。
    """
    return agent_title(agent_name)
```

- [ ] **Step 7: 改 `_step`**

替换整个 `_step` 方法为（正文、duration、error 全部由 `step_body` 承担）：

```python
    def _step(self, e) -> str:
        return f"[{e.timestamp}] [{tag('STEP')}] {step_body(e)}\n"
```

- [ ] **Step 8: 改 `_phase`**

替换整个 `_phase` 方法为：

```python
    def _phase(self, e) -> str:
        prefix = "\n" if e.event == "start" else ""
        return f"{prefix}[{e.timestamp}] [{tag('PHASE')}] {phase_body(e)}\n"
```

- [ ] **Step 9: 改 `_agent`**

替换整个 `_agent` 方法为：

```python
    def _agent(self, e) -> str:
        return f"[{e.timestamp}] [{tag('AGENT')}] {agent_body(e)}\n"
```

- [ ] **Step 10: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS（重写的 step/agent 断言 + 新对齐测试 + 既有 phase/tool/llm/error/summary/resume 测试 —— 后者正文未改）

- [ ] **Step 11: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "refactor(file): PHASE/STEP/AGENT 行接入共享格式层 + tag 标签列对齐"
```

---

## Task 4: 端到端回归验证

**Files:**
- 不改代码（纯验证 gate）。若集成测试意外失败，才按失败修。

**Interfaces:**
- Consumes: Task 1-3 全部产出

> 预期：L2 集成测试断言的是正文片段（`▶ [Injection] name started`、`Starting {phase}`、`PHASE`、`AGENT`），与本设计保持的正文格式一致 —— **不会因改动失败**。本任务确认这一点 + 跑全量相关单测。

- [ ] **Step 1: 跑 core display 全量单测**

Run: `uv run pytest packages/core/tests/display/ -v`
Expected: PASS（formatters + rich_renderer + file_renderer + events + dispatcher + dashboard_state 全绿）

- [ ] **Step 2: 跑 whitebox L2 集成门禁**

Run: `uv run pytest packages/whitebox/tests/test_display_integration.py -v`
Expected: PASS —— `▶ [Injection] injection-vuln started` / `Starting vulnerability-analysis` / `PHASE` / `AGENT` 断言均仍命中（正文格式未变）

- [ ] **Step 3: 跑 blackbox L2 集成门禁**

Run: `uv run pytest packages/blackbox/tests/test_display_integration.py -v`
Expected: PASS —— `▶ [Injection] injection-exploit started` / `Starting exploitation` 断言均仍命中

- [ ] **Step 4: 确认无代码改动则免 commit；否则 commit 修复**

若 Step 1-3 全绿：本任务无代码改动，**不 commit**，记录「端到端回归通过」。

若任一集成测试意外失败：读取失败输出，按本次正文格式调整断言（保持正文片段不变原则），`git add ... && git commit -m "test(display): 同步集成测试断言"`。

- [ ] **Step 5: 人工冒烟提示（交付前必做）**

实跑核对终端对齐效果（memory 多处强调真机冒烟待验证）：

```bash
uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat
```

肉眼核对：PHASE / STEP / AGENT 三类行的正文起点落在同一列；Turn 行 `💭 [Agent]` 保持方括号、与 AGENT 事件行（无方括号 `AGENT`）视觉可清晰区分。核对无误后本次工作可交付/merge。

---

## Self-Review 结论

**1. Spec coverage：**
- 标签列对齐（tag 补齐）→ Task 1（tag）+ Task 2/3（接入）+ 对齐测试 ✓
- 终端无方括号、file 方括号 → Task 2（`[cyan]{tag}[/]` 无字面方括号）/ Task 3（`[{tag}]`）✓
- 正文逐字统一（共享 step_body/phase_body/agent_body）→ Task 1 实现 + Task 2/3 接入 ✓
- live_dashboard 不动 → File Structure 标注不动 ✓
- Turn/Tool/Error/Resume 不动 → 仅改 step/phase/agent，`_render_llm/_tool/_error/_resume` 不触碰 ✓
- file 丢 name 字段（已接受代价）→ Task 3 重写 step 断言体现 ✓
- `[TOOL]/[LLM]` follow-up 不做 → 未纳入任何 task ✓

**2. Placeholder scan：** 无 TBD/TODO；每个 step 含完整代码或精确命令 + 预期输出。

**3. Type consistency：** `tag`/`step_body`/`phase_body`/`agent_title`/`agent_body` 在 Task 1 定义、Task 2/3 消费的签名一致；`agent_title` 取代 rich `_agent_panel_title` 与 file `_prefixed` 的重复逻辑，命名统一。
