# Graceful Shutdown 收尾实现计划（Remaining）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 Graceful Shutdown 的剩余工作——blackbox worker 接入协作式取消、三个 CLI 入口（whitebox/blackbox/combined）加 `cancelled → SystemExit(130)` 分支、combined orchestrator 在 whitebox 被取消时短路——使 Ctrl+C 在三个扫描入口都优雅退出（退出码 130），且不删 deliverables / workflow.log。

**Architecture:** `shannon_core.runtime.scan_runner` 的核心（`ScanCancelled`、`ShutdownController`、`poll_progress`、`await_workflow_with_shutdown`、`run_scan_graceful`）已在之前的 commit 完成。本计划只做收尾：blackbox worker 仿照 whitebox 现状接入 `await_workflow_with_shutdown`（保留其 display lifecycle）；三个 CLI 在 `completed` 分支前识别 `cancelled` 并 `SystemExit(130)`；combined orchestrator 在 whitebox `cancelled` 时短路、不跑 blackbox。

**Tech Stack:** Python 3、asyncio、temporalio（Python SDK）、click、pytest + pytest-asyncio（`asyncio_mode = "auto"`）、unittest.mock。

**关联文档:**
- 原始计划（Task 1-6 已完成）：[`2026-06-15-graceful-shutdown.md`](2026-06-15-graceful-shutdown.md)
- 设计 spec：[`../specs/2026-06-15-graceful-shutdown-design.md`](../specs/2026-06-15-graceful-shutdown-design.md)

---

## 背景与已完成范围

原计划 Task 1-6 **已在最近的 commit 中完成**，且实际实现比原计划更优（额外重构出 `await_workflow_with_shutdown`，并集成了 Rich display lifecycle）。**本计划不要重做这些**——原计划里 Task 6/7 的"把 worker.py 整体替换为简化版"会**删除**已实现的 display lifecycle，属于倒退，已废弃。

**已就位（不要改）：**
- `packages/core/src/shannon_core/runtime/scan_runner.py`：`ScanCancelled`、`ShutdownController`（SIGINT 双击 + SIGTERM 优雅）、`poll_progress`、`await_workflow_with_shutdown`、`run_scan_graceful`、`_do_cancel`。
- `packages/core/src/shannon_core/runtime/__init__.py`：已导出上述公共 API。
- `packages/core/tests/runtime/test_scan_runner.py`：完整单元测试。
- `packages/whitebox/src/shannon_whitebox/worker.py`：已接入 `await_workflow_with_shutdown`（含 display lifecycle）。

**真正剩余（本计划）：**
- blackbox worker 尚未接入（当前直接 `await handle.result()`，无 `ShutdownController`/`ScanCancelled`）。
- 三个 CLI 的 `start`/`scan` 只有 `completed` 分支；`cancelled` 会错误掉进失败分支（打印 "unknown error" + exit 1）。
- combined orchestrator 把 whitebox `cancelled` 当 `failed` 处理。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/blackbox/src/shannon_blackbox/worker.py` | `run_scan` 接入 `await_workflow_with_shutdown`；保留 display lifecycle | **Modify** |
| `packages/blackbox/tests/test_worker.py` | 现有两个测试补 `ShutdownController` patch；新增 cancelled 路径测试 | **Modify** |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | `start` 加 `cancelled` → `SystemExit(130)` | **Modify** |
| `packages/whitebox/tests/test_cli.py` | 新增 cancelled → 130 测试 | **Modify** |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | `start` 加 `cancelled` → `SystemExit(130)` | **Modify** |
| `packages/blackbox/tests/test_cli.py` | 新增 cancelled → 130 测试 | **Modify** |
| `packages/combined/src/shannon_combined/orchestrator.py` | whitebox 阶段 `cancelled` 短路 | **Modify** |
| `packages/combined/src/shannon_combined/cli/main.py` | `scan` 加 `cancelled` → `SystemExit(130)` | **Modify** |
| `packages/combined/tests/test_orchestrator.py` | 新增 whitebox cancelled 短路测试 | **Modify** |
| `packages/combined/tests/test_cli.py` | 新增 cancelled → 130 测试 | **Modify** |

**依赖方向约束**：`scan_runner` 在 `core`，blackbox worker 通过注入 `progress_type=None`（blackbox 用 Rich 仪表盘，不 poll）调用 `await_workflow_with_shutdown`，不引入新的 core→上层耦合。

---

## Task A: blackbox worker 接入 graceful shutdown（TDD）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`
- Modify: `packages/blackbox/tests/test_worker.py`

接入模式严格仿照已实现的 whitebox worker（见 `packages/whitebox/src/shannon_whitebox/worker.py`）：创建 `ShutdownController`、`install`/`uninstall` 生命周期包住 `async with worker`，把 `await handle.result()` 换成 `await await_workflow_with_shutdown(handle, ctrl, cancel_grace_seconds=15.0)`，`except ScanCancelled` 返回 `BlackboxPipelineState(status="cancelled")`。**保留** blackbox 现有的 display lifecycle（`run_with_display`、`_to_workflow_summary`、workflow-level failed-summary finalize、phase logging activities、`use_rich`）。

- [ ] **Step 1: 写 cancelled 路径失败测试**

在 `packages/blackbox/tests/test_worker.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_run_scan_returns_cancelled_on_scan_cancelled():
    """On user interrupt (ScanCancelled), run_scan returns
    BlackboxPipelineState(status='cancelled') and still clears the audit session."""
    from shannon_core.runtime.scan_runner import ScanCancelled
    import contextlib

    input = BlackboxPipelineInput(
        web_url="http://example.com",
        workspace_name="test-bb-cancel",
    )

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=AsyncMock())

    def capture_worker(**kwargs):
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    class FakeSession:
        log_workflow_complete = AsyncMock()
        log_error = AsyncMock()

    @contextlib.asynccontextmanager
    async def fake_display(meta, use_rich=False):
        yield FakeSession()

    with (
        patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)),
        patch("shannon_blackbox.worker.Worker", side_effect=capture_worker),
        patch("shannon_blackbox.worker.run_with_display", fake_display),
        patch("shannon_blackbox.worker.ShutdownController.install"),
        patch("shannon_blackbox.worker.ShutdownController.uninstall"),
        patch(
            "shannon_blackbox.worker.await_workflow_with_shutdown",
            AsyncMock(side_effect=ScanCancelled()),
        ),
        patch("shannon_blackbox.worker.clear_audit_session") as mock_clear,
    ):
        from shannon_blackbox.worker import run_scan
        result = await run_scan(input, "localhost:7233")

    assert result == BlackboxPipelineState(status="cancelled")
    mock_clear.assert_called()  # 清理在 cancel 路径仍执行
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/test_worker.py::test_run_scan_returns_cancelled_on_scan_cancelled -v`
Expected: FAIL（`patch("shannon_blackbox.worker.await_workflow_with_shutdown", ...)` 报 `AttributeError: <module 'shannon_blackbox.worker'> does not have the attribute 'await_workflow_with_shutdown'`，因为 worker 尚未 import 它）

- [ ] **Step 3: 接入 worker —— 加 import**

在 `packages/blackbox/src/shannon_blackbox/worker.py` 的 import 区，紧接现有的：

```python
from shannon_core.audit.display_lifecycle import run_with_display
from shannon_core.audit.session_registry import set_audit_session, clear_audit_session
```

之后，追加：

```python
from shannon_core.runtime.scan_runner import (
    ScanCancelled,
    ShutdownController,
    await_workflow_with_shutdown,
)
```

- [ ] **Step 4: 接入 worker —— 改写 `run_scan`**

把 `packages/blackbox/src/shannon_blackbox/worker.py` 中的整个 `run_scan` 函数（从 `async def run_scan(` 到它 `return result` 结束、即 `def main():` 之前）替换为：

```python
async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233",
                   use_rich: bool = False) -> BlackboxPipelineState:
    """跑黑盒扫描；Ctrl+C 时优雅取消并返回 BlackboxPipelineState(status="cancelled")。"""
    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[BlackboxScanWorkflow],
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, assemble_report, run_report_agent,
            log_phase_start_activity, log_phase_complete_activity,
        ],
    )

    meta = SessionMetadata(
        id=input.workspace_name or "blackbox-scan",
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(resolve_workspaces_dir(input.repo_path)),
    )

    ctrl = ShutdownController()
    ctrl.install(asyncio.get_running_loop())
    try:
        async with worker:
            async with run_with_display(meta, use_rich=use_rich) as session:
                set_audit_session(session)
                scan_start = time.monotonic()
                handle = await client.start_workflow(
                    BlackboxScanWorkflow.run,
                    input,
                    id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
                    task_queue=task_queue,
                )
                try:
                    result = await await_workflow_with_shutdown(
                        handle, ctrl, cancel_grace_seconds=15.0,
                    )
                except ScanCancelled:
                    return BlackboxPipelineState(status="cancelled")
                except Exception as e:
                    # Workflow-level failure: finalize the dashboard with a failed
                    # summary so the Live context closes cleanly, then re-raise so
                    # the CLI surfaces the error. (In-activity failures are already
                    # surfaced via log_error during the run; this covers workflow-
                    # level raises like browser-engine-unavailable / config-parse.)
                    await session.log_workflow_complete(_to_workflow_summary(
                        BlackboxPipelineState(status="failed", errors=[str(e)]),
                        int((time.monotonic() - scan_start) * 1000),
                    ))
                    raise
                finally:
                    clear_audit_session()

                total_duration_ms = int((time.monotonic() - scan_start) * 1000)
                await session.log_workflow_complete(_to_workflow_summary(result, total_duration_ms))
                return result
    finally:
        ctrl.uninstall()
```

> 关键：`except ScanCancelled` 必须在 `except Exception` **之前**（`ScanCancelled` 是 `Exception` 子类）。`progress_type` 不传 → `await_workflow_with_shutdown` 默认不 poll（blackbox 用 Rich 仪表盘），与 whitebox 一致。

- [ ] **Step 5: 更新现有两个 worker 测试，补 ShutdownController patch**

接入后 `run_scan` 会真实调用 `ctrl.install(...)`，需像 whitebox 测试那样把信号 handler 注册 patch 掉，保持测试纯净。

在 `packages/blackbox/tests/test_worker.py` 的 `test_run_scan_uses_dynamic_task_queue` 里，把：

```python
    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker):
```

改为：

```python
    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"):
```

在同一个文件的 `test_run_scan_emits_failed_summary_on_workflow_error` 里，把：

```python
    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.run_with_display", fake_display):
```

改为：

```python
    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.run_with_display", fake_display), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"):
```

> 这两个测试接入后仍应通过：`mock_handle.result` 立即完成 → `await_workflow_with_shutdown`（真实执行）走正常返回路径（task-queue 测试）或重新抛出 `boom` 被 `except Exception` 捕获 finalize（failure 测试）。补 patch 只为避免真实信号 handler 注册。

- [ ] **Step 6: 运行 worker 测试确认通过**

Run: `cd packages/blackbox && python -m pytest tests/test_worker.py -v`
Expected: PASS（3 个测试全绿：task-queue、failed-summary、cancelled）

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py packages/blackbox/tests/test_worker.py
git commit -m "refactor(blackbox): run_scan adopts await_workflow_with_shutdown for graceful cancel"
```

> **不跑 blackbox 全量 `tests/`**：本包全量含集成级慢用例，会长时间挂起；改动仅触及 `worker.py`，已由 Step 6 的 `test_worker.py`（3 个测试：task-queue / failed-summary / cancelled）完全覆盖。

---

## Task B: whitebox CLI `start` 加 cancelled 分支（TDD）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`（`start` 命令，`:52-53`）
- Modify: `packages/whitebox/tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_cli.py` 末尾追加：

```python
def test_start_exits_130_on_cancelled():
    """When the scan is cancelled, the CLI should print a message and exit 130."""
    with (
        patch("shannon_whitebox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch(
            "shannon_whitebox.worker.run_scan",
            new=AsyncMock(return_value={"status": "cancelled"}),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output
```

> `ensure_infra` 是顶层导入（`cli/main.py` 顶部 `from shannon_core.services.temporal_infra import ensure_infra`）；`ensure_prerequisite` 是 `start()` 内局部导入（`from shannon_core.runtime.prerequisites import ensure_prerequisite`），故 patch 其源头模块；`run_scan` 是 `start()` 内局部导入，patch `shannon_whitebox.worker.run_scan`。用 `AsyncMock(return_value=...)` 避免真实签名的 `use_rich` 参数不匹配。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/whitebox && python -m pytest tests/test_cli.py::test_start_exits_130_on_cancelled -v`
Expected: FAIL（`result.exit_code` 为 1——cancelled 当前掉进 else 分支打印 "Scan failed: unknown error"；且输出无 "Scan cancelled."）

- [ ] **Step 3: 加 cancelled 分支**

定位 `packages/whitebox/src/shannon_whitebox/cli/main.py` 的：

```python
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    if result.get("status") == "completed":
```

改为：

```python
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    if result.get("status") == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.get("status") == "completed":
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/whitebox && python -m pytest tests/test_cli.py::test_start_exits_130_on_cancelled -v`
Expected: PASS（exit_code 130）

- [ ] **Step 5: 跑 whitebox `start` 相关测试确认不回归**

Run: `cd packages/whitebox && python -m pytest tests/test_cli.py -k start -v`
Expected: PASS（仅 `start` 相关用例：新增 cancelled + 修好的 4 个 fake；避开 logs/workspace 等慢用例）

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox-cli): start exits 130 on cancelled scan"
```

---

## Task C: blackbox CLI `start` 加 cancelled 分支（TDD）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`（`start` 命令，`:133-134`）
- Modify: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

在 `packages/blackbox/tests/test_cli.py` 末尾追加（仿现有 `test_start_shows_error_on_failure` 风格）：

```python
def test_start_exits_130_on_cancelled():
    """When the scan is cancelled, the CLI should print a message and exit 130."""
    async def fake_run_scan(input, temporal_address, use_rich=False):
        return BlackboxPipelineState(status="cancelled")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_blackbox.cli.main.find_workspaces_by_url", return_value=[]),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output
```

> blackbox `start` 在无 `-w`/`--latest` 时会 auto-detect（`find_workspaces_by_url`），patch 成空列表避免读真实 workspaces 或卡在 confirm prompt。`result.status` 是属性（`BlackboxPipelineState` 是 dataclass）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/test_cli.py::test_start_exits_130_on_cancelled -v`
Expected: FAIL（`result.exit_code` 为 1——cancelled 当前掉进 else 分支）

- [ ] **Step 3: 加 cancelled 分支**

定位 `packages/blackbox/src/shannon_blackbox/cli/main.py` 的：

```python
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    if result.status == "completed":
```

改为：

```python
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    if result.status == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.status == "completed":
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd packages/blackbox && python -m pytest tests/test_cli.py::test_start_exits_130_on_cancelled -v`
Expected: PASS（exit_code 130）

- [ ] **Step 5: 跑 blackbox `start` 相关测试确认不回归**

Run: `cd packages/blackbox && python -m pytest tests/test_cli.py -k start -v`
Expected: PASS（仅 `start` 相关用例；避开慢用例）

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox-cli): start exits 130 on cancelled scan"
```

---

## Task D: combined orchestrator 短路 + combined CLI cancelled 分支（TDD）

**Files:**
- Modify: `packages/combined/src/shannon_combined/orchestrator.py`（`run_combined_scan`，`:38-45`）
- Modify: `packages/combined/src/shannon_combined/cli/main.py`（`scan` 命令，`:43`）
- Modify: `packages/combined/tests/test_orchestrator.py`
- Modify: `packages/combined/tests/test_cli.py`

- [ ] **Step 1: 写 orchestrator 短路失败测试**

在 `packages/combined/tests/test_orchestrator.py` 末尾追加（仿现有 `test_run_combined_scan_stops_on_whitebox_failure`）：

```python
@pytest.mark.asyncio
async def test_whitebox_cancelled_short_circuits_before_blackbox():
    """If whitebox is cancelled, the combined scan returns cancelled and
    does not run blackbox."""
    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value={"status": "cancelled"}) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock) as mock_bb,
    ):
        result = await run_combined_scan(
            repo_path="/data/repos/myrepo",
            url="https://example.com",
            temporal_address="localhost:7233",
        )

    mock_wb.assert_called_once()
    mock_bb.assert_not_called()  # 关键：blackbox 阶段未执行
    assert result == {"status": "cancelled", "phase": "whitebox"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd packages/combined && python -m pytest tests/test_orchestrator.py::test_whitebox_cancelled_short_circuits_before_blackbox -v`
Expected: FAIL（当前 whitebox `cancelled` 被当 `failed`：`result == {"status": "failed", "phase": "whitebox", "error": "whitebox scan failed"}`，且 mock_bb 仍 `not_called` 所以 `mock_bb.assert_not_called()` 通过——失败点在 status 不匹配）

- [ ] **Step 3: orchestrator 加 cancelled 短路**

定位 `packages/combined/src/shannon_combined/orchestrator.py` 的：

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

> blackbox 阶段 `cancelled` 无需在此特殊处理：blackbox worker 接入后（Task A）`ScanCancelled` → `BlackboxPipelineState(status="cancelled")`，下方既有的 `asdict` 逻辑（`:67-71`）会把它转成 `{"status": "cancelled", ...}`，combined CLI 的 cancelled 分支捕获。

- [ ] **Step 4: 运行 orchestrator 测试确认通过**

Run: `cd packages/combined && python -m pytest tests/test_orchestrator.py -v`
Expected: PASS（3 个测试：calls-then、stops-on-failure、cancelled-short-circuit）

- [ ] **Step 5: 写 combined CLI cancelled 失败测试**

在 `packages/combined/tests/test_cli.py` 末尾追加（仿现有 `test_scan_calls_orchestrator`）：

```python
def test_scan_exits_130_on_cancelled():
    """When the combined scan is cancelled, the CLI should print a message and exit 130."""
    async def fake_combined(*args, **kwargs):
        return {"status": "cancelled", "phase": "whitebox"}

    with (
        patch("shannon_combined.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_combined.orchestrator.run_combined_scan", side_effect=fake_combined),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--repo", "/tmp/repo", "--url", "https://example.com"])

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output
```

- [ ] **Step 6: 运行测试确认失败**

Run: `cd packages/combined && python -m pytest tests/test_cli.py::test_scan_exits_130_on_cancelled -v`
Expected: FAIL（`result.exit_code` 为 1——cancelled 当前掉进 else 分支）

- [ ] **Step 7: combined CLI 加 cancelled 分支**

定位 `packages/combined/src/shannon_combined/cli/main.py` 的：

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

- [ ] **Step 8: Commit**

```bash
git add packages/combined/src/shannon_combined/orchestrator.py packages/combined/src/shannon_combined/cli/main.py packages/combined/tests/test_orchestrator.py packages/combined/tests/test_cli.py
git commit -m "feat(combined): short-circuit on cancelled sub-scan; scan exits 130"
```

> **不跑 combined 全量 `tests/`**：全量含集成级慢用例；改动触及 `orchestrator.py` + `cli/main.py`，已由 Step 4 的 `test_orchestrator.py` 与 Step 6-7 的 `test_cli.py` 覆盖。

---

## Task E: 端到端手动验证

> 这一步需要本地 Temporal server（`shannon-whitebox infra up`）和一个可扫描的小仓库。自动化集成测试需要真实 Temporal 环境，超出单测范围；改为结构化手动 checklist。覆盖 whitebox + blackbox + combined 三个入口。

**Files:** 无（仅运行验证）

- [ ] **Step 1: 起基础设施**

Run: `shannon-whitebox infra up`，等到 "Temporal is ready!"。

- [ ] **Step 2: whitebox 扫描，进度出现后按一次 Ctrl+C**

Run: `shannon-whitebox start -r <小仓库路径> -w ws-e2e --pipeline-testing`

等到看到进度（Rich 仪表盘或 `[Ns] Phase: ...`）后，按 **一次** Ctrl+C。

Expected:
- 终端打印 `正在优雅取消…（再按一次 Ctrl+C 立即退出）` 与 `正在取消 Temporal workflow…`
- **不**出现 `CancelledError` / gRPC channel 关闭的堆栈刷屏
- 最终打印 `Scan cancelled.`
- `echo $?` → **130**

- [ ] **Step 3: blackbox 扫描，进度出现后按一次 Ctrl+C**

Run: `shannon-blackbox start --url <目标URL> --pipeline-testing`

进度出现后按一次 Ctrl+C。

Expected:
- 同 Step 2 的优雅取消输出（dashboard 干净收尾）
- `Scan cancelled.`
- `echo $?` → **130**

- [ ] **Step 4: combined 扫描，whitebox 阶段按一次 Ctrl+C**

Run: `shannon-combined scan -r <小仓库路径> -u <目标URL> --pipeline-testing`

在 whitebox 阶段进度出现后按一次 Ctrl+C。

Expected:
- 优雅取消（不跑 blackbox）
- `Scan cancelled.`
- `echo $?` → **130**

- [ ] **Step 5: 验证 server 端 workflow 已停**

Run: `docker compose logs temporal | tail -20`（或 temporal ui）确认对应 workflow 状态为 cancelled，非 running。

- [ ] **Step 6: 验证 deliverables / log 未被删**

Run: `ls workspaces/ws-e2e/ && ls workspaces/ws-e2e/deliverables/ 2>/dev/null; tail -5 workspaces/ws-e2e/workflow.log`

Expected: workspace 目录、已生成的 deliverables 文件、workflow.log 均存在；log 含已执行 activity 的记录。

- [ ] **Step 7: 验证子进程已清理**

Run: `pgrep -fl gitnexus; pgrep -fl playwright`

Expected: 无残留的 gitnexus / playwright 进程（或仅与本次无关的）。

- [ ] **Step 8: 验证第二次 Ctrl+C 强制退出**

重跑 Step 2 的 whitebox scan，进度出现后**连按两次** Ctrl+C。

Expected: 第一次打印 `正在优雅取消…`，第二次打印 `强制退出`，立即终止（不等 cancel 完成）；`echo $?` → **130**。

- [ ] **Step 9: 记录验证结果**

在 PR 描述或提交说明里贴出 Step 2-8 的观察结果。

---

## Self-Review（已执行）

**1. Spec 覆盖（剩余部分）：**
- blackbox 全链路取消 → Task A（接入 `await_workflow_with_shutdown`）。
- 三个扫描入口的退出码 130 → Task B（whitebox cli）、Task C（blackbox cli）、Task D（combined cli）。
- combined whitebox 阶段 cancelled 短路 → Task D orchestrator。
- blackbox 阶段 cancelled → 由 Task A（worker 返回 `BlackboxPipelineState(status="cancelled")`）+ orchestrator 既有的 `asdict`（不改）+ combined CLI cancelled 分支（Task D）自动覆盖，无需额外 orchestrator 逻辑。
- 双击语义 / 退出码 130 / 强制退出 → 已在 scan_runner（Task 1）实现；Task E Step 8 手动验证。
- 不删 deliverables/log → scan_runner 设计本身不动文件；Task E Step 6 验证。
- 子进程/临时配置清理依赖 workflow 已有 finally → Task E Step 7 验证。

**2. Placeholder 扫描**：无 TODO/TBD；每步含完整代码或确切命令与期望输出。

**3. 类型一致性：**
- blackbox worker 接入用 `await_workflow_with_shutdown(handle, ctrl, cancel_grace_seconds=15.0)`，签名与 `scan_runner.py:102` 一致（`progress_type` 默认 None）。
- `ScanCancelled` 从 `shannon_core.runtime.scan_runner` import，与 scan_runner 定义一致。
- blackbox cancelled payload 用 `BlackboxPipelineState(status="cancelled")`（Task A），与 blackbox CLI 属性访问 `result.status`（Task C）及 orchestrator `asdict`（Task D 注释）一致。
- whitebox CLI 用 `result.get("status")`（dict，worker 返回 `{"status": "cancelled"}`）；blackbox CLI 用 `result.status`（dataclass）——与各自 worker 返回类型一致。
- combined orchestrator 短路返回 `{"status": "cancelled", "phase": "whitebox"}`（Task D），combined CLI 测试断言同结构（Task D Step 5）。

**4. 回归风险**：
- Task A Step 5 显式更新 blackbox 两个现有 worker 测试补 patch；回归由 `test_worker.py`（Step 6，3 测试）覆盖——**不跑 blackbox 全量**（含集成级慢用例，且改动仅触及 `worker.py`）。
- Task B/C 的 Step 5 只跑各自 `test_cli.py -k start`（改动触及的 `start` 命令相关用例，避开慢用例）；Task D 由 `test_orchestrator.py`（Step 4，3 个用例全测被改函数）+ `test_cli.py`（Step 6-7）覆盖——**不跑任何包的全量**。
