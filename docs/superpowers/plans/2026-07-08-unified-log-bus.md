# 统一日志总线实现计划 — logging 汇入 dispatcher，根除 Rich Live footer 鬼影

- **日期**：2026-07-08
- **状态**：plan（已审批，待 TDD 实现）
- **相关 spec**：`docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md`（本计划是其 §10/§11 的演进）
- **续作指引**：按「TDD 步骤」7 个 task 顺序实现，每 task 先 Red 测试再 Green。最关键不变量测试 `test_logging_never_writes_stderr` 是防鬼影根。前置调查结论已固化在本文 Context/架构段，回家可直接续。

## Context（为什么改）

白盒/黑盒扫描时终端满屏"鬼影横线"（`─`×满宽分隔线在不合时宜处堆积）。根因：

- Rich Live footer（底部状态栏：满宽横线 + `pre-recon · step 0/8 · 37.0s · $0` + spinner）每秒 3 次原地刷新，靠 ANSI 光标重绘保持画面。
- 散落的 `logging.getLogger(__name__)`（~20 处）经 root 的 `StreamHandler(sys.stderr)`（`logging/setup.py:61`）**直写真实 TTY，绕过 Live 协调**。`display_lifecycle.py:43` 是 `redirect_stderr=False`（硬约束——本进程同时是 Temporal worker，sandbox 线程 logging 一旦被 rich 接管会循环 ImportError 崩所有 workflow task）。
- 生产线程的 stderr 写入插入 footer 区域，打乱光标重绘 → footer 满宽横线每 tick 叠画不擦 → 鬼影。

**用户诉求**：display 流（PHASE/STEP/AGENT）和 logging 流（散落 getLogger）汇成**一条统一通道**，终端流畅无鬼影，文件对应记录，散落 ~20 处 `getLogger` **零改动**。

**这是现有 spec 的演进**（`docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md`，状态 design）：spec §10 否决"合并两流"（同步 vs 异步语义冲突）——本方案用 `QueueHandler(同步生产) + event-loop drain(异步消费)` 桥接化解该否决理由；spec §11 把"stderr/footer 冲突"标为真机冒烟待确认项——本方案从根消除（logging 不再直写 stderr）。严守 spec §4 铁律：诊断 logging 不套扫描符号（`▶/✓/✗/○`），用等宽 LEVEL 列；`temporalio.activity` 的 `propagate=False` 独立线不动。

## 架构（统一日志总线）

```
生产线程（sandbox / activity-pool / event-loop）
  logger.warning(...) → root → LogBusHandler.emit           ← 替代 StreamHandler(stderr)+FileHandler
    ├─ prepare: record.getMessage() 物化 message（生产线程，不碰 rich）
    └─ 分流（GIL 原子读 log_bus.is_attached）:
         ├─ session 活跃 → queue.put_nowait(LogEvent)        ← 只 put，不碰 console/stderr/rich/import
         │     │
         │     ▼  event-loop 线程
         │   drain task（run_with_display 内起）: 批量 get_nowait
         │     └─ await dispatcher.dispatch(LogEvent)        ← 与 PHASE/STEP 同 asyncio.Lock 序列化
         │          ├─ RichConsoleRenderer._render_log  → console.print（与 Live 同 console）
         │          └─ DiagnosticLogRenderer           → diagnostic.log
         │          （FileLogRenderer 对 LogEvent no-op → 不进 workflow.log，clean 分离）
         │
         └─ 无 session（CLI 同步层 / 无 Live）→ DiagnosticLog.write_sync（threading.Lock，无竞态）
```

**两个核心论证：**

1. **为什么无鬼影**：改前鬼影根因是 `StreamHandler(stderr)` 在生产线程直写 TTY。改后碰 console 的线程**仍只有两个**（与仓库现已 work 的搭配一致）——event-loop 线程做所有 `console.print`（display + logging 都经 dispatcher），Live refresh 线程只重绘 renderable 不 print。logging 与 display 在 dispatcher 同一 `asyncio.Lock` 序列化点汇合，未引入第三个线程。
2. **为什么 sandbox 安全**：`LogBusHandler.emit` 在 sandbox/生产线程只做 `record.getMessage()`（纯字符串，不 import）+ `queue.put_nowait`（标准库线程安全，不碰 stderr/stdout/rich）。format/渲染全移到 event-loop 线程的 renderer。`redirect_stderr=False` 保持。故 sandbox 线程 emit 不触发 rich 重导入。

**不用标准 `QueueListener.start()`**：它自起 daemon 线程 dispatch → 第三个线程碰 console → 鬼影回归（agent 调查风险 C）。console 输出必须走 event-loop drain task。`queue.Queue + event-loop poll`（非 `call_soon_threadsafe`，因生产线程拿 event-loop 引用复杂且脆）。

## 文件改动清单

| 文件 | 动作 | 改什么 |
|---|---|---|
| `logging/log_bus.py` | **新增** | `LogBus` 模块级单例（`queue.Queue` + `_dispatcher`/`_drain_task`/`_diagnostic`/`_attached` + `configure_diagnostic`/`attach`/`detach`/`write_fallback`/`is_attached`）+ `LogBusHandler`（继承 `logging.handlers.QueueHandler`，重写 `prepare`（物化 message、构造 LogEvent、try/except 兜底）、`emit` 按 `is_attached` 分流）。 |
| `logging/diagnostic_log.py` | **新增** | `DiagnosticLog`：单一 sync 文件句柄 + `threading.Lock`，`write_sync(line)`/`close()`。被 fallback（生产线程）和 DiagnosticLogRenderer（event-loop）共用。行频低、单行 <1ms，不用 aiofiles。 |
| `logging/setup.py` | **改** | `configure_logging` 不再加 `StreamHandler(stderr)`+`FileHandler`；挂单个 `LogBusHandler`（`_shannon_configured=True` 沿用幂等标记）；调 `log_bus.configure_diagnostic(log_dir/"diagnostic.log")`。保留幂等、log_dir 自建、root level、噪声库限 WARNING、跳过 temporalio.activity。`_FORMAT`/`_DATEFMT` 常量保留（renderer 格式对齐用，但不再挂 Formatter 于 handler）。 |
| `display/events.py` | **改** | 新增 `LogEvent(DisplayEvent)`：`logger_name`/`level`(levelname)/`message`/`exc_text`。category=levelname（对齐 ErrorEvent 先例）。 |
| `display/rich_renderer.py` | **改** | match 加 `case LogEvent(): self._render_log(e)`；`_render_log` 按 level 选色（ERROR/CRITICAL=bold red、WARNING=yellow、INFO=cyan、DEBUG=dim），输出 `[ts] [color]{tag(level)}[/] name: msg`，**无扫描符号**（spec §4）。 |
| `display/file_renderer.py` | **改** | (a) `FileLogRenderer` match 对 `LogEvent` 落空 no-op（不进 workflow.log）。(b) 新增 `DiagnosticLogRenderer`（同文件并列），只 match `LogEvent` → `diag.write_sync([ts] [LEVEL5] name: msg\n)` + exc_text。经 `dispatcher.add()` 运行时挂。 |
| `audit/display_lifecycle.py` | **改**（生命周期核心） | `run_with_display` 在 `session.initialize()` 后 `await log_bus.attach(session.dispatcher)`、`with live:` 内 yield；`finally`（session.close 前）`await log_bus.drain_and_detach()`（final flush + cancel drain + `_attached=False`）。rich 与 non-rich 两分支都 attach。 |
| `audit/session.py` | **改** | AuditSession 加 `@property dispatcher`（→ `workflow_logger._dispatcher`，供 log_bus.attach 拿引用）。 |
| `display/live_dashboard.py` | **改** | 删 `:68` footer 满宽横线 `Text("─"*options.max_width, style="dim")`，`rows` 直接从 row1 起（视觉兜底）。 |
| `logging/__init__.py` | **改** | 导出 `LogBus`/`LogBusHandler`。 |

**不改**：~20 处 `getLogger` 调用点（核心收益）；`temporalio_redirect.py`；`workflow_logger.py`（renderer 装配不动）；三个 `cli/main.py`（configure_logging 调用点/签名不变）；whitebox `display_lifecycle.py` shim（自动 re-export core）。

## 关键设计点

- **LogEvent 新建**（非复用 InfoEvent）：InfoEvent.level 仅 `info|warning`、无 logger_name、语义是"workflow 用户消息"；LogEvent 需 full 5 级 + logger_name + exc_text，语义是"诊断"。
- **drain task**：`asyncio.create_task`，循环批量 `get_nowait` 到空再 `asyncio.sleep(0.05)`（空转才 sleep 降延迟）。drain 内 `dispatch` 异常隔离：`except Exception: log_bus.write_fallback(event)`（降级文件，不碰 console 不死循环）。
- **attach/detach 过渡窗口**：`_attached` 是 GIL 原子读，最坏丢/重 0~1 条诊断行（非数据通道，可接受）；detach 做 final flush 排空剩余。
- **上屏级别**：默认全级别经 dispatcher 上屏（与现状 stderr 上屏量一致，只是协调无鬼影）。若后续嫌 INFO 刷屏，加 `SHANNON_CONSOLE_LOG_LEVEL` 在 drain 过滤（可选，非阻塞项）。
- **lastResort（风险 A）**：configure_logging 先于 run_scan（三 CLI 入口已核实），root 早早挂 LogBusHandler → 未处理 record 不落 `logging.lastResort`（硬编码 stderr）。

## TDD 步骤（Red→Green→Refactor，7 task）

沿用 `test_logging_setup.py` 的 `_restore_root_logger`/`_restore_temporalio_logger` autouse fixture，**新增 teardown 也 restore LogBus 单例**（`_attached=False`、清 queue、关 diagnostic）。

1. **LogEvent + 渲染**（纯数据）：events.py 加 LogEvent；rich_renderer `_render_log`；FileLogRenderer 对 LogEvent no-op。Red: `test_logevent_render_no_scan_symbols`、`test_logevent_not_written_to_workflow_log`、各 level 着色。
2. **DiagnosticLog + DiagnosticLogRenderer**（文件侧）：Red: `test_diagnostic_log_renderer_writes_line`（[ts] [LEVEL5] name: msg 进文件）。
3. **LogBus + LogBusHandler（无 session fallback 先行）**：log_bus.py + setup.py 改挂 LogBusHandler。Red: `test_logging_never_writes_stderr`（**防鬼影根**：capsys 断言 stderr 无 shannon 输出）、`test_logging_falls_back_to_diagnostic_log_without_session`、`test_logbus_handler_emit_does_not_touch_console_or_stderr`（mock，锁 sandbox 安全）、`test_configure_logging_idempotent_bus_handler`。**适配** `test_logging_setup.py` 旧断言面（root 不再有 Stream/FileHandler；`_stderr_handlers(root)==[]` 成新不变量；`_third_party_logger_uses_root_formatter` 改读 diagnostic.log + 断 stderr 空）。
4. **attach/detach + drain task**：display_lifecycle attach/detach；session dispatcher property；drain task。Red: `test_logging_routes_through_dispatcher_to_console`（spy dispatch / 捕获 console buffer）、`test_drain_task_drains_remaining_on_detach`、`test_lastresort_never_engages`。
5. **footer 横线清理**：改 `test_live_dashboard.py:36-42` `test_separator_spans_full_console_width` → `test_no_full_width_separator_in_footer`（断 footer 不含满宽 `─*width`）；Green: 删 `live_dashboard.py:68`。
6. **边界回归**：跑 `test_temporalio_log_redirect.py`、`test_live_ghost_frames.py`（Live kwargs 锁，**不可改**，应继续绿）、`test_live_dashboard.py`、`test_logging_setup.py`、`test_display_lifecycle.py`、`test_workflow_logger.py`。
7. **spec 文档 + 真机冒烟**：spec §10/§11 加演进注记 + 状态 design→implemented（或新 plan 文档）；真机。

## 验证（端到端）

- **单测**：`cd packages/core && uv run pytest tests/test_logging_setup.py tests/display/ tests/audit/test_display_lifecycle.py tests/audit/test_workflow_logger.py tests/test_temporalio_log_redirect.py -x`（只跑改动相关，CLAUDE.md §3 警告全套有预存挂起/失败）。
- **真机冒烟**（核心验收）：`uv run shannon-whitebox start -r /root/shannon-py/repos/backend/kol_mapping_service`，观察：
  1. footer 不再出现鬼影横线堆积（pre-recon step 0 期间尤为关键，曾是最严重区段）；
  2. 散落 logging（如 `[INFO] shannon_core.code_index:`/`[INFO] shannon_core.git_manager:`）以干净行滚动在 footer 上方、不粘连 spinner；
  3. `workspaces/<session>/logs/diagnostic.log` 有完整诊断记录；`workflow.log` 不混入诊断行（clean 分离）；
  4. worker 不崩（sandbox 循环 import 未触发）。
- 可对照起见，同 repo 改前输出已留存（满屏横线 + `running...[ INFO]` 粘连）作为 before。

## 风险（已附缓解）

- **lastResort**：configure_logging 先挂 LogBusHandler（已核实时序），新测试 `test_lastresort_never_engages` 锁。
- **prepare 在生产线程 format**：LogBusHandler 不挂 Formatter，只 `record.getMessage()` + try/except；渲染移 event-loop。测试锁 emit 不碰 console/stderr。
- **drain 死循环**：dispatch 异常降级 `write_fallback`（文件），不死循环、不碰 console。
- **worker.main() 不经 configure_logging 的入口**：实际部署走 CLI（已 configure）；可选在 attach 前检测 root 无 `_shannon_configured` handler 时 best-effort 提示，非阻塞。
- **diagnostic.log sync 写阻塞 event-loop**：行频低、单行 <1ms，footer 动画在独立 Live refresh 线程不受影响；profiling 显示问题再换 aiofiles（YAGNI）。
