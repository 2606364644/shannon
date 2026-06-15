# Rich 实时展示排版修复设计：去重 + 单状态行 + 对齐

> 日期: 2026-06-16 | 状态: 设计待评审
>
> 关联文档:
> - 直接前序（live 管线接入，已实现）: [`2026-06-15-whitebox-live-display-design.md`](./2026-06-15-whitebox-live-display-design.md) · [`2026-06-15-blackbox-live-display-design.md`](./2026-06-15-blackbox-live-display-design.md)
> - 渲染层基线: [`2026-06-13-logging-display-optimization-design.md`](./2026-06-13-logging-display-optimization-design.md)
>
> 一句话：rich 模式下「滚动日志行 + 钉住 dashboard」两套显示叠加 + grid 全宽拉伸，导致终端排版乱；本设计保留双渲染器分层，只修去重、状态行排版、生命周期收尾三处。

---

## 1. 背景与问题

`2026-06-15-*-live-display-design` 已把渲染管线接入 whitebox/blackbox 的 Temporal 生命周期，rich 模式（TTY）下能看到实时输出。但实际运行 `uv run shannon-whitebox start -r <repo>` 时排版仍然乱：

```
[2026-06-16 02:51:06] PHASE  Starting pre-recon ────────────────────
[2026-06-16 02:51:06] AGENT  ▶ pre-recon started (attempt 1)
Phase: pre-recon                                       0 done            25s       $0.0000
───────────────────────────────────────────────────────────────────────────────
⠋  [Agent] pre-recon
```

不是某一行写错，而是**两套显示范式叠加 + 布局参数不当**。根因见 §3。

## 2. 目标 / 非目标

**目标**

1. rich 模式下输出干净、不重复：同一状态不被上下各表达一遍。
2. 底部钉**一条**紧凑状态行（phase · 已完成数 · 耗时 · 花费 + 当前 agent spinner），取代臃肿的全宽多行 dashboard。
3. 修掉分隔线与真实终端宽度对不齐、四列等比撑出大片空白两处布局缺陷。
4. 扫描结束时最终输出是 summary（Panel + 表），无残留状态行。
5. 改动集中在 `shannon_core`，whitebox / blackbox 两个 CLI 同时受益（二者 display_lifecycle 均委托 core）。

**非目标**

- 不改 `DashboardState` 纯数据状态机（字段已足够）。
- 不动非 rich / 管道 / CI 路径（保持逐行流式）。
- 不引入暂停/跳过等交互控制、不做子任务级进度分解。
- 不改 `workflow.log` 文件内容（`FileLogRenderer` 不变）。

## 3. 根因（已读码确认）

rich 模式下 `WorkflowLogger.initialize`（`packages/core/src/shannon_core/audit/workflow_logger.py:46-52`）往 `DisplayDispatcher` 挂了**两个** stdout 渲染器，二者共享 `display_lifecycle.py` 创建的同一个 `Console`：

- `RichConsoleRenderer(console)` —— 每个事件 `console.print()` 一行带时间戳日志（`PHASE / AGENT ▶ / 🔧 / 💭`），Rich 将其渲染到 Live 区域**上方**滚动。
- `LiveDashboardRenderer` —— 把同一事件折叠进 Live 区域钉住的 dashboard。

由此产生三个具体问题：

1. **重复**：`pre-recon` 既滚出一行 `[ts] AGENT ▶ pre-recon started`，又在下方 dashboard 以 `Phase: pre-recon` / `⠋ [Agent] pre-recon` 出现。`phase` 尤其明显——它本是持久状态，状态行已常驻显示，再滚一行纯冗余。
2. **被拉宽**：`live_dashboard.py:53` 与 `:62` 两处 `Table.grid(expand=True)`，在宽终端里把 `Phase: pre-recon` / `0 done` / `25s` / `$0.0000` 四格按等比撑满整行，中间出现大片空白。
3. **分隔线对不齐**：`live_dashboard.py:64` 写死 `Text("─" * 60)`，与上方被 `expand` 撑宽的行宽度不一致，视觉上忽长忽短。

## 4. 方案选择

| | 做法 | 权衡 |
|---|---|---|
| **方案 1（采纳）** | 保留双渲染器与 dispatcher/`DashboardState` 分层，只改各 renderer 职责与排版参数 | diff 最小；复用现有干净分层；直击三处根因；仍依赖 Rich「print 自动落 Live 上方」行为（已 probe 验证可行） |
| 方案 2 | 合并成单 renderer 独占 Live，去重在源头 | 终端单所有者，心智清晰；但要打破纯数据/渲染分层，重构面大，不值 |
| 方案 3 | 弃用 `Live`，用 `console.status` / `\r` 重写状态行 | `\r` 只能单行、并发 agent 无法表达；更脆 |

**采纳方案 1。**

## 5. 设计

### 5.1 去重策略（rich 模式）

| 事件 | 上方滚动一行 | 进底部状态行 |
|---|:---:|:---:|
| `WorkflowHeader`（banner） | ✅（Live 启动前打印，天然在顶） | — |
| `PhaseEvent` | ❌（rich 下抑制） | ✅ `current_phase` |
| `AgentEvent` start/end | ✅ `AGENT ▶/✓`（含耗时/花费） | ✅ running agent → spinner |
| `ToolCallEvent` | ✅ `🔧 …` | — |
| `LlmTurnEvent` | ✅ `💭 Tn …` | — |
| `ErrorEvent` | ✅ 红色 | — |
| `SummaryEvent` | ✅ 结束 Panel + 表 | — |

- **`Phase` 去重**：phase 是持久状态，状态行常驻显示，rich 模式不再额外滚 `[ts] PHASE` 行。
- **`Agent` 不去重**：agent start/end 是**瞬态事件**，带时间戳滚上去有审计价值；状态行的 spinner 表达「当前在跑」，二者语义不同，保留双出不算冗余。
- **非 rich / 管道 / CI**：无状态行可承载 phase，`PHASE` 行照常滚动（`show_phase=True`）。该路径行为不变。

### 5.2 状态行渲染（`live_dashboard.py`）

把 `_render` 从「两层 `expand=True` grid + 多行 agent 表 + `─*60`」改为「全宽 dim 分隔线 + 一条紧凑状态行」：

- 外层 `Table.grid()` 用**默认 `expand=False`**：各列按自然宽度排列，不再等比撑出空白（修根因 2）。
- 分隔线宽度取 `options.max_width`：与真实终端宽度恒等对齐（修根因 3）。
- 删除 `_agent_line` 整张多行表：running agent 已并入状态行尾部。

```python
from rich.console import Group
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner
# format_duration 复用自 shannon_core.display.formatters

def __rich_console__(self, console, options):
    yield self._render(options)

def _render(self, options):
    snap = self._snapshot
    elapsed = format_duration(int(time.monotonic() - self._start_monotonic) * 1000)
    running = [r for r in snap.agents.values() if r.status == "running"]
    cells = [
        Text(snap.current_phase or "—", style="bold cyan"),
        Text(f" · {snap.completed_count} done", style="green"),
        Text(f" · {elapsed}"),
        Text(f" · ${snap.total_cost:.4f}", style="yellow"),
    ]
    if running:
        cells += [Text("    "), Spinner("dots"),
                  Text(" " + " · ".join(r.name for r in running), style="blue")]
    row = Table.grid()                 # expand=False：自然宽度，无大片空白
    row.add_row(*cells)
    return Group(
        Text("─" * options.max_width, style="dim"),   # 与真实宽度对齐
        row,
    )
```

渲染效果（单行）：

```
──────────────────────────────────────────────────
 pre-recon · 2 done · 1m 12s · $0.0421    ⠋ recon
```

`DashboardState` 一行不改（`current_phase` / `completed_count` / `total_cost` / `agents` 字段已满足）。

### 5.3 生命周期与接线

**`display_lifecycle.py`**：`Live(..., transient=False)` → **`transient=True`**。

> 这是对 `2026-06-15-*-live-display-design` 中 `transient=False` 决定的修订。原决定保留末帧作为记录，但实际造成 summary 打印后下方残留一条过期状态行——正是「乱」的一部分。`transient=True` 下，`SummaryEvent`（在 `with live:` 内 dispatch）先打印到 Live 上方，Live 退出时擦除状态行，最终可见输出即 summary（banner + 滚动事件 + summary）。`workflow.log` 文件仍保留完整记录，无信息损失。

**`workflow_logger.py:46-49`**：构造 `RichConsoleRenderer` 时传去重开关：

```python
if self._console is not None:
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    renderers.append(RichConsoleRenderer(self._console, show_phase=not self._use_rich))
```

rich → `show_phase=False`；非 rich → `show_phase=True`。

**`rich_renderer.py`**：新增 `show_phase` 开关（默认 `True`，保持其它调用方行为）：

```python
def __init__(self, console=None, show_phase: bool = True) -> None:
    self._console = console or Console()
    self._show_phase = show_phase

# render() 内：
case PhaseEvent():
    if self._show_phase:
        self._render_phase(event)
```

### 5.4 改动文件清单（集中在 core）

| 文件 | 改动 |
|---|---|
| `core/.../display/live_dashboard.py` | 重写 `_render` 为「全宽 dim 分隔线 + 单状态行」；删除 `_agent_line` |
| `core/.../display/rich_renderer.py` | 新增 `show_phase` 开关；`_render_phase` 受其门控 |
| `core/.../audit/workflow_logger.py` | 构造 `RichConsoleRenderer` 传 `show_phase=not use_rich` |
| `core/.../audit/display_lifecycle.py` | `Live(..., transient=True)` |
| `core/tests/display/test_live_dashboard.py` | 更新断言：单状态行、分隔线宽度 == 注入 max_width、有 running agent 时含 `Spinner` |
| `core/tests/display/test_rich_renderer.py` | 新增 `show_phase=False` 抑制 `PhaseEvent`、默认仍渲染的用例 |
| `whitebox/tests/test_display_integration.py`、`blackbox/tests/test_display_integration.py` | rich 路径：`PhaseEvent` 只更新 dashboard 不滚动；结束 summary 干净 |

`packages/whitebox/src/shannon_whitebox/audit/display_lifecycle.py` 为 `from ... import *` 兼容垫片，blackbox 直接 import core，故无需改动 CLI 侧。

## 6. 边界与假设

1. **错误处理**：`ErrorEvent` 按上方红色滚动行处理；状态行不额外标记失败——失败的 agent 体现在 `N done` 计数 + 滚动的 `AGENT ✗` 行。
2. **并发 agent**：状态行展示**所有** `status=="running"` 的 agent，名取 ` · ` 连接（通常 1 个；并发时 `⠋ recon · ⠋ vuln-scan`）。`DashboardState.agents` 本就是按名索引 dict，天然支持。
3. **取消（exit 130）**：与近期 graceful-shutdown 工作不冲突——`finally: session.close()` 触发 Live 退出，`transient=True` 擦除状态行，取消路径反而更干净。
4. **宽度探测**：`options.max_width` 由 Rich 依 TTY 实测给出；非 TTY 不挂 dashboard（既有行为），无影响。

## 7. 测试策略

- **`LiveDashboardRenderer` 单测**：构造 `DashboardState` 快照（含/不含 running agent），用带固定 `max_width` 的 `ConsoleOptions` 渲染，断言：(a) 输出为「一条 dim 全宽分隔线 + 一行状态」；(b) 分隔线 `─` 数量 == 注入 `max_width`；(c) 不含旧多行 agent 表；(d) 有 running agent 时行内含 `Spinner`。
- **`RichConsoleRenderer` 单测**：`show_phase=False` 下 dispatch `PhaseEvent`，捕获 console 输出断言无 `PHASE` 行；默认 `show_phase=True`（及非 rich）下仍有。
- **集成（whitebox/blackbox `test_display_integration.py`）**：rich 路径断言 `PhaseEvent` 仅更新 dashboard 状态、不产生滚动 `PHASE` 行；`SummaryEvent` 后 Live 退出、最终可见为 summary。
- 回归限定在 display 改动路径（与本仓库近期 plan 的「回归限定到变更路径」约定一致），不跑全包扫描。

## 8. 风险

- **Rich「print 落 Live 上方」行为**：本设计仍依赖之（前序 spec §6 已 probe 验证）。状态行行数固定为 2（分隔线 + 状态），Live 重绘区域稳定，光标控制更不易错位。
- **`transient=True` 末帧消失**：有人可能期望保留末状态行作记录——但 summary 表更完整，且 `workflow.log` 有全量记录，无信息损失。若后续需要保留，单点回退 `transient=False` 即可。
- **`options.max_width` 极窄终端**：分隔线随宽度收缩，状态行可能折行；可接受（折行不破坏可读性），不额外处理。
