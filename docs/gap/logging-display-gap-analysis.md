# 日志展示差距分析：原始 Shannon (TypeScript) vs Shannon-py (Python)

> 版本: v1 | 日期: 2026-06-13 | 状态: 初始分析

## 目的

对比原始 Shannon (TypeScript) 与重构 Shannon-py (Python) 在日志系统上的差异，覆盖架构层（handler、formatter、输出目标）和展示层（终端可见输出）。每个识别的 gap 标注编号、优先级和修复状态，为后续修复提供追踪依据。

## 对比范围

| 维度 | 覆盖内容 |
|------|----------|
| 架构层 | 日志接口、Handler 实现、Formatter、输出目标 |
| 展示层 | 格式风格、颜色/符号、进度指示、错误展示 |
| 数据层 | 日志文件结构、JSON schema、会话元数据 |

## 方法论

代码级逐文件对比，两个项目中对应的日志组件直接对照，引用源文件路径和行号作为证据。

---

## Part 1: 架构层对比

### 1.1 日志接口设计

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 核心接口 | `ActivityLogger` interface: `info/warn/error` | `ActivityLogger` ABC: `info/warn/error` |
| 接口位置 | `apps/worker/src/types/activity-logger.ts` | `packages/core/src/shannon_core/logging/activity_logger.py:6-16` |
| 工厂模式 | 无，直接实例化 | `create_activity_logger()` 工厂函数 (`:51-58`) |
| Null 模式 | 无 | `NullAuditLogger` / 空会话降级 |
| 审计接口 | 直接嵌入 agent | 独立 `AuditLogger` ABC + `RealAuditLogger` |
| 工具审计 | 无独立接口 | `ToolAuditLogger` ABC + `NullToolAuditLogger` (`packages/core/src/shannon_core/agents/tool_audit_logger.py:17-41`) |

**差异说明：** Shannon-py 在接口设计上更完善——引入了工厂函数（自动选择 Temporal vs Console 后端）、Null Object 模式（静默降级）、独立的工具审计接口。原始 Shannon 更简单直接。

### 1.2 Handler 实现

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 控制台 | `ConsoleActivityLogger` — `console.log/warn/error` | `ConsoleActivityLogger` — `logging.Logger` (`:35-48`) |
| Temporal | `TemporalActivityLogger` — `Context.current().log` | `TemporalActivityLogger` — `activity.logger` (`:19-32`) |
| 文件流 | `LogStream` — `fs.WriteStream` + 显式背压处理 | `LogStream` — `aiofiles`，无显式背压 (`packages/whitebox/src/shannon_whitebox/audit/log_stream.py:9-47`) |
| 日志文件跟踪 | `logs()` 命令 — `fs.statSync` + `readRange` | `LogFileHandler` — watchdog `FileSystemEventHandler` (`packages/core/src/shannon_core/cli/logs.py:12-37`) |
| Handler 位置 | `apps/worker/src/local/console-logger.ts` | `packages/core/src/shannon_core/logging/activity_logger.py` |
| 文件流位置 | `apps/worker/src/audit/log-stream.ts` | `packages/whitebox/src/shannon_whitebox/audit/log_stream.py` |

**差异说明：** 核心架构一致（Console / Temporal / File 三种 handler），但实现语言特性不同：
- TS 用 `fs.WriteStream` 自带背压处理，Python 用 `aiofiles` 每次 write 后立即 flush
- TS 用轮询（`fs.statSync`），Python 用 watchdog 事件驱动（更高效）

### 1.3 格式化工具

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 时间戳 (ISO) | `formatTimestamp()` — ISO 8601 UTC (`apps/worker/src/utils/formatting.ts:34-36`) | `format_timestamp()` — ISO 8601 UTC 含毫秒 (`packages/whitebox/src/shannon_whitebox/audit/utils.py:19-25`) |
| 时间戳 (可读) | `formatLogTime()` — `toISOString().replace('T',' ').slice(0,19)` | `format_log_time()` — `strftime("%Y-%m-%d %H:%M:%S")` (`:28-30`) |
| 持续时间 | `formatDuration()` — ms/s/m (`formatting.ts:16-29`) | `format_duration()` — ms/s/m (`utils.py:7-16`) |
| 工具调用格式化 | `formatToolParams()` — 逐工具 switch (`workflow-logger.ts:218-273`) | `_format_tool_params()` — dict 映射 (`workflow_logger.py:13-39`) |
| 工具调用可读化 | `filterJsonToolCalls()` — JSON→可读文本 (`output-formatters.ts:184-242`) | **无对应实现** |

**差异说明：** 基础格式化工具（时间戳、持续时间、工具参数截断）功能对齐。`format_duration` 两版逻辑完全一致。关键差距在于原始 Shannon 的 `output-formatters.ts` 提供了完整的工具调用可读化系统（详见 1.5 节）。

### 1.4 输出目标

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| stdout | `console.log` / `process.stdout.write` | `click.echo` / `sys.stdout.write` |
| stderr | `console.error` / `console.warn` | `click.secho(fg=red/yellow)` |
| 工作流日志 | `workspaces/{session}/workflow.log` | `workspaces/{session}/workflow.log` |
| Agent 日志 | `{timestamp}_{agent}_attempt-{n}.log` (JSON Lines) | 同结构 (`utils.py:48-50`) |
| Prompt 快照 | `{agent}.md` | 同结构 (`utils.py:53-55`) |
| 会话元数据 | `session.json` | 同结构 (`utils.py:63-65`) |

**差异说明：** 文件结构完全对齐，目录命名和文件命名规则一致。

### 1.5 架构层差距汇总

| Gap ID | 描述 | 优先级 | 状态 | 证据 |
|--------|------|--------|------|------|
| LOG-A1 | 缺少工具调用可读化过滤器 (`filterJsonToolCalls`) | **中** | 待修复 | TS: `output-formatters.ts:184-242`，Py: 无对应文件 |
| LOG-A2 | 缺少 Agent 前缀映射表 (`getAgentPrefix`) | **中** | 待评估 | TS: `output-formatters.ts:35-66`，Py: 无对应 |
| LOG-A3 | LogStream 缺少背压处理 | **低** | 待评估 | TS: `log-stream.ts` 有 `drain` 事件监听，Py: `log_stream.py:21-26` 每次 flush |
| LOG-A4 | 缺少浏览器操作格式化 (`formatBrowserAction`) | **低** | 仅黑盒适用 | TS: `output-formatters.ts:83-152`，Py: 无对应 |
| LOG-A5 | 缺少 TodoWrite 更新摘要 (`summarizeTodoUpdate`) | **低** | 待评估 | TS: `output-formatters.ts:157-179`，Py: 无对应 |
| LOG-A6 | 缺少错误分类展示 (`classifyErrorForTemporal`) | **中** | 待评估 | TS: `services/error-handling.ts`，Py: 无对应 |

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

**Shannon-py 输出示例：**
```
================================================================================
Shannon Pentest - Workflow Log
================================================================================
Workflow ID: workflow-abc123
Target URL:  https://example.com
Started:     2024-01-15 14:30:45
================================================================================

[2024-01-15 14:30:45] [PHASE] pre-reconnaissance started
[2024-01-15 14:30:45] [AGENT] vuln_scanner started (attempt 1)
[2024-01-15 14:30:45] [TOOL] vuln_scanner → Bash(command=ls -la /app)
[2024-01-15 14:30:45] [LLM] vuln_scanner turn 1: Analyzing source code...
[2024-01-15 14:30:50] [AGENT] vuln_scanner ended (5.2s, cost: $0.1500, ✓)
```

| 差异点 | 原始 Shannon | Shannon-py | 来源 |
|--------|-------------|------------|------|
| Header 时间戳 | ISO 8601 含 `Z` 后缀 | 本地格式，无时区 | TS: `workflow-logger.ts:87` vs Py: `workflow_logger.py:63` |
| PHASE 动词 | `Starting: / Completed:` | `started / completed` (后置) | TS: `:136-137` vs Py: `:72-73` |
| PHASE 前空行 | start 事件前插入空行 | 无 | TS: `:139-141` |
| AGENT 消息格式 | `{name}: Starting/Completed` | `{name} started/ended` | TS: `:155-179` vs Py: `:79-95` |
| AGENT 结束详情 | `Completed (5.2s $0.15)` | `ended (5.2s, cost: $0.1500, ✓)` | TS: `:159-177` vs Py: `:86-94` |
| TOOL 行格式 | `[{agent}] [TOOL] {tool}: {params}` | `[TOOL] {agent} → {tool}({params})` | TS: `:283` vs Py: `:102` |
| TOOL 参数引号 | 命令无引号: `ls -la /app` | 带 key 名: `command=ls -la /app` | TS: `:229` vs Py: `:29-32` |
| LLM 行格式 | `[{agent}] [LLM] Turn {n}: ...` | `[LLM] {agent} turn {n}: ...` | TS: `:295` vs Py: `:109` |
| LLM 换行处理 | 转义为 `\\n` (单行) | 截断 200 字符 | TS: `:294` vs Py: `:108` |
| 完成摘要 | Agent 费用 `$0.15` (2位) | Agent 费用 `$0.1500` (4位) | TS: `:345` vs Py: `:147-148` |

### 2.2 控制台输出样式

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 颜色库 | 原生 ANSI / `console` 方法 | `click.secho` (fg 参数) |
| 成功输出 | `console.log('✅ ...')` | `click.secho('✅ ...', fg='green')` |
| 错误输出 | `console.error('❌ ...')` | `click.secho('❌ ...', fg='red')` |
| 警告输出 | `console.warn('⚠️ ...')` | `click.secho('警告', fg='yellow')` |
| 状态标记 | `✓ / ✗` | `✓ / ✗` |
| 进度动画 | Spinner: `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | **无 spinner** |

**差异说明：** 颜色和符号使用基本一致（都使用 ✅❌⚠️✓✗），关键差距是原始 Shannon 有旋转动画 spinner 用于长时间运行的 agent。

### 2.3 Agent 专属展示

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| Agent 前缀 | `[Injection]` `[XSS]` `[Auth]` `[Authz]` `[SSRF]` | 无前缀，仅 agent name |
| 前缀来源 | `getAgentPrefix()` 映射表 (`output-formatters.ts:35-66`) | — |
| 并行执行标记 | 前缀 + 紧凑输出格式 | 无区分 |
| 顺序执行标记 | `Turn {n} ({description}):` 缩进格式 | 无区分 |
| Task 启动显示 | `🚀 Launching {description}` | 无对应 |
| TodoWrite 显示 | `✅ {completed}` / `🔄 {in_progress}` | 无对应 |
| 浏览器操作图标 | 🌐🖱️⌨️📸📝 等 | 无对应 |

### 2.4 错误展示

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| 错误行格式 | `[ERROR] {error.message} ({context})` | `[ERROR] {ErrorType}: {message} (context: {ctx})` |
| 错误位置 | `workflow-logger.ts:199-205` | `workflow_logger.py:117-124` |
| 分类展示 | `classifyErrorForTemporal()` — 类型 + retryable 标记 | 无独立分类 |
| 账单错误 | 专用 billing pattern 检测 | 无专用检测 |
| 错误块格式化 | `formatErrorBlock()` — pipe 分隔→多行缩进 (`workflow-logger.ts:305-313`) | 无对应 |
| 并行错误 | `{prefix} Failed ({duration})` + 详细字段 | 无并行上下文 |

### 2.5 Resume 展示

| 维度 | 原始 Shannon (TS) | Shannon-py (Python) |
|------|-------------------|---------------------|
| Resume 标记 | `RESUMED` (大写块) | `[RESUME]` (标签行) |
| 格式 | 分隔线包围的多行块 | 标签格式 + 缩进详情 |
| 位置 | `workflow-logger.ts:97-120` | `workflow_logger.py:156-167` |

### 2.6 展示层差距汇总

| Gap ID | 描述 | 优先级 | 状态 | 证据 |
|--------|------|--------|------|------|
| LOG-D1 | 缺少进度 Spinner 动画 | **低** | 待评估 | TS: spinner frames，Py: 无 |
| LOG-D2 | PHASE 动词风格不统一 (started vs Starting) | **低** | 待修复 | TS: `workflow-logger.ts:136-137`，Py: `workflow_logger.py:72` |
| LOG-D3 | TOOL 行格式不同 (标签 vs 箭头) | **低** | 已对齐 | 两种格式均清晰可读 |
| LOG-D4 | 缺少 Agent 前缀图标映射 | **中** | 待评估 | TS: `output-formatters.ts:35-66` |
| LOG-D5 | 缺少浏览器操作图标 | **低** | 仅黑盒适用 | TS: `output-formatters.ts:83-152` |
| LOG-D6 | 缺少 Task/TodoWrite 可读化展示 | **中** | 待修复 | TS: `output-formatters.ts:206-219` |
| LOG-D7 | 缺少错误分类展示和格式化错误块 | **中** | 待评估 | TS: `error-handling.ts` + `workflow-logger.ts:305-313` |
| LOG-D8 | LLM 输出处理策略不同 (转义 vs 截断) | **低** | 待评估 | TS: `:294` 转义换行，Py: `:108` 截断 200 字符 |
| LOG-D9 | Resume 格式差异 (块 vs 标签) | **低** | 已对齐 | 两种格式均功能完整 |
| LOG-D10 | Agent 费用精度不同 (2位 vs 4位) | **低** | 已对齐 | Py 使用 4 位精度，更精确 |

---

## 总结

### 整体评估

Shannon-py 的日志系统在架构层基本对齐原始 Shannon，核心接口 (`ActivityLogger`) 和 Handler 模式（Console / Temporal / File）已完成移植。Shannon-py 在接口设计上甚至更优——引入了工厂函数、Null Object 模式、独立工具审计接口。

主要差距集中在**展示层增强功能**——这些是原始 Shannon 在 `output-formatters.ts` 中积累的用户体验优化，Shannon-py 尚未移植。

### 优先级排序

| 优先级 | Gap | 说明 | 复杂度 |
|--------|-----|------|--------|
| **中** | LOG-A1: 工具调用可读化过滤器 | 提升日志可读性，影响调试效率 | 中 |
| **中** | LOG-A2: Agent 前缀映射 | 多 Agent 并行时辨识度 | 低 |
| **中** | LOG-D4: Agent 前缀图标映射 | 与 LOG-A2 同源 | 低 |
| **中** | LOG-D6: Task/TodoWrite 可读化 | 并行 Agent 场景关键展示 | 中 |
| **中** | LOG-D7: 错误分类展示 | 影响错误定位效率 | 中 |
| 低 | LOG-A3: LogStream 背压 | 高并发场景潜在风险，Python aiofiles 行为不同 | 低 |
| 低 | LOG-A4: 浏览器操作格式化 | 仅黑盒适用 | 低 |
| 低 | LOG-A5: TodoWrite 摘要 | 与 LOG-D6 同源 | 低 |
| 低 | LOG-D1: Spinner | 纯美化 | 低 |
| 低 | LOG-D2: PHASE 动词统一 | 风格一致性 | 低 |
| 低 | LOG-D5: 浏览器图标 | 仅黑盒适用 | 低 |
| 低 | LOG-D8: LLM 输出策略 | 两种策略各有取舍 | 低 |

### Shannon-py 新增能力

| 能力 | 说明 | 来源 |
|------|------|------|
| `create_activity_logger()` 工厂函数 | 自动选择 Temporal vs Console 后端 | `activity_logger.py:51-58` |
| `NullAuditLogger` | 无会话时静默降级，避免 null 检查 | `tool_audit_logger.py:30-41` |
| `ToolAuditLogger` ABC | 独立工具审计接口 | `tool_audit_logger.py:17-28` |
| watchdog 文件监听 | 事件驱动替代轮询，更高效 | `logs.py:12-37` |
| click 集成 | 统一终端颜色输出 | 多处 |
| `_format_tool_params` 智能截断 | 每 tool 类型指定关键字段 | `workflow_logger.py:13-39` |
