# workflow → 用户提示显示通道（InfoEvent）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 workflow 面向用户的流程提示经 activity → `InfoEvent` 显示通道输出，消除与 Live footer 的行堆叠，并补齐 workflow.log 持久化。

**Architecture:** 新增 `InfoEvent` + `WorkflowLogger.log_info` + File/Rich renderer 支持 + session 桥（core，两 pipeline 共享）；blackbox / whitebox 各加 `log_info_activity`（照 `log_phase_start_activity` 范式）+ worker 注册 + 守卫，迁移 blackbox 6 处 / whitebox 3 处裸 `logger.info/warning`。

**Tech Stack:** Python 3.13、temporalio、rich、pytest（asyncio）

## Global Constraints

- workflow sandbox 线程禁裸文件 I/O / stderr 提示（见 `blackbox-workflow-sandbox-paths-invariant` memory）——提示经 activity 走 dispatcher。
- 黑盒 activity 必须 `async def`（worker 无 `activity_executor`）。
- 新 activity 三处同步（定义 / 调用 / worker 注册），AST 守卫 `count >= 2`。
- TDD：每步先红后绿；frequent commits。
- **只跑改动相关测试文件**——全套 pytest 有预存 hang（见 memory `pytest-whitebox-hang`），勿广跑。
- `retry_for` 来自 `shannon_core.models.retry`，两 pipeline 共享；轻量提示 activity 用 `retry_for("log")`（blackbox `log_phase_start_activity` 已用此 key，见 workflows.py)。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/display/events.py` | DisplayEvent 纯数据 | 加 `InfoEvent` |
| `packages/core/src/shannon_core/audit/workflow_logger.py` | event → dispatcher 发射 | 加 `log_info` |
| `packages/core/src/shannon_core/display/file_renderer.py` | event → workflow.log 文本 | 加 `_info` + match case |
| `packages/core/src/shannon_core/display/rich_renderer.py` | event → rich 终端 | 加 `_render_info` + match case |
| `packages/core/src/shannon_core/audit/session.py` | session facade → workflow_logger | 加 `AuditSession.log_info` |
| `packages/core/src/shannon_core/audit/session_registry.py` | NullAuditSession no-op | 加 `NullAuditSession.log_info`（whitebox 经 re-export 共用） |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | BlackboxActivityInput | 加 `info_message` / `info_level` |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | blackbox activities | 加 `log_info_activity` |
| `packages/blackbox/src/shannon_blackbox/worker.py` | worker 注册 | 注册 `log_info_activity` |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | blackbox workflow | 6 处迁移 |
| `packages/blackbox/tests/test_sandbox_safety.py` | AST 守卫 | 加 worker 注册守卫 |
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | ActivityInput | 加 `info_message` / `info_level` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | whitebox activities | 加 `log_info_activity` |
| `packages/whitebox/src/shannon_whitebox/worker.py` | worker 注册 | 注册 `log_info_activity` |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | whitebox workflow | 3 处迁移 |

测试统一放 `packages/core/tests/display/`（已确认存在；workflow_logger / session 属显示层）。

---

## Task 1: InfoEvent 类型 + WorkflowLogger.log_info

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py`
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`
- Test: `packages/core/tests/display/test_events.py`（加测试函数）
- Test: `packages/core/tests/display/test_workflow_logger.py`（**新建**）

**Interfaces:**
- Produces: `InfoEvent(timestamp, category, message, level)`；`WorkflowLogger.log_info(message: str, level: Literal["info","warning"]="info") -> None`

- [ ] **Step 1: 写 InfoEvent 失败测试**

在 `packages/core/tests/display/test_events.py` 末尾追加：

```python
def test_info_event_defaults_to_info_level():
    from shannon_core.display.events import InfoEvent
    e = InfoEvent(timestamp="2026-06-28 12:00:00", category="INFO", message="hello")
    assert e.message == "hello"
    assert e.level == "info"


def test_info_event_warning_level():
    from shannon_core.display.events import InfoEvent
    e = InfoEvent(timestamp="t", category="INFO", message="careful", level="warning")
    assert e.level == "warning"
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest packages/core/tests/display/test_events.py::test_info_event_defaults_to_info_level packages/core/tests/display/test_events.py::test_info_event_warning_level -v`
Expected: FAIL — `ImportError: cannot import name 'InfoEvent'`

- [ ] **Step 3: 实现 InfoEvent**

在 `packages/core/src/shannon_core/display/events.py` 的 `LlmTurnEvent` 之后、`ErrorEvent` 之前插入（`Literal` 已在文件顶部 import）：

```python
@dataclass(frozen=True)
class InfoEvent(DisplayEvent):
    """A user-facing info/warning message emitted from the workflow itself.

    Routed through the dispatcher like other events, so it scrolls above the
    Live footer (no stderr/footer collision) and is persisted to workflow.log.
    level: "info" (cyan) or "warning" (yellow).
    """
    message: str
    level: Literal["info", "warning"] = "info"
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest packages/core/tests/display/test_events.py::test_info_event_defaults_to_info_level packages/core/tests/display/test_events.py::test_info_event_warning_level -v`
Expected: 2 passed

- [ ] **Step 5: 写 log_info 失败测试**

新建 `packages/core/tests/display/test_workflow_logger.py`：

```python
import pytest
from unittest.mock import AsyncMock

from shannon_core.audit.workflow_logger import WorkflowLogger
from shannon_core.display.events import InfoEvent


@pytest.mark.asyncio
async def test_log_info_dispatches_info_event():
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    await wl.log_info("running recon from scratch", level="warning")
    wl._dispatcher.dispatch.assert_awaited_once()
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, InfoEvent)
    assert event.message == "running recon from scratch"
    assert event.level == "warning"
    assert event.category == "INFO"


@pytest.mark.asyncio
async def test_log_info_noop_when_no_dispatcher():
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = None
    await wl.log_info("x")  # 不应 raise
```

- [ ] **Step 6: 跑测试确认红**

Run: `uv run pytest packages/core/tests/display/test_workflow_logger.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'log_info'`

- [ ] **Step 7: 实现 log_info**

在 `packages/core/src/shannon_core/audit/workflow_logger.py`：

(a) `events` import（第 9-12 行）加入 `InfoEvent`：

```python
from shannon_core.display.events import (
    AgentEvent, AgentMetric, ErrorEvent, InfoEvent, LlmTurnEvent, PhaseEvent,
    ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
)
```

(b) 在 `log_phase` 方法之后加 `log_info`：

```python
    async def log_info(self, message: str, level: str = "info") -> None:
        """Emit a user-facing info/warning line.

        Routes through the dispatcher (not bare logging → stderr), so the line
        scrolls above the Live footer and is persisted to workflow.log — avoids
        the stderr/footer collision that bare ``logger.warning`` causes in the
        workflow sandbox thread.
        """
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(InfoEvent(
            timestamp=format_log_time(), category="INFO",
            message=message, level=level,
        ))
```

- [ ] **Step 8: 跑测试确认绿**

Run: `uv run pytest packages/core/tests/display/test_workflow_logger.py -v`
Expected: 2 passed

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/display/events.py \
        packages/core/src/shannon_core/audit/workflow_logger.py \
        packages/core/tests/display/test_events.py \
        packages/core/tests/display/test_workflow_logger.py
git commit -m "feat(core): InfoEvent + WorkflowLogger.log_info 显示通道"
```

---

## Task 2: File/Rich renderer 支持 InfoEvent

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`
- Test: `packages/core/tests/display/test_file_renderer.py`
- Test: `packages/core/tests/display/test_rich_renderer.py`

**Interfaces:**
- Consumes: `InfoEvent`（Task 1 产出）

- [ ] **Step 1: 写 file_renderer 失败测试**

在 `packages/core/tests/display/test_file_renderer.py` 追加（顶部按需补 import：`from unittest.mock import AsyncMock` 与 `from shannon_core.display.events import InfoEvent`——若文件已有这些 import 则不重复）：

```python
@pytest.mark.asyncio
async def test_file_renderer_info_event_info_level():
    from shannon_core.display.file_renderer import FileLogRenderer
    from shannon_core.display.events import InfoEvent
    writer = AsyncMock()
    await FileLogRenderer(writer).render(
        InfoEvent(timestamp="2026-06-28 12:00:00", category="INFO", message="hi", level="info"))
    written = writer.write.await_args.args[0]
    assert "[INFO]" in written and "hi" in written and written.endswith("\n")


@pytest.mark.asyncio
async def test_file_renderer_info_event_warning_level():
    from shannon_core.display.file_renderer import FileLogRenderer
    from shannon_core.display.events import InfoEvent
    writer = AsyncMock()
    await FileLogRenderer(writer).render(
        InfoEvent(timestamp="t", category="INFO", message="careful", level="warning"))
    assert "[WARNING]" in writer.write.await_args.args[0]
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py::test_file_renderer_info_event_info_level packages/core/tests/display/test_file_renderer.py::test_file_renderer_info_event_warning_level -v`
Expected: FAIL — InfoEvent 落入 match 的默认分支（无写入），`writer.write.assert_awaited` 失败

- [ ] **Step 3: 实现 file_renderer._info**

在 `packages/core/src/shannon_core/display/file_renderer.py`：

(a) `render` 的 events import（第 35-38 行）加入 `InfoEvent`：

```python
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, InfoEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
```

(b) match/case 加一行（在 `case ResumeEvent()` 之前）：

```python
            case InfoEvent(): await self._writer.write(self._info(event))
```

(c) 加 `_info` 方法（放在 `_step` 之后）：

```python
    def _info(self, e) -> str:
        label = "WARNING" if e.level == "warning" else "INFO"
        return f"[{e.timestamp}] [{label}] {e.message}\n"
```

- [ ] **Step 4: 跑 file_renderer 测试确认绿**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS（含新增 2 个）

- [ ] **Step 5: 写 rich_renderer 失败测试**

在 `packages/core/tests/display/test_rich_renderer.py` 追加（参照该文件现有 console mock 风格；若用 `MagicMock` 则顶部补 `from unittest.mock import MagicMock`）：

```python
@pytest.mark.asyncio
async def test_rich_renderer_info_event_info_level_cyan():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import InfoEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(
        InfoEvent(timestamp="t", category="INFO", message="hi", level="info"))
    printed = console.print.call_args.args[0]
    assert "INFO" in printed and "cyan" in printed and "hi" in printed


@pytest.mark.asyncio
async def test_rich_renderer_info_event_warning_level_yellow():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import InfoEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(
        InfoEvent(timestamp="t", category="INFO", message="careful", level="warning"))
    printed = console.print.call_args.args[0]
    assert "WARNING" in printed and "yellow" in printed
```

- [ ] **Step 6: 跑测试确认红**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py::test_rich_renderer_info_event_info_level_cyan packages/core/tests/display/test_rich_renderer.py::test_rich_renderer_info_event_warning_level_yellow -v`
Expected: FAIL — InfoEvent 无匹配分支，`console.print` 未被调用

- [ ] **Step 7: 实现 rich_renderer._render_info**

在 `packages/core/src/shannon_core/display/rich_renderer.py`：

(a) `render` 的 events import（第 40-43 行）加入 `InfoEvent`：

```python
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, InfoEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
```

(b) match/case 加一行（在 `case ErrorEvent()` 之前）：

```python
            case InfoEvent(): self._render_info(event)
```

(c) 加 `_render_info` 方法（放在 `_render_step` 之后）：

```python
    def _render_info(self, e) -> None:
        if e.level == "warning":
            self._console.print(
                f"[{e.timestamp}] [yellow]{tag('WARNING')}[/]  {e.message}",
                highlight=False,
            )
        else:
            self._console.print(
                f"[{e.timestamp}] [cyan]{tag('INFO')}[/]  {e.message}",
                highlight=False,
            )
```

- [ ] **Step 8: 跑 rich_renderer 测试确认绿**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS（含新增 2 个）

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py \
        packages/core/src/shannon_core/display/rich_renderer.py \
        packages/core/tests/display/test_file_renderer.py \
        packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(core): File/Rich renderer 支持 InfoEvent(info cyan/warning yellow)"
```

---

## Task 3: session 桥（AuditSession + NullAuditSession）

**Files:**
- Modify: `packages/core/src/shannon_core/audit/session.py`（加 `AuditSession.log_info`，照 `log_phase_start` 模式，见 session.py:92-97）
- Modify: `packages/core/src/shannon_core/audit/session_registry.py`（加 `NullAuditSession.log_info` no-op，照 line 23）
- Test: `packages/core/tests/display/test_session_log_info.py`（**新建**）

**Interfaces:**
- Consumes: `WorkflowLogger.log_info`（Task 1）
- Produces: `AuditSession.log_info(message, level)` / `NullAuditSession.log_info(message, level)`——被 Task 4/5 的 `log_info_activity` 调用

- [ ] **Step 1: 写失败测试**

新建 `packages/core/tests/display/test_session_log_info.py`：

```python
import pytest
from unittest.mock import AsyncMock

from shannon_core.audit.session import AuditSession
from shannon_core.audit.session_registry import NullAuditSession


@pytest.mark.asyncio
async def test_audit_session_log_info_routes_to_workflow_logger():
    session = AuditSession.__new__(AuditSession)
    session._workflow_logger = AsyncMock()
    await session.log_info("msg", level="warning")
    session._workflow_logger.log_info.assert_awaited_once_with("msg", level="warning")


@pytest.mark.asyncio
async def test_audit_session_log_info_defaults_info():
    session = AuditSession.__new__(AuditSession)
    session._workflow_logger = AsyncMock()
    await session.log_info("msg")
    session._workflow_logger.log_info.assert_awaited_once_with("msg", level="info")


@pytest.mark.asyncio
async def test_audit_session_log_info_noop_without_logger():
    session = AuditSession.__new__(AuditSession)
    session._workflow_logger = None
    await session.log_info("msg")  # 不应 raise


@pytest.mark.asyncio
async def test_null_session_log_info_is_noop():
    await NullAuditSession().log_info("x", level="warning")  # 不应 raise
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest packages/core/tests/display/test_session_log_info.py -v`
Expected: FAIL — `AttributeError: 'AuditSession'/'NullAuditSession' has no attribute 'log_info'`

- [ ] **Step 3: 实现 AuditSession.log_info**

在 `packages/core/src/shannon_core/audit/session.py` 的 `log_phase_complete`（line 99-102）之后加：

```python
    async def log_info(self, message: str, level: str = "info") -> None:
        """Emit a user-facing info/warning line (routed via dispatcher, not stderr).

        Replaces bare ``logger.warning/info`` in workflow threads, which would
        hit stderr and collide with the Live footer (redirect_stderr=False).
        """
        if self._workflow_logger:
            await self._workflow_logger.log_info(message, level=level)
```

- [ ] **Step 4: 实现 NullAuditSession.log_info**

在 `packages/core/src/shannon_core/audit/session_registry.py` 的 `log_phase_complete`（line 24）之后加：

```python
    async def log_info(self, message: str, level: str = "info") -> None: pass
```

- [ ] **Step 5: 跑测试确认绿**

Run: `uv run pytest packages/core/tests/display/test_session_log_info.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/audit/session.py \
        packages/core/src/shannon_core/audit/session_registry.py \
        packages/core/tests/display/test_session_log_info.py
git commit -m "feat(core): AuditSession/NullAuditSession.log_info 桥"
```

---

## Task 4: blackbox 接通（字段 + activity + worker + 守卫 + 迁移 6 处）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`（BlackboxActivityInput 加字段）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`（加 `log_info_activity`，照 `log_phase_start_activity` activities.py:403-406）
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`（注册）
- Modify: `packages/blackbox/tests/test_sandbox_safety.py`（加 worker 注册守卫）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`（6 处迁移）

**Interfaces:**
- Consumes: `session.log_info`（Task 3）
- Produces: `log_info_activity(input)`；workflow 侧调用模式 `workflow.execute_activity(activities.log_info_activity, BlackboxActivityInput(**{**act_input.__dict__, "info_message": ..., "info_level": ...}), start_to_close_timeout=timedelta(seconds=10), retry_policy=retry_for("log"))`

> **迁移通则**：原 `logger.info/warning(fmt, *args)` 的 `%`-格式化参数，需在调用处用 f-string 预格式化成单个字符串传入 `info_message`（activity 只接受 str）。多行 message（summary）直接传 join 后的字符串，`console.print` / writer 原生支持。

- [ ] **Step 1: 加 BlackboxActivityInput 字段**

`packages/blackbox/src/shannon_blackbox/pipeline/shared.py` 的 `BlackboxActivityInput`（line 37-50）末尾（`correlation_context` 之后）加：

```python
    info_message: str | None = None   # log_info_activity 的用户提示文本（替代裸 logger.warning→stderr 抢行）
    info_level: str = "info"          # "info" | "warning"（rich 着色：cyan/yellow）
```

- [ ] **Step 2: 加 log_info_activity + 守卫测试（先红）**

在 `packages/blackbox/tests/test_sandbox_safety.py` 末尾追加：

```python
def test_worker_registers_log_info_activity():
    """防回归：log_info_activity 必须在 worker.py 注册（import + activities 列表）。

    见 temporalio-activity-worker-registration 教训：新 activity 三处同步，第 3 处 worker
    注册易漏。提示经 activity 走显示通道，未注册则 workflow 调用时 Temporal 找不到 activity 崩。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("log_info_activity")
    assert count >= 2, (
        f"log_info_activity 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )
```

- [ ] **Step 3: 跑守卫测试确认红**

Run: `uv run pytest packages/blackbox/tests/test_sandbox_safety.py::test_worker_registers_log_info_activity -v`
Expected: FAIL — `assert 0 >= 2`

- [ ] **Step 4: 实现 log_info_activity**

在 `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 的 `log_phase_start_activity`（line 403-406）之后加（照同款范式，无 try/except，best-effort）：

```python
@activity.defn
async def log_info_activity(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
    await get_audit_session().log_info(input.info_message, input.info_level)
```

- [ ] **Step 5: worker 注册**

在 `packages/blackbox/src/shannon_blackbox/worker.py`：

(a) import 块（line 7-17）加入 `log_info_activity`（放 `log_phase_start_activity` 旁）：

```python
from .pipeline.activities import (
    run_blackbox_preflight,
    run_blackbox_auth_validation,
    run_recon,
    run_exploit_agent,
    validate_exploitation_queue,
    assemble_report,
    run_report_agent,
    log_phase_start_activity,
    log_phase_complete_activity,
    log_info_activity,
    load_correlation_context,
)
```

(b) activities 列表（line 82-87）加入 `log_info_activity`：

```python
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, validate_exploitation_queue, assemble_report, run_report_agent,
            log_phase_start_activity, log_phase_complete_activity,
            log_info_activity,
            load_correlation_context,
        ],
```

- [ ] **Step 6: 跑守卫测试确认绿 + import 冒烟**

Run:
```bash
uv run pytest packages/blackbox/tests/test_sandbox_safety.py -v
uv run python -c "import shannon_blackbox.worker; import shannon_blackbox.pipeline.workflows; print('import OK')"
```
Expected: 守卫全绿（4 passed）+ `import OK`

- [ ] **Step 7: 迁移 blackbox workflows.py 第 1 处（No whitebox results warning）**

`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 中（`has_whitebox_results` 判定后的 else 分支），把：

```python
                logger.warning(
                    "No whitebox results found at %s — running RECON_BLACKBOX from scratch. "
                    "Tip: pass --repo <path> to reuse whitebox scan results.",
                    deliverables,
                )
```

替换为：

```python
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": f"No whitebox results found at {deliverables} — running RECON_BLACKBOX from scratch. Tip: pass --repo <path> to reuse whitebox scan results.",
                       "info_level": "warning"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
```

- [ ] **Step 8: 迁移第 2、3 处（whitebox results / correlation results info）**

同文件，把：

```python
                logger.info(
                    "Whitebox results detected at %s for classes: %s — skipping RECON_BLACKBOX",
                    deliverables,
                    found_classes,
                )
```

替换为：

```python
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": f"Whitebox results detected at {deliverables} for classes: {found_classes} — skipping RECON_BLACKBOX",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
```

把：

```python
                    logger.info(
                        "Correlation workspace results detected at %s for classes: %s — "
                        "skipping RECON_BLACKBOX (§6.2 closed loop)",
                        corr_dlv, corr_classes,
                    )
```

替换为：

```python
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        BlackboxActivityInput(**{**act_input.__dict__,
                           "info_message": f"Correlation workspace results detected at {corr_dlv} for classes: {corr_classes} — skipping RECON_BLACKBOX (§6.2 closed loop)",
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
```

- [ ] **Step 9: 迁移第 4 处（Skipping exploit anomalous warning）**

同文件（exploit gating 循环里，`if not validation.valid` 的 anomalous 分支），把：

```python
                            logger.warning(
                                "Skipping exploit for %s (anomalous): %s | queue_path=%s",
                                vt,
                                validation.message,
                                validation.context.get("queue_path", "N/A"),
                            )
```

替换为：

```python
                            await workflow.execute_activity(
                                activities.log_info_activity,
                                BlackboxActivityInput(**{**act_input.__dict__,
                                   "info_message": f"Skipping exploit for {vt} (anomalous): {validation.message} | queue_path={validation.context.get('queue_path', 'N/A')}",
                                   "info_level": "warning"}),
                                start_to_close_timeout=timedelta(seconds=10),
                                retry_policy=retry_for("log"),
                            )
```

- [ ] **Step 10: 迁移第 5、6 处（validation summary / exploit summary）**

把 validation summary（`logger.info("\n".join(summary_lines))`）替换为：

```python
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": "\n".join(summary_lines),
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
```

把 exploit summary（`logger.info(format_exploit_summary(outcomes))`）替换为：

```python
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        BlackboxActivityInput(**{**act_input.__dict__,
                           "info_message": format_exploit_summary(outcomes),
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
```

> 注意缩进：第 5 处在 phase 块主缩进（与 `summary_lines` 定义同级），第 6 处在更深的循环/分支内（与 `format_exploit_summary(outcomes)` 调用同级）。保留原缩进。

- [ ] **Step 11: 验证无残留裸 logger + import 冒烟**

Run:
```bash
echo "=== 应只剩注释/无用户提示的 logger（理想为空或仅诊断）==="
grep -n "logger.info\|logger.warning" packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
uv run python -c "import shannon_blackbox.worker; import shannon_blackbox.pipeline.workflows; print('import OK')"
```
Expected: 6 处已全部改为 `execute_activity`；`import OK`。

- [ ] **Step 12: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py \
        packages/blackbox/src/shannon_blackbox/pipeline/activities.py \
        packages/blackbox/src/shannon_blackbox/worker.py \
        packages/blackbox/src/shannon_blackbox/pipeline/workflows.py \
        packages/blackbox/tests/test_sandbox_safety.py
git commit -m "feat(blackbox): log_info_activity + 迁移 6 处裸 logging 到显示通道"
```

---

## Task 5: whitebox 接通（字段 + activity + worker + 守卫 + 迁移 3 处）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`（ActivityInput 加字段，照 `phase` shared.py:50）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（加 `log_info_activity`，照 `log_phase_start_activity` activities.py:191，但**不需要** steps/intents）
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`（注册，import line 34 + 列表 line 106）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（3 处 `workflow.logger.info` 迁移）
- Test: `packages/whitebox/tests/test_worker_activities.py`（**新建**，worker 注册守卫）

**Interfaces:**
- Consumes: `session.log_info`（Task 3；whitebox session_registry 是 core re-export，NullAuditSession.log_info 已在 Task 3 加）
- Produces: `log_info_activity(input: ActivityInput)`；调用模式 `workflow.execute_activity(activities.log_info_activity, ActivityInput(**{**act_input.__dict__, "info_message": ..., "info_level": ...}), start_to_close_timeout=timedelta(seconds=10), retry_policy=retry_for("log"))`

> **范围说明**：本 task 只迁移 3 处 `workflow.logger.info`（主流程提示）。同文件 except 块里的 `logging.getLogger(__name__).warning`（约 line 301、369，非致命降级诊断）**不迁移**——它们是异常降级语义，且在 except 内迁移需额外 try 保护，留 follow-up。

- [ ] **Step 1: 加 ActivityInput 字段**

`packages/whitebox/src/shannon_whitebox/pipeline/shared.py` 的 `ActivityInput`（line 39）的 `phase` 字段（line 50）之后加：

```python
    info_message: str | None = None   # log_info_activity 用户提示（替代 workflow.logger.info→stderr 抢行）
    info_level: str = "info"          # "info" | "warning"
```

- [ ] **Step 2: 写 worker 注册守卫测试（先红）**

新建 `packages/whitebox/tests/test_worker_activities.py`：

```python
"""whitebox worker activity 注册守卫。"""
from pathlib import Path

WORKER_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_whitebox" / "worker.py"
)


def test_worker_registers_log_info_activity():
    """防回归：log_info_activity 必须在 worker.py 注册（import + activities 列表）。

    见 temporalio-activity-worker-registration 教训。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("log_info_activity")
    assert count >= 2, (
        f"log_info_activity 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )
```

- [ ] **Step 3: 跑守卫测试确认红**

Run: `uv run pytest packages/whitebox/tests/test_worker_activities.py -v`
Expected: FAIL — `assert 0 >= 2`

- [ ] **Step 4: 实现 log_info_activity**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 的 `log_phase_complete_activity`（line 200-210）之后加：

```python
@activity.defn
async def log_info_activity(input: ActivityInput) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    await get_audit_session().log_info(input.info_message, input.info_level)
```

- [ ] **Step 5: worker 注册**

`packages/whitebox/src/shannon_whitebox/worker.py`：

(a) import 块（line 34-35 旁）加入：

```python
    log_phase_start_activity,
    log_phase_complete_activity,
    log_info_activity,
```

(b) activities 列表（line 106 旁）加入 `log_info_activity`：

```python
            log_phase_start_activity, log_phase_complete_activity,
            log_info_activity,
```

- [ ] **Step 6: 跑守卫测试确认绿 + import 冒烟**

Run:
```bash
uv run pytest packages/whitebox/tests/test_worker_activities.py -v
uv run python -c "import shannon_whitebox.worker; import shannon_whitebox.pipeline.workflows; print('import OK')"
```
Expected: 守卫绿 + `import OK`

- [ ] **Step 7: 迁移第 1 处（Auth config scan ok）**

`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:298`，把：

```python
                    workflow.logger.info("Auth config scan ok: %s findings", _auth_scan.get("total_findings", 0))
```

替换为：

```python
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": f"Auth config scan ok: {_auth_scan.get('total_findings', 0)} findings",
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
```

- [ ] **Step 8: 迁移第 2 处（llm_track=disabled）**

同文件 line 318-319，把：

```python
                workflow.logger.info("llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0); "
                                     "running GitNexus track only")
```

替换为：

```python
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0); running GitNexus track only",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
```

- [ ] **Step 9: 迁移第 3 处（GitNexus chain verdict ok）**

同文件 line 366，把：

```python
                workflow.logger.info("GitNexus chain verdict ok: %s", _gn_verdict.get("per_class", {}))
```

替换为：

```python
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": f"GitNexus chain verdict ok: {_gn_verdict.get('per_class', {})}",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
```

- [ ] **Step 10: 验证无残留 + import 冒烟**

Run:
```bash
grep -n "workflow.logger.info" packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
uv run python -c "import shannon_whitebox.worker; import shannon_whitebox.pipeline.workflows; print('import OK')"
```
Expected: 3 处 `workflow.logger.info` 已迁移（except 块的 `logging.getLogger().warning` 保留，符合范围说明）；`import OK`。

- [ ] **Step 11: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py \
        packages/whitebox/src/shannon_whitebox/pipeline/activities.py \
        packages/whitebox/src/shannon_whitebox/worker.py \
        packages/whitebox/src/shannon_whitebox/pipeline/workflows.py \
        packages/whitebox/tests/test_worker_activities.py
git commit -m "feat(whitebox): log_info_activity + 迁移 3 处 workflow.logger.info 到显示通道"
```

---

## 收尾验证（Task 5 后）

- [ ] **跨包回归**：`uv run pytest packages/core/tests/display/ packages/blackbox/tests/test_sandbox_safety.py packages/whitebox/tests/test_worker_activities.py -v` 全绿。
- [ ] **真机冒烟（人工）**：`uv run shannon-blackbox start --url <LAN_IP>:4000 --repo <NodeGoat> -w <ws> --rerun`，确认 exploitation 阶段前 `No whitebox results...` 以独立 `WARNING` 行显示、不再与 footer 堆叠，且出现在 workflow.log。
- [ ] **更新 memory** `blackbox-workflow-sandbox-paths-invariant`（或新建 display 相关 memory）：记录 InfoEvent 通道 + "workflow 面向用户提示走 dispatcher，勿裸 logger.warning→stderr"约定。

---

## Follow-up（不在本计划范围）

- whitebox except 块的 `logging.getLogger().warning`（非致命降级诊断）迁移。
- whitebox 其余 `workflow.logger.*`（若 grep 发现更多）。
- 评估是否给"workflow 不裸调 logger.warning 用户提示"加软守卫（当前靠 review）。
