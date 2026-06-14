# Graceful Shutdown 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 whitebox / blackbox / combined 三个扫描入口在 Ctrl+C 时优雅退出——全链路取消（本地清理 + Temporal `handle.cancel()`）、双击语义（第一次优雅、第二次强制）、退出码 130，且**不删 deliverables / workflow.log**。

**Architecture:** 在 `shannon_core.runtime.scan_runner` 新增 `ShutdownController`（双击 SIGINT / SIGTERM 信号处理）与 `run_scan_graceful`（统一封装 connect / worker / start / result / cancel）。三个 worker 的 `run_scan` 改为调用它，捕获 `ScanCancelled` 返回 cancelled 状态；CLI 加 cancelled 分支 → `SystemExit(130)`；combined orchestrator 识别 cancelled 短路。子进程与临时注入配置的清理依赖各 workflow **已有**的 `except CancelledError` + `finally`，由 `handle.cancel()` 触发——scan_runner 不直接管这些，也不删任何结果/日志文件。

**Tech Stack:** Python 3、asyncio、temporalio（Python SDK）、click、pytest + pytest-asyncio（`asyncio_mode = "auto"`）、unittest.mock。

**关联 spec:** [`docs/superpowers/specs/2026-06-15-graceful-shutdown-design.md`](../specs/2026-06-15-graceful-shutdown-design.md)

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/runtime/scan_runner.py` | `ScanCancelled`、`ShutdownController`、`poll_progress`、`run_scan_graceful` | **Create** |
| `packages/core/src/shannon_core/runtime/__init__.py` | 导出上述公共 API | **Modify** |
| `packages/core/tests/runtime/__init__.py` | 测试包标识（空） | **Create** |
| `packages/core/tests/runtime/test_scan_runner.py` | ShutdownController / poll_progress / run_scan_graceful 单元测试 | **Create** |
| `packages/whitebox/src/shannon_whitebox/worker.py` | `run_scan` 接入 `run_scan_graceful`；删本地 `poll_workflow_progress` | **Modify** |
| `packages/blackbox/src/shannon_blackbox/worker.py` | 同上 | **Modify** |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | `start` 加 `cancelled` → `SystemExit(130)` | **Modify** |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | `start` 加 `cancelled` → `SystemExit(130)` | **Modify** |
| `packages/combined/src/shannon_combined/orchestrator.py` | whitebox 阶段 `cancelled` 短路 | **Modify** |
| `packages/combined/src/shannon_combined/cli/main.py` | `scan` 加 `cancelled` → `SystemExit(130)` | **Modify** |

**依赖方向约束**：`scan_runner` 在 `core`，**不得** import whitebox/blackbox 包的类型。`PipelineProgress`（poll 用）由各 worker 通过 `progress_type` 参数注入。

---

## Task 1: `ShutdownController` —— 双击信号语义（TDD）

**Files:**
- Create: `packages/core/src/shannon_core/runtime/scan_runner.py`
- Create: `packages/core/tests/runtime/__init__.py`
- Test: `packages/core/tests/runtime/test_scan_runner.py`

- [ ] **Step 1: 建测试包标识**

创建空文件 `packages/core/tests/runtime/__init__.py`（内容为空）。

- [ ] **Step 2: 写 `ShutdownController` 的失败测试**

创建 `packages/core/tests/runtime/test_scan_runner.py`：

```python
"""scan_runner 单元测试：ShutdownController / poll_progress / run_scan_graceful。"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.runtime.scan_runner import (
    ScanCancelled,
    ShutdownController,
)


class TestShutdownController:
    def test_first_sigint_triggers_graceful(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)
        assert ctrl.is_set() is True
        assert ctrl._count == 1

    def test_second_sigint_force_exits_130(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)  # 第 1 次
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)  # 第 2 次
        mock_exit.assert_called_once_with(130)

    def test_sigterm_triggers_graceful_without_counting(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGTERM)
        assert ctrl.is_set() is True
        assert ctrl._count == 0  # SIGTERM 不参与双击计数

    def test_repeated_graceful_only_sets_event_once(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)
        ctrl._on_signal(signal.SIGTERM)  # 已 set，不重复
        assert ctrl._count == 1

    def test_install_registers_sigint_and_sigterm(self):
        ctrl = ShutdownController()
        loop = MagicMock()
        ctrl.install(loop)
        registered = {call.args[0] for call in loop.add_signal_handler.call_args_list}
        assert registered == {signal.SIGINT, signal.SIGTERM}

    def test_uninstall_removes_handlers(self):
        ctrl = ShutdownController()
        loop = MagicMock()
        ctrl.install(loop)
        ctrl.uninstall()
        removed = {call.args[0] for call in loop.remove_signal_handler.call_args_list}
        assert removed == {signal.SIGINT, signal.SIGTERM}

    @pytest.mark.asyncio
    async def test_wait_returns_after_event_set(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)  # set event
        await asyncio.wait_for(ctrl.wait(), timeout=1.0)  # 立即返回
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py -v`
Expected: FAIL（`ImportError: cannot import name 'ScanCancelled' ...` —— 模块还不存在）

- [ ] **Step 4: 写最小实现**

创建 `packages/core/src/shannon_core/runtime/scan_runner.py`：

```python
"""共享扫描运行时：优雅退出（SIGINT 双击 + Temporal 协作式取消）。

设计见 docs/superpowers/specs/2026-06-15-graceful-shutdown-design.md。
清理范围：只关连接/子进程 + 还原临时注入配置，不删 deliverables / workflow.log。
"""

import asyncio
import os
import signal


class ScanCancelled(Exception):
    """扫描被用户中断（Ctrl+C / SIGTERM）时由 run_scan_graceful 抛出。"""


class ShutdownController:
    """管理 SIGINT 双击 + SIGTERM 直接优雅的退出语义。

    - 第 1 次 SIGINT：set 事件（主协程 await wait() 醒来走取消流程）。
    - 第 2 次 SIGINT：os._exit(130) 立即强制退出。
    - SIGTERM：直接 set 事件（不计数；docker stop / kill 的确定性终止）。
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._count = 0
        self._loop = None

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        """在给定 event loop 上注册信号 handler（仅 Unix）。"""
        self._loop = loop
        loop.add_signal_handler(signal.SIGINT, self._on_signal, signal.SIGINT)
        loop.add_signal_handler(signal.SIGTERM, self._on_signal, signal.SIGTERM)

    def _on_signal(self, signum: int) -> None:
        if signum == signal.SIGTERM:
            self._trigger_graceful()
            return
        # SIGINT：双击语义
        self._count += 1
        if self._count >= 2:
            self._force_exit()
        else:
            self._trigger_graceful()

    def _trigger_graceful(self) -> None:
        if not self._event.is_set():
            print("\n正在优雅取消…（再按一次 Ctrl+C 立即退出）", flush=True)
            self._event.set()

    def _force_exit(self) -> None:
        print("\n强制退出", flush=True)
        os._exit(130)

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def uninstall(self) -> None:
        if self._loop is None:
            return
        self._loop.remove_signal_handler(signal.SIGINT)
        self._loop.remove_signal_handler(signal.SIGTERM)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py -v`
Expected: PASS（7 个测试全绿）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/runtime/scan_runner.py packages/core/tests/runtime/__init__.py packages/core/tests/runtime/test_scan_runner.py
git commit -m "feat(runtime): ShutdownController — SIGINT double-click + SIGTERM graceful"
```

---

## Task 2: `poll_progress` —— 进度轮询（TDD）

**Files:**
- Modify: `packages/core/src/shannon_core/runtime/scan_runner.py`
- Test: `packages/core/tests/runtime/test_scan_runner.py`

- [ ] **Step 1: 写失败测试**

在 `test_scan_runner.py` 末尾追加：

```python
from shannon_core.runtime.scan_runner import poll_progress


class TestPollProgress:
    @pytest.mark.asyncio
    async def test_queries_and_prints_one_iteration(self, capsys):
        fake_handle = AsyncMock()
        progress = MagicMock(
            elapsed_ms=30000,
            current_phase="scan",
            current_agent="agent1",
            completed_agents=["a", "b"],
        )
        fake_handle.query = AsyncMock(return_value=progress)

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 1:
                raise asyncio.CancelledError()

        with patch("shannon_core.runtime.scan_runner.asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poll_progress(fake_handle, progress_type=MagicMock(), total=13)

        fake_handle.query.assert_awaited_once_with(
            "PipelineProgress", result_type=progress.__class__
        )
        out = capsys.readouterr().out
        assert "[30s] Phase: scan | Agent: agent1 | Completed: 2/13" in out

    @pytest.mark.asyncio
    async def test_swallows_query_exception_and_continues(self):
        fake_handle = AsyncMock()
        fake_handle.query = AsyncMock(side_effect=RuntimeError("workflow gone"))

        async def fake_sleep(seconds):
            raise asyncio.CancelledError()

        with patch("shannon_core.runtime.scan_runner.asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poll_progress(fake_handle, progress_type=MagicMock(), total=13)
        # 异常被吞掉，没有向上抛 RuntimeError
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py::TestPollProgress -v`
Expected: FAIL（`ImportError: cannot import name 'poll_progress'`）

- [ ] **Step 3: 写实现**

在 `scan_runner.py` 顶部 import 区追加 `from typing import Any`，并在 `ShutdownController` 类之后追加：

```python
async def poll_progress(
    handle,
    progress_type,
    total: int = 13,
    interval_seconds: int = 30,
) -> None:
    """周期性查询 workflow 进度并打印一行（取代三个 worker 里复制的版本）。

    progress_type 由各 worker 注入（whitebox/blackbox 各自的 PipelineProgress），
    保持 core 不依赖上层包类型。
    """
    while True:
        try:
            progress = await handle.query("PipelineProgress", result_type=progress_type)
            elapsed = int(progress.elapsed_ms / 1000)
            phase = progress.current_phase or "unknown"
            agent = progress.current_agent or "none"
            completed = len(progress.completed_agents)
            print(
                f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/{total}",
                flush=True,
            )
        except Exception:
            pass  # workflow 可能已完成或暂时不可查询
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py::TestPollProgress -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/runtime/scan_runner.py packages/core/tests/runtime/test_scan_runner.py
git commit -m "feat(runtime): poll_progress — injected progress_type, swallows errors"
```

---

## Task 3: `run_scan_graceful` —— 正常完成路径（TDD）

**Files:**
- Modify: `packages/core/src/shannon_core/runtime/scan_runner.py`
- Test: `packages/core/tests/runtime/test_scan_runner.py`

- [ ] **Step 1: 写失败测试**

在 `test_scan_runner.py` 顶部 import 区追加：

```python
from shannon_core.runtime.scan_runner import run_scan_graceful
```

在文件末尾追加（含一个共享的 fake-worker fixture 工厂）：

```python
def _make_fake_worker():
    """构造可作 async context manager 的 fake Worker。"""
    fake = AsyncMock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    return fake


class TestRunScanGracefulNormal:
    @pytest.mark.asyncio
    async def test_normal_completion_returns_result_without_cancel(self):
        fake_handle = AsyncMock()
        fake_handle.result = AsyncMock(return_value={"status": "completed", "vulns": 3})
        fake_handle.cancel = AsyncMock()
        fake_handle.query = AsyncMock()

        fake_client = AsyncMock()
        fake_client.start_workflow = AsyncMock(return_value=fake_handle)

        fake_worker = _make_fake_worker()

        with patch(
            "shannon_core.runtime.scan_runner.Client.connect",
            AsyncMock(return_value=fake_client),
        ), patch(
            "shannon_core.runtime.scan_runner.Worker", return_value=fake_worker
        ), patch(
            "shannon_core.runtime.scan_runner.generate_task_queue",
            return_value="tq-test",
        ), patch.object(ShutdownController, "install"), patch.object(
            ShutdownController, "uninstall"
        ):  # 不注册真实信号 handler，保持测试纯净
            result = await run_scan_graceful(
                temporal_address="localhost:7233",
                task_queue_prefix="x",
                workflow_cls=MagicMock(),
                workflow_input=MagicMock(workspace_name="ws1"),
                activities=[],
                progress_type=MagicMock(),
            )

        assert result == {"status": "completed", "vulns": 3}
        fake_handle.cancel.assert_not_awaited()  # 正常完成不取消
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py::TestRunScanGracefulNormal -v`
Expected: FAIL（`ImportError: cannot import name 'run_scan_graceful'`）

- [ ] **Step 3: 写实现**

在 `scan_runner.py` 顶部 import 区追加：

```python
import contextlib

from temporalio.client import Client
from temporalio.worker import Worker

from shannon_core.services.temporal_infra import generate_task_queue
```

在 `poll_progress` 之后追加：

```python
async def run_scan_graceful(
    *,
    temporal_address: str,
    task_queue_prefix: str,
    workflow_cls,
    workflow_input,
    activities: list,
    progress_type,
    progress_total: int = 13,
    cancel_grace_seconds: float = 15.0,
) -> Any:
    """连接 Temporal、起 worker、跑 workflow；支持 SIGINT/SIGTERM 优雅取消。

    成功返回 workflow result；被用户中断时抛 ScanCancelled（由调用方捕获）。
    清理范围只含连接/子进程 + 临时注入配置（由 workflow 已有的 finally 触发），
    不删 deliverables / workflow.log。
    """
    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(task_queue_prefix)
    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[workflow_cls],
        activities=activities,
    )

    ctrl = ShutdownController()
    ctrl.install(asyncio.get_running_loop())

    async with worker:
        workflow_id = (
            getattr(workflow_input, "workspace_name", None)
            or f"{task_queue_prefix}-scan"
        )
        handle = await client.start_workflow(
            workflow_cls.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue,
        )
        poll_task = asyncio.create_task(
            poll_progress(handle, progress_type=progress_type, total=progress_total)
        )
        result_task = asyncio.ensure_future(handle.result())
        shutdown_wait_task = asyncio.create_task(ctrl.wait())
        try:
            await asyncio.wait(
                {result_task, shutdown_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ctrl.is_set():
                await _do_cancel(handle, result_task, cancel_grace_seconds)
                raise ScanCancelled()
            return result_task.result()
        finally:
            for task in (poll_task, shutdown_wait_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            ctrl.uninstall()


async def _do_cancel(handle, result_task, cancel_grace_seconds: float) -> None:
    """发协作式 cancel，并在 grace 期内等待结果；超时放弃等待（不 escalate）。"""
    print("正在取消 Temporal workflow…", flush=True)
    try:
        await handle.cancel()
    except Exception as exc:
        print(f"cancel 请求失败（忽略）: {exc}", flush=True)
    try:
        await asyncio.wait_for(result_task, timeout=cancel_grace_seconds)
    except asyncio.TimeoutError:
        print(
            f"{cancel_grace_seconds}s 内 workflow 未响应取消，放弃等待"
            f"（server 端 cancel 仍生效）",
            flush=True,
        )
    except Exception:
        # result_task 因 cancel 抛出的异常属预期，吞掉
        pass
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py::TestRunScanGracefulNormal -v`
Expected: PASS

- [ ] **Step 5: 运行全量 scan_runner 测试**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py -v`
Expected: PASS（Task 1-2 测试仍绿）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/runtime/scan_runner.py packages/core/tests/runtime/test_scan_runner.py
git commit -m "feat(runtime): run_scan_graceful normal-completion path"
```

---

## Task 4: `run_scan_graceful` —— 取消 / 超时 / cancel 异常路径（TDD）

**Files:**
- Modify: `packages/core/tests/runtime/test_scan_runner.py`

- [ ] **Step 1: 写失败测试**

在 `test_scan_runner.py` 末尾追加：

```python
class TestRunScanGracefulCancel:
    @pytest.mark.asyncio
    async def test_shutdown_triggers_cancel_and_raises_scan_cancelled(self):
        fake_handle = AsyncMock()
        # result 永不自然完成 → 模拟必须靠 cancel
        fake_handle.result = MagicMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        fake_handle.cancel = AsyncMock()

        fake_client = AsyncMock()
        fake_client.start_workflow = AsyncMock(return_value=fake_handle)
        fake_worker = _make_fake_worker()

        triggered = ShutdownController()
        triggered._event.set()  # 预置：中断已发生
        triggered.install = MagicMock()   # 不注册真实信号 handler
        triggered.uninstall = MagicMock()

        with patch(
            "shannon_core.runtime.scan_runner.Client.connect",
            AsyncMock(return_value=fake_client),
        ), patch(
            "shannon_core.runtime.scan_runner.Worker", return_value=fake_worker
        ), patch(
            "shannon_core.runtime.scan_runner.generate_task_queue",
            return_value="tq-test",
        ), patch(
            "shannon_core.runtime.scan_runner.ShutdownController",
            return_value=triggered,
        ):
            with pytest.raises(ScanCancelled):
                await run_scan_graceful(
                    temporal_address="localhost:7233",
                    task_queue_prefix="x",
                    workflow_cls=MagicMock(),
                    workflow_input=MagicMock(workspace_name="ws1"),
                    activities=[],
                    progress_type=MagicMock(),
                    cancel_grace_seconds=0.01,  # 立即超时
                )

        fake_handle.cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_exception_still_raises_scan_cancelled(self):
        fake_handle = AsyncMock()
        fake_handle.result = MagicMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        fake_handle.cancel = AsyncMock(side_effect=RuntimeError("server unreachable"))

        fake_client = AsyncMock()
        fake_client.start_workflow = AsyncMock(return_value=fake_handle)
        fake_worker = _make_fake_worker()

        triggered = ShutdownController()
        triggered._event.set()

        with patch(
            "shannon_core.runtime.scan_runner.Client.connect",
            AsyncMock(return_value=fake_client),
        ), patch(
            "shannon_core.runtime.scan_runner.Worker", return_value=fake_worker
        ), patch(
            "shannon_core.runtime.scan_runner.generate_task_queue",
            return_value="tq-test",
        ), patch(
            "shannon_core.runtime.scan_runner.ShutdownController",
            return_value=triggered,
        ):
            with pytest.raises(ScanCancelled):  # 不是 RuntimeError
                await run_scan_graceful(
                    temporal_address="localhost:7233",
                    task_queue_prefix="x",
                    workflow_cls=MagicMock(),
                    workflow_input=MagicMock(workspace_name="ws1"),
                    activities=[],
                    progress_type=MagicMock(),
                    cancel_grace_seconds=0.01,
                )
```

- [ ] **Step 2: 运行测试确认通过（实现已在 Task 3 完成）**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py::TestRunScanGracefulCancel -v`
Expected: PASS（`_do_cancel` 已吞掉 cancel 异常并走超时；`run_scan_graceful` 抛 `ScanCancelled`）。如失败，检查 Task 3 Step 5 的 `await _do_cancel(...)` 是否就位。

- [ ] **Step 3: 运行全量 scan_runner 测试**

Run: `cd packages/core && python -m pytest tests/runtime/test_scan_runner.py -v`
Expected: PASS（全部测试绿）

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/runtime/test_scan_runner.py
git commit -m "test(runtime): cover cancel / timeout / cancel-exception paths"
```

---

## Task 5: 导出公共 API

**Files:**
- Modify: `packages/core/src/shannon_core/runtime/__init__.py`

- [ ] **Step 1: 更新导出**

把 `packages/core/src/shannon_core/runtime/__init__.py` 内容改为：

```python
"""Runtime prerequisite detection, installation, and shared scan runner."""

from .scan_runner import (  # noqa: F401
    ScanCancelled,
    ShutdownController,
    poll_progress,
    run_scan_graceful,
)
```

> 不导出 `prerequisites`：现有代码走 `from shannon_core.runtime.prerequisites import ...` 子模块路径导入，保持原状以免引入不必要耦合。`prerequisites.py` 本身不动。

- [ ] **Step 2: 验证导入可用**

Run: `cd packages/core && python -c "from shannon_core.runtime import run_scan_graceful, ScanCancelled, ShutdownController; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/runtime/__init__.py
git commit -m "feat(runtime): export scan_runner public API"
```

---

## Task 6: whitebox worker 接入 `run_scan_graceful`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`

- [ ] **Step 1: 改写 `run_scan`，删除本地 `poll_workflow_progress`**

把 `packages/whitebox/src/shannon_whitebox/worker.py` 整体替换为：

```python
import asyncio
from dataclasses import asdict
from pathlib import Path

from shannon_core.runtime.scan_runner import ScanCancelled, run_scan_graceful
from shannon_core.session import SessionManager
from shannon_core.utils.paths import resolve_workspaces_dir

from .pipeline.activities import (
    render_findings,
    run_agent,
    run_auth_validation,
    run_code_index,
    run_credential_check,
    run_merge_sink_reports,
    run_entry_point_fusion,
    run_preflight,
    run_render_dataflow_hints,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
    run_attack_chain_assembly,
    run_framework_analysis,
    run_frontend_mapping,
    run_route_chain_building,
)
from .pipeline.shared import PipelineInput, PipelineProgress
from .pipeline.workflows import WhiteboxScanWorkflow

TASK_QUEUE_PREFIX = "shannon-py-wb"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    """跑白盒扫描；Ctrl+C 时优雅取消并返回 {"status": "cancelled"}。"""
    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path,
            name=input.workspace_name,
        )

    try:
        result = await run_scan_graceful(
            temporal_address=temporal_address,
            task_queue_prefix=TASK_QUEUE_PREFIX,
            workflow_cls=WhiteboxScanWorkflow,
            workflow_input=input,
            activities=[
                render_findings,
                run_agent,
                run_auth_validation,
                run_code_index,
                run_credential_check,
                run_merge_sink_reports,
                run_entry_point_fusion,
                run_preflight,
                run_render_dataflow_hints,
                run_risk_scoring,
                run_save_adjudication,
                run_vuln_agent,
                run_attack_chain_assembly,
                run_framework_analysis,
                run_frontend_mapping,
                run_route_chain_building,
            ],
            progress_type=PipelineProgress,
            progress_total=13,
        )
    except ScanCancelled:
        return {"status": "cancelled"}

    # Convert PipelineState to enriched dict for CLI consumption
    result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
    result_dict["workspace_name"] = input.workspace_name
    result_dict["web_url"] = input.web_url

    workspaces_dir = resolve_workspaces_dir(input.repo_path)
    if input.workspace_name:
        result_dict["deliverables_path"] = str(
            workspaces_dir / input.workspace_name / input.deliverables_subdir
        )
    else:
        result_dict["deliverables_path"] = str(
            Path(input.repo_path) / input.deliverables_subdir
        )

    return result_dict


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
```

- [ ] **Step 2: 验证导入与现有测试**

Run: `cd packages/whitebox && python -c "from shannon_whitebox.worker import run_scan; print('ok')"`
Expected: 输出 `ok`

Run: `cd packages/whitebox && python -m pytest tests/ -k "runner or worker" -q 2>/dev/null || echo "no worker tests"`
Expected: 现有测试不回归（若 `run_scan` 有现存测试被 mock 结构影响，按需更新 mock）。

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py
git commit -m "refactor(whitebox): run_scan delegates to run_scan_graceful; drop poll copy"
```

---

## Task 7: blackbox worker 接入 `run_scan_graceful`

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`

- [ ] **Step 1: 改写 `run_scan`，删除本地 `poll_workflow_progress`**

把 `packages/blackbox/src/shannon_blackbox/worker.py` 整体替换为：

```python
import asyncio

from shannon_core.runtime.scan_runner import ScanCancelled, run_scan_graceful

from .pipeline.activities import (
    run_blackbox_preflight,
    run_blackbox_auth_validation,
    run_recon,
    run_exploit_agent,
    assemble_report,
    run_report_agent,
)
from .pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState, PipelineProgress
from .pipeline.workflows import BlackboxScanWorkflow

TASK_QUEUE_PREFIX = "shannon-py-bb"


async def run_scan(
    input: BlackboxPipelineInput, temporal_address: str = "localhost:7233"
) -> BlackboxPipelineState:
    """跑黑盒扫描；Ctrl+C 时优雅取消并返回 BlackboxPipelineState(status="cancelled")。"""
    try:
        result = await run_scan_graceful(
            temporal_address=temporal_address,
            task_queue_prefix=TASK_QUEUE_PREFIX,
            workflow_cls=BlackboxScanWorkflow,
            workflow_input=input,
            activities=[
                run_blackbox_preflight,
                run_blackbox_auth_validation,
                run_recon,
                run_exploit_agent,
                assemble_report,
                run_report_agent,
            ],
            progress_type=PipelineProgress,
            progress_total=13,
        )
    except ScanCancelled:
        return BlackboxPipelineState(status="cancelled")
    return result


def main():
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
    asyncio.run(run_scan(BlackboxPipelineInput(web_url=url)))
```

- [ ] **Step 2: 验证导入**

Run: `cd packages/blackbox && python -c "from shannon_blackbox.worker import run_scan; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py
git commit -m "refactor(blackbox): run_scan delegates to run_scan_graceful; drop poll copy"
```

---

## Task 8: whitebox CLI `start` 加 cancelled 分支

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`（`start` 命令，约 `:50` 处的 `if result.get("status") == "completed":`）

- [ ] **Step 1: 加 cancelled 分支**

定位 `start` 命令里的：

```python
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
```

改为：

```python
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.get("status") == "completed":
```

- [ ] **Step 2: 写 CLI 测试**

在 `packages/whitebox/tests/` 下新建（或追加到已有 cli 测试文件）`test_cli_start_cancelled.py`：

```python
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from shannon_whitebox.cli.main import cli


def test_start_cancelled_exits_130(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    # ensure_infra 是顶层导入；ensure_prerequisite / run_scan 是 start() 内局部导入，
    # 故 patch 它们的源头模块。
    with patch("shannon_whitebox.cli.main.ensure_infra", new=AsyncMock()), \
         patch("shannon_core.runtime.prerequisites.ensure_prerequisite"), \
         patch(
             "shannon_whitebox.worker.run_scan",
             new=AsyncMock(return_value={"status": "cancelled"}),
         ):
        result = CliRunner().invoke(
            cli, ["start", "-r", str(repo), "-w", "ws-test", "--pipeline-testing"]
        )

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output
```

> 若 `ensure_infra` / `ensure_prerequisite` 的 import 路径与 cli/main.py 不完全一致，先 `grep -n "ensure_infra\|ensure_prerequisite" packages/whitebox/src/shannon_whitebox/cli/main.py` 确认 patch 目标。

- [ ] **Step 3: 运行测试**

Run: `cd packages/whitebox && python -m pytest tests/test_cli_start_cancelled.py -v`
Expected: PASS（exit_code 130）

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli_start_cancelled.py
git commit -m "feat(whitebox-cli): start exits 130 on cancelled scan"
```

---

## Task 9: blackbox CLI `start` 加 cancelled 分支

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`（`start` 命令，约 `:131` 处的 `if result.status == "completed":`）

- [ ] **Step 1: 加 cancelled 分支**

定位：

```python
    result = asyncio.run(run_scan(input, temporal_address))
    if result.status == "completed":
```

改为：

```python
    result = asyncio.run(run_scan(input, temporal_address))
    if result.status == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.status == "completed":
```

- [ ] **Step 2: 写 CLI 测试**

新建 `packages/blackbox/tests/test_cli_start_cancelled.py`：

```python
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from shannon_blackbox.cli.main import cli
from shannon_blackbox.pipeline.shared import BlackboxPipelineState


def test_start_cancelled_exits_130():
    # ensure_infra 顶层导入；ensure_prerequisite / run_scan 是 start() 内局部导入；
    # find_workspaces_by_url 顶层导入，patch 成空避免 auto-detect 读真实 workspaces。
    runner = CliRunner()
    with runner.isolated_filesystem(), \
         patch("shannon_blackbox.cli.main.ensure_infra", new=AsyncMock()), \
         patch("shannon_core.runtime.prerequisites.ensure_prerequisite"), \
         patch("shannon_blackbox.cli.main.find_workspaces_by_url", return_value=[]), \
         patch(
             "shannon_blackbox.worker.run_scan",
             new=AsyncMock(return_value=BlackboxPipelineState(status="cancelled")),
         ):
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output
```

- [ ] **Step 3: 运行测试**

Run: `cd packages/blackbox && python -m pytest tests/test_cli_start_cancelled.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli_start_cancelled.py
git commit -m "feat(blackbox-cli): start exits 130 on cancelled scan"
```

---

## Task 10: combined orchestrator 短路 + combined CLI cancelled 分支

**Files:**
- Modify: `packages/combined/src/shannon_combined/orchestrator.py`（`run_combined_scan`，`:40`）
- Modify: `packages/combined/src/shannon_combined/cli/main.py`（`scan` 命令，`:43`）

- [ ] **Step 1: orchestrator 加 cancelled 短路**

定位 `orchestrator.py` 的：

```python
    wb_result = await run_whitebox_scan(wb_input, temporal_address)

    if wb_result.get("status") != "completed":
        return {
            "status": "failed",
            "phase": "whitebox",
            "error": wb_result.get("error", "whitebox scan failed"),
        }
```

改为（在 `!= "completed"` 之前先识别 cancelled）：

```python
    wb_result = await run_whitebox_scan(wb_input, temporal_address)

    if wb_result.get("status") == "cancelled":
        return {"status": "cancelled", "phase": "whitebox"}

    if wb_result.get("status") != "completed":
        return {
            "status": "failed",
            "phase": "whitebox",
            "error": wb_result.get("error", "whitebox scan failed"),
        }
```

> blackbox 阶段的 cancelled 无需在此特殊处理：`BlackboxScanWorkflow` 的 `except CancelledError` 返回 `BlackboxPipelineState(status="cancelled")`，下方既有的 `asdict` 逻辑（`:67-71`）会把它转成 `{"status": "cancelled", ...}`，combined CLI 的 cancelled 分支会捕获。

- [ ] **Step 2: combined CLI 加 cancelled 分支**

定位 `cli/main.py` 的：

```python
    if result.get("status") == "completed":
```

改为：

```python
    if result.get("status") == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.get("status") == "completed":
```

- [ ] **Step 3: 写 orchestrator 短路测试**

新建 `packages/combined/tests/test_orchestrator_cancelled.py`：

```python
import pytest
from unittest.mock import AsyncMock, patch

from shannon_combined.orchestrator import run_combined_scan


@pytest.mark.asyncio
async def test_whitebox_cancelled_short_circuits_before_blackbox():
    run_blackbox = AsyncMock(name="run_blackbox_should_not_run")

    with patch(
        "shannon_combined.orchestrator.run_whitebox_scan",
        AsyncMock(return_value={"status": "cancelled"}),
    ), patch(
        "shannon_combined.orchestrator.run_blackbox_scan", run_blackbox
    ):
        result = await run_combined_scan(
            repo_path="/tmp/repo", url="http://example.com"
        )

    assert result == {"status": "cancelled", "phase": "whitebox"}
    run_blackbox.assert_not_awaited()  # 关键：blackbox 阶段未执行
```

- [ ] **Step 4: 运行测试**

Run: `cd packages/combined && python -m pytest tests/test_orchestrator_cancelled.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/combined/src/shannon_combined/orchestrator.py packages/combined/src/shannon_combined/cli/main.py packages/combined/tests/test_orchestrator_cancelled.py
git commit -m "feat(combined): short-circuit on cancelled sub-scan; scan exits 130"
```

---

## Task 11: 端到端手动验证

> 这一步需要本地 Temporal server（`shannon-whitebox infra up`）和一个可扫描的小仓库。自动化集成测试需要真实 Temporal 环境，超出单测范围；改为结构化手动 checklist。

**Files:** 无（仅运行验证）

- [ ] **Step 1: 起基础设施**

Run: `shannon-whitebox infra up`，等到 "Temporal is ready!"。

- [ ] **Step 2: 起一次 whitebox 扫描，在进度出现后按一次 Ctrl+C**

Run: `shannon-whitebox start -r <小仓库路径> -w ws-e2e --pipeline-testing`

等到看到 `[Ns] Phase: ... | Completed: x/13` 进度行后，按 **一次** Ctrl+C。

Expected:
- 终端打印 `正在优雅取消…（再按一次 Ctrl+C 立即退出）` 与 `正在取消 Temporal workflow…`
- **不**出现 `CancelledError` / gRPC channel 关闭的堆栈刷屏
- 最终打印 `Scan cancelled.`
- shell 退出码：`echo $?` → **130**

- [ ] **Step 3: 验证 server 端 workflow 已停**

Run: `docker compose logs temporal | tail -20`（或 temporal ui）确认 workflow 状态为 cancelled，非 continued-as-new / running。

- [ ] **Step 4: 验证 deliverables / log 未被删**

Run: `ls workspaces/ws-e2e/ && ls workspaces/ws-e2e/deliverables/ 2>/dev/null; tail -5 workspaces/ws-e2e/workflow.log`

Expected: workspace 目录、已生成的 deliverables 文件、workflow.log 均存在；log 含已执行 activity 的记录（后续 activity 因取消未产生新行属正常）。

- [ ] **Step 5: 验证子进程已清理**

Run: `pgrep -fl gitnexus; pgrep -fl playwright`
Expected: 无残留的 gitnexus / playwright 进程（或仅与本次无关的）。

- [ ] **Step 6: 验证第二次 Ctrl+C 强制退出**

重跑 Step 2 的 scan，进度出现后**连按两次** Ctrl+C。

Expected: 第一次打印 `正在优雅取消…`，第二次打印 `强制退出`，立即终止（不等 cancel 完成）；`echo $?` → **130**。

- [ ] **Step 7: 记录验证结果**

在 PR 描述或提交说明里贴出 Step 2/3/4/5/6 的观察结果。

---

## Self-Review（已执行）

**1. Spec 覆盖**：
- 全链路取消（本地 + Temporal cancel）→ Task 1-4（scan_runner）、6-7（worker 接入）。
- 双击语义 → Task 1（ShutdownController）、Task 11 Step 6 手动验证。
- 三个扫描入口 → Task 6/7（worker）、8/9（whitebox/blackbox cli）、10（combined）。
- 退出码 130 → Task 8/9/10 CLI 分支、Task 11 Step 2 验证。
- 不删 deliverables/log → scan_runner 设计本身不动文件；Task 11 Step 4 验证。
- 子进程/临时配置清理依赖 workflow 已有 finally → spec 已述，Task 11 Step 5 验证。

**2. Placeholder 扫描**：无 TODO/TBD；每步含完整代码或确切命令与期望输出。

**3. 类型一致性**：
- `run_scan_graceful` 签名（`progress_type`、`progress_total`、`cancel_grace_seconds`）在 Task 2/3 定义，Task 6/7 调用一致。
- `ScanCancelled` 在 Task 1 定义，Task 3/4/6/7 引用一致。
- blackbox cancelled payload 用 `BlackboxPipelineState(status="cancelled")`（Task 7），与 blackbox CLI 属性访问 `result.status`（Task 9）一致。
- `_do_cancel` 在 Task 3 Step 3 定义为 `async`，`run_scan_graceful` 内 `await _do_cancel(...)` 调用一致。
