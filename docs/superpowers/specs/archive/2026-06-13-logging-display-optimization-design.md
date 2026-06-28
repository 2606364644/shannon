# 日志展示优化 Spec：让 Shannon-py 超越原始 Shannon

> 版本: v1 | 日期: 2026-06-13 | 状态: 待评审
> 前置文档: [logging-display-gap-analysis.md](../../gap/logging-display-gap-analysis.md)

## 目的

基于日志展示差距分析，设计 Shannon-py 的日志展示优化方案。目标不是补齐 gap，而是**在补齐的基础上超越原始 Shannon**——通过统一渲染层架构，让日志在展示功能完整性、实时反馈、终端美观、可调试性四个维度全面领先。

## 优化维度

| 维度 | 目标 |
|------|------|
| 展示功能完整性 | 移植原始项目 `output-formatters.ts` 全部能力，无遗漏 |
| 实时反馈与进度 | Rich Live display + 多 agent 并行进度条，原始项目无此能力 |
| 终端美观与可读性 | Rich Panel/颜色/图标，系统化超越原始 ANSI 拼接 |
| 可调试性 | 统一事件模型 + 错误分类 + 事件流可重放，超越原始项目的散落函数 |

## 技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 终端美化库 | **Rich** | 表格/Panel/Progress/Live 一站式，上限远高于原生 click |
| 优化载体 | 实时 stdout + workflow.log 双输出 | 两类载体都需优化 |
| 实现策略 | **方案 A：统一渲染层** | 事件模型 + 双 renderer，单一数据源、不破坏现有架构 |
| 范围 | 白盒为主，黑盒特有功能预留 | 当前项目主战场是白盒 |

## 与 Gap 分析的追溯关系

本 Spec 补齐并超越以下 gap（见 [gap 分析](../../gap/logging-display-gap-analysis.md)）：

| Gap ID | 处理方式 | 本 Spec 位置 |
|--------|---------|-------------|
| LOG-A1 工具调用可读化 | 移植 + 事件模型化 | §4.1 `humanize_tool_call` |
| LOG-A2 Agent 前缀映射 | 移植，扩展到文件日志 | §4.1 `agent_prefix` |
| LOG-A6 错误分类展示 | 独立模块，双函数语义保留 | §4.2 |
| LOG-D1 Spinner | Rich Progress 替代 | §3.2 |
| LOG-D2 PHASE 动词统一 | 统一为 `Starting/Completed` | §3.2 |
| LOG-D4 Agent 前缀图标 | 同 LOG-A2 | §4.1 |
| LOG-D6 Task/TodoWrite 可读化 | 移植 `filterJsonToolCalls` | §4.1 |
| LOG-D7 错误分类展示 | 同 LOG-A6 | §4.2 |
| LOG-A4 浏览器操作格式化 | 移植，黑盒启用时适用 | §4.1 |

---

## Part 1: 事件模型（统一日志抽象）

核心思想：把"发生了什么"（事件数据）和"怎么展示"（渲染）分离。所有日志活动先抽象成不可变的冻结 dataclass，再由 renderer 渲染。

```python
# packages/core/src/shannon_core/display/events.py
from dataclasses import dataclass
from typing import Any, Literal

@dataclass(frozen=True)
class DisplayEvent:
    """Base for all display events. Pure data, no rendering logic."""
    timestamp: str          # format_log_time() 本地可读
    category: str           # PHASE / AGENT / TOOL / LLM / ERROR / ...

@dataclass(frozen=True)
class WorkflowHeader(DisplayEvent):
    workflow_id: str | None
    target_url: str | None

@dataclass(frozen=True)
class PhaseEvent(DisplayEvent):
    phase: str
    event: Literal["start", "complete"]

@dataclass(frozen=True)
class AgentEvent(DisplayEvent):
    agent_name: str
    event: Literal["start", "end"]
    attempt: int
    duration_ms: int | None = None
    cost_usd: float | None = None
    success: bool | None = None
    error: str | None = None

@dataclass(frozen=True)
class ToolCallEvent(DisplayEvent):
    agent_name: str
    tool_name: str
    parameters: Any

@dataclass(frozen=True)
class LlmTurnEvent(DisplayEvent):
    agent_name: str
    turn: int
    content: str

@dataclass(frozen=True)
class ErrorEvent(DisplayEvent):
    error_type: str
    message: str
    context: str | None = None
    classified: str | None = None         # 新增：错误分类（移植 classifyErrorForTemporal）
    display_retryable: bool | None = None # 新增：展示用重试标记（移植 isRetryableError）

@dataclass(frozen=True)
class SummaryEvent(DisplayEvent):
    status: str
    total_duration_ms: int
    total_cost_usd: float
    agents: list           # list[AgentMetric]
    error: str | None = None

@dataclass(frozen=True)
class ResumeEvent(DisplayEvent):
    previous_workflow_id: str
    new_workflow_id: str
    checkpoint_hash: str
    completed_agents: list[str]
```

**设计要点：**
- 事件是**纯数据**，无渲染逻辑——单一数据源、双输出
- 冻结 dataclass，事件流可重放、可测试、可序列化为 JSON（调试时能 dump 整个事件流）
- `ErrorEvent` 携带 `classified` 和 `display_retryable` 两个字段，承接错误分类增强（详见 §4.2）

---

## Part 2: RichConsoleRenderer（实时 stdout 渲染）

把 `DisplayEvent` 渲染成 Rich 实时终端输出，使用 Panel 分组、Progress 进度条、颜色语义。

```python
# packages/core/src/shannon_core/display/rich_renderer.py
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress

class RichConsoleRenderer:
    """Render DisplayEvent to Rich live terminal output."""

    STYLE_MAP = {
        "PHASE":  "bold cyan",
        "AGENT":  "blue",
        "TOOL":   "yellow",
        "LLM":    "magenta",
        "ERROR":  "bold red",
        "RESUME": "dim yellow",
    }

    def __init__(self, console: Console | None = None):
        self._console = console or Console()
        self._progress: Progress | None = None   # 多 agent 并行进度

    def render(self, event: DisplayEvent) -> None:
        match event:
            case WorkflowHeader():  self._render_header(event)
            case PhaseEvent():      self._render_phase(event)
            case AgentEvent():      self._render_agent(event)
            case ToolCallEvent():   self._render_tool(event)
            case LlmTurnEvent():    self._render_llm(event)
            case ErrorEvent():      self._render_error(event)
            case SummaryEvent():    self._render_summary(event)
            case ResumeEvent():     self._render_resume(event)
```

**实时输出效果示例：**

```
╭─ Shannon Pentest ───────────────────────────────────────╮
│ Workflow:  workflow-abc123                              │
│ Target:    https://example.com                          │
│ Started:   14:30:45                                     │
╰─────────────────────────────────────────────────────────╯

[14:30:45] PHASE  pre-reconnaissance ──────────────────────
[14:30:46] AGENT  ▶ pre_recon started

╭─ [Injection] vuln_scanner ──────────────────────────────╮
│ [14:30:47] 🔧 Bash(command=ls -la /app)                 │
│ [14:30:48] 💭 turn 1: Analyzing source code for SQL...   │
│ [14:30:52] 🔧 Grep(pattern=SELECT.*FROM)                │
╰─────────────────────────────────────────────────────────╯

Scanning ━━━━━━━━━━━━━━━━━━━━━━━━━━╸ 67% 3/5 agents
  ✓ pre_recon        (1.2s, $0.03)
  ⠹ vuln_scanner     (running, 4.8s)
  ○ xss_scanner      (pending)
```

**超越原始项目的能力：**
1. **Panel 分组**——每个 agent 一个 Rich Panel，框出工具调用和 LLM 回合，比原始项目扁平 `[agent]` 标签更清晰
2. **并行进度条**——`Progress` 实时显示多 agent 状态（✓完成 / ⠹运行中 / ○待开始），原始项目无此能力（补齐 LOG-D1）
3. **颜色语义**——类别 + 成功/失败状态系统化颜色，超越原始 ANSI 拼接
4. **Agent 前缀映射**——移植 `getAgentPrefix()`，注意 authz 必须先于 auth 匹配（补齐 LOG-A2/LOG-D4）
5. **工具调用图标**——🔧 tool、💭 LLM、🚀 task launch、✅ todo done、🔄 todo in-progress

---

## Part 3: FileLogRenderer（workflow.log 优化）

文件日志保持**纯文本**（可 grep、可 tail -f，不混入 ANSI 转义码），但系统化提升可读性。

```python
# packages/core/src/shannon_core/display/file_renderer.py

class FileLogRenderer:
    """Render DisplayEvent to plain text for workflow.log."""

    def render(self, event: DisplayEvent) -> str:
        """Return plain-text line(s), no ANSI codes. Written by LogStream."""
        match event:
            case WorkflowHeader():  return self._fmt_header(event)
            case PhaseEvent():      return self._fmt_phase(event)
            case AgentEvent():      return self._fmt_agent(event)
            case ToolCallEvent():   return self._fmt_tool(event)
            ...
```

**优化后的 workflow.log 示例：**

```
================================================================================
Shannon Pentest - Workflow Log
================================================================================
Workflow ID: workflow-abc123
Target URL:  https://example.com
Started:     2024-01-15 14:30:45
================================================================================

[14:30:45] [PHASE] Starting pre-reconnaissance
[14:30:46] [AGENT] pre_recon: Completed (1.2s, $0.0300)

[14:30:46] [PHASE] Starting vulnerability analysis
[14:30:47] [AGENT] [Injection] vuln_scanner: Starting (attempt 1)
[14:30:47] [TOOL]  [Injection] Bash: ls -la /app
[14:30:48] [LLM]   [Injection] Turn 1: Analyzing source code...
[14:30:52] [AGENT] [Injection] vuln_scanner: Completed (5.2s, $0.1500)

================================================================================
Workflow COMPLETED
────────────────────────────────────────
Workflow ID: workflow-abc123
Status:      completed
Duration:    12.4s
Total Cost:  $0.3450
Agents:      3 completed

Agent Breakdown:
  ✓ pre_recon      (1.2s, $0.0300)
  ✓ vuln_scanner   (5.2s, $0.1500)
  ✓ xss_scanner    (4.1s, $0.1650)
================================================================================
```

**优化点（vs 现有 + 原始项目）：**
1. **统一 PHASE 动词**——采用 `Starting/Completed`（补齐 LOG-D2），start 前加空行提升阶段分隔感
2. **Agent 前缀进文件**——`[Injection]` 等前缀同样写入文件日志（原始项目仅在 stdout 用），并行 agent 时文件也能一眼区分（补齐 LOG-A2）
3. **向后兼容**——保留 `Workflow COMPLETED/FAILED` 完成标记，现有 `COMPLETION_PATTERN = re.compile(r"^Workflow (COMPLETED|FAILED)$")` 正则不变，`LogFileHandler` 的 tail 自动退出逻辑不受影响
4. **Summary agent 对齐**——`✓ agent_name (duration, cost)` 列对齐 + 状态标记，比原始项目纯列表更清晰
5. **工具调用可读化**——移植 `_format_tool_params` 智能截断（Bash→command、Grep→pattern in path）

---

## Part 4: 展示增强功能（移植 + 超越）

核心——把原始项目 `output-formatters.ts` 的优秀特性移植过来，并在事件模型框架内做得更好。

### 4.1 移植的格式化函数

```python
# packages/core/src/shannon_core/display/formatters.py

# (1) Agent 前缀映射 —— 移植 getAgentPrefix() [LOG-A2/LOG-D4]
def agent_prefix(agent_name: str) -> str:
    """Map agent name to display prefix.
    NOTE: authz MUST match before auth, else auth preempts authz."""
    AGENT_PREFIXES = {
        "injection-vuln":    "[Injection]",
        "xss-vuln":          "[XSS]",
        "auth-vuln":         "[Auth]",
        "authz-vuln":        "[Authz]",   # ← 必须在 auth 之前判断
        "ssrf-vuln":         "[SSRF]",
        # exploit 变体同前缀
    }
    # 第一层：精确匹配；第二层：description 子串降级匹配
    ...

# (2) 工具调用可读化 —— 移植 filterJsonToolCalls() [LOG-A1/LOG-D6]
def humanize_tool_call(tool_name: str, params: dict) -> str:
    match tool_name:
        case "Task":      return f"🚀 Launching {params.get('description')}"
        case "TodoWrite": return summarize_todo(params)        # ✅/🔄
        case "Bash":      return maybe_browser_action(params)  # 🌐🖱️⌨️📸（黑盒）
        case _:           return default_tool_params(tool_name, params)

# (3) 浏览器操作图标 —— 移植 formatBrowserAction() [LOG-A4，黑盒启用时适用]
def maybe_browser_action(params: dict) -> str | None:
    """Parse playwright-cli command → emoji phrase.
    navigate→🌐, click→🖱️, type→⌨️, snapshot→📸, etc."""
    ...

# (4) TodoWrite 摘要 —— 移植 summarizeTodoUpdate() [LOG-A5]
def summarize_todo(params: dict) -> str | None:
    """Show latest completed (✅) or first in-progress (🔄) todo."""
    ...

# (5) 错误块格式化 —— 移植 formatErrorBlock()（pipe 分隔→对齐缩进多行）
def format_error_block(error_str: str) -> str: ...
```

### 4.2 错误分类系统（移植 classifyErrorForTemporal + isRetryableError）

> ⚠️ **关键约束**：原始项目有两个语义不同的错误函数，必须分别移植，不可合并。

```python
# packages/core/src/shannon_core/errors/classification.py
from enum import StrEnum

class ErrorType(StrEnum):
    BILLING = "BillingError"                # 可重试
    RATE_LIMIT = "RateLimitError"           # 可重试
    AUTHENTICATION = "AuthenticationError"  # 不可重试（401）
    PERMISSION = "PermissionError"          # 不可重试（403）
    OUTPUT_VALIDATION = "OutputValidationError"  # 可重试（必须在 validation 前）
    INVALID_REQUEST = "InvalidRequestError"      # 不可重试（400）
    CONFIG = "ConfigurationError"           # 不可重试（ENOENT）
    EXECUTION_LIMIT = "ExecutionLimitError"      # 不可重试（max turns/budget）
    TRANSIENT = "TransientError"            # 兜底可重试

def classify_for_temporal(error: Exception) -> tuple[ErrorType, bool]:
    """Temporal retry-policy decision. Match order MUST mirror original.
    Fallback: retryable=True (delegate to Temporal backoff)."""

def is_retryable_for_display(error: Exception) -> bool:
    """Display-only retry flag. Semantics DIFFER from classify_for_temporal!
    Fallback: retryable=False (fail-safe default).
    Used by ErrorEvent.display_retryable."""
```

**匹配顺序（严格复制原始项目）：**
Billing → Authentication → Permission → OutputValidation（必须在 InvalidRequest 前）→ InvalidRequest → RequestTooLarge → Configuration → ExecutionLimit → InvalidTarget → 兜底。

**兜底策略相反**——`classify_for_temporal` 兜底可重试（交 Temporal），`is_retryable_for_display` 兜底不可重试（fail-safe 展示）。混用会改变 Temporal 重试行为，是正确性红线。

### 4.3 超越原始项目的增强

| 增强点 | 原始项目 | Shannon-py 超越之处 |
|--------|---------|---------------------|
| 渲染架构 | 函数散落 `output-formatters.ts` + `workflow-logger.ts` | 统一事件模型 + 双 renderer，逻辑不重复 |
| Agent 前缀范围 | 仅 stdout | stdout + 文件日志都用 |
| 错误分类 | 两函数隐式耦合 Temporal | 独立 `classification.py`，`ErrorEvent` 携带分类字段 |
| 进度展示 | 无并行进度 | Rich `Progress` 多 agent 实时进度条 |
| 浏览器图标 | TS 正则 | Python `re` 等价，emoji Unicode 保留 |
| 类型安全 | TS interface | Python `StrEnum` + `dataclass`/`TypedDict`，可静态检查 |
| 可测试性 | 渲染逻辑耦合 I/O | 事件纯数据，renderer 可单独测试、事件流可重放 |

---

## Part 5: 集成与测试

### 5.1 数据流与集成点

```
Agent 运行时
   │ emit DisplayEvent (纯数据)
   ▼
DisplayDispatcher ── 新增，不触碰现有 ABC
   ├─▶ RichConsoleRenderer ──▶ stdout (Rich Live / Panel / Progress)
   └─▶ FileLogRenderer     ──▶ workflow.log (纯文本，经 LogStream)
```

**集成原则——最小侵入：**
- **不动** `ActivityLogger` ABC（`info/warn/error` 继续桥接 Temporal，已验证组件零风险）
- **不动** `LogStream`、`LogFileHandler` 的 tail/COMPLETION_PATTERN 逻辑
- **改造** `workflow_logger.py`：现有方法从"直接写 LogStream"改为"构造 DisplayEvent 并 emit 给 dispatcher"
- **新增** `display/` 模块与 `errors/classification.py`

### 5.2 文件结构

```
packages/core/src/shannon_core/
├── display/                    ← 新增模块
│   ├── events.py               # DisplayEvent dataclass 族
│   ├── dispatcher.py           # DisplayDispatcher（事件分发）
│   ├── rich_renderer.py        # RichConsoleRenderer（实时 stdout）
│   ├── file_renderer.py        # FileLogRenderer（workflow.log 纯文本）
│   └── formatters.py           # humanize_tool_call / agent_prefix / ...
├── errors/                     ← 新增模块
│   └── classification.py       # classify_for_temporal / is_retryable_for_display
├── logging/
│   └── activity_logger.py      # 不变（Temporal 桥接）
└── whitebox/src/shannon_whitebox/audit/
    └── workflow_logger.py      # 改造：emit 事件替代直接写流
```

### 5.3 测试策略

| 层次 | 测试内容 | 方法 |
|------|---------|------|
| 事件构造 | frozen dataclass 不可变、字段完整 | 普通单测，断言字段 |
| formatters | `agent_prefix`/`humanize_tool_call`/`summarize_todo` 各分支 | 表驱动测试（输入→预期输出），含 authz-before-auth 边界 |
| 错误分类 | `classify_for_temporal`/`is_retryable_for_display` 匹配顺序 | 每个错误类型一个用例，验证两函数兜底策略相反 |
| FileLogRenderer | 事件序列→纯文本 | **快照测试**（snapshot），断言输出文本 |
| RichConsoleRenderer | 事件→Rich renderable | 断言生成的 `Panel`/`Progress` 结构 |
| **兼容性** | workflow.log 的 `COMPLETION_PATTERN` 仍匹配 | 集成测试：正则匹配 `^Workflow (COMPLETED\|FAILED)$` |
| 集成 | 模拟 agent 运行全流程 | 端到端：检查 stdout + workflow.log 双输出正确 |

---

## 交付：4 阶段滚动

每阶段独立可测、可合并，降低风险：

| 阶段 | 内容 | 验收 |
|------|------|------|
| 1 | 事件模型 + Dispatcher 骨架 | Dispatcher 能接收事件并分发；不改现有行为（先只接 FileLogRenderer） |
| 2 | FileLogRenderer + formatters | workflow.log 输出符合 §3 格式；`COMPLETION_PATTERN` 兼容性测试通过 |
| 3 | 错误分类模块 | `classification.py` 两函数语义测试通过，回填 `ErrorEvent.classified`/`display_retryable` |
| 4 | RichConsoleRenderer | 实时 stdout 展示 Panel/Progress/颜色；全面超越原始项目 |

**阶段间依赖说明：** 阶段3（错误分类）可在阶段2之后任意时机插入。阶段2的 `ErrorEvent` 先以 `classified=None`/`display_retryable=None` 渲染（错误块格式化 `format_error_block` 是纯字符串处理，不依赖分类）；阶段3完成后回填这两个字段，renderer 增强错误展示（如标注 `[BillingError · retryable]`）。阶段4（Rich）依赖阶段1-3全部完成。

---

## 成功标准

1. **功能对齐**：gap 分析中 LOG-A1/A2/A4/A6、LOG-D1/D2/D4/D6/D7 全部补齐
2. **超越验证**：并行进度条、Panel 分组、事件流可重放——三项原始项目没有的能力可用
3. **零破坏**：`ActivityLogger` Temporal 桥接不变；`COMPLETION_PATTERN` tail 退出逻辑兼容
4. **可测试**：formatters / classification / 两 renderer 均有独立测试覆盖
5. **文档**：本 Spec 与 gap 分析建立完整追溯关系

## 非目标（YAGNI）

- 不重写 `LogStream` 背压逻辑（gap LOG-A3 低优先级，aiofiles 行为可接受）
- 不实现黑盒专用展示（浏览器图标代码预留，黑盒启用时激活）
- 不改 `ActivityLogger` 接口签名
- 不引入实时日志的 WebSocket / 远程推送（超出当前范围）
