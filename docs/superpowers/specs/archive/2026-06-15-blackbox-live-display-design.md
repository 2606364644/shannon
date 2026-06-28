# Blackbox 实时日志展示设计：复用 core audit 层 + 并行 exploit 仪表盘

> 日期: 2026-06-15 | 状态: 已实现（Tasks 1-7 完成；手动冒烟待人工签字）
>
> 关联文档:
> - 姊妹 spec（白盒，已实现）: [`2026-06-15-whitebox-live-display-design.md`](./2026-06-15-whitebox-live-display-design.md)
> - 白盒实现计划（本设计镜像其方案）: [`../plans/2026-06-15-whitebox-live-display.md`](../plans/2026-06-15-whitebox-live-display.md)
> - 渲染层 spec（事件/dispatcher/renderer，已实现）: [`2026-06-13-logging-display-optimization-design.md`](./2026-06-13-logging-display-optimization-design.md)
> - 差距分析（§3.4 接入缺口，白盒已堵、黑盒待堵）: [`docs/gap/logging-display-gap-analysis.md`](../../gap/logging-display-gap-analysis.md)
> - 已取代的旧设计（file-watcher 路线，**不采用**）: [`2026-06-10-realtime-console-logging-design.md`](./2026-06-10-realtime-console-logging-design.md)

---

## 1. 背景与问题

白盒 live-display（[`2026-06-15-whitebox-live-display.md`](../plans/2026-06-15-whitebox-live-display.md)）已交付并合入 `feat/fork-py`：渲染管线真正接入白盒 Temporal activity 生命周期、Rich 实时 stdout、并行 agent 仪表盘、删除 `print()` 心跳。**但黑盒被整体跳过**——白盒 spec §2 非目标明确写了 "Blackbox 扫描（whitebox 先打通；blackbox 后续套同一模式）"。

代码级核对（2026-06-15，`feat/fork-py` HEAD）确认黑盒处于**白盒改之前 1:1 的状态**，gap 分析 §3.4 的接入缺口原封不动：

- `packages/blackbox` **没有 `audit/` 目录**——无 `AuditSession`、无 `WorkflowLogger`、无 `get_audit_session`、无 `SessionToolAuditLogger`、无 display 生命周期。
- 黑盒 activity 实际走 `shannon_core.logging.create_activity_logger()`（core `ActivityLogger`，仅 info/warn/error），与 display 管线无关（`pipeline/activities.py` 的 `run_recon`/`run_exploit_agent`/`run_report_agent`/`run_blackbox_auth_validation` 全部 `audit_logger=create_activity_logger()`）。
- 扫描期间用户在 stdout 看到的唯一动态输出是 `worker.py:poll_workflow_progress` 里的一行 `print()`（`worker.py:30`：`print(f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/13")`），每 30s 一次，与渲染管线平行、互不相通。
- CLI `start`（`cli/main.py`）**无 `--plain` flag、无 TTY autodetect、无 `use_rich`**。

**结果**：`shannon-blackbox start` 视角下，渲染层组件对用户**按设计不可见**。

**关键利好**：黑盒的 `exploitation` phase 用 `asyncio.Semaphore(max_concurrent)` **并行跑 N 个 `{vt}-exploit` agent**（`workflows.py:215-226`）——这正是白盒仪表盘要解决的"并行 agent 抢同一行"主场景，且比白盒更典型（白盒并行 vuln agent，黑盒并行 exploit agent）。所以黑盒是仪表盘**更强烈的用例**，而非边缘场景。

> **关于已取代的 `2026-06-10-realtime-console-logging` 设计**：那份走的是 file-watcher tail `workflow.log` 路线（`WorkflowEventBridge` + `ActivityAuditContext` + watchdog tail），且其 "Out of Scope" 自己写了 "Blackbox side — same pattern, implement after whitebox is verified"。白盒最终落地的是**事件驱动**路线（AuditSession + Live，而非 file-watcher）。**本设计镜像白盒已验证的事件驱动方案，不采用旧 file-watcher 路线。**

## 2. 目标 / 非目标

**目标（"对齐 + 上移 + 并行仪表盘"档）**

1. 把白盒 `audit/` 层**上移到 `shannon_core/audit/`**（已核对：包无关，零 whitebox 特化），白盒经兼容 shim 继续可用。
2. 把渲染管线接入黑盒 Temporal activity 生命周期（堵住黑盒版 §3.4 缺口）。
3. 开启 Rich 实时 stdout：滚动事件日志 + 底部并行 exploit agent 仪表盘 + 总体进度（phase、N done、elapsed、$sofar）。
4. 删除黑盒 `print()` 心跳，进度改为**事件驱动、零轮询**。
5. CLI 加 `--plain` + TTY autodetect，与非 TTY/CI 优雅降级。

**非目标（本次不做）**

- 白盒显示逻辑改动（仅做 audit 层物理位置上移 + import 重定向，不改逻辑）。
- exploit 校验摘要（✅/⏭️/⚠️ per vuln）接入 display——留在 Temporal logger；被跳过的 vuln 自然不发 `AgentEvent(start)`、不出现在仪表盘。
- `combined` 包显示 wiring——它是 orchestrator，委托各自 `run_scan`，两端接好后自动受益。
- 交互控制、重试退避倒计时、recon-skip 显式提示（YAGNI）。
- 旧 file-watcher 路线（`2026-06-10`）的任何组件。

## 3. 关键约束（已验证，2026-06-15）

1. **Temporal workflow 必须 deterministic、禁止 I/O**——`AuditSession`/`WorkflowLogger`/renderers/`Live` 只能在 activity 或 driver 侧，不能放 workflow。（与白盒同；黑盒 `workflows.py` 现有 phase-marker 模式可直接套用。）
2. **worker 进程内运行**：黑盒 `run_scan`（`worker.py`）用 `async with worker:` 在进程内跑 Temporal worker，activity 在同一 event loop、同一终端所有权下执行 → Rich `console.print` 直达用户终端。
3. **async activity 单 event loop**：并行 exploit agent 作为 asyncio task 并发（`workflows.py:223` `asyncio.gather` + Semaphore），只在 await 点交错；无真并行。
4. **`DisplayDispatcher.dispatch` 已持全局 `asyncio.Lock`**（白盒 Task 4 已加，在 core）→ 并行 exploit agent 的事件**排队逐个渲染**，日志行/文件写入/快照构建不交错。黑盒**直接复用，无需再加锁**。
5. **Rich `Live` 语义**（白盒 spec §3.6 已 probe 验证，本设计继承）：TTY 下 refresh 线程每 tick 重调 `__rich_console__`；事件只改状态、动画/elapsed 白送；非 TTY 不启 refresh 线程 → plain/CI 不挂仪表盘。
6. **白盒 `audit/` 层包无关**（逐文件核对）：
   - `session.py`：仅 import `shannon_core.models` + 包内 `.agent_logger`/`.metrics_tracker`/`.utils`/`.workflow_logger`。
   - `workflow_logger.py`：仅 import `shannon_core.display.*`/`shannon_core.models.*` + 包内 `log_stream`/`utils`（其中 `from shannon_whitebox.audit.log_stream import LogStream` 与 `from shannon_whitebox.audit.utils import ...` 是**仅有的 2 处指向白盒的绝对 import**，上移时改相对 import）。
   - `session_registry.py`：仅 import `typing.Any`，**完全包无关**。
   - `display_lifecycle.py`/`session_tool_audit_logger.py`：仅 import core + 包内相对。
   - → 上移到 `shannon_core/audit/` 不引入循环依赖（`display`/`models`/`agents.tool_audit_logger`/`errors` 均不反向依赖 `audit`）。

## 4. 架构与拓扑

### 4.1 上移（先做，前置依赖）

`packages/whitebox/src/shannon_whitebox/audit/` 整目录 → `packages/core/src/shannon_core/audit/`：

```
shannon_core/audit/
├── __init__.py
├── session.py                   # AuditSession（facade）
├── workflow_logger.py           # WorkflowLogger → DisplayEvent
├── session_registry.py          # get/set/clear_audit_session + NullAuditSession（进程级单例）
├── session_tool_audit_logger.py # SessionToolAuditLogger（core ToolAuditLogger → AuditSession）
├── display_lifecycle.py         # run_with_display（Console+Live 生命周期）
├── agent_logger.py              # per-agent JSON 日志
├── metrics_tracker.py           # session.json 指标
├── utils.py                     # generate_workflow_log_path / initialize_audit_structure / save_prompt
└── log_stream.py                # aiofiles 流
```

上移时机械修正：
- `workflow_logger.py:14` `from shannon_whitebox.audit.log_stream import LogStream` → `from .log_stream import LogStream`
- `workflow_logger.py:15` `from shannon_whitebox.audit.utils import generate_workflow_log_path` → `from .utils import generate_workflow_log_path`
- `session_tool_audit_logger.py` 的 `TYPE_CHECKING: from shannon_whitebox.audit.session import AuditSession` → `from .session import AuditSession`

**白盒兼容 shim**：`shannon_whitebox/audit/__init__.py` 改为 re-export `shannon_core.audit` 的公开 API；深 import 调用点（`worker.py`/`activities.py`/测试里 `from shannon_whitebox.audit.session_registry import ...`）直接改成 `shannon_core.audit.*`。shim 后续可清理。

**单元测试随代码迁移**：`test_audit_session`/`test_session_registry`/`test_session_tool_audit_logger`/`test_workflow_logger` 等迁到 `packages/core/tests/audit/`（测 core 代码）；白盒/黑盒各保留自己的接线集成测试（L2/L3）。

**注册表单例共享**：core 持唯一 `_current`，白盒/黑盒各跑各的扫描（每进程同时一个），无冲突。

### 4.2 黑盒接线（镜像白盒 2026-06-15 方案）

```
run_scan (blackbox worker.py, 拥有终端)
 │ 1. use_rich = sys.stdout.isatty() and not plain
 │ 2. meta = SessionMetadata(id=workspace_name, web_url, repo_path, output_path=workspaces_dir)
 │ 3. async with run_with_display(meta, use_rich=use_rich) as session:   ← shannon_core.audit
 │ 4.    set_audit_session(session)
 │ 5.    起 worker、await handle.result()
 │ 6.    收尾：把 BlackboxPipelineState 适配成 WorkflowSummary → session.log_workflow_complete(summary)
 │ 7.    finally: clear_audit_session()
 ▼
BlackboxScanWorkflow.run (deterministic, 禁 I/O) 排程:
   • phase-marker activities → get_audit_session().log_phase_*        → PhaseEvent
   • run_recon / run_exploit_agent(×N 并行) / run_report_agent
        → get_audit_session().start_agent/end_agent                   → AgentEvent
        → SessionToolAuditLogger(session) 传入 executor               → ToolCallEvent / LlmTurnEvent
   • run_blackbox_auth_validation（按判断点 1 当 agent 行处理）
   • 异常 → session.log_error(err, context) 再 raise ApplicationFailure
   收尾 → session.log_workflow_complete(summary)                      → SummaryEvent
            │ 每事件经 DisplayDispatcher.dispatch（持 asyncio.Lock，整段串行）
            ├─▶ FileLogRenderer       → workspaces/<bb-ws>/workflow.log
            ├─▶ RichConsoleRenderer   → console.print（落 Live 上方）
            └─▶ LiveDashboardRenderer → 新 DashboardState 快照，原子换引用
```

## 5. 组件清单

### 🚚 上移（whitebox/audit → core/audit）

| 组件 | 改动 |
|---|---|
| `session.py`/`workflow_logger.py`/`session_registry.py`/`session_tool_audit_logger.py`/`display_lifecycle.py`/`agent_logger.py`/`metrics_tracker.py`/`utils.py`/`log_stream.py` | `git mv` 到 `shannon_core/audit/`；修正 3 处绝对 import 为相对；逻辑不动 |
| `shannon_whitebox/audit/__init__.py` | 改为 re-export shim 指向 `shannon_core.audit` |

### 🆕 新增

| 组件 | 位置 | 职责 |
|---|---|---|
| 黑盒 phase-marker activities | `blackbox/pipeline/activities.py` | `log_phase_start_activity`/`log_phase_complete_activity` → `get_audit_session().log_phase_*`（极薄，镜像白盒） |

### ✏️ 改动

| 组件 | 改动 |
|---|---|
| `blackbox/worker.py` | 套 `run_with_display`；`set/clear_audit_session`；删 `poll_workflow_progress` + `print()`；收尾发 `SummaryEvent`（适配 `BlackboxPipelineState → WorkflowSummary`） |
| `blackbox/cli/main.py` | `start` 加 `--plain` flag；`use_rich = sys.stdout.isatty() and not plain`；透传 `run_scan(input, temporal_address, use_rich=use_rich)` |
| `blackbox/pipeline/activities.py` | `run_recon`/`run_exploit_agent`/`run_report_agent`/`run_blackbox_auth_validation`：取 `get_audit_session()`，传 `SessionToolAuditLogger(session)`，前后 `start_agent`/`end_agent(AgentEndResult(...))`，异常 `log_error` 再 raise（镜像白盒 `run_agent`） |
| `blackbox/pipeline/workflows.py` | 在 preflight / auth-validation / recon-blackbox / exploitation / reporting 边界排程 phase-marker activities |
| `formatters.agent_prefix` | 补 `{vt}-exploit` 键（或剥 `-exploit` 后缀复用 vuln 前缀）——plan 期定精确形式 |

### 🗑️ 删除

- `blackbox/worker.py:21-33` 的 `poll_workflow_progress` + 其 `print()`（`worker.py:30`）。
- `worker.py:55` 的 `poll_task = asyncio.create_task(poll_workflow_progress(handle))` 及其 cancel/await 收尾。

### ♻️ 原样复用（core，**不改**）

`DisplayDispatcher`（含 `asyncio.Lock`）、`DashboardState`、`LiveDashboardRenderer`、`FileLogRenderer`、`RichConsoleRenderer`、`events.py`、`formatters.py`（`agent_prefix`/`humanize_tool_call`/`maybe_browser_action`/`summarize_todo`/`format_*`）、`errors/classification.py`、`AgentExecutor`、`MessageDispatcher.log_assistant_turn`。

## 6. 数据流与并发

### 启停（driver `blackbox/worker.py`）
1. `use_rich = sys.stdout.isatty() and not plain`
2. `meta = SessionMetadata(id=input.workspace_name or "blackbox-scan", web_url=input.web_url, repo_path=input.repo_path, output_path=str(resolve_workspaces_dir(input.repo_path)))`
3. `async with run_with_display(meta, use_rich=use_rich) as session:` → 注册单例
4. `await session.initialize(wf_id=meta.id)`（由 `run_with_display` 内部完成）→ 开 `workspaces/<bb-ws>/workflow.log` → 发 `WorkflowHeader`
5. rich：`run_with_display` 内进入 `Live(...)`；header 已在上方
6. 跑 worker、`await handle.result()`
7. 收尾：`summary = _to_workflow_summary(result)`（黑盒 state → `WorkflowSummary`）→ `await session.log_workflow_complete(summary)` → 退出 Live → `session.close()` → 清单例

### 事件热路径（与白盒完全一致，复用 core）
```
activity 内 (message_dispatcher / phase-marker / 收尾)
   │ await get_audit_session().log_*(...)
   ▼
DisplayDispatcher.dispatch(event)   # 持 core 全局 asyncio.Lock，整段串行
   ├─▶ FileLogRenderer       → await LogStream.write(line)
   ├─▶ RichConsoleRenderer   → console.print(line)           # Live 上方
   └─▶ LiveDashboardRenderer → 新建不可变 DashboardState 快照，原子换引用
```

### 仪表盘内容（黑盒具体形态）
```
───────────────────────────────────────
 Phase: exploitation   3/7 done · 142s · $0.84
 ⠋ Injection-exploit  t4  🖊️ Bash(curl ...)
 ⠹ XSS-exploit        t3  🌐 navigate /search?q=
 ✓ Authz-exploit      45s $0.23
 ⠼ SSRF-exploit       t2  …
```
- **顶栏**：current phase（`PhaseEvent`）、completed（数 `AgentEvent(end)`）、elapsed（renderer 记启动时刻、渲染时实时算）、cost（累加 `AgentEvent(end).cost_usd`）。
- **agent 行**：`AgentEvent(start)` 建行；`ToolCallEvent`/`LlmTurnEvent` 更新"最近动作+轮次"；`AgentEvent(end)` 定格 ✓/✗ + 时长花费。并行 exploit agent = 多个同时 running 的行（核心场景）。

### 并发安全（三道各管一摊，与白盒同；黑盒复用不改）
1. **全局 `asyncio.Lock`**（core `DisplayDispatcher.dispatch` 内）：并行 exploit agent 的事件排队逐个渲染。
2. **`DashboardState` 不可变快照 + 原子换引用**：事件产出完整快照后 `self._snapshot = snap`；GIL 保证赋值原子；`Live` refresh 线程读到完整状态。
3. **Rich `Console` 内部锁**：终端写串行。

> **关于 workflow 的 `current_agent` racy**：并行 gather 时 `workflows.py` loop 里 `self._state.current_agent = agent_name.value` 反复赋值。但 **display 用 agent 级事件、不读** `current_agent`，故无影响。

## 7. 降级 / `--plain` + 错误 / 重试 / resume

### 7.1 三种渲染模式（镜像白盒，由 core `run_with_display` 统一）
| 模式 | 触发 | renderer 组合 | 用户看到 |
|---|---|---|---|
| Rich | TTY 且非 `--plain` | File + RichConsole + LiveDashboard | 滚动日志 + 底部并行 exploit 仪表盘 |
| Plain | `--plain` 显式 | File + RichConsole（非终端 console） | 每事件一行纯文本，无仪表盘 |
| CI/管道 | stdout 非 TTY | 同 Plain（自动降级） | 同 Plain，无乱码 |

### 7.2 心跳全删
`poll_workflow_progress` + `print()` 整条删除。进度全事件驱动。

### 7.3 错误
- exploit agent 捕获异常时先 `session.log_error(err, context=agent_name)` 再 raise `ApplicationFailure`（当前未调，要补；镜像白盒 `run_agent` 的两个 except 分支）。
- Rich 滚动日志：`ErrorEvent` → 分类块；仪表盘该 agent 行变 `✗`（红）；Plain 同块纯文本。

### 7.4 重试
Temporal 重试 → activity 重跑 → `AgentEvent(start, attempt=N+1)`；仪表盘行 attempt 号更新。退避倒计时不显示（YAGNI）。

### 7.5 Resume
黑盒支持 `-w <workspace>` 续跑（`workflows.py` 用 `completed_agents` 跳过已完成）。driver 在续跑时调 `session.log_resume_header(ResumeInfo(..., completed_agents=...))` 预填仪表盘（已完成 agent 以 ✓ 起始），避免误导。（镜像白盒 §7.5。）

### 7.6 收尾摘要
`BlackboxPipelineState → WorkflowSummary` 适配后 `session.log_workflow_complete(summary)` 发 `SummaryEvent`；Rich 模式渲染 Table（取代/兼容 CLI `start` 末尾的 `click.echo` 摘要，plan 期定去重）。

### 7.7 判断点（默认如此，plan 可推翻）
1. **auth-validation 当 agent 行**：`run_blackbox_auth_validation` 内 `validate_authentication(...)` 包 `start_agent("auth-validation")`/`end_agent` + `SessionToolAuditLogger`，让浏览器登录可见。
2. **exploit 校验摘要不接 display**：被跳过的 vuln 不发 `AgentEvent(start)`、不出现仪表盘；摘要留 Temporal logger。

## 8. 测试与验收（防重蹈 §3.4 覆辙）

白盒上一版失败根因：组件单测全绿，但无测试驱动 `AuditSession → 渲染器` 真实链路。黑盒除复用此闸外，**新增"上移回归闸"**确保不动白盒。

### 测试金字塔
| 层级 | 测什么 | 依赖 | CI 必过 |
|---|---|---|---|
| L1 单元 | `DashboardState`/`SessionToolAuditLogger`/`get_audit_session`+Null/`AuditSession` 显示配置——**随代码迁到 `packages/core/tests/audit/`** | 无 | ✅ |
| **L2 显示集成（关键闸）** | 脚本化事件序列**驱动黑盒 AuditSession**（真实 WorkflowLogger→dispatcher→renderers），`Console(StringIO, force_terminal=True)` 捕获，断言**同时**含滚动日志行 + 仪表盘区（顶栏 phase + 并行 exploit 行 + done 计数） | 无 Temporal/LLM | ✅ |
| L3 activity 接线 | 黑盒 activity 路径（如 `run_exploit_agent`）取 `get_audit_session()` + `SessionToolAuditLogger`，喂脚本化 SDK 事件，断言到达 `workflow.log` | 无 Temporal/LLM | ✅ |
| **L0 上移回归闸（新增）** | 白盒既有 L2/L3 显示测试（`test_display_integration`/`test_activity_display_wiring`）**上移后仍全绿**——证明 shim/上移没破坏白盒 | 无 | ✅ |
| L4 手动冒烟 | `uv run shannon-blackbox start --url <fixture> --pipeline-testing` 真 TTY 人眼确认滚动日志+并行 exploit 仪表盘；再验 `--plain` 与 `\| cat` 降级 | Temporal dev server | 人工签字 |

**L2 是当年缺的那道闸**：若 `AuditSession` 没真正驱动渲染器，捕获为空 → 测试直接红。

### Definition of Done（每条可 grep/可观察）
- [ ] `grep "AuditSession("` 命中 `packages/blackbox/src` **非测试**调用方
- [ ] 黑盒 activities 调了 `get_audit_session()`（`run_recon`/`run_exploit_agent`/`run_report_agent`/`run_blackbox_auth_validation`）
- [ ] 黑盒 `poll_workflow_progress` / `print()` 已删（`worker.py` grep 不到）
- [ ] `shannon-blackbox start --plain` 存在；管道非 TTY 自动降级（无 ANSI 乱码）
- [ ] **白盒显示测试上移后仍全绿**（L0 上移回归闸）
- [ ] 真跑 blackbox，`workspaces/<bb-ws>/workflow.log` 由 `FileLogRenderer` 写出非空内容
- [ ] 真跑 blackbox，TTY 终端同时出现滚动日志 + 并行 exploit 仪表盘

任一条不满足 = 任务没完成。

## 9. 交 plan 的开放项（不影响验收）

1. **白盒 shim 形式**——`__init__.py` re-export + 改深 import 调用点 vs 全量改 import 去 shim，plan 期定。
2. **`agent_prefix` 补 exploit 键的精确形式**——加 `{vt}-exploit` 显式键 vs 剥 `-exploit` 后缀复用 vuln 前缀。
3. **`BlackboxPipelineState → WorkflowSummary` 字段映射**——`agent_metrics`(dict) → `AgentMetricsSummary`、`failed_agents`/`errors` → summary.error、`total_duration_ms` 来源。
4. **`run_blackbox_auth_validation` 改造点**——`validate_authentication` 当前接 `audit_logger=`（core ActivityLogger）；要让它吃 `SessionToolAuditLogger`（core `ToolAuditLogger`），需确认 `validate_authentication`/其内部 executor 的 `tool_audit_logger` 透传链（镜像白盒 `run_agent` 经 executor→runner 的 `tool_audit_logger` 参数）。
5. **收尾摘要去重**——Rich 模式 `SummaryEvent` Table 与 `cli/main.py:131-139` 的 `click.echo` 摘要是否合并。
6. **执行顺序**——上移（§4.1）是黑盒接线（§4.2）的前置依赖；plan 应先做上移 + L0 回归闸绿，再动黑盒。

## 10. 验证证据（代码核对，2026-06-15，`feat/fork-py`）

| 论断 | 证据 |
|---|---|
| 黑盒无 `audit/` 目录 | `find packages/blackbox/src -type d` → 仅 `services`/`cli`/`agents`/`pipeline`，无 `audit` |
| 黑盒 activity 走 `create_activity_logger()` | `activities.py:87,122,154,207` 全部 `audit_logger=create_activity_logger()` |
| 黑盒有 `print()` 心跳 | `worker.py:30` `print(f"[{elapsed}s] Phase: {phase} \| Agent: {agent} \| Completed: {completed}/13")`；`worker.py:55` `poll_task = asyncio.create_task(poll_workflow_progress(handle))` |
| 黑盒 CLI 无 `--plain` | `cli/main.py:28-41` `start` 装饰器无 `--plain`；签名无 `plain` |
| 黑盒并行 exploit agent | `workflows.py:215` `Semaphore(input.max_concurrent)`；`:223` `asyncio.gather(*[bounded_exploit(...)])` |
| 黑盒 phase 结构 | `workflows.py`: preflight `:68`、auth-validation `:108`、recon-blackbox `:149`、exploitation `:158`、reporting `:268` |
| 白盒 `audit/` 层包无关 | 逐文件读：`session.py`/`workflow_logger.py`/`session_registry.py`/`display_lifecycle.py`/`session_tool_audit_logger.py` 仅 import `shannon_core.*` + 包内相对；仅 2 处绝对 import 指向白盒（`workflow_logger.py:14,15`） |
| `DisplayDispatcher` 已持锁 | core `dispatcher.py:19` `self._lock = asyncio.Lock()`、`:22` `async with self._lock:` |
| `shannon_core/display` 在 core（上移先例） | gap 分析 §1.3：`format_*` 已从 whitebox 迁至 `shannon_core.display.formatters` |
| 旧 file-watcher 设计已取代 | `2026-06-10-realtime-console-logging-design.md` "Out of Scope": "Blackbox side — same pattern, implement after whitebox is verified"；白盒实际走事件驱动（AuditSession+Live），非 file-watcher |
