# Rich 模式日志可见性恢复设计

日期：2026-06-19
分支：feat/fork-py

## 背景

原始 shannon（TypeScript）用纯 `console.log` 堆积式打印——phase 分隔、每个工具调用、每个 agent 的 LLM turn 都堆在终端，上滑即可看完整历史。shannon-py 改用 Rich `Live` 仪表盘后，为避免刷屏，rich 模式主动抑制了 PHASE 行和所有工具调用行，并把多行 agent 表简化成一行 pinned detail。

结果：终端可见信息密度显著低于原始项目；并行 agent 的 LLM turn 还因为渲染时丢失了 agent 标识而无法区分。用户反馈"rich 框显示内容不够多"。

## 问题根因（精确到行）

1. **PHASE 行被静音** — `workflow_logger.py:65` `show_phase=not self._use_rich`：rich 模式为 `False`，`PhaseEvent` 被 `RichConsoleRenderer.render` 跳过。
2. **LLM turn 行无 agent 标识** — `rich_renderer.py:_render_llm` 只打 `💭 Turn N: {line}`，不带 `e.agent_name`。并行 6 个 vuln agent 时滚动区一堆 `💭 Turn 3: …` 无法归因。
3. **状态栏只显示最后一个 running agent** — `live_dashboard.py:_pinned_detail` 取 `narrating[-1]`，其余并行 agent 不可见。
4. **现成工具未被复用** — 项目早有 `agent_prefix()`（`[Injection]`/`[XSS]`/`[Auth]`…，与原始项目短前缀一致），`_render_agent` 用了它，但 `_render_llm` 和状态栏都没用。

注：完整信息（含被抑制的 PHASE/工具行）本就写入 `workflow.log`，本设计只补齐终端可见性，不改文件日志。

## 设计目标

在保留 Rich `Live` 底部状态栏的前提下，让滚动区和状态栏的信息密度接近原始项目的堆积式日志，且并行 agent 可区分。不回归纯堆积打印，也不做全屏 TUI。

## 方案选择（brainstorming 确认）

- **期望形态**：保留状态栏 + 完整滚动（既不放弃 dashboard，也不堆文字墙）。
- **要看的信息**：Phase 分隔行 + 每个 agent 的 turn；明确不要工具调用/工具结果刷屏。
- **范围**：方案 A（放开 phase + turn 加前缀）+ 状态栏多 agent。verbosity 开关、状态栏上限等多视为 YAGNI，本次不做。

## 详细改动

3 个文件，无新文件、无新事件类型、无新依赖。事件层、`DashboardState`、文件日志完全不动。

### 改动 A — 放开 PHASE 分隔行

文件：`packages/core/src/shannon_core/audit/workflow_logger.py:65`

```python
# before
show_phase=not self._use_rich,   # rich: 压住 PHASE 行
# after
show_phase=True,                  # rich/plain 都显示 PHASE 行（恢复结构感）
```

效果：滚动区出现 `[ts] PHASE  Starting <phase> ────` 分隔行。

### 改动 B — LLM turn 行加 agent 前缀

文件：`packages/core/src/shannon_core/display/rich_renderer.py`（`_render_llm`，`agent_prefix` 已在 import 中）

```python
def _render_llm(self, e) -> None:
    line = first_nonempty_line(e.content) or "(无文本)"
    self._console.print(
        f"[{e.timestamp}] [magenta]💭 {agent_prefix(e.agent_name)} "
        f"Turn {e.turn}: {line}[/]", highlight=False)
```

效果：`💭 [Injection] Turn 3: Analyzing entry points…` —— 并行 agent 可归因。

### 改动 C — 状态栏多 agent 布局（核心）

文件：`packages/core/src/shannon_core/display/live_dashboard.py`

把 `_pinned_detail`（最后一个 agent 单行）替换为：`_render` 内为**每个 running agent 渲染一行**。`agent_prefix` 需新增 import。

```python
for a in running:
    action = a.last_action_detail or a.last_turn_text or "running..."
    grid = Table.grid()
    grid.add_row(
        Spinner("dots"),
        Text(f" {agent_prefix(a.name)} t{a.turn}  {action}", style="blue"),
    )
    rows.append(grid)   # Table.add_row() 返回 None，不能链式 append
```

- **每行内容优先级**：当前工具（`last_action_detail`，如 `Bash(rg …)`）> turn 文本（`last_turn_text`）> `"running..."`。工具更频繁、更实时。
  - 注：状态栏显示"当前工具"是 dashboard 概览视角（一行一 agent 的即时动作），区别于「非目标」中明确不做的「滚动区放开工具调用行刷屏」——两者不是一回事。状态栏复用的 `last_action_detail` 本就由 `ToolCallEvent` 维护，无需放开滚动区工具行。
- **布局用 `Table.grid()` 自然宽度**，不 expand —— 规避 2026-06-16 砍掉多行 agent 表时的坑（expand-to-width 把短 token 拉成大间隙）。
- **done/failed agent 不进状态栏**：去滚动区看 `AGENT Completed`。状态栏只反映"当前活跃"，高度随并发数动态变化而非无限增长。

效果对比：

```
改造前（只 1 个）:                       改造后（所有并行 agent）:
────────────────────────                ────────────────────────
Vuln Analysis · 2/8 · 4m · $0.01        Vuln Analysis · 2/8 · 4m · $0.01
⠋ pre-recon · Turn 4: Checking SQL…     ⠋ [Inj]  t4  Bash(rg -n eval)
                                        ⠹ [XSS]  t3  Checking reflected params…
                                        ⠼ [Auth] t2  Read(login.js)
```

## 数据流 / 事件层

完全不动。`ToolCallEvent` 早就在 `dashboard_state.py:112` 更新 `AgentRow.last_action_detail`（即使滚动区隐藏工具调用，状态栏仍能拿到当前工具）；`LlmTurnEvent` 在 `dashboard_state.py:121` 更新 `turn`/`last_turn_text`。`DisplayDispatcher` → 三 renderer（FileLog / RichConsole / LiveDashboard）的扇出结构不变。

## 错误处理

无新增错误路径。`agent_prefix` 对未知 agent 名回退 `[Agent]`；`first_nonempty_line` 对空文本回退 `"(无文本)"` —— 均已存在，本次不引入新的失败面。

## 测试

- `DashboardState` 已是纯数据、可单测：新增"多 running agent → 快照含每个 agent 行"。
- `_render_llm`：断言输出含 `agent_prefix(agent_name)`。
- `LiveDashboardRenderer._render`：快照测试多 agent 布局（验证用 `Table.grid()`、不 expand）。
- ⚠️ 按 [[pytest-whitebox-hang]]：只跑这三个文件相关子集，**不跑全量**（全量会卡在 Temporal/网络慢测试）。
- 真机冒烟：真仓库跑一次白盒扫描，人工确认滚动区有 phase 行 + turn 前缀、状态栏列出所有并行 agent。

## 权衡与边界

- **状态栏会变高**：phase 3 并发约 6 个 vuln agent 时，状态栏约 8 行，压缩上方滚动区。这是"看全并行 agent"的代价（用户已选此方向）。若日后觉得太挤，可加上限（如最多 4 行 + `…and N more`），本次不做。
- **`transient=True` 保持**：退出时清屏，不留残影。
- **plain 模式受益**：`show_phase=True` 后 rich/plain 一致；turn 前缀在 plain 模式也加上。
- **`workflow.log` 不动**：被抑制的工具调用本就在文件日志里，本次不改文件输出。

## 非目标

- 不放开工具调用/工具结果到滚动区（用户明确不要刷屏）。
- 不做全屏 TUI 仪表盘。
- 不加 verbosity 开关（YAGNI，后续按需）。
- 不改文件日志格式 / 事件类型 / `DashboardState` 数据层。
