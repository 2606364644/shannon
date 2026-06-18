# Rich 模式日志可见性恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 rich 模式终端显示的信息密度接近原始 shannon 项目——放开 PHASE 分隔行、给每个并行 agent 的 LLM turn 加可归因前缀、底部状态栏列出所有并行 running agent（而非只显示最后一个）。

**Architecture:** 改动集中在显示渲染层三处：`WorkflowLogger`（放开 `show_phase`）、`RichConsoleRenderer._render_llm`（加 `agent_prefix`）、`LiveDashboardRenderer._render`（单行 pinned detail → 每个 running agent 一行）。事件层 `DisplayEvent`、数据层 `DashboardState`、文件日志 `FileLogRenderer` 完全不动——`ToolCallEvent`/`LlmTurnEvent` 早已把 `last_action_detail`/`turn`/`last_turn_text` 维护进 `AgentRow`，渲染层直接复用。

**Tech Stack:** Python 3.12、rich（Live/Panel/Table/Spinner/Text）、pytest + pytest-asyncio（`asyncio_mode=auto`）、ruff（line-length 120）。

## Global Constraints

- **只跑改动相关测试子集**（`pytest <file>::<test> -v`），**严禁跑全量** `pytest`——全量会卡在 Temporal/网络慢测试（见 [[pytest-whitebox-hang]]）。每个 task 的命令已精确到文件/测试名。
- **不新增事件类型、不改 `DashboardState` 数据层、不改 `workflow.log` 文件格式**——本计划纯渲染层。
- **复用现有工具函数**：`agent_prefix()`（`formatters.py`，短前缀如 `[Injection]`/`[XSS]`）、`first_nonempty_line()`、`humanize_tool_call()`（已驱动 `AgentRow.last_action_detail`）。
- `shannon_whitebox.audit.workflow_logger` 是 compat shim（re-export `shannon_core` 版），改动落在 core 即可，whitebox 测试自动覆盖。
- `RichConsoleRenderer.show_phase` 默认就是 `True`（`rich_renderer.py:27`），问题纯粹在 `workflow_logger.py:65` 传了 `not self._use_rich`。
- Python 3.12、ruff `line-length=120`、`target-version=py312`。

---

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `packages/core/src/shannon_core/audit/workflow_logger.py` | 构造 renderer 时决定 `show_phase`/`show_tools` | Task 1：`:65` 改 `show_phase=True` |
| `packages/core/src/shannon_core/display/rich_renderer.py` | 事件 → 滚动日志行（`console.print`） | Task 2：`_render_llm` 加 `agent_prefix`（已 import） |
| `packages/core/src/shannon_core/display/live_dashboard.py` | 事件 → 底部状态栏（`__rich_console__`） | Task 3：`_render` 多 agent 行 + 删 `_pinned_detail` + import `agent_prefix` |
| `packages/whitebox/tests/test_workflow_logger.py` | WorkflowLogger 集成测试 | Task 1：+1 测试 |
| `packages/core/tests/display/test_rich_renderer.py` | RichConsoleRenderer 单测 | Task 2：+1 测试 |
| `packages/core/tests/display/test_live_dashboard.py` | LiveDashboardRenderer 单测 | Task 3：+3 测试，更新 3 个现有测试 |

---

### Task 1: 放开 PHASE 分隔行

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py:65`
- Test: `packages/whitebox/tests/test_workflow_logger.py`（末尾追加）

**Interfaces:**
- Consumes: `WorkflowLogger(meta, use_rich=True, console=..., dashboard=...)` 现有构造签名
- Produces: rich 模式下 `PhaseEvent` 经 `RichConsoleRenderer` 输出 `PHASE Starting <phase>` 行到 console（之前被 `show_phase=False` 压住）

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_workflow_logger.py` 末尾追加：

```python
async def test_rich_mode_renders_phase_line_to_stdout(tmp_path):
    """rich 模式下 PHASE 分隔行应输出到 stdout（show_phase=True），
    否则滚动区缺少阶段结构感。"""
    import io
    from rich.console import Console
    from shannon_core.display.live_dashboard import LiveDashboardRenderer
    meta = _make_meta(tmp_path)
    buf = io.StringIO()
    console = Console(file=buf, width=100, force_terminal=True, color_system=None)
    dashboard = LiveDashboardRenderer(console)
    logger = WorkflowLogger(meta, use_rich=True, console=console, dashboard=dashboard)
    await logger.initialize(workflow_id="wf-1")
    await logger.log_phase("vulnerability-analysis", "start")
    await logger.close()
    out = buf.getvalue()
    assert "PHASE" in out                      # RichConsoleRenderer 打了 PHASE 行
    assert "vulnerability-analysis" in out     # 且带 phase 名
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_rich_mode_renders_phase_line_to_stdout -v`
Expected: FAIL —— `show_phase=not use_rich` 在 rich 模式为 `False`，`RichConsoleRenderer` 不打 PHASE；`LiveDashboardRenderer` 在无 `Live` 上下文时不会被 `print`，故 buf 中既无 `PHASE` 也无 `vulnerability-analysis`。

- [ ] **Step 3: 实现**

`packages/core/src/shannon_core/audit/workflow_logger.py:65`：

```python
# before
                show_phase=not self._use_rich,   # rich: 压住 PHASE 行
# after
                show_phase=True,                  # rich/plain 都显示 PHASE 分隔行（恢复结构感）
```

仅改这一行（`show_tools=not self._use_rich` 保持不动——用户明确不要工具调用刷屏）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_rich_mode_renders_phase_line_to_stdout -v`
Expected: PASS

- [ ] **Step 5: 回归相邻测试**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_use_rich_attaches_dashboard_renderer packages/whitebox/tests/test_workflow_logger.py::test_plain_mode_attaches_rich_console_and_produces_stdout packages/core/tests/display/test_rich_renderer.py::test_phase_suppressed_when_show_phase_false packages/core/tests/display/test_rich_renderer.py::test_phase_rendered_by_default -v`
Expected: PASS（这些测 renderer 的 `show_phase` 开关行为与 workflow_logger 解耦，不受影响）

- [ ] **Step 6: lint + commit**

```bash
uv run ruff check packages/core/src/shannon_core/audit/workflow_logger.py
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "feat(display): rich 模式放开 PHASE 分隔行"
```

---

### Task 2: LLM turn 行加 agent 短前缀

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（`_render_llm`，约 127-130 行）
- Test: `packages/core/tests/display/test_rich_renderer.py`（末尾追加）

**Interfaces:**
- Consumes: `agent_prefix(agent_name)`（已在该文件 import，`formatters.py`）
- Produces: `LlmTurnEvent` 渲染为 `💭 [Injection] Turn N: <首行>`，并行多个 agent 的 turn 行可归因

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/display/test_rich_renderer.py` 末尾追加：

```python
async def test_llm_renders_agent_prefix_for_attribution():
    """并行 agent 的 turn 行必须带短前缀，否则滚动区一堆 💭 Turn N 无法区分。"""
    from shannon_core.display.events import LlmTurnEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=3, content="Checking SQL injection in login form"))
    out = renderer._console.export_text()
    assert "[Injection]" in out               # agent 短前缀
    assert "Turn 3" in out
    assert "Checking SQL injection in login form" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py::test_llm_renders_agent_prefix_for_attribution -v`
Expected: FAIL —— 现 `_render_llm` 输出 `💭 Turn 3: ...`，不含 `[Injection]`。

- [ ] **Step 3: 实现**

`packages/core/src/shannon_core/display/rich_renderer.py` 的 `_render_llm`：

```python
# before
    def _render_llm(self, e) -> None:
        line = first_nonempty_line(e.content) or "(无文本)"
        self._console.print(
            f"[{e.timestamp}] [magenta]💭 Turn {e.turn}: {line}[/]", highlight=False)
# after
    def _render_llm(self, e) -> None:
        line = first_nonempty_line(e.content) or "(无文本)"
        self._console.print(
            f"[{e.timestamp}] [magenta]💭 {agent_prefix(e.agent_name)} "
            f"Turn {e.turn}: {line}[/]", highlight=False)
```

`agent_prefix` 已在文件顶部 import，无需改动 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py::test_llm_renders_agent_prefix_for_attribution -v`
Expected: PASS

- [ ] **Step 5: 回归相邻测试**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: 全部 PASS（现有 `test_llm_renders_turn` 不断言前缀，新增前缀不影响其 `Turn 1`/`Analyzing code` 断言）

- [ ] **Step 6: lint + commit**

```bash
uv run ruff check packages/core/src/shannon_core/display/rich_renderer.py
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): LLM turn 行加 agent 短前缀(并行可归因)"
```

---

### Task 3: 状态栏列出所有并行 running agent

**Files:**
- Modify: `packages/core/src/shannon_core/display/live_dashboard.py`（import + `_render` 重写 + 删 `_pinned_detail`）
- Test: `packages/core/tests/display/test_live_dashboard.py`（+3 测试，更新 3 个现有测试）

**Interfaces:**
- Consumes: `AgentRow.{name,turn,last_action_detail,last_turn_text}`、`DashboardState.{agents,unit_intent,running_units,current_phase,completed_units,total_units,completed_count,total_cost}`、`agent_prefix(name)`（需新增 import）
- Produces: 状态栏从"最后一个 narrating agent 单行"变为"每个 running agent 一行"；无 running agent 时回退显示 running unit 名（保留旧行为）。每个 agent 行格式：`{label} t{turn}  {action}`，其中 `label = unit_intent[name] or agent_prefix(name)`，`action = last_action_detail or last_turn_text or "running..."`。

**为什么用 `Table.grid()` 不 expand：** 2026-06-16 曾因 expand-to-width 把短 token 拉成大间隙而砍掉多行 agent 表；`grid()` 用自然宽度规避。`Table.add_row()` 返回 `None`，必须先建 grid 再 add_row，不能链式 append。

- [ ] **Step 1: 写失败测试 —— 多 agent 各占一行**

在 `packages/core/tests/display/test_live_dashboard.py` 末尾追加：

```python
async def test_multiple_running_agents_each_get_a_row():
    """并行多个 running agent 时，状态栏应为每个 agent 渲染一行（短前缀），
    而非只显示最后一个。"""
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE",
                              phase="vulnerability-analysis", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="injection-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="xss-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="auth-vuln", event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "Injection" in out      # 三个并行 agent 各一行
    assert "XSS" in out
    assert "Auth" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py::test_multiple_running_agents_each_get_a_row -v`
Expected: FAIL —— 现 `_pinned_detail` 无 `last_turn_text` 时回退到 `running_unit_names`（全名 `injection-vuln · xss-vuln · auth-vuln` 一行），输出含全名而非短前缀 `Injection`/`XSS`/`Auth`。

- [ ] **Step 3: 写失败测试 —— action 优先级（当前工具 > turn 文本）**

继续追加：

```python
async def test_agent_row_prefers_current_tool_over_turn_text():
    """每行优先显示当前工具（更实时），其次 turn 文本。"""
    from shannon_core.display.events import LlmTurnEvent, ToolCallEvent
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="vuln", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="injection-vuln", event="start", attempt=1))
    await r.render(LlmTurnEvent(timestamp="t", category="LLM",
                                agent_name="injection-vuln", turn=4, content="Analyzing"))
    await r.render(ToolCallEvent(timestamp="t", category="TOOL",
                                 agent_name="injection-vuln",
                                 tool_name="Bash", parameters={"command": "rg -n eval"}))
    console.print(r)
    out = buf.getvalue()
    assert "rg -n eval" in out    # humanize 后的当前工具
    assert "t4" in out            # turn 号
```

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py::test_agent_row_prefers_current_tool_over_turn_text -v`
Expected: FAIL —— 现 `_pinned_detail` 用 `last_turn_text`（"Analyzing"），不含工具 `rg -n eval`。

- [ ] **Step 4: 实现 —— 重写 `_render` + 删 `_pinned_detail` + 加 import**

`packages/core/src/shannon_core/display/live_dashboard.py`：

(a) import 行（约 33 行）：

```python
# before
from shannon_core.display.formatters import format_duration
# after
from shannon_core.display.formatters import agent_prefix, format_duration
```

(b) 用以下方法**整体替换**现有 `_render`（约 52-79 行）和 `_pinned_detail`（约 81-93 行）两个方法 —— 即把这两个方法删掉，换成一个 `_render`：

```python
    def _render(self, options: ConsoleOptions) -> Group:
        snap = self._snapshot
        elapsed = format_duration(int(time.monotonic() - self._start_monotonic) * 1000)
        running = [r for r in snap.agents.values() if r.status == "running"]

        cells: list = [Text(snap.current_phase or "—", style="bold cyan")]
        if snap.total_units > 0:
            cells.append(Text(f" · step {snap.completed_units}/{snap.total_units}", style="green"))
        else:
            cells.append(Text(f" · {snap.completed_count} done", style="green"))
        cells.append(Text(f" · {elapsed}"))
        cells.append(Text(f" · ${snap.total_cost:.4f}", style="yellow"))

        row1 = Table.grid()
        row1.add_row(*cells)

        rows = [Text("─" * options.max_width, style="dim"), row1]
        if running:
            # 每个 running agent 一行；label 优先 step intent，否则 agent 短前缀；
            # action 优先当前工具，其次 turn 文本，再次 "running..."。
            # Table.grid() 用自然宽度，避免 expand-to-width 拉大间隙。
            for a in running:
                intent = snap.unit_intent.get(a.name)
                label = intent or agent_prefix(a.name)
                action = a.last_action_detail or a.last_turn_text or "running..."
                grid = Table.grid()
                grid.add_row(Spinner("dots"),
                             Text(f" {label} t{a.turn}  {action}", style="blue"))
                rows.append(grid)
        elif snap.running_units:
            # 无 running agent 但有 running step（如 code-index 这类非 agent 单元）：
            # 保留旧行为，显示运行中单元名。
            grid = Table.grid()
            grid.add_row(Spinner("dots"),
                         Text(" " + " · ".join(snap.running_units), style="blue"))
            rows.append(grid)
        return Group(*rows)
```

（删掉旧的 `_pinned_detail` 方法——其职责已并入上面的循环。）

- [ ] **Step 5: 跑两个新测试确认通过**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py::test_multiple_running_agents_each_get_a_row packages/core/tests/display/test_live_dashboard.py::test_agent_row_prefers_current_tool_over_turn_text -v`
Expected: PASS

- [ ] **Step 6: 跑整个 live_dashboard 测试，定位被破坏的现有测试**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -v`
Expected: 3 个现有测试 FAIL（格式/语义变化），记录失败名：
- `test_status_line_shows_phase_counts_cost_and_running_agent`（断言 `injection-vuln` 全名 → 现输出短前缀 `Injection`）
- `test_status_line_shows_step_progress_and_running_units`（断言 `code-index` → 有 running agent 时不再单独显示非 agent 单元）
- `test_pinned_row_shows_step_intent_and_latest_turn`（断言 `Turn 33` → 新格式 `t33`）

- [ ] **Step 7: 更新被破坏的现有测试**

`packages/core/tests/display/test_live_dashboard.py`：

(a) `test_status_line_shows_phase_counts_cost_and_running_agent`（约 22-33 行），把：
```python
    assert "injection-vuln" in out           # running agent appended with spinner
```
改为：
```python
    assert "Injection" in out                # running agent 行用短前缀（全名在滚动区 AGENT 行）
```

(b) `test_status_line_shows_step_progress_and_running_units`（约 59-74 行），把末尾两条断言：
```python
    assert "code-index" in out           # running unit
    assert "pre-recon" in out            # running unit (agent)
```
改为：
```python
    assert "pre-recon" in out            # phase 名（状态行）
    assert "running..." in out           # pre-recon agent 行：无 turn/tool 时 action 回退
    # 注：code-index 是非 agent 单元，当存在 running agent 时不在状态栏单独显示，
    # 其进度由状态行 "step 0/3" 体现。
```

(c) `test_pinned_row_shows_step_intent_and_latest_turn`（约 90-106 行），把：
```python
    assert "Turn 33" in out                  # 钉住行：最新轮
```
改为：
```python
    assert "t33" in out                      # agent 行：intent label + turn 号
```

- [ ] **Step 8: 再跑整个 live_dashboard 测试**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -v`
Expected: 全部 PASS（含新增 2 个 + 更新 3 个 + 其余未动的）

- [ ] **Step 9: 回归 DashboardState 单测（数据层未动，应全绿）**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py -v`
Expected: 全部 PASS

- [ ] **Step 10: lint + commit**

```bash
uv run ruff check packages/core/src/shannon_core/display/live_dashboard.py
git add packages/core/src/shannon_core/display/live_dashboard.py packages/core/tests/display/test_live_dashboard.py
git commit -m "feat(display): 状态栏列出所有并行 running agent(多行)"
```

---

### Task 4: 真机冒烟（手动验证）

**Files:** 无代码改动，仅人工观察。

**Interfaces:** 依赖 Task 1-3 已合入。

- [ ] **Step 1: 跑一次真实白盒扫描**

在一个 TTY 终端（rich 模式默认启用）运行：

```bash
uv run shannon-whitebox start --repo <一个小型目标仓库路径>
```

（运行方式参见 `README.md`「使用方法 / 白盒扫描」。）

- [ ] **Step 2: 人工核对四个观察点**

在扫描运行过程中（尤其进入 phase 3 并发漏洞分析时）确认：

1. **PHASE 分隔行**：滚动区出现 `PHASE  Starting <phase> ────────`（之前看不到）。
2. **turn 行可归因**：滚动区的 `💭` 行带 `[Injection]`/`[XSS]`/`[Auth]` 等短前缀，并行 agent 不再混淆。
3. **状态栏多 agent**：底部状态栏在并发阶段列出每个 running agent 各一行（`⠋ [Inj] t4  Bash(...)` 形式），而非只一行。
4. **无刷屏**：滚动区**不**出现大量 `🔧 Bash(...)` 工具调用行（`show_tools` 仍为 False），信息密度提升但不刷屏。

- [ ] **Step 3: 核对 workflow.log 未受影响**

```bash
uv run shannon-whitebox logs <workspace> | head -40
```

确认文件日志格式（`[PHASE]`/`[AGENT]`/`[TOOL]`/`[LLM]` 行）与改动前一致——本计划不应改变文件输出。

- [ ] **Step 4: 记录冒烟结果**

在 PR/commit 描述或工作记录中注明四个观察点的实际表现（通过/异常）。如发现回归，回到对应 Task 修测试或实现。

---

## Self-Review（写计划后自查，已执行）

**1. Spec coverage：**
- 改动 A（放开 PHASE 行）→ Task 1 ✓
- 改动 B（LLM turn 加前缀）→ Task 2 ✓
- 改动 C（状态栏多 agent，工具优先、grid 避免拉间隙、done 不进状态栏、transient 保持）→ Task 3 ✓
- 数据层/事件层/文件日志不动 → Global Constraints + Task 3 Step 9 + Task 4 Step 3 ✓
- 测试只跑子集 → Global Constraints + 每个 task 命令 ✓
- 真机冒烟 → Task 4 ✓

**2. Placeholder scan：** 无 TBD/TODO；每个 step 含完整代码或确切命令；冒烟 task 的 `--repo <仓库>` 是真实参数占位（用户填路径），已在 Step 1 注明参照 README，非计划缺陷。

**3. Type consistency：** `agent_prefix` 在 Task 2（已 import）与 Task 3（Step 4 新增 import）用法一致；`AgentRow.last_action_detail`/`last_turn_text`/`turn` 与 `dashboard_state.py` 字段名一致；`snap.running_units`/`unit_intent` 与 `DashboardState` 属性一致；`Table.grid().add_row` 两步写法（非链式）在 Task 3 与现有 `row1` 写法一致。
