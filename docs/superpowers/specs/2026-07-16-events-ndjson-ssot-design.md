# 2026-07-16 · events.ndjson 升格 SSOT + 修 worker logging 断线 + 黑盒 web 扫描 C1 化

> **状态**：设计（brainstorming 已与用户对齐方向、范围、模糊点）
> **分支**：`feat/fork-py`
> **关联**：[[unified-log-bus-plan-status]]（LogBus 已实现，本 spec 是其"最后一公里"接线）、[[c1-worker-container-phase-a-status]]（黑盒 C1 化即其 Phase C）、[[web-scan-worker-e2e-fix-status]]

> ⚠️ **本 spec 合并了两个功能**：(1) 日志一致性 / SSOT（改动 1/2/3/5），(2) 黑盒 web 扫描 C1 化（改动 4）。建议 plan 阶段拆成两个 plan：P1+P2+改动5 一个、改动 4（黑盒 C1 化）一个。合并是出于用户选择；两者**仅在 bb_worker 并发=1 上耦合**，其余独立。

---

## 1. 背景与问题

用户观察到扫描的三个日志展示渠道内容"不相近"：WEB live 页（`/p/<ws>/live`）、`workflow.log`、CLI rich 终端框。调查后定位到是**一个真 bug + 一处刻意的 clean separation + 黑盒 web 扫描整条功能未实现**叠加。

### 1.1 三渠道同源（已验证）

所有结构化进度事件流经 `DisplayDispatcher`（`packages/core/src/shannon_core/display/dispatcher.py:21`），fan-out 给所有挂载 renderer：

| renderer | 产物 | 对 `LogEvent` |
|---|---|---|
| `StructuredEventRenderer` | `events.ndjson`（JSON，**无过滤**）→ SSE → live 页 | **写入**（侧漏） |
| `FileLogRenderer` | `workflow.log`（纯文本） | **no-op**（`file_renderer.py:41-52` 无 case；测试 `test_logevent_not_written_to_workflow_log` 锁定） |
| `RichConsoleRenderer` + `LiveDashboardRenderer` | 终端 Live 框 | 渲染（灰显） |

实证：`workspaces/trip_1784167551` 的 `events.ndjson`(20 行) 与 `workflow.log`(33 行) 逐条一一对应、时间戳一致。→ live 页 ≈ workflow.log，本就该相近。

### 1.2 裂痕四 — worker 容器漏调 `configure_logging`

散落 59 处 `logging.getLogger(...)` 经 `LogBusHandler`（`logging/setup.py:68`，**已实现**——memory `unified-log-bus-plan-status` 的"待实现"已过时）转成 `LogEvent` 喂回 dispatcher。三个 CLI 入口都在 `main.py` 调了 `configure_logging`（whitebox:94 / blackbox:134 / combined:39），**worker 容器入口 `packages/worker/src/shannon_worker/runner.py:92` `main()` 没调**。

后果：root logger 无 `LogBusHandler` → 散落 `getLogger` 经 `lastResort` 直写 stderr；`LogBus._diagnostic` 为 None → `DiagnosticLogRenderer` 不挂 → **web 扫描不产 diagnostic.log，live 页/events.ndjson 无任何 LogEvent**。`LogBus.attach` 本身已在 worker 路径接好（白盒 `setup_display` activity `activities.py:1553`），缺的只是 handler 挂载。

### 1.3 黑盒 web 扫描整条 C1 化未实现

| 路径 | display 接线 | configure_logging | 发起方式 |
|---|---|---|---|
| 白盒 host CLI | ✅ `run_with_display` | ✅ main.py:94 | CLI |
| 白盒 worker 容器 | ✅ `setup_display` activity | ❌ 漏（裂痕四） | `scan_manager._submit_whitebox` → `WEB_TASK_QUEUE_WHITEBOX` |
| 黑盒 host CLI | ✅ `run_with_display`（worker.py:186） | ✅ main.py:134 | CLI |
| **黑盒 worker 容器** | ❌ **无** | ❌ | **`scan_manager.py:103` `NotImplementedError("blackbox C1 化留 Phase C")`** |

黑盒 `BlackboxScanWorkflow.run`（`workflows.py:68`）无 `setup_display`/`set_audit_session`/`run_heartbeat`，`log_phase_start_activity` 调 `get_audit_session()` 返回 `NullAuditSession` → 日志被吞。**且 web 端根本无法发起黑盒扫描**（scan_manager 直接 raise）。

> 关键洞察：黑盒**没有"日志不一致"问题**——CLI 路径完全正常，web 路径是整个功能缺失。改动 4 是"完成 Phase C 黑盒 C1 化"，不是"补 display 接线"。

---

## 2. 目标与非目标

### 目标
1. **events.ndjson 升格为 SSOT**：含 DisplayEvent + LogEvent 全集。workflow.log = 精炼文本子集（剔除 LogEvent），diagnostic.log = LogEvent 文本镜像。
2. **修裂痕四**：worker 容器入口补 `configure_logging`，LogEvent 经 LogBus 进 dispatcher → events.ndjson + diagnostic.log。
3. **前端可见**：live 页 LogStream 渲染 LogEvent（灰显 + WARNING/ERROR 高亮）。
4. **全量文本入口**：CLI `logs --full` 渲染 events.ndjson 全量。
5. **黑盒 web 扫描 C1 化**：照抄白盒 C1 模式，让黑盒 web 扫描从无到有。

### 非目标
- 不让 workflow.log 收 LogEvent（保留 clean separation）。
- 不让三渠道渲染统一。
- 不把 `activity_failures.log`（temporalio 子树）并入 SSOT。
- 不做 AuditSession contextvar 化（靠 worker 并发=1 规避）。

---

## 3. 设计

### 改动 1 · `configure_logging` 接线（worker 容器入口补缺）

CLI 三入口 `main.py` 已在启动时调 `configure_logging(ws/logs)`，无需改。只补 worker 容器入口——`setup_display` activity 的 `session.initialize` 之前：

| 入口 | 接线点 |
|---|---|
| 白盒 worker 容器 | `setup_display`（`packages/whitebox/src/shannon_whitebox/pipeline/activities.py:1541`），`session.initialize`(:1549) 前 |
| 黑盒 worker 容器 | 新增 `setup_display`（见改动 4），`session.initialize` 前 |

```python
ws_logs = ws_path / "logs"
configure_logging(log_dir=ws_logs)                  # 挂 LogBusHandler（幂等）+ per-scan diagnostic
await session.initialize(workflow_id=meta.id, event_file=input.event_file)
await LogBus.attach(session.dispatcher)              # attach 时 _diagnostic 已配 → 挂 DiagnosticLogRenderer
```
- **时序**：configure_logging → session.initialize → LogBus.attach（`log_bus.py:72-74`）。
- **幂等**（`setup.py:57-65`）：worker 串行多 scan（并发=1），每 scan 调一次；不同 ws/logs 替换 diagnostic 句柄、handler 不堆叠。
- **不碰 `run_with_display`**（CLI 已自配 ws/logs，避免 meta 路径 ≠ main.py 路径的风险）。
- **边界**：scan 间隙（finalize 后到下个 setup_display 前）的 logging 落到上个 ws 的 diagnostic（句柄未切）——可接受，间隙 logging 少且归属上个 scan 合理。

### 改动 2 · 前端 LogStream 渲染 LogEvent（基于 `level` 着色）

`packages/web/frontend/src/components/LogStream.tsx` + `api/types.ts`：

- **NdjsonEvent 类型**补 LogEvent 字段：`level: string`、`logger_name: string`、`message: string`、`exc_txt?: string`（对齐 `display/events.py:103` LogEvent dataclass）。
- **`summarize` 加 `case "LogEvent"`**：`[${e.level}] ${e.logger_name}: ${e.message}`；`exc_txt` 存在时追加一行（折叠展示）。
- **着色基于 `e.level`，不基于 `e.category`**（关键）：LogEvent 的 `category`=levelname 字符串（如 `"WARNING"`），但 `CAT_CLASS` 只有 `"WARN"` key（无 `"WARNING"`）——若按 category 查会 miss。`rowClass` 对 LogEvent 直接判 level：`ERROR`→`ev-error`、`WARNING`→`ev-warn`、INFO/DEBUG/NOTSET→`text-muted-foreground`（灰显）。**绕过 CAT_CLASS**。

### 改动 3 · CLI `logs --full`（同步 renderer + scan_end 退出）

`packages/whitebox/src/shannon_whitebox/cli/main.py:203` `logs` 加 `--full`：
- 读 `<ws>/events.ndjson`。
- **新写轻量同步 renderer**（不复用 async `FileLogRenderer`，避免污染其 clean separation）：switch on `event["type"]`——DisplayEvent 类型复用 `display/formatters.py` 的格式化函数输出文本；`LogEvent` 用 `[ts] [LEVEL] logger: msg`（对齐 `logging/diagnostic_log.format_diagnostic_line`）。
- **follow 退出条件 = `type == "scan_end"`**（events.ndjson 的 scan_end 行），**不是** `COMPLETION_PATTERN`（那是 workflow.log 的 `Workflow COMPLETED|FAILED`）。
- 与 `--diagnostic` 互斥（`--full` 优先）；支持 `--follow`（tail -f，遇 scan_end 自动退出）。

### 改动 4 · 黑盒 web 扫描 C1 化（Phase C，照抄白盒 C1）

照抄白盒 C1 已落地的模式（`scan_manager._submit_whitebox` + `WhiteboxScanWorkflow.run` worker 分支 + 白盒 `setup_display`/`run_heartbeat`/`finalize_summary` activity）。**6 项**：

**4.1 Input 字段补齐**（`packages/blackbox/src/shannon_blackbox/pipeline/shared.py`）
- `BlackboxPipelineInput`（:8）加 `event_file: str | None = None`。
- `BlackboxActivityInput`（:37）加 `event_file: str | None = None`。
- （`BasePipelineInput` core 基类只有 `workspace_name`，无 event_file——白盒也是在自己的 shared.py 加，黑盒同。）

**4.2 `scan_manager._submit_blackbox`**（替换 `scan_manager.py:103` NotImplementedError）
仿 `_submit_whitebox`（:115-137）：构造 `BlackboxPipelineInput(repo_path=..., web_url=..., workspace_name=ws, event_file=str(event_file), workspaces_root=str(self._workspaces_dir))` → `client.start_workflow(BlackboxScanWorkflow.run, inp, id=workflow_id, task_queue=WEB_TASK_QUEUE_BLACKBOX)`。复用 `_resolve_workflow_id`。

**4.3 `BlackboxScanWorkflow.run` worker 分支**（`workflows.py:68`）
- 开头加 `is_worker_path = input.event_file is not None`（区分 worker 容器 vs CLI；CLI 路径 `worker.py run_scan` 外层已 `run_with_display`，不重复）。
- `act_input` 带 `event_file=input.event_file`。
- worker 路径：`log_phase_start_activity(setup)` 之前并行前导 `setup_display` + `run_heartbeat`（仿白盒 `workflows.py:95-109`）。
- 现有 `finally`（`workflows.py:431`）里加 `finalize_display` 调用（在 cleanup_settings 之后），保证正常/cancel/异常都收尾。

**4.4 新增黑盒 activity**（`packages/blackbox/src/shannon_blackbox/pipeline/activities.py`）
- `setup_display(input: BlackboxActivityInput)`：仿白盒 `activities.py:1541-1554`——ws_path 解析（`input.workspace_path or workspace_name`）→ `configure_logging(ws/logs)`（改动 1）→ `AuditSession` + `session.initialize(event_file=input.event_file)` → `LogBus.attach` → `set_audit_session`。
- `finalize_display(input, summary: dict)`：仿白盒 `finalize_summary`（`activities.py:1574-1607`）——`drain_and_detach` → `log_workflow_complete(WorkflowSummary)` → `clear_audit_session`。summary 由 workflow 从 `self._state`(BlackboxPipelineState) 构造（status 在 :419/424/428 各路径已设）。
- `run_heartbeat(input: BlackboxActivityInput)`：仿白盒 `run_heartbeat`（`activities.py:1558`）——`HeartbeatManager(ws_dir)` + `asyncio.Event().wait()`（activity cancel 退出）。**黑盒目前无此 activity**，scan_liveness 靠 heartbeat mtime 判活，不加则黑盒 web 扫描会被误判 interrupted。

**4.5 bb_worker 注册 + 并发=1**（`packages/worker/src/shannon_worker/runner.py:74-87`）
- activities 列表注册 `setup_display` / `finalize_display` / `run_heartbeat`。
- 加 `max_concurrent_workflow_tasks=1`（见改动 5）。

**4.6 黑盒 event_file env 兜底**
worker 容器路径靠 `scan_manager` 传 `event_file` 参数（4.2），不经 `wire_web_event_file`（env）。`session.initialize(event_file=input.event_file)` 直接用参数；CLI 路径 `event_file=None` → `WorkflowLogger.initialize` 读 `SHANNON_WEB_EVENT_FILE` env（`workflow.py:91`，黑盒 CLI `worker.py:123` 已 `wire_web_event_file` 设）。两条路径都覆盖。

### 改动 5 · bb_worker 并发=1

`runner.py:74` bb_worker 加 `max_concurrent_workflow_tasks=1`，对齐 wb_worker（`:71`）。`LogBus` 单例 + `AuditSession` 全局单例（黑盒也用 `session_registry`）在多 scan 并发下串台/冲突；并发=1 是 `configure_logging` per-scan 切换、LogBus attach、AuditSession 的正确性前提。（黑盒此前未限并发是潜在 bug，本次顺带修。）

---

## 4. 修后 LogEvent 数据流

```
getLogger(...) → LogBusHandler.emit → LogBus.queue → drain → dispatcher.dispatch(LogEvent)
   ├→ RichConsoleRenderer   (终端灰显；worker 无 TTY → Console plain)
   ├→ StructuredEventRenderer → events.ndjson → live 页   [本次让 worker 也收]
   ├→ DiagnosticLogRenderer → diagnostic.log              [本次让 worker 也产]
   └→ FileLogRenderer: no-op（保留 clean separation）
```

排查心智模型：看全部 → `events.ndjson` / `logs --full`；看进度 → `workflow.log`；看实时 → live 页；看框架故障 → `activity_failures.log`。

---

## 5. 并发安全

- wb_worker 并发=1（已有 `runner.py:71`）；bb_worker 并发=1（改动 5）。
- → LogBus 单例同时只 attach 一个 scan 的 dispatcher；`configure_logging(ws/logs)` per-scan diagnostic 切换不串；AuditSession 全局单例安全。

---

## 6. 测试策略（TDD）

**日志一致性（P1/P2）**
1. worker 路径 LogEvent 进 events.ndjson + diagnostic.log：模拟 worker 路径（不经 CLI main configure_logging），`setup_display` 后发 `getLogger().warning()`，断言 events.ndjson 出现 `type=LogEvent` 行 + diagnostic.log 对应行。（修前 fail）
2. configure_logging 幂等 + per-scan diagnostic 切换：同 log_dir 二次调 no-op；不同 log_dir 替换句柄、handler 不堆叠。
3. LogStream 渲染 LogEvent：`[LEVEL] logger: msg`；INFO 灰显、WARNING ev-warn、ERROR ev-error（**基于 level 字段**）；exc_txt 折叠。vitest。
4. `logs --full`：含 LogEvent 行；`--follow` 遇 `type==scan_end` 退出；与 `--diagnostic` 互斥。
5. 回归：`test_logevent_not_written_to_workflow_log` 仍绿（没破坏 clean separation）。

**黑盒 C1 化（P3）**
6. `BlackboxPipelineInput`/`BlackboxActivityInput` 序列化含 event_file（temporal 要求 dataclass 可序列化）。
7. `scan_manager._submit_blackbox`：提交到 `WEB_TASK_QUEUE_BLACKBOX`、PipelineInput 带 event_file/workspace_name/workspaces_root。
8. 黑盒 `setup_display`/`finalize_display`/`run_heartbeat` activity 单测（session 建+attach+set；finalize drain+close+clear；heartbeat 永阻塞+cancel 退出）。
9. `BlackboxScanWorkflow.run` worker 分支：`is_worker_path=True` 时前导 setup_display+heartbeat、finally 调 finalize_display（含异常路径）。
10. bb_worker 配置：activities 含三个新 activity + `max_concurrent_workflow_tasks==1`。
11. 端到端：web 发起黑盒扫描 → events.ndjson/workflow.log 产生 + live 页有事件 + scan_end 收尾。

---

## 7. 风险与边界

- **改动 4 风险最高**：动了 `BlackboxScanWorkflow.run` 结构（worker 分支 + finally 接 finalize）。须保证异常/cancel 路径 finalize 不漏（否则下个黑盒 scan 拿到上个 session）。现有 finally（`workflows.py:431`）已覆盖正常/cancel/异常，finalize 加在 finally 内安全。
- **黑盒 dataclass 序列化**：temporal workflow 跨进程传 input，`BlackboxPipelineInput`/`BlackboxActivityInput` 加字段后必须仍可被 temporal codec 序列化（白盒 event_file 已验证可行，黑盒同模式）。
- **黑盒 worker 路径与 CLI 路径分叉**：`is_worker_path` 判断必须正确——CLI 路径（`worker.py run_scan` 外层 `run_with_display` + `set_audit_session`）不能再调 setup_display（会重复 attach/双重 session）。白盒已用 `input.event_file is not None` 区分，黑盒照抄。
- **logs --full 同步 renderer**：不复用 async FileLogRenderer；新写同步 switch renderer，复用 `formatters.py` 的纯函数（不引入 async）。
- **diagnostic 并发**：仅并发=1 下安全；未来放开并发需 AuditSession contextvar 化（排除本次范围）。

---

## 8. 分阶段（建议拆两个 plan）

- **Plan A · 日志一致性**（改动 1 + 2 + 3 + 5）：worker configure_logging 接线 + 前端 LogEvent + logs --full + bb_worker 并发=1。完成即解决用户核心痛点（白盒 web 扫描 live 页有诊断行 + diagnostic.log 产生）。改动 5 顺带修 bb_worker AuditSession 并发 bug。
- **Plan B · 黑盒 web 扫描 C1 化**（改动 4）：6 项完整链路。独立大功能，让黑盒 web 扫描从无到有。与 Plan A 仅在 bb_worker 并发=1（改动 5）上耦合——若 Plan B 先做，需带上改动 5。

两 plan 顺序：A 先（低风险、立即收益），B 后（大功能、独立验证）。
