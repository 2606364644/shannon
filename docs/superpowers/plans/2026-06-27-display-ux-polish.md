# 白盒 Live 显示 UX 优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除白盒扫描途中突兀的 Python 堆栈（重定向到日志）、让 Agent Turn 与交付物散文倾向中文（分语境）、并修正自相矛盾的失败标签（与真实重试判定同源）。

**Architecture:** 三处互相独立的修正（Part A / B / C），可分开实现、测试与 merge。Part A 给 `temporalio.activity` logger 装 per-workspace FileHandler 并 `propagate=False`，在 `WorkflowLogger.initialize` 安装（同进程、同 meta 源）。Part B 在 runner/provider 层注入一段 system-prompt 语言指令（claude 用 `system_prompt={"type":"preset","append":…}`，openai 用 `instructions=…`），不动 `prompts/*.txt`。Part C 让显示标签改用 `models/errors.py:classify_error_for_temporal`（与 `ApplicationFailure` 同源）并透传 attempt/max。

**Tech Stack:** Python 3.13、temporalio 1.27.2、claude-agent-sdk（preset/append system prompt）、openai-agents（Agent.instructions）、pytest、rich。

**Spec:** `docs/superpowers/specs/2026-06-27-display-ux-polish-design.md`

## Global Constraints

- **只跑改动相关测试文件**（CLAUDE.md：全套 pytest 有预存 hang/失败）。每个 task 给出精确 pytest 调用。
- **双轨铁律不动**（CLAUDE.md §1）：Part B 是语言指令，禁止 `@include` 确定性产物、禁止改 `prompts/*.txt` 内容。锚点测试锁定。
- **双引擎一致**（CLAUDE.md §2）：Part B 在 claude 与 openai 两轨各自用原生缝注入同一份 `narration_directive()`。
- **frequent commits**：每个 task 末尾 commit。
- env 命名沿用既有约定：`SHANNON_AGENT_NARRATION_LANG`（默认 `"zh"`）。
- 不改 `FindingsRenderer` 写死的英文标签（YAGNI，spec §2 非目标）。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/models/retry.py` | 加 `agent_retry_category(agent_name)` 单一映射 | Modify |
| `packages/core/src/shannon_core/display/events.py` | `ErrorEvent` 加 `attempt`/`max_attempts`/`detail_path` | Modify |
| `packages/core/src/shannon_core/display/rich_renderer.py` | `_render_error` 渲染"将重试 N/M"/"不可重叹" + detail_path | Modify |
| `packages/core/src/shannon_core/display/file_renderer.py` | `_error` 同上 | Modify |
| `packages/core/src/shannon_core/audit/workflow_logger.py` | `log_error` 换同源 classify、透传 attempt/max、装 redirect、填 detail_path | Modify |
| `packages/core/src/shannon_core/audit/session.py` | `log_error` 透传 kwargs | Modify |
| `packages/core/src/shannon_core/audit/session_registry.py` | 抽象 `log_error` 加 kwargs | Modify |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_agent` 传 attempt/max | Modify |
| `packages/core/src/shannon_core/logging/temporalio_redirect.py` | `install_temporalio_log_redirect(path)` | Create |
| `packages/core/src/shannon_core/agents/narration.py` | `narration_directive()` + 指令串 | Create |
| `packages/core/src/shannon_core/agents/providers_anthropic.py` | `_build_options` 注入 system_prompt preset | Modify |
| `packages/core/src/shannon_core/agents/providers_openai.py` | `build_agent` 注入 instructions | Modify |

---

## Part C — 失败标签与重试判定同源（先做，最小且独立）

### Task 1: `agent_retry_category()` 单一映射

**Files:**
- Modify: `packages/core/src/shannon_core/models/retry.py`（末尾追加）
- Test: `packages/core/tests/models/test_retry_category.py`（Create）

**Interfaces:**
- Produces: `agent_retry_category(agent_name: str) -> Category`，其中 `Category = Literal["standard","vuln","log","preflight","auth-validation"]`（retry.py 已定义）。vuln agent（`*-vuln`）→ `"vuln"`，其余 → `"standard"`。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/models/test_retry_category.py
from shannon_core.models.retry import agent_retry_category


def test_vuln_agents_map_to_vuln():
    for name in ("injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"):
        assert agent_retry_category(name) == "vuln"


def test_non_vuln_agents_map_to_standard():
    for name in ("pre-recon", "recon", "report", "validate-authentication"):
        assert agent_retry_category(name) == "standard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/models/test_retry_category.py -v`
Expected: FAIL — `ImportError: cannot import name 'agent_retry_category'`

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 packages/core/src/shannon_core/models/retry.py 末尾
def agent_retry_category(agent_name: str) -> Category:
    """Map an agent name to its retry-policy category (single source of truth).

    Mirrors workflows.py retry_for() calls: vuln agents (per-vt fan-out) → 'vuln'
    (VULN_RETRY, max 5); pre-recon/recon/report and others → 'standard'
    (PRODUCTION_RETRY, max 50). Used by the live display to resolve max_attempts.
    """
    if agent_name.endswith("-vuln"):
        return "vuln"
    return "standard"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/models/test_retry_category.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/retry.py packages/core/tests/models/test_retry_category.py
git commit -m "feat(core): add agent_retry_category single-source mapping for retry display"
```

---

### Task 2: `ErrorEvent` 新字段 + 两 renderer 渲染

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py:75-80`
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py:117-124`
- Modify: `packages/core/src/shannon_core/display/file_renderer.py:94-101`
- Test: `packages/core/tests/display/test_error_event_render.py`（Create）

**Interfaces:**
- Produces: `ErrorEvent` 新增 `attempt: int | None = None`、`max_attempts: int | None = None`、`detail_path: str | None = None`。
- Consumes: Task 1 的 `agent_retry_category`（Task 3 用）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/display/test_error_event_render.py
from io import StringIO

from rich.console import Console

from shannon_core.display.events import ErrorEvent
from shannon_core.display.rich_renderer import RichRenderer
from shannon_core.display.file_renderer import FileLogRenderer


def _evt(**kw):
    base = dict(timestamp="2026-06-27 20:00:00", category="ERROR",
                error_type="PentestError", message="Agent xss-vuln execution failed",
                context="xss-vuln", classified="AgentExecutionError",
                display_retryable=True)
    base.update(kw)
    return ErrorEvent(**base)


def test_rich_retryable_shows_attempt_max():
    buf = StringIO()
    r = RichRenderer(Console(file=buf, width=200, color_system=None))
    r._render_error(_evt(attempt=2, max_attempts=5))
    out = buf.getvalue()
    assert "AgentExecutionError" in out
    assert "将重试 2/5" in out
    assert "non-retryable" not in out


def test_rich_non_retryable():
    buf = StringIO()
    r = RichRenderer(Console(file=buf, width=200, color_system=None))
    r._render_error(_evt(display_retryable=False, classified="AuthenticationError"))
    assert "不可重试" in out if (out := buf.getvalue()) else False


def test_rich_detail_path_suffix():
    buf = StringIO()
    r = RichRenderer(Console(file=buf, width=200, color_system=None))
    r._render_error(_evt(detail_path="logs/activity_failures.log"))
    assert "详细堆栈见 logs/activity_failures.log" in buf.getvalue()


def test_file_retryable_and_detail():
    r = FileLogRenderer(stream=None)  # _error 不依赖 stream
    line = r._error(_evt(attempt=2, max_attempts=5, detail_path="logs/activity_failures.log"))
    assert "[AgentExecutionError · 将重试 2/5]" in line
    assert "详细堆栈见 logs/activity_failures.log" in line
```

> 注：若 `RichRenderer` / `FileLogRenderer` 类名或 `_render_error`/`_error` 可见性与实际不符，先 `grep -n "class .*Renderer" packages/core/src/shannon_core/display/rich_renderer.py file_renderer.py` 对齐再跑。`FileLogRenderer._error` 是纯函数（返回 str），不写 stream，可直接调。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_error_event_render.py -v`
Expected: FAIL — `attempt`/`max_attempts`/`detail_path` 非法 kwargs，或断言找不到"将重试"。

- [ ] **Step 3: Add ErrorEvent fields**

```python
# packages/core/src/shannon_core/display/events.py:75-80 替换 ErrorEvent
@dataclass(frozen=True)
class ErrorEvent(DisplayEvent):
    error_type: str
    message: str
    context: str | None = None
    classified: str | None = None
    display_retryable: bool | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    detail_path: str | None = None
```

- [ ] **Step 4: Update rich_renderer._render_error**

```python
# packages/core/src/shannon_core/display/rich_renderer.py:117-124 替换 _render_error
    def _render_error(self, e) -> None:
        line = f"[{e.timestamp}] [bold red]ERROR[/]  {e.error_type}: {e.message}"
        if e.context:
            line += f" (context: {e.context})"
        if e.classified:
            if e.display_retryable:
                suffix = (
                    f"将重试 {e.attempt}/{e.max_attempts}"
                    if (e.attempt and e.max_attempts) else "将重试"
                )
                line += f" [{e.classified} · {suffix}]"
            else:
                line += f" [{e.classified} · 不可重试]"
        if e.detail_path:
            line += f"  (详细堆栈见 {e.detail_path})"
        self._console.print(line, highlight=False)
```

- [ ] **Step 5: Update file_renderer._error**

```python
# packages/core/src/shannon_core/display/file_renderer.py:94-101 替换 _error
    def _error(self, e) -> str:
        msg = f"[{e.timestamp}] [ERROR] {e.error_type}: {e.message}"
        if e.context:
            msg += f" (context: {e.context})"
        if e.classified:
            if e.display_retryable:
                suffix = (
                    f"将重试 {e.attempt}/{e.max_attempts}"
                    if (e.attempt and e.max_attempts) else "将重试"
                )
                msg += f" [{e.classified} · {suffix}]"
            else:
                msg += f" [{e.classified} · 不可重试]"
        if e.detail_path:
            msg += f"  (详细堆栈见 {e.detail_path})"
        return msg + "\n"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_error_event_render.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/display/events.py packages/core/src/shannon_core/display/rich_renderer.py packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_error_event_render.py
git commit -m "feat(core): ErrorEvent retry/detail fields + 将重试/不可重试 rendering"
```

---

### Task 3: `log_error` 同源 classify + attempt/max 透传

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py:140-148`
- Modify: `packages/core/src/shannon_core/audit/session.py:154-157`
- Modify: `packages/core/src/shannon_core/audit/session_registry.py:26`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:141-160`
- Test: `packages/core/tests/audit/test_log_error_classification.py`（Create）

**Interfaces:**
- Consumes: Task 1 `agent_retry_category` + `retry_for`；Task 2 `ErrorEvent` 新字段。
- Produces: `WorkflowLogger.log_error(error, context=None, *, attempt=None, max_attempts=None)`；`AuditSession.log_error` 同签名（透传）；`run_agent` 调用时传 `attempt`+`max_attempts`。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/audit/test_log_error_classification.py
import asyncio

from shannon_core.audit.workflow_logger import WorkflowLogger
from shannon_core.models.errors import ErrorCode, PentestError


class _SpyDispatcher:
    def __init__(self):
        self.events = []

    async def dispatch(self, event):
        self.events.append(event)


def _make_logger():
    class _Meta:
        repo_path = "/tmp/repo"
        workspace_name = "ws"
        session_id = "s"
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = _SpyDispatcher()
    wl._meta = _Meta()
    wl._activity_failure_log_path = None
    return wl


def test_retryable_agent_execution_uses_models_classification():
    wl = _make_logger()
    err = PentestError(
        "Agent xss-vuln execution failed", "validation",
        retryable=True, error_code=ErrorCode.AGENT_EXECUTION_FAILED,
    )
    asyncio.run(wl.log_error(err, context="xss-vuln", attempt=2, max_attempts=5))
    ev = wl._dispatcher.events[-1]
    assert ev.classified == "AgentExecutionError"      # 同源（非 TransientError）
    assert ev.display_retryable is True
    assert ev.attempt == 2 and ev.max_attempts == 5


def test_non_retryable_auth():
    wl = _make_logger()
    err = PentestError("bad key", "auth", retryable=False, error_code=ErrorCode.AUTH_FAILED)
    asyncio.run(wl.log_error(err))
    ev = wl._dispatcher.events[-1]
    assert ev.classified == "AuthenticationError"
    assert ev.display_retryable is False
```

> 注：`PentestError` 构造签名以 `models/errors.py` 为准（`(message, category, *, retryable=..., error_code=...)`）；若顺序不同，按实际调整。`WorkflowLogger.__new__` 绕过 `__init__` 以单测 `log_error`。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/audit/test_log_error_classification.py -v`
Expected: FAIL — 仍用 `errors.classification.classify_for_temporal`（返回 `TransientError`/StrEnum），`attempt`/`max_attempts` 未透传。

- [ ] **Step 3: Swap WorkflowLogger.log_error to same-source classify**

```python
# packages/core/src/shannon_core/audit/workflow_logger.py:140-148 替换 log_error
    async def log_error(self, error: Exception, context: str | None = None,
                        *, attempt: int | None = None,
                        max_attempts: int | None = None) -> None:
        if self._dispatcher is None:
            return
        from shannon_core.models.errors import classify_error_for_temporal
        etype, retryable = classify_error_for_temporal(error)
        await self._dispatcher.dispatch(ErrorEvent(
            timestamp=format_log_time(), category="ERROR",
            error_type=type(error).__name__, message=str(error), context=context,
            classified=etype, display_retryable=retryable,
            attempt=attempt, max_attempts=max_attempts,
            detail_path=self._activity_failure_log_path))
```

> `self._activity_failure_log_path` 在 Task 5（Part A）的 `initialize` 里设置；本 task 先在 `__init__` 初始化为 `None`（下一步），保证 `log_error` 不报 AttributeError。

- [ ] **Step 4: Init `_activity_failure_log_path` in WorkflowLogger.__init__**

在 `workflow_logger.py` 的 `__init__`（约 line 43-48，`self._stream: LogStream | None = None` 附近）加：
```python
        self._activity_failure_log_path: str | None = None
```

- [ ] **Step 5: Thread kwargs through AuditSession + abstract base**

```python
# packages/core/src/shannon_core/audit/session.py:154-157 替换 log_error
    async def log_error(self, error: Exception, context: str | None = None,
                        *, attempt: int | None = None,
                        max_attempts: int | None = None) -> None:
        """Log an error to the workflow log (renders an [ERROR] line)."""
        if self._workflow_logger:
            await self._workflow_logger.log_error(
                error, context=context, attempt=attempt, max_attempts=max_attempts)
```

```python
# packages/core/src/shannon_core/audit/session_registry.py:26 替换抽象签名
    async def log_error(self, error: Any, context: str | None = None, *,
                        attempt: int | None = None,
                        max_attempts: int | None = None) -> None: pass
```

> 检查有无测试 fake 实现 `log_error`（`grep -rn "async def log_error" packages/*/tests`）；有则补同签名 kwargs（默认值，不破坏调用）。

- [ ] **Step 6: Pass attempt + max_attempts from run_agent**

```python
# packages/whitebox/src/shannon_whitebox/pipeline/activities.py
# 顶部 import 区加（若未有）：
from shannon_core.models.retry import agent_retry_category, retry_for

# run_agent 内，attempt 已在 line 101 取得（attempt = activity.info().attempt）。
# 在两处 except 分支的 session.log_error 调用替换为：
        max_attempts = retry_for(
            agent_retry_category(agent_name.value)).maximum_attempts
        await session.log_error(
            e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
```
> 两处 except（`except PentestError` line 141-151、`except Exception` line 152-160）的 `session.log_error(...)` 行（原 147、158）都改。`max_attempts` 计算可提到 try 之前算一次（attempt 取值之后），两处复用，避免重复。

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/audit/test_log_error_classification.py packages/core/tests/models/test_retry_category.py packages/core/tests/display/test_error_event_render.py -v`
Expected: PASS（Part C 全绿）

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/core/src/shannon_core/audit/session.py packages/core/src/shannon_core/audit/session_registry.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/audit/test_log_error_classification.py
git commit -m "fix(core): error label uses same-source classify_error_for_temporal + attempt/max"
```

---

## Part A — 失败堆栈重定向到 per-workspace 日志

### Task 4: `install_temporalio_log_redirect()` helper

**Files:**
- Create: `packages/core/src/shannon_core/logging/temporalio_redirect.py`
- Test: `packages/core/tests/test_temporalio_log_redirect.py`（Create）

**Interfaces:**
- Produces: `install_temporalio_log_redirect(log_path: Path) -> Path`。给 `logging.getLogger("temporalio.activity")` 设 `propagate=False` + 挂 `FileHandler`（level=WARNING，formatter 自动带 exc_info），幂等。返回 `log_path`。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/test_temporalio_log_redirect.py
import logging
from pathlib import Path

from shannon_core.logging.temporalio_redirect import install_temporalio_log_redirect


def _capture_stderr_records(capsys):
    return capsys  # placeholder; 用 propagate 断言代替


def test_failure_record_goes_to_file_not_stderr(tmp_path, capsys):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)

    logger = logging.getLogger("temporalio.activity")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.warning("Completing activity as failed", exc_info=True)

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err              # 不上终端
    assert "Traceback" in log_path.read_text()          # 进文件
    assert "Completing activity as failed" in log_path.read_text()


def test_debug_records_filtered_out_of_file(tmp_path):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)
    logging.getLogger("temporalio.activity").debug("heartbeat noise")
    assert "heartbeat noise" not in log_path.read_text()   # handler level=WARNING


def test_install_is_idempotent(tmp_path):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)
    install_temporalio_log_redirect(log_path)
    handlers = [h for h in logging.getLogger("temporalio.activity").handlers
                if isinstance(h, logging.FileHandler)]
    assert len(handlers) == 1                              # 不重复添加
```

> 测试间清理：`temporalio.activity` logger 的 handlers 会跨测试残留；可在 module fixture 里备份/还原 `handlers` 与 `propagate`，或各测试先 `logging.getLogger("temporalio.activity").handlers.clear()`。实现稳定后补一个 `@pytest.fixture(autouse=True)` 还原。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_temporalio_log_redirect.py -v`
Expected: FAIL — `ImportError: install_temporalio_log_redirect`。

- [ ] **Step 3: Write minimal implementation**

```python
# packages/core/src/shannon_core/logging/temporalio_redirect.py
"""Redirect temporalio's verbose activity-failure logging to a per-workspace file.

temporalio 1.27.2 logs every activity failure with a full chained traceback via
``temporalio.activity.logger.warning("Completing activity as failed", exc_info=True)``
(worker/_activity.py:474). With no logging config in shannon-py that record hits
stderr via root's lastResort handler — scary, redundant noise next to our own clean
[ERROR] line. We divert it to a file and keep the terminal clean.
"""
from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "temporalio.activity"


def install_temporalio_log_redirect(log_path: Path) -> Path:
    """Divert ``temporalio.activity`` records to *log_path*; suppress from terminal.

    - ``propagate=False`` → records never reach root's lastResort stderr handler.
    - FileHandler level=WARNING → captures failure tracebacks (logged at WARNING),
      drops DEBUG heartbeat noise.
    - Idempotent: a FileHandler on the same resolved path is not re-added.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    target = log_path.resolve()
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            try:
                if Path(h.baseFilename).resolve() == target:
                    return log_path  # already installed on this path
            except OSError:
                continue

    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)   # don't filter at logger level; handler decides
    return log_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/test_temporalio_log_redirect.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/logging/temporalio_redirect.py packages/core/tests/test_temporalio_log_redirect.py
git commit -m "feat(core): install_temporalio_log_redirect diverts activity traceback to file"
```

---

### Task 5: 在 `WorkflowLogger.initialize` 装 redirect + 填 detail_path

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`（`initialize` 约 line 54-58；import 区）
- Test: `packages/core/tests/audit/test_workflow_logger_redirect.py`（Create）

**Interfaces:**
- Consumes: Task 4 `install_temporalio_log_redirect`；既有 `generate_workflow_log_path(meta)`（`audit/utils.py:36`）。
- Produces：`WorkflowLogger` 初始化后 `self._activity_failure_log_path` 指向 `<workflow.log 同目录>/activity_failures.log`，与 redirect 写入路径同源。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/audit/test_workflow_logger_redirect.py
from pathlib import Path
from unittest.mock import patch

from shannon_core.audit.workflow_logger import WorkflowLogger


def test_initialize_installs_redirect_and_sets_detail_path(tmp_path):
    wf_log = tmp_path / "workflow.log"
    with patch("shannon_core.audit.workflow_logger.generate_workflow_log_path",
               return_value=wf_log):
        wl = WorkflowLogger.__new__(WorkflowLogger)
        wl._meta = type("M", (), {"repo_path": str(tmp_path)})()
        wl._use_rich = False
        wl._console = None
        wl._dashboard = None
        wl._dispatcher = None
        wl._activity_failure_log_path = None
        # 只测 redirect 安装这段；stream/dispatcher 用最小桩
        with patch.object(WorkflowLogger, "_open_stream", lambda self, p: None), \
             patch("shannon_core.logging.temporalio_redirect.logging.FileHandler"):
            WorkflowLogger.initialize.__wrapped__(wl, None) if hasattr(
                WorkflowLogger.initialize, "__wrapped__") else None
    # detail_path 指向同源 sibling
    assert wl._activity_failure_log_path == str(wf_log.with_name("activity_failures.log"))
```

> 这条测试偏脆弱（依赖 `initialize` 内部结构）。**优先用更稳的断言**：直接断言 `logging.getLogger("temporalio.activity").propagate is False` 且存在指向 `<wf_dir>/activity_failures.log` 的 FileHandler。若 `initialize` 逻辑较重难以单测，改为：抽一个 `_install_failure_redirect(self)` 私有方法专门装 redirect + 设 `self._activity_failure_log_path`，对它单测（更 TDD 友好）。**推荐走抽方法路线**，测试改为：

```python
def test_install_failure_redirect_sets_path_and_propagate(tmp_path):
    wf_log = tmp_path / "workflow.log"
    import logging
    wl = WorkflowLogger.__new__(WorkflowLogger)
    with patch("shannon_core.audit.workflow_logger.generate_workflow_log_path",
               return_value=wf_log):
        wl._install_failure_redirect()
    assert wl._activity_failure_log_path == str(wf_log.with_name("activity_failures.log"))
    assert logging.getLogger("temporalio.activity").propagate is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/audit/test_workflow_logger_redirect.py -v`
Expected: FAIL — `_install_failure_redirect` 不存在。

- [ ] **Step 3: Add `_install_failure_redirect` + call it in initialize**

```python
# packages/core/src/shannon_core/audit/workflow_logger.py
# 顶部 import 区加：
from shannon_core.logging.temporalio_redirect import install_temporalio_log_redirect
# generate_workflow_log_path 已在文件内 import（line 16 区）

# 新增私有方法（放在 initialize 之后）：
    def _install_failure_redirect(self) -> None:
        """Install the temporalio.activity → file redirect; set detail_path hint.

        Same-source path as workflow.log (sibling). On install failure, degrade
        silently (tracebacks may appear on terminal) — never break the scan.
        """
        try:
            failure_path = generate_workflow_log_path(self._meta).with_name(
                "activity_failures.log")
            install_temporalio_log_redirect(failure_path)
            self._activity_failure_log_path = str(failure_path)
        except Exception:
            logger.warning(
                "temporalio log redirect install failed; "
                "activity tracebacks may appear on terminal", exc_info=True)
            self._activity_failure_log_path = None
```

在 `initialize`（line 54-58 附近，`self._stream = LogStream(path)` 之后、构造 renderers 之前）调用：
```python
        self._install_failure_redirect()
```

> `logger` 为模块级 logger（文件顶部应已有 `logger = logging.getLogger(__name__)`；若无则加）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/audit/test_workflow_logger_redirect.py -v`
Expected: PASS

- [ ] **Step 5: Run Part A + C regression together**

Run: `uv run pytest packages/core/tests/test_temporalio_log_redirect.py packages/core/tests/audit/test_workflow_logger_redirect.py packages/core/tests/audit/test_log_error_classification.py packages/core/tests/display/test_error_event_render.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/core/tests/audit/test_workflow_logger_redirect.py
git commit -m "feat(core): wire temporalio activity traceback redirect in WorkflowLogger.initialize"
```

---

## Part B — Agent 口述与交付物散文倾向中文（分语境）

### Task 6: `narration_directive()` helper + env

**Files:**
- Create: `packages/core/src/shannon_core/agents/narration.py`
- Test: `packages/core/tests/agents/test_narration.py`（Create）

**Interfaces:**
- Produces: `narration_directive() -> str | None`。env `SHANNON_AGENT_NARRATION_LANG`（默认 `"zh"`）→ 返回中文分语境指令；`"en"` 或其它 → `None`。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/agents/test_narration.py
import pytest

from shannon_core.agents.narration import narration_directive, _DIRECTIVE_ZH


@pytest.mark.parametrize("val", ["zh", "ZH", " zh "])
def test_zh_returns_directive(monkeypatch, val):
    monkeypatch.setenv("SHANNON_AGENT_NARRATION_LANG", val)
    assert narration_directive() == _DIRECTIVE_ZH


@pytest.mark.parametrize("val", ["en", "EN", "", "off"])
def test_non_zh_returns_none(monkeypatch, val):
    monkeypatch.setenv("SHANNON_AGENT_NARRATION_LANG", val)
    assert narration_directive() is None


def test_default_is_zh(monkeypatch):
    monkeypatch.delenv("SHANNON_AGENT_NARRATION_LANG", raising=False)
    assert narration_directive() == _DIRECTIVE_ZH


def test_directive_enforces_english_for_structure():
    """安全锚点：受控词/标题/JSON key 必须留英文（spec §4.2）。"""
    d = _DIRECTIVE_ZH
    assert "vulnerability_type" in d
    assert "## Executive Summary" in d
    assert "JSON" in d
    assert "中文" in d            # 确实要求中文口述
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_narration.py -v`
Expected: FAIL — `ImportError: narration_directive`。

- [ ] **Step 3: Write minimal implementation**

```python
# packages/core/src/shannon_core/agents/narration.py
"""Agent narration-language directive (Part B of display-ux-polish spec).

Injects a SYSTEM-prompt directive so GLM narrates in Chinese while keeping
code-consumed structure (JSON keys, controlled-vocabulary field values,
structural Markdown headers) in English. Engine-agnostic: each provider applies
it via its native system-prompt seam. Does NOT touch prompts/*.txt (CLAUDE.md
dual-track invariant — this is a language directive, not a deterministic bridge).
"""
from __future__ import annotations

import os

_DIRECTIVE_ZH = """<language>
- 用中文进行所有口述、推理过程与每轮总结（narration）。
- 人读散文用中文：notes / exploitation_hypothesis / missing_defense /
  evidence_chain 的叙述、报告正文、执行摘要正文。
- 以下必须保持英文（代码解析/匹配）：JSON 字段名、代码/文件路径/命令/ID；
  受控词汇字段的"值"——vulnerability_type、confidence、
  suggested_exploit_technique 等保持 prompt 给定的英文枚举；
  结构性 Markdown 标题，尤其 "## Executive Summary"。
</language>"""


def narration_directive() -> str | None:
    """Return the Chinese narration directive, or None when disabled.

    env SHANNON_AGENT_NARRATION_LANG (default "zh"): "zh" → directive on,
    anything else ("en", etc.) → None (unchanged English behavior).
    """
    lang = os.getenv("SHANNON_AGENT_NARRATION_LANG", "zh").strip().lower()
    return _DIRECTIVE_ZH if lang == "zh" else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/agents/test_narration.py -v`
Expected: PASS (4+ passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/narration.py packages/core/tests/agents/test_narration.py
git commit -m "feat(core): narration_directive zh/en directive (split-language, env-controlled)"
```

---

### Task 7: claude + openai provider 注入缝 + prompt 不变锚点

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py:222-256`（`_build_options`）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py:74-83`（`build_agent`）
- Test: `packages/core/tests/agents/test_narration_injection.py`（Create）

**Interfaces:**
- Consumes: Task 6 `narration_directive`。
- claude 缝：`options.system_prompt = {"type": "preset", "append": directive}`（SDK `subprocess_cli.py:235-238` 映射到 `--append-system-prompt`，真·系统提示位追加，不替换 base）。
- openai 缝：`Agent(instructions=directive, …)`（directive 为 None 时行为不变）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/agents/test_narration_injection.py
from unittest.mock import patch

from shannon_core.agents.narration import _DIRECTIVE_ZH


def test_claude_options_get_append_system_prompt_when_zh():
    from shannon_core.agents.providers_anthropic import AnthropicProvider
    with patch("shannon_core.agents.providers_anthropic.narration_directive",
               return_value=_DIRECTIVE_ZH):
        prov = AnthropicProvider.__new__(AnthropicProvider)
        prov._adaptive_thinking = False
        opts = prov._build_options(cwd="/tmp", model="m", output_format=None)
    assert opts.system_prompt == {"type": "preset", "append": _DIRECTIVE_ZH}


def test_claude_options_unchanged_when_disabled():
    from shannon_core.agents.providers_anthropic import AnthropicProvider
    with patch("shannon_core.agents.providers_anthropic.narration_directive",
               return_value=None):
        prov = AnthropicProvider.__new__(AnthropicProvider)
        prov._adaptive_thinking = False
        opts = prov._build_options(cwd="/tmp", model="m", output_format=None)
    assert opts.system_prompt is None


def test_openai_agent_instructions_carry_directive_when_zh():
    with patch("shannon_core.agents.providers_openai.narration_directive",
               return_value=_DIRECTIVE_ZH):
        # build_agent 依赖 _get_client/_max_turns；用 patch 规避网络
        from shannon_core.agents import providers_openai as po
        prov = po.OpenAIProvider.__new__(po.OpenAIProvider)
        with patch.object(prov, "_get_client"), patch.object(prov, "_max_turns", return_value=5):
            agent = prov.build_agent(model="m", output_format=None)
    assert agent.instructions == _DIRECTIVE_ZH
```

> 注：`AnthropicProvider._is_adaptive_thinking_enabled` 在 `_build_options` 内被调（需 env/可 stub）。若 `_build_options` 还读其它 env 导致桩不稳，改为更小范围：抽 `system_prompt` 赋值为独立可测单元，或直接断言 `_build_options` 返回对象的 `system_prompt` 字段（如上）。`providers_openai.build_agent` 内部构造 `OpenAIChatCompletionsModel` + `build_tools()`，可能较重；若难单测，**改为**：把 instructions 决策抽成 `_instructions()` 方法单测。**推荐抽方法**（见 Step 3b）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_narration_injection.py -v`
Expected: FAIL — `options.system_prompt` 仍为 None / Agent.instructions 仍为 None。

- [ ] **Step 3a: Inject into claude `_build_options`**

在 `packages/core/src/shannon_core/agents/providers_anthropic.py` 顶部 import 区加：
```python
from .narration import narration_directive
```
在 `_build_options` 的 `return options` 之前（line 254 之前）加：
```python
        # Part B: append narration-language directive as system prompt (SDK maps
        # preset/append → --append-system-prompt, true system-prompt position,
        # does NOT replace the base system prompt).
        directive = narration_directive()
        if directive:
            options.system_prompt = {"type": "preset", "append": directive}
```

- [ ] **Step 3b: Inject into openai `build_agent`（抽 `_instructions()`）**

在 `packages/core/src/shannon_core/agents/providers_openai.py` 顶部 import 区加：
```python
from .narration import narration_directive
```
新增方法 + 改 `build_agent` 用它：
```python
    def _instructions(self) -> str | None:
        """Part B: narration-language directive as the Agent's system message.

        None when disabled → unchanged behavior (prompt passed as user input).
        Only the parent agent gets it; task subagents (return terse code output)
        do not, to avoid Chinese leaking into data consumed by the parent.
        """
        return narration_directive()

    def build_agent(self, model: str, output_format: dict | None) -> Agent:
        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        return Agent(
            name="shannon-openai-agent",
            instructions=self._instructions(),  # None when disabled
            tools=build_tools(),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
        )
```
对应测试改为单测 `_instructions()`（更稳）：
```python
def test_openai_instructions_carry_directive_when_zh():
    from shannon_core.agents import providers_openai as po
    with patch("shannon_core.agents.providers_openai.narration_directive",
               return_value=_DIRECTIVE_ZH):
        prov = po.OpenAIProvider.__new__(po.OpenAIProvider)
        assert prov._instructions() == _DIRECTIVE_ZH
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/agents/test_narration_injection.py packages/core/tests/agents/test_narration.py -v`
Expected: PASS

- [ ] **Step 5: Prompt-unchanged anchor test (B4)**

```python
# 追加到 packages/core/tests/agents/test_narration_injection.py
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def test_prompts_do_not_contain_narration_directive():
    """CLAUDE.md dual-track invariant: language directive lives in system-prompt
    layer, never in prompts/*.txt (no deterministic bridge, prompts stay English)."""
    directive_snippet = "narration-language"  # _DIRECTIVE_ZH 的独有片段
    offenders = []
    for p in PROMPTS_DIR.rglob("*.txt"):
        if directive_snippet in p.read_text(encoding="utf-8"):
            offenders.append(p.name)
    assert not offenders, f"directive leaked into prompts: {offenders}"
```

- [ ] **Step 6: Run all Part B tests**

Run: `uv run pytest packages/core/tests/agents/test_narration.py packages/core/tests/agents/test_narration_injection.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/test_narration_injection.py
git commit -m "feat(core): inject zh narration directive via system-prompt (claude preset/append + openai instructions)"
```

---

### Task 8: 人工冒烟验证（spec §6 / CLAUDE.md §2）

**Files:** 无代码改动（验证清单）。

- [ ] **Step 1: Part C/A 真机——触发一次瞬态失败看终端**

Run: `uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat`
Expected：
- 失败的 agent 行后**不再有 Python traceback**；
- `[ERROR]` 行形如 `… [AgentExecutionError · 将重试 2/5]  (详细堆栈见 …/activity_failures.log)`；
- `workspaces/<ws>/logs/activity_failures.log`（或 workflow.log 同目录）含完整 traceback。

> 若该次跑无失败，可临时把 `CLAUDE_MAX_TURNS` 设极小（如 `CLAUDE_MAX_TURNS=2 uv run …`）人为制造一次 vuln agent 早停失败来观察标签与重定向。

- [ ] **Step 2: Part B 真机——看 💭 Turn 是否倾向中文**

同一份 run 输出里观察 `💭 [Agent] Turn N: …` 是否多为中文；交付物（`*_analysis_deliverable.md`、`comprehensive_security_assessment_report.md` 的正文）是否中文、而 `vulnerability_type`/`## Executive Summary` 等仍英文、`*_queue.json` 的 key 仍英文。

- [ ] **Step 3: 双引擎 Task probe（CLAUDE.md §2）**

Run: `uv run python scripts/validate_glm_task_probe.py`（claude 轨）与 `uv run python scripts/validate_openai_task_probe.py`（openai 轨，若已就绪）
Expected：2/2 子代理委派正常，行为不退化。

- [ ] **Step 4: env 关闭回归**

Run: `SHANNON_AGENT_NARRATION_LANG=en uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat`（可只跑 pre-recon 即 Ctrl-C）
Expected：💭 Turn 回到英文（指令关闭，行为与改动前一致）。

> 冒烟通过后再随 `feat/fork-py` merge。

---

## Self-Review

**1. Spec coverage**
- Part A（堆栈重定向）：Task 4（helper）+ Task 5（装在 WorkflowLogger + detail_path）✓。spec §3.3 的"两个 Worker 构造点 install"**改为**装在 `WorkflowLogger.initialize`（同进程/同 meta 源/`workflow.log` sibling），更稳且单源——满足 spec §3 目标（终端零 traceback + 文件可查 + ERROR 指向）。
- Part B（中文分语境）：Task 6（directive+env+安全锚点）+ Task 7（claude preset/append + openai instructions + prompt 不变锚点 B4）✓。覆盖 spec §4 全部。
- Part C（标签同源）：Task 1（category）+ Task 2（ErrorEvent+renderers）+ Task 3（classify 换源 + attempt/max 透传）✓。覆盖 spec §5 全部。
- 横切（CLAUDE.md 不变量）：Task 6 安全锚点 + Task 7 B4 锚点 + Task 8 冒烟 ✓。
- 测试 A1–A3 / B1–B4 / C1–C4 均有对应 step ✓。

**2. Placeholder scan**：无 TBD/TODO；所有 code step 含完整代码；rendering 逻辑、`_install_failure_redirect`、`narration_directive` 均给出完整实现。部分单测因依赖私有结构给了"推荐抽方法"的稳健替代路线（非占位）。

**3. Type consistency**：`ErrorEvent.attempt/max_attempts/detail_path`（Task 2 定义）→ `workflow_logger.log_error` 填充（Task 3）→ 两 renderer 读取（Task 2）一致；`agent_retry_category`（Task 1）→ `run_agent` 调用（Task 3）一致；`narration_directive`（Task 6）→ 两 provider 调用（Task 7）一致；`install_temporalio_log_redirect`（Task 4）→ `_install_failure_redirect` 调用（Task 5）一致。

**4. 已知脆弱点**（实现时优先用推荐的"抽方法"单测路线）：`workflow_logger.initialize` 与 `openai build_agent` 内部较重，直接单测不稳；Task 5/7 已给出抽 `_install_failure_redirect` / `_instructions()` 的替代。
