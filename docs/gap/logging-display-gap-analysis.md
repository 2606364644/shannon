# 日志展示差距分析：原始 Shannon (TypeScript) vs Shannon-py (Python)

> 版本: v2 | 日期: 2026-06-13（v1 初始）/ v2 复审: 2026-06-14 | 状态: v2 复审（展示层重构已实现，**接入 activity 待完成**）
>
> **v2 复审要点**（2026-06-14，代码级复核，HEAD `e799c9e`）：
> [logging-display-optimization](../superpowers/plans/2026-06-13-logging-display-optimization.md) plan 的 17 个 task 已全部实现并合入（commits `42c27e6` RichConsoleRenderer、`4734086` WorkflowLogger emit events、`a268f28` classification 对齐等），`uv run pytest packages/core/tests/display/ packages/core/tests/errors/ packages/whitebox/tests/test_workflow_logger.py` **93 项全绿**。本次复审据此更新全部 gap 状态。
> - **架构重构**：新增 `packages/core/src/shannon_core/display/`（`events.py` DisplayEvent 族 + `dispatcher.py` + `file_renderer.py` + `rich_renderer.py` + `formatters.py`）与 `packages/core/src/shannon_core/errors/classification.py`。`WorkflowLogger` 从「直接写 LogStream」重写为「构造 DisplayEvent 并经 dispatcher 双路分发」。`format_*` 时间助手从 whitebox 迁至 core 并回 re-export。
> - **gap 状态**：LOG-A1/A2/A4/A5/A6、LOG-D2/D4/D6/D7 由「待修复/待评估」→ **✅ 已修复**；LOG-D1 由「无 spinner」→ **⚠️ 部分**（Rich 渲染器就绪，live `Progress` 待 workflow runner 接 `Live` context，见 plan Self-Review）；LOG-A3、LOG-D8 经裁定**维持原状**（YAGNI / 各有取舍）。
> - **展示层对比更新**：v1 的 Shannon-py 输出示例基于旧 `workflow_logger.py`（`started/ended`、`→` 箭头），已不反映代码。§2.1 示例与差异表更新为当前 `FileLogRenderer` 实际输出（`Starting/Completed`、`[Prefix] agent:` 对齐格式）。
> - **配套文档**：[设计 spec](../superpowers/specs/2026-06-13-logging-display-optimization-design.md) 与本文件建立完整追溯关系。
> - **⚠️ 接入状态（重要）**：统一渲染层组件实现完整、测试通过（93 绿），但 `AuditSession`/`WorkflowLogger` 接入链**尚未挂到 Temporal activity**——生产运行中 workflow.log 暂未由本子系统写入。启用 Rich（`use_rich`）以此接入为前置依赖。详见 §3.4。

## 目的

对比原始 Shannon (TypeScript) 与重构 Shannon-py (Python) 在日志系统上的差异，覆盖架构层（handler、formatter、输出目标）和展示层（终端可见输出）。每个识别的 gap 标注编号、优先级和修复状态，为后续修复提供追踪依据。

## 对比范围

| 维度 | 覆盖内容 |
|------|----------|
| 架构层 | 日志接口、Handler 实现、Formatter、输出目标 |
| 展示层 | 格式风格、颜色/符号、进度指示、错误展示 |
| 数据层 | 日志文件结构、JSON schema、会话元数据 |

## 方法论

代码级逐文件对比，两个项目中对应的日志组件直接对照，引用源文件路径和行号作为证据。v2 复审以 HEAD `e799c9e` 实际代码为准。

---

## Part 1: 架构层对比

### 1.1 日志接口设计

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 核心接口 | `ActivityLogger` interface: `info/warn/error` | `ActivityLogger` ABC: `info/warn/error` |
| 接口位置 | `apps/worker/src/types/activity-logger.ts` | `packages/core/src/shannon_core/logging/activity_logger.py:6-16` |
| 工厂模式 | 无，直接实例化 | `create_activity_logger()` 工厂函数 (`activity_logger.py:51-58`) |
| Null 模式 | 无 | `NullAuditLogger` / 空会话降级 |
| 审计接口 | 直接嵌入 agent | 独立 `AuditLogger` ABC + `RealAuditLogger` |
| 工具审计 | 无独立接口 | `ToolAuditLogger` ABC + `NullToolAuditLogger` (`packages/core/src/shannon_core/agents/tool_audit_logger.py:17-41`) |

**差异说明：** Shannon-py 在接口设计上更完善——引入了工厂函数（自动选择 Temporal vs Console 后端）、Null Object 模式（静默降级）、独立的工具审计接口。原始 Shannon 更简单直接。

### 1.2 Handler 实现

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 控制台 | `ConsoleActivityLogger` — `console.log/warn/error` | `ConsoleActivityLogger` — `logging.Logger` (`activity_logger.py:35-48`) |
| Temporal | `TemporalActivityLogger` — `Context.current().log` | `TemporalActivityLogger` — `activity.logger` (`activity_logger.py:19-32`) |
| 文件流 | `LogStream` — `fs.WriteStream` + 显式背压处理 | `LogStream` — `aiofiles`，无显式背压 (`packages/whitebox/src/shannon_whitebox/audit/log_stream.py:9-51`) |
| 日志文件跟踪 | `logs()` 命令 — `fs.statSync` + `readRange` | `LogFileHandler` — watchdog `FileSystemEventHandler` (`packages/core/src/shannon_core/cli/logs.py:12-37`) |
| Handler 位置 | `apps/worker/src/local/console-logger.ts` | `packages/core/src/shannon_core/logging/activity_logger.py` |
| 文件流位置 | `apps/worker/src/audit/log-stream.ts` | `packages/whitebox/src/shannon_whitebox/audit/log_stream.py` |

**差异说明：** 核心架构一致（Console / Temporal / File 三种 handler），但实现语言特性不同：
- TS 用 `fs.WriteStream` 自带背压处理，Python 用 `aiofiles` 每次 write 后立即 flush
- TS 用轮询（`fs.statSync`），Python 用 watchdog 事件驱动（更高效）

### 1.3 格式化工具

> **v2 复审**：`format_duration`/`format_timestamp`/`format_log_time` 已从 `shannon_whitebox.audit.utils` 迁至 `shannon_core.display.formatters`（消除 core→whitebox 反向依赖），whitebox 经 re-export 保持向后兼容。工具调用格式化由 v1 的 `_format_tool_params` 升级为 `humanize_tool_call` 分发器 + `default_tool_params` 智能截断。

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 时间戳 (ISO) | `formatTimestamp()` — ISO 8601 UTC (`apps/worker/src/utils/formatting.ts:34-36`) | `format_timestamp()` — ISO 8601 UTC 含毫秒 (`packages/core/src/shannon_core/display/formatters.py:26-32`) |
| 时间戳 (可读) | `formatLogTime()` — `toISOString().replace('T',' ').slice(0,19)` | `format_log_time()` — `strftime("%Y-%m-%d %H:%M:%S")` (`formatters.py:35-37`) |
| 持续时间 | `formatDuration()` — ms/s/m (`formatting.ts:16-29`) | `format_duration()` — ms/s/m (`formatters.py:14-23`) |
| 工具调用格式化 | `formatToolParams()` — 逐工具 switch (`workflow-logger.ts:218-273`) | `humanize_tool_call()` — match 分发 (`formatters.py:148-163`) + `default_tool_params` 智能截断 (`formatters.py:96-117`) |
| 工具调用可读化 | `filterJsonToolCalls()` — JSON→可读文本 (`output-formatters.ts:184-242`) | ✅ **v2 已移植**：`humanize_tool_call` + `summarize_todo` + `maybe_browser_action` (`formatters.py:63-163`) |

**差异说明：** 基础格式化工具（时间戳、持续时间、工具参数截断）功能对齐。`format_duration` 两版逻辑完全一致。v2 复审后，原始 Shannon `output-formatters.ts` 的工具调用可读化系统已完整移植（详见 1.5 LOG-A1/A4/A5）。

### 1.4 输出目标

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| stdout | `console.log` / `process.stdout.write` | `click.echo` / `sys.stdout.write`（默认）/ Rich Panel（`use_rich=True`）|
| stderr | `console.error` / `console.warn` | `click.secho(fg=red/yellow)` |
| 工作流日志 | `workspaces/{session}/workflow.log` | `workspaces/{session}/workflow.log` |
| Agent 日志 | `{timestamp}_{agent}_attempt-{n}.log` (JSON Lines) | 同结构 (`utils.py:26-28`) |
| Prompt 快照 | `{agent}.md` | 同结构 (`utils.py:31-33`) |
| 会话元数据 | `session.json` | 同结构 (`utils.py:41-43`) |

**差异说明：** 文件结构完全对齐，目录命名和文件命名规则一致。v2 新增可选 Rich stdout 通路（`RichConsoleRenderer`，默认关闭以保持 CLI 行为不变）。

### 1.5 架构层差距汇总

| Gap ID | 描述 | 优先级 | 状态 | 证据 |
|--------|------|--------|------|------|
| LOG-A1 | 缺少工具调用可读化过滤器 (`filterJsonToolCalls`) | **中** | ✅ **已修复（v2）** | `humanize_tool_call` (`formatters.py:148-163`) + `default_tool_params` (`formatters.py:96-117`) |
| LOG-A2 | 缺少 Agent 前缀映射表 (`getAgentPrefix`) | **中** | ✅ **已修复（v2）** | `agent_prefix` (`formatters.py:58-60`)，精确键匹配消除 authz-before-auth 风险 |
| LOG-A3 | LogStream 缺少背压处理 | **低** | ⏸️ **维持（YAGNI 裁定）** | `log_stream.py:21-26` 每次 flush；spec 非目标，aiofiles 行为可接受 |
| LOG-A4 | 缺少浏览器操作格式化 (`formatBrowserAction`) | **低** | ✅ **已实现（黑盒启用时）** | `maybe_browser_action` (`formatters.py:120-145`) |
| LOG-A5 | 缺少 TodoWrite 更新摘要 (`summarizeTodoUpdate`) | **低** | ✅ **已修复（v2）** | `summarize_todo` (`formatters.py:63-77`) |
| LOG-A6 | 缺少错误分类展示 (`classifyErrorForTemporal`) | **中** | ✅ **已修复（v2）** | `classification.py` `classify_for_temporal` (`:52-61`) + `is_retryable_for_display` (`:78-91`) |

---

## Part 2: 展示层对比

### 2.1 工作流日志格式

**原始 Shannon 输出示例：**
```
================================================================================
Shannon Pentest - Workflow Log
================================================================================
Workflow ID: workflow-abc123
Target URL:  https://example.com
Started:     2024-01-15T14:30:45.123Z
================================================================================

[2024-01-15 14:30:45] [PHASE] Starting: pre-reconnaissance

[2024-01-15 14:30:45] [AGENT] vuln_scanner: Starting (attempt 1)
[2024-01-15 14:30:45] [vuln_scanner] [TOOL] Bash: ls -la /app
[2024-01-15 14:30:45] [vuln_scanner] [LLM] Turn 1: Analyzing source code...
[2024-01-15 14:30:50] [AGENT] vuln_scanner: Completed (5.2s $0.15)
```

**Shannon-py 输出示例（v2，`FileLogRenderer` 实际输出）：**
```
================================================================================
Shannon Pentest - Workflow Log
================================================================================
Workflow ID: workflow-abc123
Target URL:  https://example.com
Started:     2024-01-15 14:30:45
================================================================================

[2024-01-15 14:30:45] [PHASE] Starting pre-reconnaissance
[2024-01-15 14:30:45] [AGENT] pre-reconnaissance: Starting (attempt 1)

[2024-01-15 14:30:46] [PHASE] Starting vulnerability analysis
[2024-01-15 14:30:47] [AGENT] [Injection] injection-vuln: Starting (attempt 1)
[2024-01-15 14:30:47] [TOOL]  [Injection] injection-vuln: Bash: command=ls -la /app
[2024-01-15 14:30:48] [LLM]   [Injection] injection-vuln: Turn 1: Analyzing source code...
[2024-01-15 14:30:52] [AGENT] [Injection] injection-vuln: Completed (5.2s, $0.1500)
```

> **v1 → v2 输出变化**：v1 示例为旧 `workflow_logger.py` 输出（`{name} started/ended`、`[TOOL] {agent} → {tool}({params})`、无前缀）。v2 重构后，动词改为 `Starting/Completed`、标签列对齐（`[TOOL]`/`[LLM]` 后补空格）、vuln agent 注入 `[Injection]` 等前缀。

| 差异点 | 原始 Shannon | Shannon-py (v2) | 状态 |
|--------|-------------|-----------------|------|
| Header 时间戳 | ISO 8601 含 `Z` 后缀 | 本地格式，无时区 | 维持（`FileLogRenderer` 用 `format_log_time` 本地格式，`file_renderer.py:51`）|
| PHASE 动词 | `Starting: / Completed:` | `Starting / Completed` | ✅ **已统一（v2）**（`file_renderer.py:55-58`）|
| PHASE 前空行 | start 事件前插入空行 | start 事件前插入空行 | ✅ **已对齐（v2）**（`file_renderer.py:57`）|
| AGENT 消息格式 | `{name}: Starting/Completed` | `[Prefix] {name}: Starting/Completed` | ✅ **已对齐 + 前缀增强（v2）**（`file_renderer.py:60-75`）|
| AGENT 结束详情 | `Completed (5.2s $0.15)` | `Completed (5.2s, $0.1500)` | ✅ **已对齐（v2）**，4 位精度 |
| TOOL 行格式 | `[{agent}] [TOOL] {tool}: {params}` | `[TOOL]  [Prefix] {agent}: {tool}: {params}` | ✅ **重新设计（v2）**，标签列对齐 |
| TOOL 参数引号 | 命令无引号: `ls -la /app` | `humanize_tool_call` 智能化: `command=ls -la /app` | ✅ **已增强（v2）**（`file_renderer.py:77-80`）|
| LLM 行格式 | `[{agent}] [LLM] Turn {n}: ...` | `[LLM]   [Prefix] {agent}: Turn {n}: ...` | ✅ **重新设计（v2）**，标签列对齐 |
| LLM 换行处理 | 转义为 `\\n` (单行) | 截断 200 字符 | 维持（各有取舍，`file_renderer.py:84`）|
| 完成摘要 | Agent 费用 `$0.15` (2位) | Agent 费用 `$0.1500` (4位) | ✅ **已对齐（v2）**，Py 更精确 |

### 2.2 控制台输出样式

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 颜色库 | 原生 ANSI / `console` 方法 | `click.secho` (fg 参数，默认) / **Rich `Console`（v2，`use_rich=True` 时）** |
| 成功输出 | `console.log('✅ ...')` | `click.secho('✅ ...', fg='green')` |
| 错误输出 | `console.error('❌ ...')` | `click.secho('❌ ...', fg='red')` |
| 警告输出 | `console.warn('⚠️ ...')` | `click.secho('警告', fg='yellow')` |
| 状态标记 | `✓ / ✗` | `✓ / ✗` |
| 进度动画 | Spinner: `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | **Rich 渲染器就绪（v2），live `Progress` 待 runner 接线（LOG-D1）** |

**差异说明：** 默认 CLI 通路颜色和符号使用基本一致（都使用 ✅❌⚠️✓✗）。v2 新增 `RichConsoleRenderer`（`rich_renderer.py`），提供 Panel 分组、Table 汇总、类别样式映射（`STYLE_MAP`），但**默认未启用**——需 `WorkflowLogger(meta, use_rich=True)` 显式开启（`workflow_logger.py:23,37-39`）。原始 Shannon 的旋转 spinner 在 v2 仍部分缺失（详见 LOG-D1）。

### 2.3 Agent 专属展示

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| Agent 前缀 | `[Injection]` `[XSS]` `[Auth]` `[Authz]` `[SSRF]` | ✅ **`agent_prefix` 已实现（v2）**，写入文件日志 + Rich |
| 前缀来源 | `getAgentPrefix()` 映射表 (`output-formatters.ts:35-66`) | `agent_prefix()` 精确键匹配 (`formatters.py:58-60`)，消除 authz-before-auth 顺序陷阱 |
| 并行执行标记 | 前缀 + 紧凑输出格式 | 前缀已对齐；并行进度条待 LOG-D1 |
| 顺序执行标记 | `Turn {n} ({description}):` 缩进格式 | `[LLM]   [Prefix] {agent}: Turn {n}: ...` |
| Task 启动显示 | `🚀 Launching {description}` | ✅ **已实现（v2）**：`humanize_tool_call("Task", ...)` (`formatters.py:152-154`) |
| TodoWrite 显示 | `✅ {completed}` / `🔄 {in_progress}` | ✅ **已实现（v2）**：`summarize_todo` (`formatters.py:63-77`) |
| 浏览器操作图标 | 🌐🖱️⌨️📸📝 等 | ✅ **已实现（v2，黑盒）**：`maybe_browser_action` (`formatters.py:120-145`) |

### 2.4 错误展示

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 错误行格式 | `[ERROR] {error.message} ({context})` | `[ERROR] {ErrorType}: {message} (context: {ctx})` (`file_renderer.py:87-94`) |
| 错误位置 | `workflow-logger.ts:199-205` | `file_renderer.py:87-94` + `workflow_logger.py:90-98` |
| 分类展示 | `classifyErrorForTemporal()` — 类型 + retryable 标记 | ✅ **已实现（v2）**：`classify_for_temporal` + `ErrorEvent.classified/display_retryable` (`classification.py:52-61`) |
| 账单错误 | 专用 billing pattern 检测 | ✅ **已对齐（v2）**：`billing`/`charge failed` 模式 (`classification.py:31-32`) |
| 错误块格式化 | `formatErrorBlock()` — pipe 分隔→多行缩进 (`workflow-logger.ts:305-313`) | ✅ **已移植（v2）**：`format_error_block` (`formatters.py:80-93`) |
| 并行错误 | `{prefix} Failed ({duration})` + 详细字段 | `Failed ({dur}) - {error}` (`file_renderer.py:65-68`)，无并行上下文 |

**v2 关键约束（错误分类双函数）：** 原始 Shannon 有两个语义不同的错误函数，v2 分别移植、绝不合并——`classify_for_temporal` 兜底 **retryable=True**（交 Temporal 退避），`is_retryable_for_display` 兜底 **retryable=False**（fail-safe 展示）。混用会改变 Temporal 重试行为（`classification.py:1-10,78-91`）。

### 2.5 Resume 展示

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| Resume 标记 | `RESUMED` (大写块) | `[RESUME] Resuming workflow` (标签行) |
| 格式 | 分隔线包围的多行块 | 缩进详情块（Previous/New Workflow ID + Checkpoint + Completed Agents） |
| 位置 | `workflow-logger.ts:97-120` | `file_renderer.py:121-128` + `workflow_logger.py:115-123` |

### 2.6 展示层差距汇总

| Gap ID | 描述 | 优先级 | 状态 | 证据 |
|--------|------|--------|------|------|
| LOG-D1 | 缺少进度 Spinner 动画 | **低** | ⚠️ **部分（v2）** | `RichConsoleRenderer` 用 Panel/Table 就绪（`rich_renderer.py`），但 live `Progress` 需 workflow runner 接 `Live` context（plan Self-Review 标注为后续工作）|
| LOG-D2 | PHASE 动词风格不统一 (started vs Starting) | **低** | ✅ **已修复（v2）** | `file_renderer.py:55-58` 统一 `Starting/Completed` |
| LOG-D3 | TOOL 行格式不同 (标签 vs 箭头) | **低** | 已对齐 | v2 重新设计为标签列对齐，清晰可读 |
| LOG-D4 | 缺少 Agent 前缀图标映射 | **中** | ✅ **已修复（v2）** | 同 LOG-A2，`agent_prefix` (`formatters.py:58-60`) |
| LOG-D5 | 缺少浏览器操作图标 | **低** | ✅ **已实现（黑盒）** | 同 LOG-A4，`maybe_browser_action` (`formatters.py:120-145`) |
| LOG-D6 | 缺少 Task/TodoWrite 可读化展示 | **中** | ✅ **已修复（v2）** | `humanize_tool_call` Task/TodoWrite 分支 (`formatters.py:148-163`) |
| LOG-D7 | 缺少错误分类展示和格式化错误块 | **中** | ✅ **已修复（v2）** | `ErrorEvent` 分类展示 (`file_renderer.py:87-94`) + `format_error_block` (`formatters.py:80-93`) |
| LOG-D8 | LLM 输出处理策略不同 (转义 vs 截断) | **低** | 维持（各有取舍） | `file_renderer.py:84` 截断 200 字符；`rich_renderer.py:88` 同 |
| LOG-D9 | Resume 格式差异 (块 vs 标签) | **低** | 已对齐 | `ResumeEvent` 统一渲染 (`file_renderer.py:121-128`) |
| LOG-D10 | Agent 费用精度不同 (2位 vs 4位) | **低** | 已对齐 | Py 使用 4 位精度，更精确 |

---

## Part 3: v2 展示层重构（统一渲染层）

> v2 新增，记录 logging-display-optimization plan 落地的架构能力。

### 3.1 事件模型（单一数据源）

所有日志活动抽象为 frozen dataclass（`events.py`），**纯数据、无渲染逻辑**——双 renderer 共享同一事件流，可重放、可序列化、可独立测试：

| 事件类型 | 字段要点 | 来源 |
|----------|----------|------|
| `WorkflowHeader` | workflow_id, target_url | `events.py:20-23` |
| `PhaseEvent` | phase, event[start/complete] | `events.py:26-29` |
| `AgentEvent` | agent_name, event[start/end], attempt, duration_ms, cost_usd, success, error | `events.py:32-40` |
| `ToolCallEvent` | agent_name, tool_name, parameters | `events.py:43-47` |
| `LlmTurnEvent` | agent_name, turn, content | `events.py:50-54` |
| `ErrorEvent` | error_type, message, context, **classified**, **display_retryable** | `events.py:57-63` |
| `SummaryEvent` | status, total_duration_ms, total_cost_usd, agents[AgentMetric] | `events.py:74-80` |
| `ResumeEvent` | previous/new workflow_id, checkpoint_hash, completed_agents | `events.py:83-88` |

### 3.2 双路分发

```
WorkflowLogger.log_*()
   │ 构造 DisplayEvent (纯数据)
   ▼
DisplayDispatcher ── 不触碰 ActivityLogger ABC
   ├─▶ FileLogRenderer     ──▶ workflow.log (纯文本，无 ANSI，grep/tail 友好)
   └─▶ RichConsoleRenderer ──▶ stdout (Rich Panel/Table/颜色，use_rich=True 时)
```

**集成约束（零破坏）**：`ActivityLogger` ABC（Temporal 桥接）不变；`LogStream`/`LogFileHandler` 的 tail 逻辑与 `COMPLETION_PATTERN`（`logs.py:9`）保持兼容——`FileLogRenderer._summary` 始终输出 `Workflow COMPLETED`/`Workflow FAILED` 行（`file_renderer.py:96-101`）。

> ⚠️ **上图与集成约束描述的是设计意图，非当前运行时状态。** 组件已实现且测试覆盖（93 项全绿），但 `AuditSession`→`WorkflowLogger` 接入链尚未挂到 Temporal activity，生产运行中 workflow.log 内容暂未由本子系统写入。详见 §3.4。

### 3.3 文件结构

```
packages/core/src/shannon_core/
├── display/                    ← v2 新增
│   ├── types.py                # Renderer/LineWriter Protocols
│   ├── events.py               # DisplayEvent dataclass 族
│   ├── dispatcher.py           # DisplayDispatcher（事件分发）
│   ├── formatters.py           # format_* + agent_prefix/humanize_tool_call/...
│   ├── file_renderer.py        # FileLogRenderer（workflow.log 纯文本）
│   └── rich_renderer.py        # RichConsoleRenderer（实时 stdout，可选）
├── errors/                     ← v2 新增
│   └── classification.py       # classify_for_temporal / is_retryable_for_display
└── ... logging/activity_logger.py（不变）
packages/whitebox/src/shannon_whitebox/audit/
├── workflow_logger.py          # v2 改造：emit 事件替代直接写流
├── utils.py                    # v2：re-import format_* from core
└── log_stream.py               # 不变（satisfies LineWriter 协议）
```

### 3.4 接入状态（重要）

> ⚠️ **统一渲染层当前未接入生产 activity pipeline。** §3.1–3.3 描述的是 logging-display-optimization 的**设计能力**——组件实现完整、测试覆盖（93 项全绿），但 `AuditSession`→`WorkflowLogger` 这条接入链尚未挂到 Temporal activity。代码静态分析证据：
>
> - `WorkflowLogger.log_*` 的全部调用封闭在 `AuditSession`（`audit/session.py`）内部
> - `AuditSession` 在生产代码**零实例化**（`grep "AuditSession("` 全仓仅命中测试）
> - 白盒 activity 实际走 `SessionManager`（`shannon_core/session.py`），它只管理 workspace 目录、清理时 **truncate workflow.log**，不写内容
> - `pipeline/activities.py:19` 的 `from ... import AuditSession` 是**未使用的 import**
>
> **影响**：生产 Temporal 运行中 workflow.log 内容暂未由本子系统写入；`FileLogRenderer`/`RichConsoleRenderer` 的新格式输出目前仅在测试与手工 `WorkflowLogger(meta, ...)` 调用中可见。**接入 activity 是启用 Rich（§3.2 的 `use_rich`）的前置依赖**——未完成前开 `use_rich=True` 只会把 Rich 输出写进无人读取的 worker stdout。
>
> **更新（2026-06-15）**：上述"接入链未挂"的判断在两次实时展示实现完成后**对白盒与黑盒两侧均已闭环**。白盒侧经 [`docs/superpowers/plans/2026-06-15-whitebox-live-display.md`](../superpowers/plans/2026-06-15-whitebox-live-display.md) 接线：activity 现通过 `get_audit_session()` 取到 `AuditSession`，worker 经 `run_with_display(..., use_rich=...)` 启动会话、CLI `--plain`/TTY 自动检测切换 Rich 仪表盘。黑盒侧经 [`docs/superpowers/specs/2026-06-15-blackbox-live-display-design.md`](../superpowers/specs/2026-06-15-blackbox-live-display-design.md) 镜像同一方案：`run_recon`/`run_exploit_agent`/`run_report_agent`/`run_blackbox_auth_validation`/`log_phase_start_activity`/`log_phase_complete_activity` 全部接入 `get_audit_session()`，blackbox worker 同样走 `run_with_display`（旧 file-watcher poller 已删除）。上方 §3.1–3.3 的静态分析保留作为历史差距记录；如需刷新"零实例化"等具体证据，请按 2026-06-15 之后的代码重核。

---

## 总结

### 整体评估

Shannon-py 的日志系统在架构层**已对齐并（设计上）超越**原始 Shannon。v2 复审确认：核心接口 (`ActivityLogger`)、Handler 模式（Console / Temporal / File）、以及**统一渲染层**（事件模型 + 双 renderer + 错误分类）均已完成实现并测试通过（93 项全绿）。⚠️ 但统一渲染层**尚未接入生产 activity**（详见 §3.4）——当前这些展示能力仅在测试与手工调用中生效，生产 workflow.log 不由本子系统写入。接口设计上 Shannon-py 更优——工厂函数、Null Object 模式、独立工具审计接口；v2 进一步引入原始项目没有的**事件流可重放**与**双输出单一数据源**架构。

v1 识别的展示层 gap 在 v2 已基本清零：**16 个 gap 中 11 个已修复**（LOG-A1/A2/A4/A5/A6、LOG-D2/D4/D6/D7，加 LOG-D3/D9/D10 原已对齐），1 个部分实现（LOG-D1），2 个经裁定维持（LOG-A3 YAGNI、LOG-D8 各有取舍）。

### gap 闭环追踪（v2）

| 状态 | Gap | 处理方式 |
|------|-----|----------|
| ✅ 已修复 | LOG-A1/A2/A4/A5/A6、LOG-D2/D4/D6/D7 | logging-display-optimization plan 实现 |
| ✅ 已对齐 | LOG-D3/D9/D10 | v1 即已对齐，v2 维持 |
| ⚠️ 部分 | LOG-D1（live spinner） | Rich 渲染器就绪，live `Progress` 待 workflow runner 接 `Live` context |
| ⏸️ 维持 | LOG-A3（背压）、LOG-D8（LLM 转义 vs 截断） | spec 非目标（YAGNI）/ 各有取舍 |

### 优先级排序（v2 更新）

| 优先级 | Gap | 说明 | 复杂度 | v2 状态 |
|--------|-----|------|--------|---------|
| **中** | LOG-A1: 工具调用可读化过滤器 | 提升日志可读性 | 中 | ✅ 已修复 |
| **中** | LOG-A2: Agent 前缀映射 | 多 Agent 并行辨识度 | 低 | ✅ 已修复 |
| **中** | LOG-D4: Agent 前缀图标映射 | 与 LOG-A2 同源 | 低 | ✅ 已修复 |
| **中** | LOG-D6: Task/TodoWrite 可读化 | 并行 Agent 场景关键展示 | 中 | ✅ 已修复 |
| **中** | LOG-D7: 错误分类展示 | 影响错误定位效率 | 中 | ✅ 已修复 |
| 低 | LOG-A3: LogStream 背压 | 高并发场景潜在风险 | 低 | ⏸️ 维持（YAGNI）|
| 低 | LOG-A4: 浏览器操作格式化 | 仅黑盒适用 | 低 | ✅ 已实现 |
| 低 | LOG-A5: TodoWrite 摘要 | 与 LOG-D6 同源 | 低 | ✅ 已修复 |
| 低 | LOG-D1: Spinner | 纯美化 | 低 | ⚠️ 部分（Rich 就绪，待接线）|
| 低 | LOG-D2: PHASE 动词统一 | 风格一致性 | 低 | ✅ 已修复 |
| 低 | LOG-D5: 浏览器图标 | 仅黑盒适用 | 低 | ✅ 已实现 |
| 低 | LOG-D8: LLM 输出策略 | 两种策略各有取舍 | 低 | 维持 |

### Shannon-py 新增能力

| 能力 | 说明 | 来源 |
|------|------|------|
| **DisplayEvent 事件模型（v2）** | frozen dataclass 族，纯数据单一数据源，事件流可重放/可序列化 | `display/events.py` |
| **DisplayDispatcher 双路分发（v2）** | 一事件→文件 + stdout 双 renderer，逻辑不重复 | `display/dispatcher.py` |
| **FileLogRenderer（v2）** | 纯文本 workflow.log，标签列对齐，`COMPLETION_PATTERN` 兼容 | `display/file_renderer.py` |
| **RichConsoleRenderer（v2）** | Rich Panel/Table/颜色，类别样式映射，默认关闭可选启用 | `display/rich_renderer.py` |
| **错误分类双函数（v2）** | `classify_for_temporal` / `is_retryable_for_display`，相反兜底语义 | `errors/classification.py` |
| `create_activity_logger()` 工厂函数 | 自动选择 Temporal vs Console 后端 | `activity_logger.py:51-58` |
| `NullAuditLogger` | 无会话时静默降级，避免 null 检查 | `tool_audit_logger.py:30-41` |
| `ToolAuditLogger` ABC | 独立工具审计接口 | `tool_audit_logger.py:17-28` |
| watchdog 文件监听 | 事件驱动替代轮询，更高效 | `logs.py:12-37` |
| click + Rich 集成 | 统一终端颜色输出（默认 click，可选 Rich）| 多处 |
| `humanize_tool_call` 智能分发 | Task/TodoWrite/Bash(浏览器)/默认各分支 | `formatters.py:148-163` |

### 后续工作（v2 遗留）

1. **接入 activity pipeline（最高优先，前置依赖）**：把 `AuditSession`/`WorkflowLogger` 挂到白盒 Temporal activity（替换或桥接当前 `SessionManager` 路径），让 workflow.log 在生产运行中真正由 `FileLogRenderer` 写入。**此项未完成前，后续 2–3 项均无运行时意义。**
2. **LOG-D1 live spinner**：接入完成后，在 workflow runner 接 Rich `Live` context，让 `_render_agent` start/end 钩子驱动实时 `Progress`（plan Self-Review 已标注，非遗漏）。
3. **Rich 渲染器默认启用**：当前 `use_rich=False` 且无开关（无 CLI flag / env var）。须在 #1 接入完成后，再评估默认开启对 CI/非交互终端的影响。
4. **LOG-A3 背压**：仅在实测出现高并发写丢失时再考虑（当前 YAGNI）。
