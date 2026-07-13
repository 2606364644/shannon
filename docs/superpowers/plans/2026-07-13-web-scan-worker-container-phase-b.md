# WEB 白盒经 worker 容器跑通（C1 Phase B）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 WEB 启动的**白盒扫描**经 Phase A 的 worker 容器完整跑通（web 提交 workflow → worker 容器消费执行 → events.ndjson 进度回显 → heartbeat 判活 → scan_end 完成）。web 容器去掉 fork，改 temporal workflow 提交者。

**Architecture:** web `scan_manager.start` 从 `create_subprocess_exec` 改为 `Client.connect` + `start_workflow`（提交到 Phase A 固定 queue `shannon-py-wb-web`，存 handle 供 cancel）；`_watch` 从 tail 子进程 stdout 改为复用 `EventTailer` tail `events.ndjson` 直到 `scan_end`。`WhiteboxScanWorkflow.run` 加 3 个迁移 activity：前导 `setup_display`（注入 AuditSession，让所有 activity 的 `get_audit_session()` 不崩）、并行 `run_heartbeat`（迁 `HeartbeatManager`，判活 + temporal 原生 cancel）、后置 `finalize_summary`（迁 `log_workflow_complete`，写 `scan_end` 让 `_watch` 停）。`event_file` 从 env 改 `PipelineInput` 字段（env 不跨容器）。**resume 探测迁移留 Phase C**（本 plan 让首次扫描跑通；resume 扫描在 Phase C 补 `run_resume_probe` activity）。

**Tech Stack:** temporalio Python SDK（workflow/activity/Client）/ FastAPI StreamingResponse（SSE，不改）/ uv workspace。

## Global Constraints

- **依赖 Phase A（plan 1）已完成**：worker 容器 + 固定 queue（`WEB_TASK_QUEUE_WHITEBOX`）+ `run_worker` 入口已就绪。
- **AuditSession 进程全局单例约束 → worker 白盒并发=1**：`AuditSession` 是进程全局 `_current`（`session_registry.py:13`），events.ndjson 经它写。worker 容器并发多白盒扫描会冲突 → `run_worker` 白盒 Worker `max_concurrent_workflow_tasks=1`。**解锁并发需把 AuditSession 改 contextvar（更大重构，留 follow-up，不在本 plan）。**
- **event_file 进 PipelineInput 字段**：`wire_web_event_file` 设的 env `SHANNON_WEB_EVENT_FILE` 是进程局部，**web 进程的 env 到不了 worker 容器**。改为 `PipelineInput.event_file` 字段，`WorkflowLogger.initialize` 读 input.event_file（env 兜底 CLI 路径，CLI 零改动）。
- **workflow_id / resume_attempt 由 web 提交端算**：activity 不能改自己的 workflow_id。web `scan_manager.start` 读 `session.json` 的 `resumeAttempts` 算 `resume_attempt` + `workflow_id`，传给 `start_workflow(id=...)`。
- **CLI 零改动**：`shannon_whitebox.worker.run_scan`（self-contained）不改——所有迁移逻辑是**新增 activity 包装共享 helper**（`HeartbeatManager`/`AuditSession`/`SessionManager` 已在 shannon_core），CLI 的 `run_scan` 继续内联用它们。新增 activity 是 worker 容器路径专属。
- **白盒聚焦**：黑盒对称改造留 Phase C。本 plan 只动白盒 + 共享件（PipelineInput/WorkflowLogger）。
- **cancel 协议**：web `cancel` ① 轨（web 自起）从 `proc.send_signal(SIGINT)` 改 `handle.cancel()`（temporal 原生，自动传播到 workflow + 所有 activity 含 heartbeat）；② ③ 轨（heartbeat/cancel.requested 文件协议）不变。
- **`active_pids()` 返空**：C1 后 web 无本机 pid。`active_pids()` 返 `{}`，判活完全靠 `scan_liveness.is_scan_recently_active`（heartbeat mtime）。`orphan_reconciler` 的 `is_scan_recently_active` 门已兜底（不会误杀 worker 还在跑的活 workflow）。
- **TDD + 只跑改动相关测试**：全套 pytest 有预存挂起（memory `feat-fork-py-test-gotchas`）。
- **分支 `feat/fork-py`**。

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | `PipelineInput` 加 `event_file` 字段 | Modify |
| `packages/core/src/shannon_core/audit/workflow_logger.py` | `initialize` 加 `event_file` 参数（env 兜底） | Modify |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 新增 `setup_display` / `run_heartbeat` / `finalize_summary` activity | Modify |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | `WhiteboxScanWorkflow.run` 加前导 setup_display + 并行 heartbeat + 后置 finalize_summary | Modify |
| `packages/worker/src/shannon_worker/runner.py` | 注册 3 个新 activity + 白盒 Worker `max_concurrent_workflow_tasks=1` | Modify |
| `packages/web/src/shannon_web/components/scan_manager.py` | `start` fork→start_workflow；`_watch` tail events.ndjson；`cancel` handle.cancel；`active_pids` 返空；删 `_build_argv` | Modify |
| 测试：`packages/whitebox/tests/test_worker.py`、`packages/web/tests/test_scan_manager.py`、`packages/worker/tests/test_runner.py`、`packages/core/tests/audit/test_workflow_logger.py` | 各 task 单测 | Modify/Create |

---

### Task 1: PipelineInput 加 event_file 字段

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:8-20`（`PipelineInput` dataclass）
- Test: `packages/whitebox/tests/pipeline/test_shared.py`（若无则新建）

**Interfaces:**
- Produces: `PipelineInput.event_file: str | None = None`——Phase B 各处（web 提交端塞、worker activity 读、WorkflowLogger 写 ndjson）共用。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_shared.py
"""PipelineInput.event_file 字段：web 提交端塞路径，worker activity 读它写 events.ndjson。"""
from shannon_whitebox.pipeline.shared import PipelineInput


def test_pipeline_input_has_event_file_field():
    """event_file 字段存在，默认 None（CLI 不显式传时为 None，走 env 兜底）。"""
    inp = PipelineInput(repo_path="/r")
    assert inp.event_file is None


def test_pipeline_input_event_file_round_trips():
    """web 提交端塞 event_file，input 序列化经 temporal 后字段保留。"""
    inp = PipelineInput(repo_path="/r", workspace_name="ws", event_file="/workspaces/ws/events.ndjson")
    assert inp.event_file == "/workspaces/ws/events.ndjson"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_shared.py -v`
Expected: FAIL with `AttributeError: 'PipelineInput' object has no attribute 'event_file'`（或 dataclass 构造 TypeError）

- [ ] **Step 3: Write minimal implementation**

在 `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` 的 `PipelineInput` dataclass 末尾（`enable_llm_track` 后）加字段：

```python
    event_file: str | None = None              # C1: web 提交端塞 events.ndjson 路径(env 不跨容器); CLI 为 None 走 env 兜底
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_shared.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/tests/pipeline/test_shared.py
git commit -m "feat(whitebox): PipelineInput 加 event_file 字段(C1 Phase B)"
```

---

### Task 2: WorkflowLogger.initialize 读 input.event_file（env 兜底）

**Files:**
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py:88-91`（`initialize` 内读 env 处）
- Test: `packages/core/tests/audit/test_workflow_logger_eventfile.py`（新建）

**Interfaces:**
- Consumes: `PipelineInput.event_file`（Task 1）
- Produces: `WorkflowLogger.initialize(event_file: str | None = None)`——`event_file` 非 None 时挂 StructuredEventRenderer 到该路径；None 时回落 env `SHANNON_WEB_EVENT_FILE`（CLI 路径不变）。

> **背景**：当前 `WorkflowLogger.initialize`（workflow_logger.py:88-91）读 `os.environ["SHANNON_WEB_EVENT_FILE"]`。CLI 的 `run_scan` 经 `wire_web_event_file` 设此 env（同进程传递 OK）。C1 web 提交端是**另一进程**（worker 容器），env 到不了 → 必须从 input 拿。CLI 仍用 env（`event_file=None` 走 env 兜底，零改动）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/audit/test_workflow_logger_eventfile.py
"""WorkflowLogger.initialize: event_file 参数优先, None 回落 env(CLI 零改动)."""
import os
from unittest.mock import patch


def test_initialize_uses_explicit_event_file_over_env(monkeypatch, tmp_path):
    """显式 event_file 参数挂 StructuredEventRenderer 到该路径, 不读 env."""
    monkeypatch.setenv("SHANNON_WEB_EVENT_FILE", "/should/not/use")
    from shannon_core.audit.workflow_logger import WorkflowLogger
    logger = WorkflowLogger()
    ef = tmp_path / "events.ndjson"
    logger.initialize(event_file=str(ef))
    # StructuredEventRenderer 挂上了(至少 1 个 renderer, 且路径对)
    assert any(getattr(r, "_path", None) == ef for r in logger._renderers), \
        f"未挂到 {ef}, renderers={logger._renderers}"


def test_initialize_falls_back_to_env_when_event_file_none(monkeypatch, tmp_path):
    """event_file=None 时回落 env(CLI 路径不变)."""
    ef = tmp_path / "from_env.ndjson"
    monkeypatch.setenv("SHANNON_WEB_EVENT_FILE", str(ef))
    from shannon_core.audit.workflow_logger import WorkflowLogger
    logger = WorkflowLogger()
    logger.initialize(event_file=None)
    assert any(getattr(r, "_path", None) == ef for r in logger._renderers)


def test_initialize_no_event_file_no_env_no_renderer(monkeypatch):
    """两边都无 → 不挂 StructuredEventRenderer(纯 CLI rich 路径)."""
    monkeypatch.delenv("SHANNON_WEB_EVENT_FILE", raising=False)
    from shannon_core.audit.workflow_logger import WorkflowLogger
    logger = WorkflowLogger()
    logger.initialize(event_file=None)
    assert not any(type(r).__name__ == "StructuredEventRenderer" for r in logger._renderers)
```

> 注：`StructuredEventRenderer` 的路径属性名以实际为准（`workflow_logger.py:88-91` 现状用 `StructuredEventRenderer(web_event_file)` 构造，renderers 的属性可能叫 `_path` 或 `_file`——实现时按实际属性名调整断言，或断言 `StructuredEventRenderer` 类型存在 + 用 `caplog`/写一行验证落盘）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/audit/test_workflow_logger_eventfile.py -v`
Expected: FAIL（`initialize()` 不接受 `event_file` 参数 → TypeError）

- [ ] **Step 3: Write minimal implementation**

读 `packages/core/src/shannon_core/audit/workflow_logger.py:85-95` 当前 `initialize` 的 env 读法，改为：

```python
    def initialize(self, event_file: str | None = None, *, use_rich: bool = False) -> None:
        # ... 现有 renderer 挂载逻辑 ...
        # C1: event_file 参数优先(来自 PipelineInput.event_file, web 提交端塞);
        # None 时回落 env SHANNON_WEB_EVENT_FILE(CLI run_scan 经 wire_web_event_file 设 env, 零改动)。
        web_event_file = event_file or os.environ.get("SHANNON_WEB_EVENT_FILE")
        if web_event_file:
            from shannon_core.display.structured_event_renderer import StructuredEventRenderer
            self._renderers.append(StructuredEventRenderer(web_event_file))
        # ... 其余 renderer(rich/file)不变 ...
```

> 实现时先读现有 `initialize` 完整体（workflow_logger.py:88 附近），把 `os.environ.get("SHANNON_WEB_EVENT_FILE")` 这一处改成 `event_file or os.environ.get(...)`，其余不动。`AuditSession.initialize` 若调 `WorkflowLogger.initialize`，要透传 event_file——见 Task 3 setup_display activity 怎么把 event_file 灌进去（AuditSession 构造时接收 event_file 并传给 WorkflowLogger）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/audit/test_workflow_logger_eventfile.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/audit/workflow_logger.py packages/core/tests/audit/test_workflow_logger_eventfile.py
git commit -m "feat(audit): WorkflowLogger.initialize 支持 event_file 参数(env 兜底 CLI)"
```

---

### Task 3: 新增 setup_display / run_heartbeat / finalize_summary activity

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（末尾加 3 个 activity）
- Test: `packages/whitebox/tests/pipeline/test_migration_activities.py`（新建）

**Interfaces:**
- Consumes: `HeartbeatManager`（`shannon_core.runtime.heartbeat`）、`AuditSession` + `set_audit_session`/`clear_audit_session`（`shannon_core.audit.session_registry`）、`run_with_display`（`shannon_core.audit.display_lifecycle`，参考其 AuditSession 构造）、`WorkflowSummary`（`shannon_core.models.audit`）、`ActivityInput.event_file`/`workspace_path`（shared.py）。
- Produces:
  - `async def setup_display(input: ActivityInput) -> None`——构造 headless AuditSession（`use_rich=False`，event_file=input.event_file）+ `set_audit_session`。
  - `async def run_heartbeat(input: ActivityInput) -> None`——long-running，`async with HeartbeatManager(ws_dir)`（**on_cancel=None**——C1 取消靠 temporal 原生，不靠文件信号触发 ShutdownController），内部 `await asyncio.Event().wait()` 永阻塞，靠 activity cancel 退出。
  - `async def finalize_summary(input: ActivityInput, summary: dict) -> None`——`await get_audit_session().log_workflow_complete(WorkflowSummary(**summary))` + `clear_audit_session()`。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_migration_activities.py
"""C1 迁移 activity: setup_display 注入 AuditSession, run_heartbeat 长驻写文件, finalize_summary 写 scan_end + 清理."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_setup_display_injects_audit_session(tmp_path):
    """setup_display 构造 AuditSession(event_file=input.event_file)并 set_audit_session."""
    from shannon_whitebox.pipeline.activities import setup_display
    from shannon_whitebox.pipeline.shared import ActivityInput
    from shannon_core.audit import session_registry

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path),
                        event_file=str(tmp_path / "events.ndjson"))
    with patch("shannon_core.audit.session_registry.set_audit_session") as mock_set:
        await setup_display(inp)
        mock_set.assert_called_once()
        session_arg = mock_set.call_args[0][0]
        assert session_arg is not None  # AuditSession 构造了


@pytest.mark.asyncio
async def test_run_heartbeat_writes_heartbeat_file_until_cancelled(tmp_path):
    """run_heartbeat 长驻写 heartbeat 文件; cancel 时干净退出(HeartbeatManager __aexit__)."""
    from shannon_whitebox.pipeline.activities import run_heartbeat
    from shannon_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))
    task = asyncio.create_task(run_heartbeat(inp))
    await asyncio.sleep(0.05)  # 让 heartbeat 初始写
    assert (tmp_path / "heartbeat").exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_finalize_summary_logs_complete_and_clears_session(tmp_path):
    """finalize_summary 调 log_workflow_complete + clear_audit_session."""
    from shannon_whitebox.pipeline.activities import finalize_summary
    from shannon_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))
    mock_session = MagicMock()
    mock_session.log_workflow_complete = AsyncMock()
    summary = {"status": "completed", "total_duration_ms": 100, "total_cost_usd": 0.0,
               "completed_agents": [], "agent_metrics": {}, "error": None}
    with patch("shannon_core.audit.session_registry.get_audit_session", return_value=mock_session), \
         patch("shannon_core.audit.session_registry.clear_audit_session") as mock_clear:
        await finalize_summary(inp, summary)
        mock_session.log_workflow_complete.assert_awaited_once()
        mock_clear.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_migration_activities.py -v`
Expected: FAIL with `ImportError: cannot import name 'setup_display'`

- [ ] **Step 3: Write minimal implementation**

在 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 末尾追加（顶部按需补 import）：

```python
import asyncio  # 若已 import 则跳过
from temporalio import activity

from shannon_core.runtime.heartbeat import HeartbeatManager
from shannon_core.audit.session_registry import (
    set_audit_session, get_audit_session, clear_audit_session,
)
from shannon_core.models.audit import WorkflowSummary


@activity.defn
async def setup_display(input: ActivityInput) -> None:
    """C1 前导 activity: 构造 headless AuditSession(event_file 来自 input) + set_audit_session。

    worker 容器无 TTY, 用 use_rich=False(StructuredEventRenderer 自动检测非 TTY)。
    AuditSession 构造逻辑复用 run_with_display(display_lifecycle.py), 但这里只取 headless 分支:
    构造 AuditSession + initialize(event_file=input.event_file)。
    """
    from shannon_core.audit.session import AuditSession  # 按实际 AuditSession 构造路径调整
    session = AuditSession()
    # initialize 接收 event_file(Task 2 改造), 挂 StructuredEventRenderer 到 input.event_file
    session.initialize(event_file=input.event_file, use_rich=False)
    set_audit_session(session)


@activity.defn
async def run_heartbeat(input: ActivityInput) -> None:
    """C1 并行 long-running activity: 周期写 heartbeat(web 据其 mtime 判活)。

    on_cancel=None: C1 取消靠 temporal 原生 handle.cancel() 传播到 workflow + activity,
    不再用 cancel.requested 文件触发 ShutdownController(web 端 cancel 兜底用文件 + temporal 双轨)。
    永阻塞(asyncio.Event().wait()), 靠 activity cancel(CancelledError)退出 → __aexit__ 清理。
    """
    from pathlib import Path
    ws_dir = Path(input.workspace_path) if input.workspace_path else Path(input.repo_path)
    mgr = HeartbeatManager(ws_dir, on_cancel=None)
    async with mgr:
        await asyncio.Event().wait()  # 永不 set; activity cancel 时 CancelledError 传出


@activity.defn
async def finalize_summary(input: ActivityInput, summary: dict) -> None:
    """C1 后置 activity: log_workflow_complete(触发 StructuredEventRenderer 写 scan_end) + 清 AuditSession。

    summary 由 workflow 从 self._state 构建(等价 run_scan worker.py:312-328 的逻辑, 移进 workflow)。
    """
    session = get_audit_session()
    if session is not None:
        ws = WorkflowSummary(
            status=summary.get("status", "failed"),
            total_duration_ms=summary.get("total_duration_ms", 0),
            total_cost_usd=summary.get("total_cost_usd", 0.0),
            completed_agents=summary.get("completed_agents", []),
            agent_metrics=summary.get("agent_metrics", {}),
            error=summary.get("error"),
        )
        await session.log_workflow_complete(ws)
    clear_audit_session()
```

> **实现注意**：
> - `AuditSession` 的实际构造路径以 `display_lifecycle.py` 现状为准（`run_with_display` 内怎么 new AuditSession + initialize）。本 activity 只复用其 headless 分支。若 `AuditSession.initialize` 签名与 Task 2 改的 `WorkflowLogger.initialize(event_file=...)` 不直接对齐，调整 `AuditSession.initialize` 透传 event_file 到 WorkflowLogger。
> - `run_heartbeat` 的 `workspace_path` 来自 `ActivityInput`（workflows.py:58-72 已构造 `workspace_path`），用它定位 ws_dir。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_migration_activities.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_migration_activities.py
git commit -m "feat(whitebox): setup_display/run_heartbeat/finalize_summary 迁移 activity(C1 Phase B)"
```

---

### Task 4: WhiteboxScanWorkflow.run 接入 3 个 activity

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:37-83`（`run` 开头）+ `:561-577`（结束处 status 计算 + return）
- Test: `packages/whitebox/tests/pipeline/test_workflow_migration.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 3 个 activity。
- Produces: `WhiteboxScanWorkflow.run` 开头 `setup_display` + 起 `run_heartbeat` 并行 future → 主体流程（现有）→ 结束 `finalize_summary`（带 status summary）→ cancel heartbeat。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/pipeline/test_workflow_migration.py
"""WhiteboxScanWorkflow.run: 前导 setup_display + 并行 run_heartbeat + 后置 finalize_summary."""
from unittest.mock import AsyncMock, patch
from datetime import timedelta


def _make_input(ws="ws-test", **kw):
    from shannon_whitebox.pipeline.shared import PipelineInput
    return PipelineInput(repo_path="/r", workspace_name=ws, event_file=f"/workspaces/{ws}/events.ndjson", **kw)


@pytest.mark.asyncio
async def test_run_invokes_setup_display_then_heartbeat_then_finalize(tmp_path):
    """workflow.run 调 setup_display(首) -> run_heartbeat(并行) -> ... 主体 ... -> finalize_summary(尾)."""
    from temporalio import workflow
    from temporalio.testing import WorkflowEnvironment
    from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow

    # 用 WorkflowEnvironment + mock activities(跳过真实 activity 执行, 只验调度顺序)
    order: list[str] = []

    @activity.defn
    async def setup_display(i): order.append("setup_display")
    @activity.defn
    async def run_heartbeat(i): order.append("heartbeat"); await asyncio.sleep(99)
    @activity.defn
    async def finalize_summary(i, s): order.append("finalize")
    # ... 其余 activity mock 成 no-op ...

    async with await WorkflowEnvironment.start_local() as env:
        # 注册 mock activities 到 worker, 跑 workflow
        ...
        # 断言 order 以 setup_display 开头, 含 finalize
        assert order[0] == "setup_display"
        assert order[-1] == "finalize"
```

> **实现注意**：temporal workflow 测试用 `WorkflowEnvironment.start_local`（temporalio.testing）。完整 mock 全部 ~25 activity 较繁——替代方案：用 `workflow.execute_activity` 的 mock（patch `activities.setup_display` 等），或只断言 workflow.run 里调了 `workflow.execute_activity(activities.setup_display, ...)` / `run_heartbeat` / `finalize_summary`（通过 spy patch 验证调用）。优先用 spy patch（更简单）：patch `workflows.activities.setup_display` 等，断言被 await。具体写法以现有 `test_worker.py` 的 mock 风格为准。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflow_migration.py -v`
Expected: FAIL（workflow.run 未调 setup_display/finalize_summary）

- [ ] **Step 3: Write minimal implementation**

改 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`：

**(a)** `run` 方法开头（现 `:38-42`，`self._state.completed_agents` 预填之后、`start_time` 之前）插入 setup_display + 起 heartbeat：

```python
    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        if input.resume_completed_agents:
            self._state.completed_agents = list(input.resume_completed_agents)
        self._state.start_time = workflow.time_ns() / 1e9

        # C1 Phase B: 前导 setup_display(注入 AuditSession, event_file 来自 input)
        act_input = ActivityInput(  # 原 workflows.py:63-73 的 act_input 构造上移到这里
            repo_path=input.repo_path, web_url=input.web_url, config_path=input.config_path,
            workspace_name=input.workspace_name, deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode, api_key=input.api_key,
            prompt_override=input.prompt_override,
            workspace_path=(str(Path(input.repo_path).parent / "workspaces" / input.workspace_name)
                            if input.workspace_name else input.repo_path),
        )
        await workflow.execute_activity(
            activities.setup_display, act_input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry_for("standard"),
        )
        # C1 Phase B: 并行 run_heartbeat(随 workflow 生命周期, cancel 时退出)
        heartbeat_handle = workflow.execute_activity(
            activities.run_heartbeat, act_input,
            start_to_close_timeout=timedelta(hours=24),  # 须 > 扫描时长
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        # ... 原 act_input 构造(workflows.py:63-73)删除(已上移), 后续全用此 act_input ...
```

> 顶部 import 补 `from temporalio.common import RetryPolicy`（若未 import）。

**(b)** `run` 方法结尾（现 `:561-570`，status 计算后、`return self._state` 前）插入 finalize_summary + 取消 heartbeat：

```python
            # 原 status 计算(561-568)不动
            if self._state.failed_agents:
                self._state.status = "failed"
                ...
            else:
                self._state.status = "completed"

            # C1 Phase B: 后置 finalize_summary(写 scan_end + 清 AuditSession)
            import time as _time
            summary = {
                "status": self._state.status,
                "total_duration_ms": int((workflow.time_ns() / 1e9 - self._state.start_time) * 1000),
                "total_cost_usd": sum((m.get("cost_usd") or 0.0) for m in self._state.agent_metrics.values()),
                "completed_agents": list(self._state.completed_agents),
                "agent_metrics": {
                    name: {"duration_ms": int(m.get("duration_ms", 0) or 0), "cost_usd": m.get("cost_usd")}
                    for name, m in self._state.agent_metrics.items()
                },
                "error": (self._state.errors[0] if self._state.errors else None),
            }
            try:
                heartbeat_handle.cancel()  # workflow 结束, 停 heartbeat
            except Exception:
                pass
            await workflow.execute_activity(
                activities.finalize_summary, args=[act_input, summary],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("standard"),
            )
            self._state.current_phase = None
            return self._state
```

**(c)** `except CancelledError` 分支（现 `:571-574`）也要清理 heartbeat + 视情况 finalize（cancelled 状态）：

```python
        except CancelledError:
            self._state.status = "cancelled"
            try:
                heartbeat_handle.cancel()
            except Exception:
                pass
            self._state.current_phase = None
            return self._state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_workflow_migration.py -v`
Expected: PASS（setup_display 首调、finalize_summary 尾调）

- [ ] **Step 5: 回归现有 workflow 测试 + CLI run_scan 路径**

Run: `uv run pytest packages/whitebox/tests/test_worker.py -v`
Expected: 现有 run_scan 测试仍 PASS（run_scan 不调 setup_display/finalize_summary——那些是 workflow 内的，run_scan 的 await_workflow 走 workflow.run 自动执行）。

> **关键回归点**：CLI 的 `run_scan`（worker.py）自己也 `set_audit_session`（worker.py:291）+ `log_workflow_complete`（worker.py:328）。workflow.run 现在也调 setup_display/finalize_summary。**CLI 路径会双重调用**（run_scan 外层 + workflow 内层）。需确认这不冲突——`set_audit_session` 是覆盖（_current = 新 session），`log_workflow_complete` 重复调用是否幂等（写两次 scan_end？）。**若冲突**：CLI run_scan 的 set_audit_session/log_workflow_complete 改为条件跳过（workflow 内已做），或 workflow 内的 setup_display/finalize_summary 检测是否已被外层设置。实现时跑 CLI run_scan 测试验证，若双重 scan_end 则在 finalize_summary 加守卫（已写过则跳过）。

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/pipeline/test_workflow_migration.py
git commit -m "feat(whitebox): workflow.run 接入 setup_display/heartbeat/finalize_summary(C1 Phase B)"
```

---

### Task 5: run_worker 注册新 activity + 白盒并发=1

**Files:**
- Modify: `packages/worker/src/shannon_worker/runner.py`（白盒 Worker activities 列表 + `max_concurrent_workflow_tasks=1`）
- Test: `packages/worker/tests/test_runner.py`（扩展）

**Interfaces:**
- Consumes: Task 3 的 3 个 activity + AuditSession 并发约束。
- Produces: `run_worker` 白盒 Worker 注册 3 个新 activity + `max_concurrent_workflow_tasks=1`。

- [ ] **Step 1: Write the failing test**（扩展 test_runner.py）

```python
@pytest.mark.asyncio
async def test_run_worker_whitebox_concurrency_one_and_migration_activities():
    """白盒 Worker: max_concurrent_workflow_tasks=1(AuditSession 单例约束) + 注册迁移 activity."""
    from shannon_worker.runner import run_worker

    mock_client = AsyncMock()
    wb_worker = MagicMock(); wb_worker.run = AsyncMock()
    bb_worker = MagicMock(); bb_worker.run = AsyncMock()

    with patch("shannon_worker.runner.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_worker.runner.Worker", side_effect=[wb_worker, bb_worker]) as mw:
        await run_worker("temporal:7233")

    wb_call = mw.call_args_list[0]
    assert wb_call.kwargs["max_concurrent_workflow_tasks"] == 1  # AuditSession 约束
    activity_names = {getattr(a, "__name__", a) for a in wb_call.kwargs["activities"]}
    assert "setup_display" in activity_names
    assert "run_heartbeat" in activity_names
    assert "finalize_summary" in activity_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/worker/tests/test_runner.py::test_run_worker_whitebox_concurrency_one_and_migration_activities -v`
Expected: FAIL（白盒 Worker 无 max_concurrent_workflow_tasks / 未注册新 activity）

- [ ] **Step 3: Write minimal implementation**

改 `packages/worker/src/shannon_worker/runner.py`：白盒 activities 列表加 3 个新 activity，白盒 Worker 加 `max_concurrent_workflow_tasks=1`：

```python
# import 补（顶部）:
from shannon_whitebox.pipeline.activities import (
    # ... 现有 25 个 ...
    setup_display, run_heartbeat, finalize_summary,  # 新增
)

# 白盒 Worker 构造改为:
wb_worker = Worker(
    client=client,
    task_queue=WEB_TASK_QUEUE_WHITEBOX,
    workflows=[WhiteboxScanWorkflow],
    activities=[
        render_findings, assemble_report, run_agent,
        # ... 现有 25 个 ...
        log_info_activity,
        setup_display, run_heartbeat, finalize_summary,  # 新增
    ],
    max_concurrent_workflow_tasks=1,  # AuditSession 进程全局单例约束(解锁需 contextvar 重构, 留 follow-up)
    graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
)
```

> 黑盒 Worker **暂不加** max_concurrent（黑盒 Phase C 才接 AuditSession 迁移；当前黑盒 workflow 不调 setup_display）。但若黑盒也跑 AuditSession，同样约束——Phase C 处理。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/worker/tests/test_runner.py -v`
Expected: 全 PASS（含新测试 + Phase A 原有 2 个）

- [ ] **Step 5: Commit**

```bash
git add packages/worker/src/shannon_worker/runner.py packages/worker/tests/test_runner.py
git commit -m "feat(worker): 白盒注册迁移 activity + 并发=1(AuditSession 约束)"
```

---

### Task 6: scan_manager.start 改 fork → start_workflow

**Files:**
- Modify: `packages/web/src/shannon_web/components/scan_manager.py:63-189`（`start` + `_build_argv` + `_procs`）
- Test: `packages/web/tests/test_scan_manager.py`（重写 mock：subprocess → temporal Client）

**Interfaces:**
- Consumes: Phase A `WEB_TASK_QUEUE_WHITEBOX`、Task 1 `PipelineInput.event_file`、`temporalio.client.Client`。
- Produces: `ScanManager.start` 提交 workflow 到固定 queue，存 `handle`（替代 `_procs`）；`_procs: dict[str, asyncio.Process]` → `_handles: dict[str, WorkflowHandle]`。

- [ ] **Step 1: Write the failing test**（重写 test_scan_manager.py 的 start 测试）

```python
@pytest.mark.asyncio
async def test_start_submits_workflow_to_fixed_queue(tmp_path, monkeypatch):
    """start 改 submit_workflow: 连 temporal + start_workflow 到 WEB_TASK_QUEUE_WHITEBOX + 存 handle."""
    from shannon_web.components.scan_manager import ScanManager
    from shannon_core.services.temporal_infra import WEB_TASK_QUEUE_WHITEBOX

    mock_handle = MagicMock()
    mock_handle.id = "ws-123"
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)
    with patch("shannon_web.components.scan_manager.Client.connect",
               AsyncMock(return_value=mock_client)):
        mgr = ScanManager(tmp_path, tmp_path, None, max_concurrent=1)
        # 构造一个 whitebox ScanRequest(mock)
        req = make_whitebox_request(repo="hr", url="http://t")
        ws = await mgr.start(req)

    mock_client.start_workflow.assert_awaited_once()
    call = mock_client.start_workflow.call_args
    assert call.kwargs["task_queue"] == WEB_TASK_QUEUE_WHITEBOX  # 固定 queue
    assert call.kwargs["id"]  # workflow_id
    # input 带 event_file
    wf_input = call.args[1]
    assert wf_input.event_file.endswith("events.ndjson")
    # handle 存进 _handles(供 cancel)
    assert ws in mgr._handles
```

> `make_whitebox_request` 以现有 test_scan_manager.py 的 fixture 工厂为准。其余 start 测试（max_concurrent / active_repo_sources）相应调整 mock。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/web/tests/test_scan_manager.py::test_start_submits_workflow_to_fixed_queue -v`
Expected: FAIL（start 仍用 subprocess，无 Client.connect）

- [ ] **Step 3: Write minimal implementation**

改 `packages/web/src/shannon_web/components/scan_manager.py`：

**(a)** 顶部 import 改：

```python
# 删: 无需 asyncio.subprocess(不再 fork)
# 加:
from temporalio.client import Client
from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from shannon_whitebox.pipeline.shared import PipelineInput
from shannon_core.services.temporal_infra import WEB_TASK_QUEUE_WHITEBOX, WEB_TASK_QUEUE_BLACKBOX
from shannon_core.session import SessionManager
```

**(b)** `__init__`：`self._procs` → `self._handles: dict[str, Any] = {}`（保留 `_tasks` / `_active_reqs`）。

**(c)** `active_pids()` 改返空（C1 无本机 pid，判活靠 heartbeat mtime）：

```python
    def active_pids(self) -> dict[str, int]:
        return {}  # C1: 无本机 pid; 判活靠 is_scan_recently_active(heartbeat mtime)
```

**(d)** `start()` 重写（替换现 `:63-109`）：

```python
    async def start(self, req: ScanRequest) -> str:
        await self._check_temporal()
        if len(self._handles) >= self._max_concurrent:
            raise TooManyScans(self._max_concurrent)

        if req.type == "correlation":
            target, yaml_path = await self._resolve_inputs(req)
            ws = self._resolve_out_workspace(yaml_path)
        else:
            target, yaml_path = await self._resolve_inputs(req)
            ws = req.workspace or self._gen_ws_name(req)

        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)
        # C1: web 端做 session 创建 + event_file wiring + owner(原 run_scan:121-132 迁来)
        SessionManager(self._workspaces_dir).create_workspace(
            web_url=req.url or "", repo_path=target or "", name=ws, scan_type=req.type)
        self._mark_owner(ws_dir, "web")
        event_file = ws_dir / "events.ndjson"
        self._active_reqs[ws] = req

        try:
            if req.type == "whitebox":
                handle = await self._submit_whitebox(target, ws, event_file, req)
            elif req.type == "blackbox":
                handle = await self._submit_blackbox(target, ws, event_file, req)  # Phase C 完善, 本 task 可先 raise NotImplementedError
            else:
                raise ValueError(f"correlation 暂未 C1 化: {req.type}")
        except BaseException:
            self._active_reqs.pop(ws, None)
            raise
        self._handles[ws] = handle
        self._tasks[ws] = asyncio.create_task(self._watch(ws, event_file))
        return ws

    async def _submit_whitebox(self, target, ws, event_file, req) -> Any:
        """算 workflow_id(读 resumeAttempts) + Client.connect + start_workflow 到固定 queue."""
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws)
        inp = PipelineInput(
            repo_path=target or "", web_url=req.url or "", workspace_name=ws,
            event_file=str(event_file),
        )
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_WHITEBOX,
        )
        return handle

    def _resolve_workflow_id(self, ws: str) -> str:
        """读 session.json 的 top-level resumeAttempts, 算 -resume-N 后缀(workflow_id 在提交时定, activity 不能改)."""
        session_file = self._workspaces_dir / ws / "session.json"
        n = 0
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text("utf-8"))
                attempts = data.get("resumeAttempts") or []
                if isinstance(attempts, list):
                    n = len(attempts)
            except (json.JSONDecodeError, OSError):
                n = 0
        return f"{ws}-resume-{n}" if n > 0 else ws
```

> `_resolve_workflow_id` 复用 `worker.py:90-103 resolve_workflow_id` 的语义（resume_attempt>0 加 -resume-N），但 web 端读 session.json 算 n（run_scan 那套读法，worker.py:209-220）。

**(e)** 删 `_build_argv`（`:177-189`，不再构造 CLI argv）。`_temporal_address` 保留（Client.connect 用）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: start 测试 PASS（其余 cancel/_watch 测试在 Task 7/8 调整）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/shannon_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): scan_manager.start fork→start_workflow(C1 Phase B 白盒)"
```

---

### Task 7: scan_manager._watch tail events.ndjson + 兜底 scan_end

**Files:**
- Modify: `packages/web/src/shannon_web/components/scan_manager.py:277-338`（`_watch` + `_has_scan_end` + `_write_scan_end`）
- Test: `packages/web/tests/test_scan_manager.py`（_watch 测试）

**Interfaces:**
- Consumes: `EventTailer`（`shannon_web.components.event_tailer`，现成 tail 工具）、`_has_scan_end`（保留）。
- Produces: `_watch` 不再 drain 子进程 stdout；改为 tail events.ndjson 直到 `scan_end`（或超时），保留兜底 scan_end + finally 清理 `_handles`/`_tasks`/`_active_reqs`。

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_watch_tails_events_until_scan_end(tmp_path):
    """_watch tail events.ndjson, 见 scan_end 后退出 + 清理 _handles/_active_reqs."""
    from shannon_web.components.scan_manager import ScanManager
    ws_dir = tmp_path / "ws"; ws_dir.mkdir()
    event_file = ws_dir / "events.ndjson"
    mgr = ScanManager(tmp_path, tmp_path, None)
    mgr._handles["ws"] = MagicMock()  # 假 handle
    mgr._active_reqs["ws"] = make_whitebox_request()

    # 异步写 scan_end
    async def write_end():
        await asyncio.sleep(0.05)
        event_file.write_text('{"type":"scan_end","status":"completed"}\n')
    asyncio.create_task(write_end())

    await mgr._watch("ws", event_file)

    assert "ws" not in mgr._handles  # finally 清理
    assert "ws" not in mgr._active_reqs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/web/tests/test_scan_manager.py::test_watch_tails_events_until_scan_end -v`
Expected: FAIL（_watch 仍 drain proc.stdout，proc 参数缺失）

- [ ] **Step 3: Write minimal implementation**

改 `_watch`（替换现 `:277-318`）：

```python
    async def _watch(self, ws: str, event_file: Path) -> None:
        """C1: tail events.ndjson 直到 scan_end(worker finalize_summary 写)或超时。

        无子进程 stdout 可 drain; scan_end 由 worker 容器 StructuredEventRenderer 在
        SummaryEvent 时写(strucured_event_renderer.py:50-57)。_watch 见 scan_end 即收尾;
        超时(scan_timeout)或文件长期无 scan_end 则兜底写一条 + 标 killed/crashed。
        """
        from shannon_web.components.event_tailer import EventTailer

        try:
            deadline = (time.monotonic() + self._scan_timeout) if self._scan_timeout > 0 else None
            while not self._has_scan_end(event_file):
                if deadline is not None and time.monotonic() > deadline:
                    # 超时: 兜底 scan_end(worker 仍可继续, 但 web 侧判超时收尾)
                    if not self._has_scan_end(event_file):
                        await self._write_scan_end(event_file, "timeout", -1, "web 超时收尾")
                    break
                await asyncio.sleep(0.5)
        finally:
            # 兜底: 若 worker 未写 scan_end(异常/crash), 补一条
            if not self._has_scan_end(event_file):
                await self._write_scan_end(event_file, "crashed", -1, "worker 未写 scan_end")
            self._handles.pop(ws, None)
            self._tasks.pop(ws, None)
            self._active_reqs.pop(ws, None)
```

> `_has_scan_end` / `_write_scan_end`（现 `:319-338`）保留不动（它们读/写 event_file，与子进程无关）。删原 `_watch` 里的 `stderr_tail`/`drain`/`proc.wait`（无子进程）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: _watch 测试 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/shannon_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): _watch tail events.ndjson 直到 scan_end(C1 Phase B)"
```

---

### Task 8: scan_manager.cancel 改 handle.cancel + active_pids 兜底确认

**Files:**
- Modify: `packages/web/src/shannon_web/components/scan_manager.py:111-137`（`cancel`）
- Test: `packages/web/tests/test_scan_manager.py`（cancel 测试）

**Interfaces:**
- Produces: `cancel` ① 轨（web 自起）从 `proc.send_signal(SIGINT)` 改 `handle.cancel()`（temporal 原生，传播到 workflow + heartbeat activity）；② ③ 轨（heartbeat/cancel.requested 文件）不变。

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_cancel_web_started_scan_calls_handle_cancel(tmp_path):
    """web 自起的 scan: cancel 调 handle.cancel()(temporal 原生), 不再 SIGINT 子进程."""
    from shannon_web.components.scan_manager import ScanManager
    mgr = ScanManager(tmp_path, tmp_path, None)
    mock_handle = AsyncMock()
    mgr._handles["ws"] = mock_handle
    (tmp_path / "ws").mkdir()

    result = await mgr.cancel("ws")

    mock_handle.cancel.assert_awaited_once()  # temporal 原生取消
    assert result == {"cancelled": "ws"}


@pytest.mark.asyncio
async def test_cancel_host_started_scan_writes_cancel_requested(tmp_path):
    """host 起(或 worker 在跑)的 scan: heartbeat fresh → 写 cancel.requested(协作式兜底)."""
    from shannon_web.components.scan_manager import ScanManager
    from shannon_core.runtime.heartbeat import HeartbeatManager
    ws_dir = tmp_path / "ws"; ws_dir.mkdir()
    mgr = ScanManager(tmp_path, tmp_path, None)
    # 造 fresh heartbeat
    async with HeartbeatManager(ws_dir):
        pass
    result = await mgr.cancel("ws")
    assert (ws_dir / "cancel.requested").exists()
    assert result == {"cancelled": "ws", "via": "signal"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/web/tests/test_scan_manager.py::test_cancel_web_started_scan_calls_handle_cancel -v`
Expected: FAIL（cancel 仍走 proc.send_signal）

- [ ] **Step 3: Write minimal implementation**

改 `cancel`（替换现 `:111-137`）：

```python
    async def cancel(self, ws: str) -> dict | None:
        """取消 scan 三轨(C1 后):
        ① _handles 有(web 自起) → handle.cancel()(temporal 原生, 传播到 workflow + heartbeat activity).
        ② heartbeat fresh(worker 在跑) → 写 cancel.requested(协作式兜底, worker heartbeat _cancel_loop 检测).
        ③ heartbeat stale(已死) → 标 cancelled + was_dead.
        """
        ws_dir = self._workspaces_dir / ws
        if not ws_dir.exists():
            return None
        # ① web 自起: handle.cancel
        handle = self._handles.get(ws)
        if handle is not None:
            try:
                await handle.cancel()
            except Exception:
                pass  # best-effort; temporal 侧 workflow cancel
            return {"cancelled": ws}
        # ②/③ owner=host 或已死
        if is_scan_recently_active(ws_dir):
            (ws_dir / "cancel.requested").write_text("", encoding="utf-8")
            await self._mark_cancelled(ws_dir)
            return {"cancelled": ws, "via": "signal"}
        await self._mark_cancelled(ws_dir)
        return {"cancelled": ws, "was_dead": True}
```

> **注意**：C1 后 ① 轨（handle.cancel）与 ② 轨（cancel.requested）可**双轨并发**——web 调 handle.cancel（temporal 原生主路径）+ 写 cancel.requested（worker heartbeat _cancel_loop 兜底，防 temporal cancel 网络丢）。但 C1 的 `run_heartbeat` activity 的 `on_cancel=None`（Task 3）——即 worker 侧 heartbeat **不再监听 cancel.requested**（取消全靠 temporal）。所以 ② 轨的 cancel.requested 文件在 C1 后**对 worker 无效**（worker 不读它）。保留 ② 轨仅为兼容 host CLI 起的 scan（CLI 的 HeartbeatManager 仍监听 cancel.requested）。若 C1 后所有 scan 都走 worker，② 轨可简化为纯 ③ 轨（标 cancelled）。本 task 保留三轨兼容，Phase C 评估是否简化。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: cancel 测试 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/shannon_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): cancel 改 handle.cancel(temporal 原生, C1 Phase B)"
```

---

### Task 9: 集成验证（web 提交 + worker 消费 + 白盒首次扫描跑通）

**Files:**
- 无新建文件（手动集成验证）

**Interfaces:**
- 验证 Phase A + Phase B 端到端：web 扫一个真实 repo → worker 容器消费 → events.ndjson 有进度 → SSE 前端可见 → heartbeat 判活 → scan_end 完成。

- [ ] **Step 1: 起全栈**

```bash
docker compose up -d --build temporal web worker
docker compose logs -f worker --tail 20  # 确认 worker 连 temporal + 注册白盒 queue(shannon-py-wb-web)poller
```

- [ ] **Step 2: WEB 发起一个白盒扫描**

浏览器开 `http://localhost:7878`，选一个 repo（如 hr），发起白盒扫描。

- [ ] **Step 3: 验证链路**

| 检查 | 命令 / 路径 | 期望 |
|------|------|------|
| temporal 收到 workflow | `docker compose exec temporal temporal workflow list --address localhost:7233` | 白盒 workflow RUNNING |
| worker 消费执行 | `docker compose logs worker --tail 50` | setup_display / preflight / code_index 等 activity 日志 |
| events.ndjson 有进度 | `tail -f workspaces/<ws>/events.ndjson` | phase_start / agent 事件 |
| SSE 前端可见 | 浏览器扫描详情页 | 实时进度更新 |
| heartbeat 判活 | `ls -la workspaces/<ws>/heartbeat` + mtime | 30s 内更新 |
| 扫描完成 | events.ndjson 末尾 | `scan_end` + `status: completed` |
| session.json 状态 | `cat workspaces/<ws>/session.json` | status: completed |

- [ ] **Step 4: 若失败，定位**

- workflow 卡 RUNNING 无 activity 日志 → worker 没注册到 `shannon-py-wb-web` queue（temporal task-queue describe 看 poller）。
- activity 报 `get_audit_session() returned None` → setup_display 未注入 / workflow 未先调 setup_display。
- events.ndjson 空 → event_file 路径不对（PipelineInput.event_file vs worker workspace_path 对齐）。
- 无 scan_end → finalize_summary 未调 / AuditSession 未挂 StructuredEventRenderer。
- heartbeat 不更新 → run_heartbeat activity 未起 / workspace_path 错。

- [ ] **Step 5: 无代码改动则跳过 commit；有 fix 则 commit**

---

### Task 10: CLI 回归（run_scan 零改动）

**Files:**
- 无新建文件（CLI 回归验证）

**Interfaces:**
- 验证 CLI 路径（`shannon-whitebox start`，self-contained run_scan）未被 Phase B 破坏。

- [ ] **Step 1: CLI 跑一个白盒扫描**

```bash
# 宿主用 shannon-user(或 root)直跑 CLI
uv run shannon-whitebox start -r repos/hr --url http://t -w cli-regression-test
```

- [ ] **Step 2: 验证 CLI 路径完整**

| 检查 | 期望 |
|------|------|
| 扫描正常跑完 | status: completed，无报错 |
| events.ndjson（CLI 默认落 workspace） | 有进度 + 单条 scan_end |
| 无双重 scan_end | Task 4 Step 5 的双重调用风险点——若 CLI run_scan 的 log_workflow_complete + workflow 内 finalize_summary 都写 scan_end，events.ndjson 会有两条 scan_end。**若发现**：在 finalize_summary 加守卫（检测已写过则跳过），或 CLI run_scan 去掉自己的 log_workflow_complete（依赖 workflow 内的）。 |
| heartbeat 正常 | workspaces/cli-regression-test/heartbeat 存在（CLI 的 HeartbeatManager 仍工作）|

- [ ] **Step 3: 若 CLI 回归失败（双重 scan_end / AuditSession 冲突），fix**

优先方案：finalize_summary activity 内加守卫——若 `get_audit_session()` 已被外层（CLI run_scan）设置且 session 已 log_workflow_complete 过，则跳过。或更简单：finalize_summary 检测 events.ndjson 末尾是否已有 scan_end，有则跳过。

- [ ] **Step 4: Commit any fix**

```bash
git add -p  # 选相关 fix
git commit -m "fix(whitebox): CLI 路径与 workflow 迁移 activity 的双重 scan_end 守卫"
```

---

## Self-Review

**1. Spec coverage（Phase B 覆盖的 spec 部分）:**
- spec §5.3 scan_manager fork→start_workflow → Task 6 ✓
- spec §5.4 run_scan 拆分（session/event_file/owner → web；heartbeat/summary → workflow activity）→ Task 3/4/6 ✓（resume 探测留 Phase C）
- spec §5.6 event_file 进 PipelineInput → Task 1/2 ✓
- spec §10 cancel handle.terminate + heartbeat mtime → Task 8 ✓（cancel 用 handle.cancel；terminate 兜底留 Phase C）
- spec §10 active_pids 无 pid → Task 6 active_pids 返空 ✓
- spec §12-7 run_scan 拆分回归（CLI）→ Task 10 ✓
- spec §14-5 最高风险（AuditSession）→ Task 3/4/5 + Global Constraints 并发=1 ✓

**Phase B 不覆盖（留 Phase C）:**
- resume 探测 → `run_resume_probe` activity（迁 `WhiteboxResumeStateBuilder.build` + cleanup）
- 黑盒对称（`BlackboxScanWorkflow` + blackbox scan_manager 路径）
- AuditSession contextvar 重构（解锁并发，follow-up）
- multi（correlation）C1 化

**2. Placeholder scan:** Task 3 setup_display 的 AuditSession 构造以 display_lifecycle.py 现状为准（标注实现时对齐）——这是"参考现有模式"，非 placeholder（步骤具体：构造 AuditSession + initialize(event_file) + set_audit_session）。Task 4 Step 5 / Task 10 的双重 scan_end 风险有明确处理方案（守卫）。Task 9/10 是集成验证（命令 + 期望表齐全）。

**3. Type consistency:** `PipelineInput.event_file`（Task 1）→ WorkflowLogger.initialize(event_file)（Task 2）→ setup_display activity 传 input.event_file（Task 3）→ workflow.run 调 setup_display（Task 4）。`_handles`（Task 6）→ cancel handle.cancel（Task 8）→ _watch finally pop（Task 7）。命名一致。

**4. 已知风险（实现时重点验）:**
- **R1 双重 AuditSession/scan_end**（Task 4 Step 5 / Task 10）：CLI run_scan 外层 + workflow 内层都 set_audit_session / log_workflow_complete。实现时跑 CLI 回归，若冲突加守卫。
- **R2 setup_display 与 workflow determinism**：setup_display 是 activity（非 workflow 内联），不破坏 determinism。但 workflow.run 调它的位置要在所有 `get_audit_session()` 调用前（即 workflow 第一个 activity 之前）——Task 4 把 setup_display 放 workflow.run 最前。
- **R3 finalize_summary 在 CancelledError 分支**：cancelled 时也要写 scan_end（否则 _watch 永等）——Task 4 (c) CancelledError 分支需补 finalize 或 _watch 兜底（Task 7 _watch finally 已兜底写 crashed scan_end）。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-13-web-scan-worker-container-phase-b.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每 task 派新 subagent + task 间 review。
**2. Inline Execution** - executing-plans 批量执行 + checkpoint。

> **Phase B 是 spec §14-5 最高风险段**（AuditSession 并发=1 约束 + 双重调用风险 R1 + workflow determinism R2）。建议 Task 3/4（迁移 activity + workflow 接入）用 subagent-driven + 每 task 详 review，Task 9/10（集成 + CLI 回归）真机必跑。
>
> Phase B 完成后，WEB 白盒首次扫描经 worker 跑通。Phase C 待写：resume 迁移 + 黑盒对称 + AuditSession contextvar（解锁并发）。
