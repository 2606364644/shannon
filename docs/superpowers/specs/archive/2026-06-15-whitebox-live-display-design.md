# Whitebox 实时日志展示设计：接入渲染管线 + 并行 agent 仪表盘

> 日期: 2026-06-15 | 状态: 设计待评审
>
> 关联文档:
> - 前置 spec（渲染层组件，已实现但未接入）: [`2026-06-13-logging-display-optimization-design.md`](./2026-06-13-logging-display-optimization-design.md)
> - 差距分析（含 §3.4 接入缺口）: [`docs/gap/logging-display-gap-analysis.md`](../../gap/logging-display-gap-analysis.md)
> - 原始项目（TypeScript）对照基线: `/Users/mango/project/shannon-refactor/shannon`

---

## 1. 背景与问题

`logging-display-optimization` plan 已交付一套**完整且单测覆盖（93 绿）的渲染层**：`DisplayEvent` 事件族、`DisplayDispatcher`、`FileLogRenderer`、`RichConsoleRenderer`、错误分类。但差距分析 §3.4 指出，这套渲染层**从未接入生产 activity pipeline**：

- `AuditSession`（唯一构造 `WorkflowLogger` 的地方）在生产代码**零实例化**，其生命周期方法（`initialize`/`start_agent`/`log_phase_*`/`log_workflow_complete`）在生产代码**零调用**。
- activity 实际走 `shannon_core.logging.create_activity_logger()`（core `ActivityLogger`，仅 info/warn/error），与 display 管线无关；`activities.py:19` 的 `from ... import AuditSession` 是**未使用 import**。
- `RichConsoleRenderer` 默认 `use_rich=False`，且**无任何 CLI flag / env / TTY 判断开启它**。
- 扫描期间用户在 stdout 看到的唯一动态输出是 `worker.py:poll_workflow_progress` 里的一行 `print()`（每 30s 一次），与渲染管线平行、互不相通。

**结果**：渲染层组件全绿，但从 `shannon-whitebox start` 的视角**按设计不可见**。

同时对照原始 Shannon（TS）的实时 stdout 体验（其 local mode 用 `fork(runner.js, {stdio:'inherit'})` 把 worker 输出直灌终端），原始项目**做得好的**：phase 分隔、并行 agent 前缀（`[Injection]`/`[XSS]`…）、工具 emoji 化、错误分类、cost/duration 追踪、单行 spinner。**做得差的（超越点）**：

- 🔴 **并行 agent 抢同一行**——同时刻只有一个 spinner、输出交错，看不到"几个 agent 在并行、各自到第几轮"。最大弱点。
- 🟡 无总体进度（phase X/N、已完成 M/total、ETA、累计花费）。
- 🟡 重试/退避不可见；仅 agent 级进度，无子任务分解。

## 2. 目标 / 非目标

**目标（"对齐 + 并行仪表盘"档）**

1. 把渲染管线真正接入白盒 Temporal activity 生命周期（堵住 §3.4 缺口）。
2. 开启 Rich 实时 stdout，达到并补齐原始项目的全部 live 能力（phase / agent / tool / error / 摘要）。
3. 新增原始项目没有的**并行 agent 实时仪表盘**（Rich `Live` + agent 行）+ 总体进度（phase X/N、M/total、elapsed、$sofar）。
4. 删除 `print()` 心跳，进度改为**事件驱动、零轮询**。

**非目标（本次不做）**

- Blackbox 扫描（whitebox 先打通；blackbox 后续套同一模式）。
- 交互控制（暂停/跳过）、子任务级进度分解、实时调用量/指标仪表盘、重试退避倒计时（属"全面超越"档，未选）。
- LOG-A3 LogStream 背压（维持 YAGNI）。

## 3. 关键约束（已验证）

1. **Temporal workflow 必须 deterministic、禁止 I/O**（不能写文件、写终端）。`AuditSession`/`WorkflowLogger`/renderers/`Live` **只能在 activity 或 driver 侧**，不能放 workflow。
2. **worker 进程内运行**：`run_scan`（`worker.py`）用 `async with worker:` 在进程内跑 Temporal worker，activity 在同一 event loop、同一终端所有权下执行 → Rich `console.print` 能直达用户终端（等价于原始项目 local mode）。
3. **async activity 单 event loop**：`async def` activity 作为 asyncio task 并发，只在 await 点交错；无真并行。
4. **`DisplayDispatcher.dispatch` 不跨事件加锁**（`for r: await r.render(event)`）→ 并发事件会交错，**需要外部锁**。
5. **`LogStream.write` 异步且每次 flush**（`await write; await flush`）→ 不加锁会撕开行。
6. **Rich `Live` 语义（已 probe 验证）**：
   - `Live(renderable, ...)` 的 `renderable` 必须是带 `__rich_console__` 的渲染对象或 `Renderable`；**不能是裸 callable**（此 Rich 版本会抛 `NotRenderableError`）。
   - TTY 模式下 `Live` 自带 refresh 线程（`refresh_per_second`），**每 tick 重新调用渲染对象的 `__rich_console__`**（probe：0.5s 内 13 次）。→ 事件只需改状态，动画/elapsed 由 refresh 线程白送。
   - `console.print(...)` 在 `Live` 期间正常工作，行落在 live 区域上方（probe：捕获到 EVENT 行 + 光标控制转义原地重绘）。
   - **非 TTY 不启动 refresh 线程**（probe：仅 1 次渲染）→ 无动画，故 plain/CI 模式不挂仪表盘。
   - `Spinner` 在 `Live` 内随 console 内部时间走帧（标准行为；本设计依赖之）。

## 4. 架构与拓扑

新增一个 **worker 级 `AuditSession` 单例**，由 `run_scan`（driver，拥有终端）在启动 worker 前创建，持有共享 `Console` + Rich `Live` 上下文 + `workflow.log` 句柄，整个扫描期间存活。activity 通过 `get_audit_session()` 取到它。事件仍走现有 `WorkflowLogger → DisplayDispatcher → renderers` 管线；新增第 3 个**有状态 renderer** `LiveDashboardRenderer` 做底部仪表盘。

```
run_scan (driver, 拥有终端)
 │ 1. use_rich = sys.stdout.isatty() and not plain
 │ 2. 建 console（plain → force_terminal=False）；若 rich 准备 Live(transient=False, refresh_per_second=10)
 │ 3. AuditSession(meta, use_rich, console, live) → 注册模块级单例
 │ 4. await session.initialize(wf_id) → 开 workflow.log → 发 WorkflowHeader
 │ 5. 若 rich：async with Live(...) 进入（终端所有权确立）；header 已打在上方
 │ 6. 跑 worker、await handle.result()
 │ 7. 收尾：发 SummaryEvent → 退出 Live → session.close() → 清单例
 ▼
WhiteboxScanWorkflow.run (deterministic, 禁 I/O) 排程 activities:
   • phase-marker activities → AuditSession.log_phase_*        → PhaseEvent
   • agent activities        → AuditSession.start/end_agent    → AgentEvent
                              SessionToolAuditLogger → AuditSession.log_event → ToolCallEvent / LlmTurnEvent
   • 收尾                    → AuditSession.log_workflow_complete → SummaryEvent
            │ 每个事件：
   DisplayDispatcher.dispatch(event)   # 持全局 asyncio.Lock（dispatcher 内），整段串行
   ├─▶ FileLogRenderer       → await LogStream.write(line)   # workflow.log，不交错
   ├─▶ RichConsoleRenderer   → console.print(line)           # 落在 Live 区域上方
   └─▶ LiveDashboardRenderer → 产出新的不可变 DashboardState 快照，原子换引用
                                 # Live refresh 线程下个 tick 读最新快照重绘
```

**三个关键决定：**
- **进度零轮询**：phase X/N、M/total、elapsed、$sofar 全部由事件流在 `LiveDashboardRenderer` 内推导（`PhaseEvent` 设当前 phase；数 `AgentEvent(end)` 得 completed；renderer 记启动时刻算 elapsed；累加 `AgentEvent(end).cost_usd` 得 cost）。`poll_workflow_progress` + `print()` **整条删除**。
- **终端唯一所有权**：只有 `LiveDashboardRenderer` 用 `Live`；`RichConsoleRenderer` 与之共享同一 `Console`，滚动日志自动落仪表盘上方。
- **复用 > 重写**：`AuditSession`/`WorkflowLogger`/`DisplayDispatcher`/`FileLogRenderer`/`RichConsoleRenderer` 均已存在且单测覆盖；本设计只**接线** + 加一个 renderer + driver 层 Live 胶水。

## 5. 组件清单

### 🆕 新增

| 组件 | 位置 | 职责 | 关键依赖 |
|---|---|---|---|
| `LiveDashboardRenderer` | `core/display/live_dashboard.py` | 底部仪表盘有状态 renderer：每个事件产出新 `DashboardState` 快照并原子换引用；持有 `Live` 引用；`Live` refresh 线程读最新快照重绘 | Rich `Live`/`Console`，复用 `agent_prefix`/`humanize_tool_call` |
| `DashboardState` | 同上（不可变 dataclass） | 纯状态机：吃 DisplayEvent、产出下一步状态。**不碰 Rich**，可脱离终端单测（L1） | `events.py` |
| `get_audit_session()` + 模块级单例槽 | `whitebox/audit/` | activity 取当前扫描的 AuditSession 单例；无单例返回 `NullAuditSession`（测试/独立运行安全） | 仿 `create_activity_logger()` |
| `SessionToolAuditLogger` | `whitebox/audit/`（实现 core `ToolAuditLogger`） | 把 `log_tool_start`/`log_tool_end`/`log_error` 桥接到 `AuditSession.log_event` → `WorkflowLogger` → `ToolCallEvent`。替换现有 `ActivityToolAuditLogger(create_activity_logger())` | core `ToolAuditLogger` ABC |
| phase-marker activities | `whitebox/pipeline/activities.py` | 极薄：`log_phase_start/complete` → `get_audit_session().log_phase_*`。**仅当选方案 (i)** | `@activity.defn` |

> **YAGNI**：原考虑 `PlainStdoutRenderer` 做 CI 降级。但 `RichConsoleRenderer` 在非 TTY `Console` 上自动去 ANSI、按行打印 → **plain 模式 = 不挂 `LiveDashboardRenderer`** 即可，无需新 renderer。

### ✏️ 改动

| 组件 | 改动 |
|---|---|
| `WorkflowLogger` | 构造参数加 `console`/`live`；`use_rich` 时同时挂 `RichConsoleRenderer(console)` 和 `LiveDashboardRenderer(live)`（当前只挂前者且自带 Console） |
| `AuditSession` | 构造参数加显示配置（`use_rich`/`console`/`live`），透传 `WorkflowLogger`；进出 `Live` 的生命周期 |
| `run_scan`（`worker.py`） | 建 shared `Console`+`Live`；构造 AuditSession（带显示配置）；注册单例；`async with Live(...)` 包住 worker；收尾清理；**删除 `poll_workflow_progress` 及其 task** |
| agent activities（`activities.py`） | **接线核心**：取 `get_audit_session()`，传 `SessionToolAuditLogger(session)` 给 `AgentExecutor`（让 tool 事件进管线）；agent 执行前后调 `session.start_agent()`/`end_agent()`；捕获异常时 `session.log_error()` 再 raise |
| CLI `start`（`main.py`） | 加 `--plain` flag；`use_rich = sys.stdout.isatty() and not plain`，透传 `run_scan` |
| `WhiteboxScanWorkflow.run` | 仅方案 (i)：在各 phase 边界排程 phase-marker activity |

### 🗑️ 删除
- `poll_workflow_progress` + `print()` 心跳（`worker.py:35-47`）。
- `activities.py:19` 未使用的 `AuditSession` import 变为真正使用（或被 `get_audit_session` 取代）。

### ♻️ 原样复用
`DisplayDispatcher`、`FileLogRenderer`、`RichConsoleRenderer`、`events.py`、`formatters.py`（`agent_prefix`/`humanize_tool_call`/`summarize_todo`/`maybe_browser_action`/`format_error_block`）、`errors/classification.py`、`LogStream`、`MetricsTracker`、`AgentLogger`。

## 6. 数据流与并发（已验证并修正）

### 启停（driver `run_scan`）
1. `use_rich = sys.stdout.isatty() and not plain`
2. `console = Console()`（plain → `force_terminal=False, no_color=True`）；rich 时准备 `Live(console=console, transient=False, refresh_per_second=10)`
3. `AuditSession(meta, use_rich, console, live)` → 注册单例
4. `await session.initialize(wf_id)` → 开 `workflow.log`（LogStream）→ 发 `WorkflowHeader`
5. rich：`async with Live(...)` 进入；header 已在上方
6. 跑 worker、`await handle.result()`
7. 收尾：发 `SummaryEvent` → 退出 `Live` → `session.close()` → 清单例

### 事件热路径
```
activity 内 (message_dispatcher / phase-marker / 收尾)
   │ await get_audit_session().log_*(...)
   ▼
DisplayDispatcher.dispatch(event)   # 持全局 asyncio.Lock，整段串行
   ├─▶ FileLogRenderer       → await LogStream.write(line)
   ├─▶ RichConsoleRenderer   → console.print(line)           # Live 上方
   └─▶ LiveDashboardRenderer → 新建不可变 DashboardState 快照，self._snapshot = snap（原子换引用）
```

### 仪表盘内容（零轮询）
```
───────────────────────────────────────
 Phase 3/7 · Vulnerability Analysis   4/13 done · 142s · $0.84
 ⠋ Injection  t4  🖊️ Bash(rg -n eval)
 ⠹ XSS        t3  ⏳ awaiting model
 ✓ Auth       45s $0.23
 ⠼ Authz      t2  📂 Read(app.js)
```
- **顶栏**：current phase（`PhaseEvent`）、completed（数 `AgentEvent(end)`）、elapsed（renderer 记启动时刻、渲染时实时算）、cost（累加 `AgentEvent(end).cost_usd`）。
- **agent 行**：`AgentEvent(start)` 建行；`ToolCallEvent`/`LlmTurnEvent` 更新"最近动作+轮次"；`AgentEvent(end)` 定格 ✓/✗ + 时长花费。
- **事件间也动**：`Live` refresh 线程每 tick 重新调用渲染对象的 `__rich_console__`，读取当前快照 + 实时算 elapsed + `Spinner` 走帧。事件只负责改状态。

### 并发安全（三道各管一摊）
1. **全局 `asyncio.Lock`**（放在 `DisplayDispatcher.dispatch` 内）：每次 `dispatch()` 整段持锁 → 并行 activity 的事件**排队逐个渲染**，日志行/文件写入不交错、快照构建不重叠。每扫描一个 dispatcher、一把锁。锁跨越 `LogStream.write` 的 await 是可接受的（事件频率低）。
2. **`DashboardState` 不可变快照 + 原子换引用**：事件产出完整快照后 `self._snapshot = snap`；GIL 保证该赋值原子；`Live` refresh 线程读到的永远是完整状态。**这修正了"refresh 线程只读即安全"的 sloppy 论断**——读可变共享状态本是竞争，改不可变快照才严谨。
3. **Rich `Console` 内部锁**：真正的终端写（我们的 `print` vs Live 重绘）由 Rich 自带锁串行。

## 7. 降级 / `--plain` + 错误 / 重试 / resume

### 7.1 三种渲染模式
| 模式 | 触发 | renderer 组合 | 用户看到 |
|---|---|---|---|
| Rich | TTY 且非 `--plain` | File + RichConsole + LiveDashboard | 滚动日志 + 底部仪表盘 |
| Plain | `--plain` 显式 | File + RichConsole（非终端 console） | 每事件一行纯文本，无仪表盘 |
| CI/管道 | stdout 非 TTY | 同 Plain（自动降级） | 同 Plain，无乱码 |

### 7.2 心跳全删（所有模式）
- Rich → 仪表盘顶栏即持续进度信号，事件间隙靠 refresh 线程照转。
- Plain/CI → 事件流本身即进度（phase start / agent start-end / tool call 各一行）。长静默不额外加心跳（YAGNI）；若 CI 实测长静默，后续可加极薄 `PipelineProgress` 轮询行。

### 7.3 错误
- activity 捕获异常时先 `session.log_error(err, context)` 再 raise `ApplicationFailure`（当前未调，要补）。
- Rich 滚动日志：`ErrorEvent` → 分类块（`ErrorType`/message/context/`display_retryable`）。
- 仪表盘：该 agent 行变 `✗`（红）+ 错误类型缩写。
- Plain：同块纯文本。

### 7.4 重试
- Temporal 重试 → activity 重跑 → `AgentEvent(start, attempt=N+1)`；仪表盘行 attempt 号更新，日志显示 `starting (attempt 2)`。
- **退避间隙不显示倒计时**（属"全面超越"档，未选）。间隙里行短暂 ✗、重跑后回 ⠋，固有可接受。

### 7.5 Resume（`--workspace` 续跑）
- `AuditSession.log_resume_header` 发 `ResumeEvent`（previous/new workflow id、checkpoint、**已完成 agents**）。
- **`LiveDashboardRenderer` 收到 ResumeEvent 用 `completed_agents` 预填仪表盘**（那些 agent 以 ✓ 起始），避免续跑仪表盘误导。日志显示 resume 头块。

### 7.6 收尾摘要（去重）
- `SummaryEvent` → Rich 模式渲染 Table。Rich 模式下**取代**现有 CLI `start` 末尾的 `click.echo` 摘要（避免重复）；Plain 模式保留文本摘要（或由事件文本覆盖，plan 期定）。

## 8. 测试与验收（防重蹈覆辙）

上一版失败根因：组件单测全绿，但无测试驱动 `AuditSession → 渲染器` 真实链路。本节堵死此洞。

### 测试金字塔
| 层级 | 测什么 | 依赖 | CI 必过 |
|---|---|---|---|
| L1 单元 | `DashboardState` 纯状态机（吃事件序列→断言 phase/agents/totals，不碰 Rich）；`SessionToolAuditLogger` 桥接；`get_audit_session()` 单例/Null；`AuditSession` 显示配置透传 | 无 | ✅ |
| **L2 显示集成（关键闸）** | 脚本化事件序列**驱动 `AuditSession`**（真实 WorkflowLogger→dispatcher→renderers），`Console(StringIO, force_terminal=True)` 捕获，断言**同时**含滚动日志行 + 仪表盘区（顶栏 phase 行 + agent 行） | 无 Temporal/LLM | ✅ |
| L3 activity 接线 | 构造 `SessionToolAuditLogger(get_audit_session())` + `MessageDispatcher`，喂脚本化 SDK 事件，断言**到达 DisplaySession** | 无 Temporal/LLM | ✅ |
| L4 手动冒烟 | `uv run shannon-whitebox start -r <fixture> --pipeline-testing` 真 TTY，人眼确认滚动日志+仪表盘；再验 `--plain` 与 `| tee` 降级 | Temporal dev server | 人工签字 |

**L2 就是当年缺的那道闸**：若 `AuditSession` 没真正驱动渲染器，捕获为空 → 测试直接红。

### Definition of Done（每条可 grep/可观察）
- [ ] `grep "AuditSession("` 出现**非测试**调用方
- [ ] activity 调了 `get_audit_session()`
- [ ] 真跑 `start`，`workflow.log` 由 `FileLogRenderer` 写出内容（非空）
- [ ] 真跑 `start`，TTY 终端同时出现滚动日志 + 仪表盘
- [ ] `poll_workflow_progress` / `print()` 心跳已删（grep 不到）
- [ ] `--plain` 生效；非 TTY 自动降级（管道无 ANSI 乱码）

任一条不满足 = 任务没完成。

## 9. 交 plan 的开放项（不影响验收）

1. **phase-marker activity vs 薄轮询**——倾向 marker（纯事件驱动、单一数据源），plan 期对照真实 workflow 代码拍。
2. **LLM-turn 事件调用点定位**——`message_dispatcher.py` 前 110 行未见 `log_llm_response` 调用，需在 provider/executor 定位或新增。
3. **`ToolAuditLogger`/`AuditLogger` ABC reconcile**——`MessageDispatcher` 要 core `ToolAuditLogger`（`log_tool_start`/`log_tool_end`/`log_error(error,*,turn_count,duration_ms)`），与 whitebox `AuditLogger` 的 `log_error` 签名不同；`SessionToolAuditLogger` 严格实现 `ToolAuditLogger`。
4. **确认扫描期间无其他代码抢 stdout**——`shannon.activity` logger 等，保证 display 独占终端。

## 10. 验证证据（probe / 代码核对，2026-06-15）

| 论断 | 证据 |
|---|---|
| `Live` 刷新线程每 tick 重渲染 | `__rich_console__` 在 0.5s（refresh_per_second=20）被调 13 次 |
| `console.print` 在 Live 期间正常输出 | 捕获含 EVENT 行 + 光标控制转义（原地重绘） |
| 非 TTY 不启动刷新线程 | 非 TTY 仅渲染 1 次 |
| `Live` 不接受裸 callable | 传 callable 抛 `NotRenderableError` → 必须 `__rich_console__` 渲染对象 |
| `DisplayDispatcher` 不跨事件加锁 | `dispatcher.py`: `for r: await r.render(event)` |
| `LogStream.write` 异步 + 每次 flush | `log_stream.py:21-26` |
| `MessageDispatcher` 要 core `ToolAuditLogger` | `message_dispatcher.py:31,85`；现有桥接 `ActivityToolAuditLogger`→core `ActivityLogger`，不经 display |
