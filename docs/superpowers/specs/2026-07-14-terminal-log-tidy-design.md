# 终端日志整洁化设计 — 2026-07-14

> branch: `feat/fork-py`。状态：已与用户对齐三处关键决策，本文档替代 plan，直接 TDD 实现。

## 1. 背景 / 问题

白盒 CLI 扫描（`shannon-whitebox start`）终端日志混乱，根因有四（已逐行对到代码层）：

1. **诊断行与进度行混流**。终端同时输出面向用户的进度行（`PHASE/STEP/AGENT/💭Turn/🔍GitNexus`，由代码主动 `log_*` 发）和开发诊断 `INFO shannon_core.xxx: ...`（散落 `getLogger().info()` 发）。两者走同一个 `DisplayDispatcher` + 同一个 `RichConsoleRenderer` + 同一个 console，但渲染函数不同、视觉无法分层。异步 50ms 批量 drain 还导致诊断行与 phase/step 时序错乱。
2. **第三方噪声未过滤**。
   - `claude_agent_sdk._internal.transport.subprocess_cli: Using bundled Claude Code CLI: <长路径>` 是 SDK 的 INFO，每起一个 agent 刷一遍，对用户零价值；长路径被 Rich 硬换行截成 4 段。
   - `temporalio_sdk_core::worker::activities: Activity not found on completion ... task_token=<hex>` 是 Rust 核心的 tracing WARN，直写 stderr（`redirect_stderr=False` 硬约束），整行超长污染。它**不是心跳**（事件触发：activity 被取消/超时后仍 completed），"防卡死"实际靠底部状态栏 spinner。
3. **长行换行续行顶格**。`Console()` 无 width/soft_wrap/overflow；所有 `_render_*` 用单字符串 `console.print`，无 textwrap/indent。Rich 默认硬换行、续行从第 0 列起。
4. **emoji 行不在标签列体系**。`💭 [Agent]`、`🔍 [GitNexus]` 用 emoji 开头，与 `PHASE/STEP/AGENT`（标签列宽 5）视觉错位。
5. **spinner 泄漏内部路径**。状态栏 `⠼ [Agent] t155 file_path=/Users/.../memory...` 把工具调用的原始 file_path 直接显示。

## 2. 目标 / 非目标

**目标**：终端日志"全量但整洁"——保留所有信息，但视觉分层（进度为主、诊断为背景）、过滤纯噪声、续行缩进对齐；`.env` 加 `SHANNON_LOG_VERBOSE` 开关 + temporalio `RUST_LOG`。

**非目标**：不改 `redirect_stderr=False` 硬约束；不改 `DisplayDispatcher` / `LogBus` / 事件类型定义 / 双轨架构；不做"折叠面板"式重布局。

## 3. 关键决策（已与用户对齐）

| 决策点 | 选择 |
|---|---|
| 终端日志方向 | **全量但整洁**（默认保留诊断 INFO）+ `.env` `SHANNON_LOG_VERBOSE` 开关 |
| 诊断行样式 | **dim 降级 + 续行缩进**；进度行（PHASE/STEP/AGENT）保持亮色 |
| temporalio Rust WARN | `RUST_LOG=temporalio_sdk_core=error` 压掉良性 WARN，保留 error 级 |
| 实现层 | **渲染层为主**（不动 dispatcher/LogBus/事件类型） |

## 4. 设计（5 模块）

### 模块 1 · 噪声过滤（`logging/setup.py`）

**subprocess_cli 静音**：`_NOISE_LOGGERS`（`setup.py:30`）追加 `"claude_agent_sdk"`。现有循环 `setup.py:80-81` 会对其 `setLevel(WARNING)`，使 `Using bundled Claude Code CLI`(INFO) 不再 propagate 到终端。

**temporalio Rust core 降级**：在 `configure_logging`（`setup.py:35`）开头加 `os.environ.setdefault("RUST_LOG", "temporalio_sdk_core=error")`——代码默认压 WARN 留 error，用户可在 `.env` 用 `RUST_LOG=...` 覆盖。`setdefault` 仅在未设时生效。

> ⚠️ 验证点：RUST_LOG 必须在 temporalio worker 初始化（tracing subscriber 读 env）前设好。`configure_logging` 由 `cli/main.py:94` 调用，早于 worker 启动，时序成立。target 名 `temporalio_sdk_core`（与日志行 `temporalio_sdk_core::worker::activities` 一致）需实现时确认；若不生效，fallback 为接受 WARN 偶现（不阻塞本 spec）。

### 模块 2 · 视觉分层（`display/rich_renderer.py`）

**核心区分**：`events.py` 已区分两类——
- `LogEvent`（散落 `getLogger()` 的诊断）→ `_render_log`（`rich_renderer.py:116`）改为 **dim**。
- `InfoEvent`（代码显式 `log_info()` 的用户消息，如 `Auth config scan ok`）→ `_render_info`（`rich_renderer.py:93`）**保持亮色不动**。

**改法**：`_render_log` 在级别色前加 `dim` 前缀（Rich 组合 style），即 `[dim {color}]`。`_log_color`（`rich_renderer.py:105`）级别映射不变（INFO=cyan / WARNING=yellow / ERROR=bold red / DEBUG=dim），仅在外层叠 dim。效果：诊断行整体暗淡，但 warning/error 仍有色调（dim yellow / dim bold red）可辨识。`exc_txt` 多行同样包在 dim 内。

进度行（PHASE/STEP/AGENT/Summary/InfoEvent/AgentEvent）一律不 dim，保持现状亮色。

### 模块 3 · 续行缩进对齐（`display/formatters.py` + `rich_renderer.py`）

**新增纯函数**（`formatters.py`，易测）：
```python
LOG_INDENT = 27  # = len("[YYYY-MM-DD HH:MM:SS]") + " " + LABEL_WIDTH(5) + "  "

def wrap_body(body: str, width: int, indent: int = LOG_INDENT) -> list[str]:
    """按 width 换行 body；首行原样，续行 pad(indent)。空 body 返回 ['']。"""
```
用 `textwrap.wrap`，`width=max(indent+1, width)` 防 width≤indent 死循环。

**渲染层统一 helper**：`RichConsoleRenderer` 加 `_emit(ts, prefix_markup, body)`，内部调 `wrap_body(body, self._console.width)`，首行打印 `{ts} {prefix_markup} {first}`，续行打印 `pad(indent){cont}`（续行套与首行相同的 style，便于 dim 等透传）。改造下列 `_render_*` 改走 `_emit`：`_render_info`、`_render_log`（含 exc_txt 续行）、`_render_agent`、`_render_llm`、`_render_gitnexus`、`_render_error`。`_render_step` / `_render_phase` 因 body 短可一并接入（统一路径，无害）。

> 终端无 TTY（CI/重定向）时 `self._console.width` 可能很大，`textwrap.wrap` 仍正常（少换行）；为 0/1 时 `max(indent+1, width)` 兜底。

### 模块 4 · verbose 开关（`.env` + `rich_renderer.py`）

`RichConsoleRenderer.__init__` 读 `SHANNON_LOG_VERBOSE`（env，默认 `"1"`）：
- `"1"` / 未设 = **全量整洁**：诊断 `LogEvent` dim 显示。
- `"0"` = **精简**：`_render_log` 开头 `return`，诊断 `LogEvent` **不上终端**。

**不变量**：`DiagnosticLogRenderer`（落 `diagnostic.log`）独立于 `RichConsoleRenderer`，不受 verbose 影响——verbose=0 时诊断仍完整落 `diagnostic.log`，只是终端隐藏。进度行 / 活动行（💭/🔍）/ ErrorEvent 不受 verbose 影响。

### 模块 5 · spinner 噪声（`display/live_dashboard.py`）

`_render`（`live_dashboard.py:78`）的 `action = a.last_action_detail or a.last_turn_text or "running..."` 泄漏原始 file_path。加截断：复用 `formatters.py` 新增的 `truncate_action(action, max_width=60)`（超长 `action[:max_width-1] + "…"`，按 `cell_len` 算显示宽度，中文算 2），spinner action 经它处理。

## 5. 不变量（不得破坏）

- `redirect_stderr=False` 不动（sandbox 线程 rich 循环 ImportError 硬约束）。
- `LogEvent` 必须仍落 `diagnostic.log`（verbose 只控终端可见性，不丢诊断）。
- `LogEvent`(dim) vs `InfoEvent`(亮) 的区分不动——前者是散落 getLogger 诊断，后者是显式用户消息。
- 进度行 / 活动行 / ErrorEvent 不受 verbose、不受 dim 影响。
- 双轨架构 / dispatcher 串行锁 / LogBus attach 分流 不动。

## 6. TDD 测试清单（每模块先红后绿）

测试文件：`packages/core/tests/`（`test_rich_renderer*.py` / `test_formatters*.py` 现有则追加，否则新建 `test_terminal_log_tidy.py`）。TDD 时先定位现有文件。

**模块 3（formatters，纯函数，先做）**
- `wrap_body` 短 body（不超宽）→ 单行原样。
- `wrap_body` 长 body（超宽）→ 首行原样、续行 pad(27)。
- `wrap_body` width ≤ indent → 用 `indent+1` 兜底不死循环。
- `wrap_body` 空 body → `[""]`。
- `truncate_action` 短 → 原样；长（>60 显示宽）→ 截断 + `…`；中文按显示宽度算。

**模块 2 + 3 + 4（rich_renderer）**——用 `Console(file=StringIO, width=80, force_terminal=False)` 捕获输出断言：
- LogEvent INFO 渲染含 `dim` markup。
- InfoEvent 渲染**不含** dim（保持亮）。
- 长 message 的 LogEvent：捕获输出续行以 27 空格起（`splitlines()[1].startswith(" "*27)`）。
- `SHANNON_LOG_VERBOSE=0` 时 LogEvent 不产生任何终端输出（StringIO 空）；但（单独测）DiagnosticLogRenderer 仍写入。
- `SHANNON_LOG_VERBOSE=1`/未设 时 LogEvent 正常渲染。
- ERROR 级 LogEvent 仍含 `red`（dim bold red）——严重诊断色调不丢。

**模块 1（setup）**
- `configure_logging` 后 `logging.getLogger("claude_agent_sdk").level == WARNING`。
- `os.environ.setdefault` 后未设 RUST_LOG 时 `os.environ["RUST_LOG"] == "temporalio_sdk_core=error"`；已设时不覆盖。

**模块 5（live_dashboard）**
- spinner action 超长（如 `file_path=/long/...`）经渲染被截断到 ≤60 显示宽（用 `Group` 渲染到 StringIO 断言文本不含超长路径全文）。

**回归**（已有测试不能破）
- `_render_step` / `_render_phase` 输出格式不变（标签列、pad_rule）。
- `_render_info` warning 分支仍 yellow。

## 7. 风险与验证

- **RUST_LOG target 名 / 生效**：实现时跑一次白盒扫描确认 `temporalio_sdk_core::...WARN` 不再出现、error 级仍可见。不生效则记 follow-up，不阻塞。
- **Rich dim + 组合色**：确认 `[dim cyan]` 等 markup 在终端正确渲染（dim 优先，色调保留）。
- **textwrap 与 Rich markup 交互**：`wrap_body` 作用于**纯文本 body**（不含 markup），markup 只在 `_emit` 包裹，避免 markup 被切断。

## 8. 不动的范围

`display/dispatcher.py`、`logging/log_bus.py`、`display/events.py`、`display/dashboard_state.py`、`display/structured_event_renderer.py`、`display/file_renderer.py` 的路由/事件定义不改（仅可能因共享 formatters 受益）。双轨 / 黑白盒 workflow 不动。

## 9. 实现顺序（TDD）

1. 模块 3 纯函数（`wrap_body` / `truncate_action`）——红→绿。
2. 模块 2 `_render_log` dim + `_emit` helper 接入各 `_render_*`——红→绿。
3. 模块 4 verbose 开关——红→绿。
4. 模块 1 `_NOISE_LOGGERS` + RUST_LOG setdefault——红→绿。
5. 模块 5 spinner 截断——红→绿。
6. 回归：跑 display 相关现有测试文件确认不破。
7. 真机冒烟：`uv run shannon-whitebox start --repo <NodeGoat>` 目视确认整洁。
