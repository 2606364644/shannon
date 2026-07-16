# events.ndjson SSOT · Plan B（黑盒 web 扫描 C1 化）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成黑盒 web 扫描 C1 化（Phase C）：让 web 发起的黑盒扫描从 `NotImplementedError` 变为端到端可用——产 events.ndjson/workflow.log、live 页可见、scan_liveness 不误判。

**Architecture:** 照抄白盒 C1 已落地模式（Plan A 后）。Input 加 `event_file` 字段；新增黑盒 `setup_display`/`finalize_display`/`run_heartbeat` activity（仿白盒 `activities.py:1519-1612`，用 `shannon_core.audit.session`/`session_registry`——白盒 audit 是 core shim）；`BlackboxScanWorkflow.run` 加 worker 分支（`is_worker_path` + 前导 setup_display/heartbeat + finally finalize_display）；`scan_manager._submit_blackbox` 提交到 `WEB_TASK_QUEUE_BLACKBOX`；bb_worker 注册三 activity（并发=1 已在 Plan A 落地）。

**Tech Stack:** Python 3.12 / pytest / temporalio

**对应 spec：** `docs/superpowers/specs/2026-07-16-events-ndjson-ssot-design.md`（改动 4 的 6 项）

**前置（Plan A 已落地，本 plan 依赖）：**
- `bb_worker max_concurrent_workflow_tasks=1`（`runner.py:88`，Plan A Task 2）
- 白盒 `setup_display` 含 `configure_logging`（`activities.py:1551`，Plan A Task 1）——黑盒照抄模板

## Global Constraints

- **照抄白盒 C1，不发明新模式**：黑盒 setup_display/finalize_display/run_heartbeat 逐行对齐白盒（`packages/whitebox/src/shannon_whitebox/pipeline/activities.py:1519-1612`），仅适配 `BlackboxActivityInput` + 直接用 `shannon_core.audit.*`（白盒 `shannon_whitebox.audit.session`/`session_registry` 是 core shim，黑盒直连 core 更直接）。
- **is_worker_path 必须正确**：CLI 路径（`worker.py run_scan` 外层 `run_with_display` + `set_audit_session`）不能再调 setup_display（双重 session/attach）。判断 = `input.event_file is not None`（白盒 `workflows.py:54` 同口径）。
- **finalize_display 在 finally**：黑盒 run 已有 `try/except(CancelledError)/finally`（`workflows.py:427-445`），finalize 加在现有 finally 内，保证正常/cancel/异常都收尾（否则下个黑盒 scan 拿到上个 session）。
- **bb_worker 并发=1 已落地**（Plan A），本 plan 不再动 runner 并发配置，只加 activity 注册。
- **测试只跑改动相关文件**，勿广跑全套。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | BlackboxPipelineInput / BlackboxActivityInput | Task 1：加 `event_file` 字段 |
| `packages/blackbox/tests/test_pipeline_shared.py` | shared 字段测试 | Task 1：加 event_file 测试 |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | 黑盒 activity | Task 2：加 setup_display/finalize_display/run_heartbeat |
| `packages/blackbox/tests/test_c1_activities.py` | 新建：C1 activity 测试 | Task 2 |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | BlackboxScanWorkflow.run | Task 3：抽 `_prepare_inputs` + worker 分支 + finally finalize |
| `packages/blackbox/tests/test_workflows.py` | workflow 测试 | Task 3：测 `_prepare_inputs` |
| `packages/web/src/shannon_web/components/scan_manager.py` | web 扫描提交 | Task 4：`_submit_blackbox`（替换 NotImplementedError） |
| `packages/web/tests/test_scan_manager.py` | scan_manager 测试 | Task 4：测 `_submit_blackbox` |
| `packages/worker/src/shannon_worker/runner.py` | bb_worker activities | Task 5：注册 setup_display/finalize_display/run_heartbeat |
| `packages/worker/tests/test_runner.py` | runner 测试 | Task 5：断言三 activity 注册 |

---

### Task 1: BlackboxPipelineInput / BlackboxActivityInput 加 `event_file`

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`（`BlackboxPipelineInput` :8、`BlackboxActivityInput` :37）
- Test: `packages/blackbox/tests/test_pipeline_shared.py`

**Interfaces:**
- Consumes: 无
- Produces: `BlackboxPipelineInput.event_file: str | None`、`BlackboxActivityInput.event_file: str | None`（默认 None，CLI 路径向后兼容）

- [ ] **Step 1: 写失败测试**

在 `packages/blackbox/tests/test_pipeline_shared.py` 末尾追加：

```python
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxActivityInput


def test_blackbox_pipeline_input_has_event_file_default_none():
    inp = BlackboxPipelineInput(web_url="https://x")
    assert inp.event_file is None  # CLI 路径向后兼容


def test_blackbox_pipeline_input_accepts_event_file():
    inp = BlackboxPipelineInput(web_url="https://x", event_file="/tmp/e.ndjson")
    assert inp.event_file == "/tmp/e.ndjson"


def test_blackbox_activity_input_has_event_file_default_none():
    inp = BlackboxActivityInput(web_url="https://x")
    assert inp.event_file is None


def test_blackbox_activity_input_accepts_event_file():
    inp = BlackboxActivityInput(web_url="https://x", event_file="/tmp/e.ndjson")
    assert inp.event_file == "/tmp/e.ndjson"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_pipeline_shared.py -xvs -k event_file`
Expected: FAIL（`TypeError: unexpected keyword argument 'event_file'`）。

- [ ] **Step 3: 实现**

在 `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`：

`BlackboxPipelineInput`（:8）末尾加字段（`workspaces_root` 之后）：
```python
    workspaces_root: str | None = None  # sandbox 外（CLI/worker）解析的 workspaces 根绝对路径（sandbox 内禁 os.getenv/Path.cwd）
    event_file: str | None = None  # C1: web 提交端塞 events.ndjson 路径(env 不跨容器); CLI 为 None 走 env 兜底
```

`BlackboxActivityInput`（:37）末尾加字段（`info_level` 之后）：
```python
    info_level: str = "info"          # "info" | "warning"（rich 着色：cyan/yellow）
    event_file: str | None = None     # C1: setup_display 透传到 AuditSession.initialize→WorkflowLogger; CLI 为 None 走 env 兜底
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_pipeline_shared.py -xvs`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py packages/blackbox/tests/test_pipeline_shared.py
git commit -m "feat(blackbox): BlackboxPipelineInput/ActivityInput 加 event_file 字段(C1 web 扫描前置)"
```

---

### Task 2: 黑盒 `setup_display` / `finalize_display` / `run_heartbeat` activity

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`（末尾加三个 activity）
- Test: `packages/blackbox/tests/test_c1_activities.py`（新建）

**Interfaces:**
- Consumes: `shannon_core.audit.session.AuditSession`、`shannon_core.audit.session_registry`（set/clear/get/NullAuditSession）、`shannon_core.logging.configure_logging`、`shannon_core.logging.log_bus.LogBus`、`shannon_core.runtime.heartbeat.HeartbeatManager`、`shannon_core.models.audit.WorkflowSummary`、`shannon_core.models.metrics.SessionMetadata`；Task 1 的 `BlackboxActivityInput.event_file`
- Produces: `setup_display(input)`、`finalize_display(input, summary)`、`run_heartbeat(input)` 三个 activity（供 Task 3 workflow 调用、Task 5 bb_worker 注册）

- [ ] **Step 1: 写失败测试**

新建 `packages/blackbox/tests/test_c1_activities.py`：

```python
import asyncio
import pytest


@pytest.mark.asyncio
async def test_setup_display_injects_audit_session_with_event_file(tmp_path):
    """setup_display 构造 headless AuditSession(event_file) + configure_logging + set_audit_session。
    仿白盒 test_setup_display_injects_audit_session_with_event_file。"""
    from shannon_blackbox.pipeline.activities import setup_display
    from shannon_blackbox.pipeline.shared import BlackboxActivityInput
    from shannon_core.audit.session_registry import clear_audit_session, get_audit_session
    from shannon_core.logging.log_bus import LogBus

    event_file = str(tmp_path / "events.ndjson")
    inp = BlackboxActivityInput(
        web_url="https://x", repo_path=str(tmp_path),
        workspace_path=str(tmp_path), workspace_name=tmp_path.name,
        event_file=event_file,
    )
    try:
        await setup_display(inp)
        session = get_audit_session()
        assert session is not None
        # WorkflowHeader 落 events.ndjson（session.initialize 触发）
        assert (tmp_path / "events.ndjson").exists()
    finally:
        await LogBus.drain_and_detach()
        clear_audit_session()


@pytest.mark.asyncio
async def test_run_heartbeat_writes_heartbeat_until_cancelled(tmp_path):
    """run_heartbeat 长驻写 heartbeat，cancel(CancelledError)退出。"""
    from shannon_blackbox.pipeline.activities import run_heartbeat
    from shannon_blackbox.pipeline.shared import BlackboxActivityInput

    inp = BlackboxActivityInput(web_url="https://x", repo_path=str(tmp_path), workspace_path=str(tmp_path))
    task = asyncio.create_task(run_heartbeat(inp))
    await asyncio.sleep(0.2)  # 让 heartbeat 初始写
    assert (tmp_path / "heartbeat").exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_finalize_display_drains_and_clears_session(tmp_path):
    """finalize_display: 先 setup_display 建会话, 再 finalize → drain + clear。
    NullAuditSession 兜底(无 session)不抛。"""
    from shannon_blackbox.pipeline.activities import setup_display, finalize_display
    from shannon_blackbox.pipeline.shared import BlackboxActivityInput
    from shannon_core.audit.session_registry import clear_audit_session, get_audit_session
    from shannon_core.audit.session_registry import NullAuditSession
    from shannon_core.logging.log_bus import LogBus

    inp = BlackboxActivityInput(
        web_url="https://x", repo_path=str(tmp_path),
        workspace_path=str(tmp_path), workspace_name=tmp_path.name,
        event_file=str(tmp_path / "events.ndjson"),
    )
    await setup_display(inp)
    await finalize_display(inp, {"status": "completed", "total_duration_ms": 100,
                                  "completed_agents": [], "agent_metrics": {}})
    # finalize 后 session 清空
    assert isinstance(get_audit_session(), NullAuditSession)


@pytest.mark.asyncio
async def test_finalize_display_no_session_is_noop():
    """无 session(NullAuditSession)时 finalize_display 不抛。"""
    from shannon_blackbox.pipeline.activities import finalize_display
    from shannon_blackbox.pipeline.shared import BlackboxActivityInput
    inp = BlackboxActivityInput(web_url="https://x")
    # 不调 setup_display, 直接 finalize —— 不应抛
    await finalize_display(inp, {"status": "failed"})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_c1_activities.py -xvs`
Expected: FAIL（`ImportError: cannot import name 'setup_display'`）。

- [ ] **Step 3: 实现**

在 `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 顶部 import 区加 `import asyncio`（若未有；现 :1 只有 `import time`）。在文件末尾追加三个 activity（逐行对齐白盒 `activities.py:1519-1612`，直连 core）：

```python
@activity.defn
async def setup_display(input: BlackboxActivityInput) -> None:
    """C1 前导 activity: 构造 headless AuditSession(event_file) + configure_logging + set_audit_session。

    仿白盒 setup_display(whitebox activities.py:1519)。黑盒 worker 容器无 TTY → use_rich=False。
    event_file 透传到 WorkflowLogger.initialize → 挂 StructuredEventRenderer 写 events.ndjson。
    """
    from rich.console import Console
    from shannon_core.models.metrics import SessionMetadata
    from shannon_core.logging import configure_logging
    from shannon_core.audit.session import AuditSession
    from shannon_core.audit.session_registry import set_audit_session
    from shannon_core.logging.log_bus import LogBus

    if input.workspace_path:
        ws_path = Path(input.workspace_path)
    else:
        ws_path = Path(input.repo_path).parent / "workspaces" / (input.workspace_name or "scan")
    meta = SessionMetadata(
        id=input.workspace_name or ws_path.name,
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(ws_path.parent),
    )
    configure_logging(log_dir=ws_path / "logs")
    console = Console()  # auto-detects non-TTY -> plain text
    session = AuditSession(meta, use_rich=False, console=console)
    await session.initialize(workflow_id=meta.id, event_file=input.event_file)
    await LogBus.attach(session.dispatcher)
    set_audit_session(session)


@activity.defn
async def run_heartbeat(input: BlackboxActivityInput) -> None:
    """C1 并行 long-running activity: 周期写 heartbeat(web 据其 mtime 判活)。

    仿白盒 run_heartbeat(whitebox activities.py:1562)。永阻塞, 靠 activity cancel 退出。
    """
    from shannon_core.runtime.heartbeat import HeartbeatManager
    ws_dir = Path(input.workspace_path) if input.workspace_path else Path(input.repo_path)
    mgr = HeartbeatManager(ws_dir, on_cancel=None)
    async with mgr:
        await asyncio.Event().wait()  # 永不 set; activity cancel 时 CancelledError 传出


@activity.defn
async def finalize_display(input: BlackboxActivityInput, summary: dict) -> None:
    """C1 后置 activity: drain_and_detach + log_workflow_complete(写 scan_end) + clear_audit_session。

    仿白盒 finalize_summary(whitebox activities.py:1579)。cost 取 session.get_metrics()
    (MetricsTracker 完整), 非 summary dict。无 session(NullAuditSession)时 no-op。
    """
    from shannon_core.audit.session_registry import (
        NullAuditSession, clear_audit_session, get_audit_session,
    )
    from shannon_core.logging.log_bus import LogBus
    from shannon_core.models.audit import WorkflowSummary

    session = get_audit_session()
    if not isinstance(session, NullAuditSession):
        final_metrics = await session.get_metrics() or {}
        ws = WorkflowSummary(
            status=summary.get("status", "failed"),
            total_duration_ms=summary.get("total_duration_ms", 0),
            total_cost_usd=final_metrics.get("total_cost_usd") or 0.0,
            cost_currency=final_metrics.get("cost_currency") or "USD",
            completed_agents=summary.get("completed_agents", []),
            agent_metrics=summary.get("agent_metrics", {}),
            error=summary.get("error"),
        )
        await LogBus.drain_and_detach()
        await session.log_workflow_complete(ws)
    clear_audit_session()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_c1_activities.py -xvs`
Expected: PASS（4 个测试）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/blackbox/src/shannon_blackbox/pipeline/activities.py packages/blackbox/tests/test_c1_activities.py
git commit -m "feat(blackbox): setup_display/finalize_display/run_heartbeat activity — 照抄白盒 C1"
```

---

### Task 3: `BlackboxScanWorkflow.run` worker 分支（`_prepare_inputs` + 前导 + finally finalize）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`（抽 `_prepare_inputs`、run 开头 worker 前导、finally 接 finalize_display）
- Test: `packages/blackbox/tests/test_workflows.py`

**Interfaces:**
- Consumes: Task 1 `event_file` 字段；Task 2 `setup_display`/`finalize_display`/`run_heartbeat`
- Produces: `BlackboxScanWorkflow.run` worker 路径（event_file 非 None）前导 setup_display+heartbeat、finally 调 finalize_display；模块级 `_prepare_inputs(input) -> tuple[bool, BlackboxActivityInput]`

- [ ] **Step 1: 写失败测试**

在 `packages/blackbox/tests/test_workflows.py` 末尾追加（测抽取的纯函数，对齐该文件轻量风格）：

```python
from shannon_blackbox.pipeline.workflows import _prepare_inputs


def test_prepare_inputs_worker_path_when_event_file_set(tmp_path):
    """event_file 非 None → is_worker_path=True, act_input 透传 event_file。"""
    inp = BlackboxPipelineInput(
        web_url="https://x", repo_path=str(tmp_path / "repo"),
        workspace_name="ws1", workspaces_root=str(tmp_path),
        event_file=str(tmp_path / "ws1" / "events.ndjson"),
    )
    is_worker, act_input = _prepare_inputs(inp)
    assert is_worker is True
    assert act_input.event_file == inp.event_file
    assert act_input.workspace_name == "ws1"
    assert act_input.workspace_path == str(tmp_path / "ws1")


def test_prepare_inputs_cli_path_when_event_file_none(tmp_path):
    """event_file None → is_worker_path=False（CLI 路径, run_with_display 外层已做）。"""
    inp = BlackboxPipelineInput(
        web_url="https://x", repo_path=str(tmp_path / "repo"),
        workspace_name="ws1", workspaces_root=str(tmp_path),
    )
    is_worker, act_input = _prepare_inputs(inp)
    assert is_worker is False
    assert act_input.event_file is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_workflows.py -xvs -k prepare_inputs`
Expected: FAIL（`ImportError: cannot import name '_prepare_inputs'`）。

- [ ] **Step 3a: 抽取 `_prepare_inputs`**

在 `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`，`BlackboxScanWorkflow` 类定义之前（`@workflow.defn` 之前）加模块级函数。把 run 现有 :74-99 的 ws_root/workspace_path/act_input 构造逻辑抽出（保留 run :74-78 的 workspaces_root 校验在 run 内）：

```python
def _prepare_inputs(input: BlackboxPipelineInput) -> tuple[bool, BlackboxActivityInput]:
    """算 is_worker_path + act_input(含 event_file)。从 run 抽出可单测。

    is_worker_path = event_file is not None(对齐白盒 workflows.py:54):
    worker 容器路径(web 提交)前导 setup_display/heartbeat; CLI 路径(run_with_display 外层
    已 set_audit_session)跳过, 避免双重 session。
    """
    ws_root = Path(input.workspaces_root) if input.workspaces_root else Path(input.repo_path).parent / "workspaces"
    if input.workspace_name:
        workspace_path = str(ws_root / input.workspace_name)
    else:
        workspace_path = input.repo_path
    is_worker_path = input.event_file is not None
    act_input = BlackboxActivityInput(
        web_url=input.web_url,
        repo_path=input.repo_path,
        config_path=input.config_path,
        workspace_name=input.workspace_name,
        deliverables_subdir=input.deliverables_subdir,
        pipeline_testing_mode=input.pipeline_testing_mode,
        api_key=input.api_key,
        workspace_path=workspace_path,
        correlated_workspace=input.correlated_workspace,
        event_file=input.event_file,
    )
    return is_worker_path, act_input
```

- [ ] **Step 3b: run 开头用 `_prepare_inputs` + worker 前导**

把 run 现有 :74-99（`ws_root = ...` 到 `act_input = BlackboxActivityInput(...)`）替换为：

```python
        if not input.workspaces_root:
            raise ValueError(
                "BlackboxPipelineInput.workspaces_root must be set before starting the "
                "workflow (sandbox cannot resolve it)."
            )
        is_worker_path, act_input = _prepare_inputs(input)

        # C1: worker 路径(web 提交, event_file 非 None)前导 setup_display + 并行 run_heartbeat。
        # CLI 路径(event_file None)跳过 —— run_with_display 外层已 set_audit_session。
        heartbeat_handle = None
        if is_worker_path:
            await workflow.execute_activity(
                activities.setup_display, act_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("log"),
            )
            heartbeat_handle = asyncio.create_task(workflow.execute_activity(
                activities.run_heartbeat, act_input,
                start_to_close_timeout=None,  # long-running, 靠 cancel 退出
                retry_policy=retry_for("log"),
            ))
```

（注意：`selected_classes` 那行 `selected_classes: list[str] = input.vuln_classes or list(ALL_VULN_CLASSES)` 保留在 `_prepare_inputs` 调用之后；run 原顺序里它在 :81，重构后放 `_prepare_inputs` 之后即可。）

- [ ] **Step 3c: finally 接 finalize_display**

在 run 现有 `finally`（`workflows.py:431`，`cleanup_settings()` 之后、`cleanup_engine_configs` 之前）加 finalize_display 调用。同时取消 heartbeat_handle。把 finally 改为：

```python
        finally:
            # C1 收尾: worker 路径才 finalize(CLI 路径 run_with_display 外层自管)。
            if is_worker_path:
                if heartbeat_handle is not None:
                    heartbeat_handle.cancel()
                total_duration_ms = int((workflow.time_ns() / 1e9 - self._state.start_time) * 1000)
                try:
                    await workflow.execute_activity(
                        activities.finalize_display, act_input,
                        {
                            "status": self._state.status,
                            "total_duration_ms": total_duration_ms,
                            "completed_agents": list(self._state.completed_agents),
                            "agent_metrics": dict(self._state.agent_metrics),
                            "error": self._state.errors[-1] if self._state.errors else None,
                        },
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=retry_for("log"),
                    )
                except Exception:
                    pass  # best-effort 收尾, 失败不阻断 cleanup
            cleanup_settings()
            if engine_name and input.repo_path:
                try:
                    await workflow.execute_activity(
                        activities.cleanup_engine_configs,
                        args=[input.repo_path, engine_name],
                        start_to_close_timeout=timedelta(seconds=15),
                        retry_policy=retry_for("log"),
                    )
                except Exception:
                    pass
            cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_workflows.py -xvs`
Expected: PASS（含原有 + 2 个新 `_prepare_inputs` 测试）。

- [ ] **Step 5: 回归 blackbox 现有 activity 测试**

Run: `cd /root/shannon-py && uv run pytest packages/blackbox/tests/test_c1_activities.py packages/blackbox/tests/test_pipeline_shared.py -xvs`
Expected: PASS（Task 1/2 不破）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_workflows.py
git commit -m "feat(blackbox): BlackboxScanWorkflow.run worker 分支 — 前导 setup_display/heartbeat + finally finalize_display"
```

---

### Task 4: `scan_manager._submit_blackbox`（替换 NotImplementedError）

**Files:**
- Modify: `packages/web/src/shannon_web/components/scan_manager.py`（:102-103 NotImplementedError → `_submit_blackbox`；仿 `_submit_whitebox` :115-137）
- Test: `packages/web/tests/test_scan_manager.py`（若无则新建）

**Interfaces:**
- Consumes: Task 1 `BlackboxPipelineInput.event_file`；`shannon_core.services.temporal_infra.WEB_TASK_QUEUE_BLACKBOX`
- Produces: web 发黑盒扫描 → 提交到 `WEB_TASK_QUEUE_BLACKBOX`（bb_worker 消费）

- [ ] **Step 1: 写失败测试**

在 `packages/web/tests/test_scan_manager.py` 末尾追加（复用该文件已有的 `_patch_temporal_ok` + `_patch_client` helper 与 `ScanManager` 构造模式，与 `test_start_submits_workflow_to_fixed_queue` 同构）：

```python
@pytest.mark.asyncio
async def test_submit_blackbox_starts_workflow_to_blackbox_queue(tmp_path, monkeypatch):
    """_submit_blackbox 提交 BlackboxScanWorkflow 到 WEB_TASK_QUEUE_BLACKBOX, PipelineInput 带 event_file。"""
    from types import SimpleNamespace
    from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow
    from shannon_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX

    mgr = ScanManager(tmp_path, tmp_path / "repos", None, max_concurrent=2)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)

    ws = "bb-ws"
    (tmp_path / ws).mkdir()
    (tmp_path / ws / "session.json").write_text("{}")  # _mark_submitted_at 读
    event_file = tmp_path / ws / "events.ndjson"
    req = SimpleNamespace(url="https://x", type="blackbox")
    await mgr._submit_blackbox(target=None, ws=ws, event_file=event_file, req=req)

    mock_client.start_workflow.assert_awaited_once()
    call = mock_client.start_workflow.call_args
    assert call.args[0] == BlackboxScanWorkflow.run
    assert call.kwargs["task_queue"] == WEB_TASK_QUEUE_BLACKBOX
    assert call.args[1].event_file == str(event_file)
    assert call.args[1].workspace_name == ws
    assert call.args[1].workspaces_root == str(tmp_path)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/web/tests/test_scan_manager.py -xvs -k submit_blackbox`
Expected: FAIL（`_submit_blackbox` 不存在）。

- [ ] **Step 3: 实现**

在 `packages/web/src/shannon_web/components/scan_manager.py`：

(a) 顶部 import 加 `BlackboxScanWorkflow` + `BlackboxPipelineInput` + `WEB_TASK_QUEUE_BLACKBOX`（对齐现有 whitebox import）：
```python
from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput
from shannon_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX, WEB_TASK_QUEUE_BLACKBOX,
)
```
（`WEB_TASK_QUEUE_WHITEBOX` 应已 import；只补 `WEB_TASK_QUEUE_BLACKBOX` 与两个 blackbox 类。）

(b) 替换 :102-103 的 `raise NotImplementedError(...)` 为调用 `_submit_blackbox`：
```python
            elif req.type == "blackbox":
                handle = await self._submit_blackbox(target, ws, event_file, req)
```

(c) 在 `_submit_whitebox` 方法之后（:137 后）加 `_submit_blackbox`（仿 `_submit_whitebox`）：
```python
    async def _submit_blackbox(self, target: str | None, ws: str,
                               event_file: Path, req: ScanRequest) -> Any:
        """提交 BlackboxScanWorkflow 到 WEB_TASK_QUEUE_BLACKBOX(bb_worker 消费)。

        仿 _submit_whitebox:event_file 塞 BlackboxPipelineInput(setup_display 据此挂
        StructuredEventRenderer); workspaces_root 传给 workflow(sandbox 内禁解析)。
        """
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws)
        inp = BlackboxPipelineInput(
            repo_path=target or "",
            web_url=req.url or "",
            workspace_name=ws,
            event_file=str(event_file),
            workspaces_root=str(self._workspaces_dir),
        )
        handle = await client.start_workflow(
            BlackboxScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_BLACKBOX,
        )
        self._mark_submitted_at(self._workspaces_dir / ws)
        return handle
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/web/tests/test_scan_manager.py -xvs -k submit_blackbox`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/web/src/shannon_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): scan_manager._submit_blackbox — 黑盒 web 扫描提交到 WEB_TASK_QUEUE_BLACKBOX"
```

---

### Task 5: bb_worker 注册 setup_display / finalize_display / run_heartbeat

**Files:**
- Modify: `packages/worker/src/shannon_worker/runner.py`（bb_worker activities 列表 :78-85 + import :33-41）
- Test: `packages/worker/tests/test_runner.py`

**Interfaces:**
- Consumes: Task 2 三个新 activity
- Produces: bb_worker 能调度黑盒 C1 activity（web 黑盒扫描端到端闭环）

- [ ] **Step 1: 写失败测试**

在 `packages/worker/tests/test_runner.py` 的 `test_run_worker_connects_and_registers_two_workers` 内（现有 `assert len(bb_call.kwargs["activities"]) >= 10` 之后）加断言：

```python
    from shannon_blackbox.pipeline.activities import (
        setup_display as bb_setup_display,
        finalize_display as bb_finalize_display,
        run_heartbeat as bb_run_heartbeat,
    )
    bb_acts = bb_call.kwargs["activities"]
    assert bb_setup_display in bb_acts, "bb_worker 缺 setup_display"
    assert bb_finalize_display in bb_acts, "bb_worker 缺 finalize_display"
    assert bb_run_heartbeat in bb_acts, "bb_worker 缺 run_heartbeat"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/worker/tests/test_runner.py::test_run_worker_connects_and_registers_two_workers -xvs`
Expected: FAIL（bb_worker activities 不含三 activity）。

- [ ] **Step 3: 实现**

在 `packages/worker/src/shannon_worker/runner.py`：

(a) 黑盒 activities import（:33-41 的 `from shannon_blackbox.pipeline.activities import (...)`）加三个：
```python
from shannon_blackbox.pipeline.activities import (
    run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
    run_exploit_agent, validate_exploitation_queue, assemble_report as bb_assemble_report,
    run_report_agent, finalize_report, generate_poc_report as bb_generate_poc_report,
    log_phase_start_activity as bb_log_phase_start, log_phase_complete_activity as bb_log_phase_complete,
    log_info_activity as bb_log_info, load_correlation_context, resolve_blackbox_engine,
    detect_whitebox_results, write_engine_config_for_session, cleanup_engine_configs,
    setup_display as bb_setup_display, finalize_display as bb_finalize_display,
    run_heartbeat as bb_run_heartbeat,
)
```

(b) bb_worker activities 列表（:78-85）加三个：
```python
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, validate_exploitation_queue, bb_assemble_report,
            run_report_agent, finalize_report, bb_generate_poc_report,
            bb_log_phase_start, bb_log_phase_complete, bb_log_info,
            load_correlation_context, resolve_blackbox_engine, detect_whitebox_results,
            write_engine_config_for_session, cleanup_engine_configs,
            bb_setup_display, bb_finalize_display, bb_run_heartbeat,
        ],
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/worker/tests/test_runner.py -xvs`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/worker/src/shannon_worker/runner.py packages/worker/tests/test_runner.py
git commit -m "feat(worker): bb_worker 注册 setup_display/finalize_display/run_heartbeat — 黑盒 C1 端到端闭环"
```

---

## Self-Review

**1. Spec 覆盖**（Plan B = spec 改动 4 的 6 项）：
- 4.1 Input event_file 字段 → Task 1 ✅
- 4.2 scan_manager._submit_blackbox → Task 4 ✅
- 4.3 BlackboxScanWorkflow.run worker 分支 → Task 3 ✅
- 4.4 新增三 activity（含 configure_logging） → Task 2 ✅
- 4.5 bb_worker 注册 + 并发=1 → Task 5（注册）✅；并发=1 已在 Plan A ✅
- 4.6 event_file 参数（worker）/env（CLI）双路径 → Task 1（字段）+ Task 3（透传）+ Task 4（提交传参）✅；CLI env 由 `wire_web_event_file`（blackbox worker.py:123，已存在）兜底 ✅

**2. 占位符扫描**：无 TBD/TODO；Task 4 测试已对齐 `test_scan_manager.py` 现有 `_patch_temporal_ok`/`_patch_client` helper + 真实 `ScanManager(tmp_path, tmp_path/"repos", None, max_concurrent=2)` 签名，无遗留提醒。

**3. 类型/签名一致性**：
- `_prepare_inputs -> tuple[bool, BlackboxActivityInput]`（Task 3 定义）↔ 测试 + run 调用一致 ✅
- 三 activity 签名（Task 2）↔ Task 3 workflow 调用、Task 5 注册一致 ✅
- `BlackboxActivityInput.event_file`（Task 1）↔ Task 2 setup_display `input.event_file`、Task 3 act_input 透传一致 ✅
- `WorkflowSummary` 字段（Task 2 finalize_display）↔ 白盒 finalize_summary（:1598-1606）一致 ✅

**4. 风险已标注**：is_worker_path 双重 session 规避；finalize 在 finally 覆盖 cancel/异常；黑盒 dataclass event_file 序列化（白盒已验证同模式）。

**5. 真机验证（plan 落地后手动）**：web 发黑盒扫描 → `workspaces/<ws>/events.ndjson` 产生 + live 页有事件 + scan_end 收尾 + heartbeat 新鲜（scan_liveness 不误判 interrupted）。
