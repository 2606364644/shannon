# 统一日志门面设计 — 分工 + 统一格式/入口（方案 A 最小收编）

- **日期**：2026-07-08
- **状态**：implemented（方案 A 最小收编已落地；2026-07-08 演进为「统一日志总线」见 §10/§11 演进注记 + plan `2026-07-08-unified-log-bus.md`）
- **相关**：`2026-06-22-log-format-redesign-design.md`（symbols/formatters 单一来源）、`2026-07-01-gitnexus-llm-progress-logging-design.md`（InfoEvent 取代裸 logging 先例）、`docs/superpowers/specs/2026-07-02-exploitable-poc-generation-design.md`

## 1. 背景

用户发现：PoC 进度行（刚加的）需要手动 `import tag/symbols/format_duration` 拼 `[timestamp] [POC] ✓ ...`，疑问"为什么没有统一日志组件、每处都要手动调"。

探索现状后澄清：**项目有统一日志组件，但覆盖不全。**

- **核心管道已统一**：`display/`（symbols/formatters/events/renderers）+ `audit/session_registry`（进程级 `AuditSession` 单例，`get_audit_session()` 无则返回 `NullAuditSession` 全 no-op）。PHASE/AGENT/STEP/Tool/LlmTurn/Info/Summary 全走 display 事件流，符号/等宽标签/时长**不用手动拼**。
- **缺口在边缘，三类绕过 display 流**：
  1. **activity 内裸 print**（~3 处）：PoC 进度行、`cli/progress.py`。activity 其实能拿 `get_audit_session()`，且 `session.log_info(msg, level)` / `log_step(...)` 通道已有——GitNexus 轨早就在用（`activities.py:419/428/578/585` 打 `InfoEvent` 避免"静默空转"）。`session.log_info` docstring 明确："Replaces bare `logger.warning/info` in workflow threads"。故 activity 内裸 print 是"没走"而非"走不了"。
  2. **非 activity 上下文裸 print**（~6 处）：`scan_runner.py` 的"正在优雅取消…/正在取消 Temporal workflow…"。在 worker 外 / 信号 handler 里，**拿不到 audit_session**。
  3. **散落 `logging.getLogger`**（~20 处）：各 service/builder 的 WARNING/ERROR，无统一格式，走 Python 默认（`WARNING:name:message` → stderr lastResort），和 display 流（`[timestamp] [AGENT] ✓ ...` → stdout）两套格式混在终端。

**真痛点**：散落 logging 无格式（用 `dictConfig` 自动解决，不动调用点）+ activity 内裸 print 手动拼符号（改走 `session.log_info`，3 处）。

## 2. 决策（brainstorming 已锁定）

- **范围**：真·统一日志门面——含散落 `logging.getLogger`。
- **两套体系关系**：**分工 + 统一格式/入口**。display 流管用户进度（stdout），logging 管诊断排障（去文件），但 Formatter 复用 `format_log_time` + 等宽 LEVEL 列风格，一处 `dictConfig` 统一入口。
- **实现路径**：**方案 A 最小收编**。`dictConfig` 统一 logging 格式（不动 ~20 处调用点）+ `print_line` helper 给非-session 裸 print + activity 内裸 print 改走 `session.log_info`。~3 处改动，低风险。
  - 否决 B（给 logging 也套 symbols）：WARNING 是排障，和 STEP 的 `✓` 语义不同，强套符号混淆。
  - 否决 C（~20 处 getLogger 全改门面）：纯度收益边际递减，破坏 `logging.getLogger(__name__)` 惯例，YAGNI。

## 3. 架构

```
扫描进度（给操作者）                 诊断排障（给开发者）
   │                                    │
   ▼                                    ▼
AuditSession.log_info / log_step      logging.getLogger(__name__)   ← 不动调用点
   │                                    │
   ▼                                    ▼
display 事件流 → rich/file renderer   Python logging → dictConfig Formatter
   │                                    │
   ▼                                    ▼
stdout                              stderr + workspaces/<session>/diagnostic.log
   │                                    │
   └──── 共用 symbols/formatters 风格 ────┘
         format_log_time + 等宽标签列 (tag/LABEL_WIDTH=5)
```

**分工**：display 管 `PHASE/AGENT/STEP/Info/Summary`（用户进度），logging 管 `WARNING/ERROR`（排障诊断）。**格式统一**（时间戳 + 等宽标签列），**入口统一**（一处 `configure_logging`），**符号语义分明**（诊断不套 `▶/✓` 扫描进度符号）。

## 4. 不变量

- **铁律**：诊断 logging（WARNING/ERROR）**不套扫描进度符号**（`▶/✓/✗/○`）。符号族归属 display 流，语义不可混淆。诊断用等宽 LEVEL 列（`WARN `/`ERROR`）对齐，不用符号。
- **铁律**：`temporalio.activity` logger 的独立 redirect 不被 `configure_logging` 收编。它已设 `propagate=False` + 独立 FileHandler，root 配 stderr handler 不会双重输出（见 `temporalio_redirect.py` docstring 的 defense-in-depth 论证）。`configure_logging` 必须跳过它（不重复 addHandler、不改其 propagate）。
- display 流的 `NullAuditSession` no-op 语义不动：activity 内 `get_audit_session().log_info(...)` 在无 session 上下文（测试/standalone）安全 no-op。

## 5. 新增组件（`shannon_core/logging/`）

### 5.1 `setup.py` — `configure_logging(log_dir=None, level="INFO")`

一次 `dictConfig`，幂等（防重复 handler）。

- **root logger Formatter**：`[%(asctime)s] [%(levelname)5s] %(name)s: %(message)s`，`datefmt="%Y-%m-%d %H:%M:%S"`（对齐 `format_log_time`）。LEVEL 5 宽等宽对齐 display 的 `tag()/LABEL_WIDTH=5` 风格（`INFO `/`WARN `/`ERROR`）。
- **Handler**：`StreamHandler(stderr)`（终端可见）+ `FileHandler(log_dir/diagnostic.log)`（持久）。两 handler 共用 Formatter。
- **幂等**：模块级 `_configured` 标志；重复调用若 `log_dir` 相同则 no-op，不同则替换 FileHandler（防重复输出）。
- **log_dir 不存在则创建**：`log_path.parent.mkdir(parents=True, exist_ok=True)`。
- **跳过 `temporalio.activity`**：不对其 addHandler、不改 propagate。`install_temporalio_log_redirect` 仍由现有调用点独立调用（`display_lifecycle`/worker）。
- **level**：root logger `INFO`；env `SHANNON_LOG_LEVEL` 可覆盖（`DEBUG`/`WARNING`）。**第三方噪声库**（`httpx`/`urllib3`/`httpcore`/`asyncio`）单独设 `WARNING`，避免 DEBUG/INFO 刷屏（不随 root level 放开）。

### 5.2 `line_print.py` — `print_line(tag_label, symbol, body)`

给**拿不到 audit_session** 的地方用（scan_runner 信号 handler、非 activity CLI 进度）。

- 内部 `print(f"[{format_log_time()}] [{tag(tag_label)}] {symbol} {body}", flush=True)`，复用 `display.formatters.tag` + `display.symbols` + `format_log_time`，**不手拼字面量**。
- `tag_label` 例：`"SCAN"`、`"CANCEL"`；`symbol` 来自 `symbols.py`（`AGENT_START`/`STEP_DONE`/…）或裸空串。
- 无异常面（纯 print）；非 async（信号 handler 上下文不能 await）。

### 5.3 `__init__.py` 导出

`configure_logging`、`print_line`。

## 6. 改动点（~3 处 + 入口）

| 位置 | 改动 | 理由 |
|---|---|---|
| `poc_generator.py` PoC 进度行 | 裸 `print` → `get_audit_session().log_info(msg)`（`NullAuditSession` no-op 安全）| activity 内能拿 session；`log_info` 是官方取代裸 logging 的通道，渲染对齐 `[INFO ]`；4 处 print 全换 |
| `scan_runner.py` 6 处取消消息 | 裸 `print` → `print_line("SCAN"/"CANCEL", symbol, body)` | 信号 handler 拿不到 session，用 helper 复用格式 |
| whitebox/blackbox/combined `cli/main.py` | 启动时调 `configure_logging(log_dir=workspaces/<session>/logs)`（紧接 `ensure_infra` 后）| 统一入口，散落 getLogger 自动套格式 |

### 6.1 不改动
- 散落 ~20 处 `logging.getLogger`（**自动**套 dictConfig 格式，零改动）— A 档核心收益。
- `display/`（symbols/formatters/renderers）— 已统一，仅复用。
- `temporalio_redirect` / `activity_logger` — 已有独立机制，不动。

## 7. UX 延伸：`cli logs` 读诊断日志

现 `cli logs <workspace>` 只读 `workflow.log`（display 流产物）。诊断日志 `diagnostic.log` 是新文件，让 `cli logs` 也能看：

- 默认仍读 `workflow.log`（向后兼容）。
- 加 `--diagnostic` 选项读 `diagnostic.log`。
- `--follow` 对两者都支持（复用 `tail_workflow_log` 的 watchdog 机制，参数化路径）。

这是"统一入口"在 UX 上的自然延伸（用户一个命令看两套日志，按 flag 切）。

## 8. 错误处理

- `configure_logging`：幂等 + `log_dir` mkdir；dictConfig 失败兜底退裸 `basicConfig`（绝不阻塞扫描启动）。
- `print_line`：纯 print，无异常面。
- PoC `log_info`：best-effort（`NullAuditSession` no-op，已有先例 `activities.py:419` 吞 session 异常）。

## 9. 测试

- `configure_logging`：
  - 幂等性（重复调用同 `log_dir` 不重复 handler）。
  - `log_dir` 不存在自动创建。
  - root logger 套上 stderr + FileHandler，Formatter 含等宽 LEVEL 列。
  - `temporalio.activity` logger 不被收编（handlers 数不变、`propagate=False` 保持）。
  - `SHANNON_LOG_LEVEL` 覆盖生效。
- `print_line`：输出格式正确（`[timestamp] [SCAN  ] symbol body`，等宽标签 + 符号）。
- PoC 改动：现有 32 测试不破（`NullAuditSession.log_info` no-op）。
- `cli logs --diagnostic`：读 `diagnostic.log`、`--follow` 可用。

## 10. 非目标 / 不做

- 不改 ~20 处 `logging.getLogger` 调用点为门面（C 档，YAGNI）。
- 不给诊断 logging 套扫描符号（B 档，语义混淆）。
- 不合并 display 流与 logging 为一套（同步 vs 异步语义冲突，分工更清晰）。

> **演进注记（2026-07-08，统一日志总线）**：本条否决的是「同一 handler 同步写 console + 文件」。后续 plan（`docs/superpowers/plans/2026-07-08-unified-log-bus.md`）用 `QueueHandler(同步生产) + event-loop drain(异步消费)` 桥接——生产线程只 `queue.put_nowait`（同步、不碰 console/stderr/rich），event-loop drain task 批量 `dispatch` 到 renderer（异步、与 PHASE/STEP 同 `asyncio.Lock` 序列化）。语义冲突被「生产同步 / 消费异步」的队列桥化解，两流在 dispatcher 同一序列化点汇合，未引入第三个碰 console 的线程（仍是 event-loop 线程做所有 console.print）。本方案 A 的 `setup.py` stderr/file handler 被替换为 `LogBusHandler`，散落 ~20 处 `getLogger` 零改动即汇入总线。
- 不动 `temporalio_redirect` / `activity_logger`。

## 11. 风险

- **dictConfig 与现有 `basicConfig` 残留冲突**：项目当前无显式 `basicConfig`（grep 确认 src 无），`configure_logging` 是首个 root 配置，无冲突。幂等防重。
- **stderr handler 与 Rich Live footer 冲突**：display 流走 dispatcher（`redirect_stderr=False`），诊断 logging 走 stderr。Rich Live 默认不重定向 stderr 时，诊断行可能和 Live footer 交错。**缓解**：诊断日志主去向是文件，stderr 是附带；若实测交错明显，给 stderr handler 套 Rich 的 `Console(file=sys.stderr)` 或降为 `WARNING` 级（只 ERROR 上终端）。spec 标注为"真机冒烟确认项"。

> **演进注记（2026-07-08，统一日志总线）**：此风险已从根消除，不再需要 stderr handler。`configure_logging` 不再加 `StreamHandler(stderr)`（也不加 `FileHandler`），散落 logging 经 `LogBusHandler.emit` 分流——session 活跃 → `queue.put_nowait(LogEvent)` → event-loop drain → `dispatcher.dispatch` → RichConsoleRenderer 上屏 + DiagnosticLogRenderer 落盘；无 session → `DiagnosticLog.write_sync` fallback。生产线程 emit 不碰真实 TTY，footer 交错/鬼影根因消失；`emit` try/except 兜底永不抛 → record 永远 handled → `logging.lastResort`（硬编码 stderr）不触发。15+ 测试锁定（`test_logging_never_writes_stderr` / `test_lastresort_never_engages` / `test_logbus_handler_emit_does_not_touch_console_or_stderr`）。真机冒烟仍待验（footer 无鬼影横线堆积、散落 logging 干净滚动、worker 不崩）。
- **测试套件副作用**：`configure_logging` 改全局 root logger，可能影响其他测试的 logging 断言。**缓解**：测试里用 fixture 调 `configure_logging` + teardown 还原（或测试专用 `NullHandler`）。
