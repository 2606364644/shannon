# 日志系统对齐设计

> 对比重构项目 (shannon-py) 与原始项目 (shannon/TS) 的日志系统差异，全量补齐缺失组件。

## 背景

原始项目 shannon 是 TypeScript/Node.js 实现，日志系统分为 5 个层次：

1. **核心数据层** — MetricsTracker + session.json（含 phase 聚合）
2. **接口抽象层** — ActivityLogger 接口（Temporal + Console 双实现）
3. **桥接层** — AuditLogger Null Object 模式
4. **审计日志层** — AuditSession + AgentLogger + WorkflowLogger
5. **CLI 层** — `shannon logs` 命令 + ProgressIndicator + Worker 进度轮询

重构项目 shannon-py 已实现第 4 层（审计日志），但其他层存在缺失。本次设计目标为全量对齐。

## 设计决策

- **并发模型**：保持 `asyncio.Lock`（单进程安全），不引入跨进程文件锁。理由：当前 Temporal worker 为单进程模式，暂无多 worker 并行写入 session.json 的场景。
- **LogStream backpressure**：保持 `aiofiles` 的简单实现。理由：Python `aiofiles` 无需手动 drain，已能正确处理异步写入。
- **实施策略**：逐层对齐，从底层到 CLI 层渐进式实现。

---

## 第 1 层：MetricsTracker Phase 聚合

### 现状

重构项目的 `MetricsTracker` 只做了 `total_duration_ms` 和 `total_cost_usd` 的简单累加，缺少按 phase（pre-recon / recon / vulnerability-analysis / exploitation / reporting）的维度聚合。

原始项目的 `session.json` 输出示例：

```json
{
  "metrics": {
    "total_duration_ms": 120000,
    "total_cost_usd": 0.85,
    "phases": {
      "pre-recon": {
        "duration_ms": 15000,
        "duration_percentage": 12.5,
        "cost_usd": 0.10,
        "agent_count": 1
      },
      "recon": { "duration_ms": 30000, "duration_percentage": 25.0, "cost_usd": 0.20, "agent_count": 1 },
      "vulnerability-analysis": { "...": "..." }
    },
    "agents": { "..." }
  }
}
```

### 改动

#### 新增模型 — `packages/core/src/shannon_core/models/audit.py`

```python
class PhaseMetrics(BaseModel):
    duration_ms: int = 0
    duration_percentage: float = 0.0
    cost_usd: float = 0.0
    agent_count: int = 0
```

#### 新增常量 — `packages/core/src/shannon_core/models/agents.py` 或单独文件

```python
AGENT_PHASE_MAP: dict[str, str] = {
    "pre-recon": "pre-recon",
    "recon": "recon",
    "injection-vuln": "vulnerability-analysis",
    "xss-vuln": "vulnerability-analysis",
    "auth-vuln": "vulnerability-analysis",
    "ssrf-vuln": "vulnerability-analysis",
    "authz-vuln": "vulnerability-analysis",
    "misconfig-vuln": "vulnerability-analysis",
    "recon-blackbox": "recon",
    "injection-exploit": "exploitation",
    "xss-exploit": "exploitation",
    "auth-exploit": "exploitation",
    "ssrf-exploit": "exploitation",
    "authz-exploit": "exploitation",
    "misconfig-exploit": "exploitation",
    "report": "reporting",
}
```

#### 改动 — `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py`

在 `end_agent()` 成功完成后：

1. 根据 `AGENT_PHASE_MAP` 找到 agent 所属的 phase
2. 累加该 phase 的 `duration_ms`、`cost_usd`、`agent_count`
3. 重算所有 phase 的 `duration_percentage = phase_duration / total_duration * 100`

在 `initialize()` 中初始化空的 `phases: {}`。

`recalculate_aggregations()` 方法在每次 `end_agent()` 后调用，只累加成功的 agent。

### 向后兼容

- `session.json` 新增 `phases` 字段，不影响已有的 `total_duration_ms`、`total_cost_usd`、`agents` 字段
- 读取旧格式 `session.json` 时，`phases` 缺失不报错（默认为空 dict）

---

## 第 2 层：ActivityLogger 接口 + 双实现

### 现状

原始项目定义了 `ActivityLogger` 接口，有 15+ 处 activity 调用使用它。两个实现：
- `TemporalActivityLogger` — 桥接到 `Context.current().log`（Temporal 原生日志）
- `ConsoleActivityLogger` — 桥接到 `console.log/warn/error`（本地运行）

重构项目中，blackbox workflow 只用了 `logging.getLogger(__name__)`，没有统一的 Activity 级日志抽象。

### 新增文件

#### `packages/core/src/shannon_core/logging/__init__.py`

```python
from .activity_logger import ActivityLogger, create_activity_logger

__all__ = ["ActivityLogger", "create_activity_logger"]
```

#### `packages/core/src/shannon_core/logging/activity_logger.py`

```python
import logging
from abc import ABC, abstractmethod
from typing import Any


class ActivityLogger(ABC):
    """统一的 Activity 日志接口，保持服务层与 Temporal 解耦。"""

    @abstractmethod
    def info(self, message: str, **attrs: Any) -> None: ...

    @abstractmethod
    def warn(self, message: str, **attrs: Any) -> None: ...

    @abstractmethod
    def error(self, message: str, **attrs: Any) -> None: ...


class TemporalActivityLogger(ActivityLogger):
    """桥接到 Temporal activity context 的日志。必须在 activity 上下文内使用。"""

    def info(self, message: str, **attrs: Any) -> None:
        from temporalio import activity
        activity.logger.info(message, extra=attrs)

    def warn(self, message: str, **attrs: Any) -> None:
        from temporalio import activity
        activity.logger.warning(message, extra=attrs)

    def error(self, message: str, **attrs: Any) -> None:
        from temporalio import activity
        activity.logger.error(message, extra=attrs)


class ConsoleActivityLogger(ActivityLogger):
    """桥接到标准 logging 的日志，用于本地运行和测试。"""

    def __init__(self) -> None:
        self._logger = logging.getLogger("shannon.activity")

    def info(self, message: str, **attrs: Any) -> None:
        self._logger.info(message, extra=attrs)

    def warn(self, message: str, **attrs: Any) -> None:
        self._logger.warning(message, extra=attrs)

    def error(self, message: str, **attrs: Any) -> None:
        self._logger.error(message, extra=attrs)


def create_activity_logger() -> ActivityLogger:
    """工厂函数：在 Temporal activity 上下文内返回 TemporalActivityLogger，否则返回 ConsoleActivityLogger。"""
    try:
        from temporalio import activity
        activity.info()  # 如果不在 activity 上下文中会抛异常
        return TemporalActivityLogger()
    except Exception:
        return ConsoleActivityLogger()
```

### 集成方式

在所有 activity 函数中：

```python
# 之前
logger = logging.getLogger(__name__)
logger.info("some message")

# 之后
logger = create_activity_logger()
logger.info("some message")
```

影响范围：`packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 中的所有 activity 函数，以及 `packages/whitebox/` 中对应的 activity 函数。

---

## 第 3 层：AuditLogger 桥接层（Null Object 模式）

### 现状

原始项目的 AI 执行层通过 `AuditLogger` 接口记录 LLM 响应和工具调用，使用 Null Object 模式避免 null 检查。

### 新增文件

#### `packages/whitebox/src/shannon_whitebox/audit/audit_logger.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import AuditSession


class AuditLogger(ABC):
    """审计日志桥接接口，供 AI 执行层使用。调用方无需检查 null。"""

    @abstractmethod
    async def log_llm_response(self, turn: int, content: str) -> None: ...

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: Exception, duration: int, turns: int) -> None: ...


class RealAuditLogger(AuditLogger):
    """桥接到 AuditSession 的实际实现。"""

    def __init__(self, audit_session: AuditSession) -> None:
        self._session = audit_session

    async def log_llm_response(self, turn: int, content: str) -> None:
        await self._session.log_event("llm_response", {"turn": turn, "content": content})

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._session.log_event("tool_start", {"toolName": tool_name, "parameters": parameters})

    async def log_tool_end(self, result: Any) -> None:
        await self._session.log_event("tool_end", {"result": result})

    async def log_error(self, error: Exception, duration: int, turns: int) -> None:
        await self._session.log_event("error", {
            "message": str(error),
            "errorType": type(error).__name__,
            "duration": duration,
            "turns": turns,
        })


class NullAuditLogger(AuditLogger):
    """空操作实现。所有方法为 no-op，确保调用方永远不需要 null 检查。"""

    async def log_llm_response(self, turn: int, content: str) -> None: pass
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: pass
    async def log_tool_end(self, result: Any) -> None: pass
    async def log_error(self, error: Exception, duration: int, turns: int) -> None: pass


def create_audit_logger(audit_session: AuditSession | None) -> AuditLogger:
    """工厂函数：session 存在返回 RealAuditLogger，否则返回 NullAuditLogger。"""
    if audit_session is not None:
        return RealAuditLogger(audit_session)
    return NullAuditLogger()
```

### 集成方式

在 agent 执行服务中：

```python
# 之前：调用方需要手动做 null 检查
if audit_session:
    await audit_session.log_event("llm_response", {...})

# 之后：无需 null 检查
audit_logger = create_audit_logger(audit_session)
await audit_logger.log_llm_response(turn, content)
```

更新 `packages/whitebox/src/shannon_whitebox/audit/__init__.py` 导出 `AuditLogger` 和 `create_audit_logger`。

---

## 第 4 层：CLI 日志体验

### 4a. ProgressIndicator

#### 新增文件 — `packages/core/src/shannon_core/cli/progress.py`

```python
import sys
import threading
import time


class ProgressIndicator:
    """终端 spinner 动画，在长时间运行的 agent 执行期间显示进度。"""

    def __init__(self, message: str = "Working...") -> None:
        self._message = message
        self._frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._index = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        # 清除 spinner 行
        sys.stdout.write("\r" + " " * (len(self._message) + 5) + "\r")
        sys.stdout.flush()
        self._running = False

    def finish(self, message: str = "Complete") -> None:
        self.stop()
        print(f"✓ {message}")

    def _spin(self) -> None:
        while not self._stop_event.is_set():
            frame = self._frames[self._index % len(self._frames)]
            sys.stdout.write(f"\r{frame} {self._message}")
            sys.stdout.flush()
            self._index += 1
            self._stop_event.wait(0.1)
```

配套的 Null Object 模式（与原始项目 ProgressManager 对齐）：

```python
class NullProgressIndicator:
    """空操作 ProgressIndicator，当禁用 spinner 时使用。"""
    def start(self) -> None: pass
    def stop(self) -> None: pass
    def finish(self, message: str = "Complete") -> None: pass


def create_progress_indicator(message: str, enabled: bool = True) -> ProgressIndicator | NullProgressIndicator:
    if enabled:
        return ProgressIndicator(message)
    return NullProgressIndicator()
```

### 4b. `shannon logs` 命令

#### 新增文件 — `packages/core/src/shannon_core/cli/logs.py`

使用 `watchdog` 库监听 `workflow.log` 文件变化，输出新增内容，检测到完成标记自动退出。

```python
import re
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


COMPLETION_PATTERN = re.compile(r"^Workflow (COMPLETED|FAILED)$", re.MULTILINE)


class LogFileHandler(FileSystemEventHandler):
    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._position = 0

    def flush(self) -> bool:
        """输出新增内容，返回 True 表示检测到完成标记。"""
        try:
            size = self._path.stat().st_size
            if size <= self._position:
                return False
            content = self._path.read_text(encoding="utf-8")
            new_content = content[self._position:]
            self._position = size
            sys.stdout.write(new_content)
            sys.stdout.flush()
            return bool(COMPLETION_PATTERN.search(new_content))
        except Exception:
            return True  # 文件被删除，视为完成

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                raise SystemExit(0)


def tail_workflow_log(workspace_id: str, workspaces_dir: str = "workspaces") -> None:
    """实时查看 workflow.log，类似 tail -f。检测到 Workflow COMPLETED/FAILED 自动退出。"""
    base = Path(workspaces_dir)

    # 1. 直接匹配
    log_path = base / workspace_id / "workflow.log"
    if not log_path.exists():
        # 2. 尝试 resume ID
        stripped = re.sub(r"_resume_\d+$", "", workspace_id)
        if stripped != workspace_id:
            log_path = base / stripped / "workflow.log"
        if not log_path.exists():
            print(f"ERROR: Workflow log not found for: {workspace_id}", file=sys.stderr)
            sys.exit(1)

    handler = LogFileHandler(log_path)
    print(f"Tailing workflow log: {log_path}")

    # 输出已有内容
    if handler.flush():
        sys.exit(0)

    # 用 watchdog 监听变化
    observer = Observer()
    observer.schedule(handler, str(log_path.parent), recursive=False)
    observer.start()

    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
```

**新增依赖**：`watchdog`（添加到 `packages/core/pyproject.toml`）

### 4c. Worker 进度轮询

在 Temporal worker 的启动/监听逻辑中，实现定期查询 `PipelineProgress` query 并输出进度。

#### 改动文件 — Worker 入口（具体文件取决于项目结构）

```python
import asyncio


async def poll_workflow_progress(handle, interval_seconds: int = 30) -> None:
    """定期查询 PipelineProgress query 并输出进度到控制台。"""
    while True:
        try:
            progress = await handle.query(PipelineProgress)
            elapsed = int(progress.elapsed_ms / 1000)
            phase = progress.current_phase or "unknown"
            agent = progress.current_agent or "none"
            completed = len(progress.completed_agents)
            print(f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/13")
        except Exception:
            pass  # Workflow 可能已完成
        await asyncio.sleep(interval_seconds)
```

在 `waitForWorkflowResult` 对等逻辑中：

```python
async def wait_for_workflow_result(handle) -> None:
    poll_task = asyncio.create_task(poll_workflow_progress(handle))
    try:
        result = await handle.result()
        poll_task.cancel()
        # 输出最终摘要
    except Exception as e:
        poll_task.cancel()
        print(f"\nPipeline failed: {e}")
        raise
```

---

## 实施顺序

按层次从底到顶：

| 阶段 | 内容 | 改动文件 | 预估工作量 |
|------|------|---------|-----------|
| 1 | MetricsTracker phase 聚合 | `models/audit.py`, `metrics_tracker.py`, `models/agents.py` | 小 |
| 2 | ActivityLogger 接口 + 双实现 | `logging/activity_logger.py`（新增），各 activity 文件 | 中 |
| 3 | AuditLogger 桥接层 | `audit/audit_logger.py`（新增），agent 执行服务 | 小 |
| 4a | ProgressIndicator | `cli/progress.py`（新增） | 小 |
| 4b | `shannon logs` 命令 | `cli/logs.py`（新增），CLI 入口 | 中 |
| 4c | Worker 进度轮询 | Worker 入口文件 | 小 |

每个阶段独立可测试，前 3 阶段完成后第 4 阶段可以并行。

## 测试策略

- **阶段 1**：单元测试验证 phase 聚合计算正确性（mock agent 结果，验证 phases dict）
- **阶段 2**：单元测试验证 `create_activity_logger()` 在有无 Temporal 上下文时返回正确类型
- **阶段 3**：单元测试验证 `create_audit_logger(None)` 返回 NullAuditLogger 且所有方法不报错
- **阶段 4a**：单元测试验证 start/stop/finish 行为
- **阶段 4b**：集成测试用临时文件验证 tail 行为和完成检测
- **阶段 4c**：集成测试 mock Temporal handle 验证轮询输出格式

## 不在范围内

- 跨进程文件锁（保持 asyncio.Lock）
- LogStream backpressure 处理（保持 aiofiles 简单实现）
- 日志输出格式变更（保持与原始项目一致的 JSON Lines + 人类可读格式）
