# session-status 同步修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** temporal workflow 进入 FAILED / cancelled 时,把 `session.json` 顶层 `status` 同步成 `failed` / `cancelled`,Web 实时页不再幽灵卡住。

**Architecture:** 三层 defense-in-depth —— (1) workflow 本源 `except Exception` 调 `finalize_summary` 写终态;(2) CLI worker `except Exception`/`ScanCancelled` 兜底;(3) Web `scan_manager._watch` 周期 `handle.describe()` 轮询,workflow FAILED 时自行落盘。一条 Temporal 铁律:`except Exception` 块内可 `execute_activity`,`except CancelledError`/cancellation 上下文不可 —— 故 cancelled 不靠 workflow,靠 web `_mark_cancelled` + CLI worker 兜底。

**Tech Stack:** Python 3.12 / temporalio / pytest / pydantic。后端测试 `uv run pytest`(只跑改动相关子集,勿广跑全套,见 memory `pytest-whitebox-hang`)。

## Global Constraints

- **分支**:`feat/fork-py`(本地多项未 push;动代码前看 git log)
- **铁律(CLAUDE.md §1)**:本修复不改 LLM 轨 prompt、不喂确定性产物给 LLM 轨;`status` 落盘只走 session/audit 通道
- **status 枚举**:`failed` / `cancelled` / `interrupted` 三态语义见 spec §5;`reconcile_orphaned` 保持 `interrupted` 不动
- **数据**:不碰历史 workspace / temporal 僵尸 workflow(纯改代码)
- **测试陷阱(memory)**:pytest 只跑改动相关文件;worker 测试传 workspace_name 必设 `SHANNON_WORKER_ROOT=tmp_path`;Rich markup 测试时间戳用真实格式
- **YAGNI**:不动 `reconcile_orphaned`、不动 blackbox web C1 化、不改 `scan_timeout` 默认值

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | whitebox workflow 主流程 | 加 `except Exception` 分支:调 `finalize_summary(status="failed")` + raise |
| `packages/whitebox/src/shannon_whitebox/worker.py` | CLI 入口 | 内层 try 加 `except Exception` + `except ScanCancelled` 落盘(抄 blackbox) |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | blackbox workflow 主流程 | 加 `except Exception` 分支:设 state + return(不调 activity) |
| `packages/web/src/shannon_web/components/scan_manager.py` | web 扫描提交 + `_watch` | `_watch` 加周期 `handle.describe()` 轮询;`_write_scan_end` 加 `session_status` 参数同步落盘 |
| `packages/whitebox/tests/test_session_status_sync.py` | 新建:whitebox workflow + worker 锚点 | AST 锚点测 |
| `packages/blackbox/tests/test_session_status_sync.py` | 新建:blackbox workflow 锚点 | AST 锚点测 |
| `packages/web/tests/test_scan_manager_session_status.py` | 新建:`_watch` describe 单测 | mock describe 返 FAILED |

---

## Task 1: whitebox workflow 加 `except Exception` 分支(本源修复)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:629-646`
- Test: `packages/whitebox/tests/test_session_status_sync.py` (Create)

**Interfaces:**
- Consumes: `activities.finalize_summary(input, summary)`(已定义于 `activities.py:1722`);`is_worker_path`、`heartbeat_handle`、`act_input`、`self._state`(workflow run 内已有变量)
- Produces: workflow `except Exception` 分支存在性(被 Task 1 测试锁定),workflow FAILED 时 `finalize_summary` 被调 → session.status=failed

- [ ] **Step 1: 写失败测试(AST 锚点)**

Create `packages/whitebox/tests/test_session_status_sync.py`:

```python
"""session-status 同步回归锚点:workflow FAILED 时必须经 finalize_summary 写 session.status=failed.

根因:WhiteboxScanWorkflow.run 旧版无 except Exception 分支,workflow raise(如 GitNexus
fail-fast ApplicationFailure / activity retry 耗尽)→ finalize_summary 永不被调 →
session.json status 永远 running → Web 幽灵卡住。
"""
import inspect
from pathlib import Path


def _wf_src() -> str:
    from shannon_whitebox.pipeline import workflows
    return inspect.getsource(workflows)


def test_workflow_has_except_exception_branch():
    """workflow.run 必须有 except Exception 分支(在 except CancelledError 之后、finally 之前)."""
    src = _wf_src()
    assert "except Exception as e:" in src, (
        "workflow 必须有 'except Exception as e:' 分支捕获 workflow-level 失败")
    ce = src.find("except CancelledError")
    ee = src.find("except Exception as e:")
    fin = src.find("finally:", ee)
    assert ce != -1 and ee != -1, "CancelledError 与 Exception 分支都应存在"
    assert ce < ee, (
        f"except Exception 必须在 except CancelledError 之后(通用分支在后), "
        f"实际 CancelledError={ce}, Exception={ee}")


def test_workflow_except_exception_calls_finalize_summary():
    """except Exception 分支(worker_path)必须调 finalize_summary 写 failed 终态."""
    src = _wf_src()
    ee = src.find("except Exception as e:")
    assert ee != -1, "先要有 except Exception 分支(test_workflow_has_except_exception_branch)"
    # 从 except Exception 到 finally 之间的片段
    fin = src.find("finally:", ee)
    branch = src[ee:fin if fin != -1 else len(src)]
    assert "finalize_summary" in branch, (
        "except Exception 分支(worker_path)必须调 activities.finalize_summary 写 session.status=failed")
    assert '"failed"' in branch or "'failed'" in branch, (
        "except Exception 分支构造的 summary status 必须是 'failed'")


def test_workflow_except_exception_reraises():
    """except Exception 分支末尾必须 raise(让 Temporal 标 FAILED,供 web _watch describe 兜底)."""
    src = _wf_src()
    ee = src.find("except Exception as e:")
    fin = src.find("finally:", ee)
    branch = src[ee:fin if fin != -1 else len(src)]
    # 分支内必含裸 raise(重抛捕获的异常)
    assert "\n        raise\n" in branch or "\n            raise\n" in branch, (
        "except Exception 分支末尾必须裸 raise,让 workflow FAILED 供 web describe 兜底")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_session_status_sync.py -x -v`
Expected: 3 个测试 FAIL(`except Exception as e:` not in source)

- [ ] **Step 3: 实现 `except Exception` 分支**

Modify `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`。在 `except CancelledError:` 块(line 630-638)之后、`finally:`(line 639)之前,插入新分支。改动后 line 629-668 区间应为:

```python
            self._state.current_phase = None
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            if heartbeat_handle is not None:
                try:
                    heartbeat_handle.cancel()
                except Exception:
                    pass
            self._state.current_phase = None
            return self._state
        except Exception as e:
            # session-status 同步:workflow-level 失败(GitNexus fail-fast ApplicationFailure /
            # activity retry 耗尽 / 任何未捕获异常)→ finalize_summary 写 session.status=failed +
            # scan_end,再 raise 让 Temporal 标 FAILED(web _watch describe 兜底依赖此信号)。
            self._state.status = "failed"
            if not self._state.errors:
                self._state.errors.append(f"{type(e).__name__}: {e}")
            if is_worker_path:
                if heartbeat_handle is not None:
                    try:
                        heartbeat_handle.cancel()
                    except Exception:
                        pass
                from shannon_core.models.audit import AgentMetricsSummary
                summary = {
                    "status": "failed",
                    "total_duration_ms": int((workflow.time_ns() / 1e9 - self._state.start_time) * 1000),
                    "total_cost_usd": sum((m.get("cost_usd") or 0.0) for m in self._state.agent_metrics.values()),
                    "completed_agents": list(self._state.completed_agents),
                    "agent_metrics": {
                        name: AgentMetricsSummary(
                            duration_ms=int(m.get("duration_ms", 0) or 0),
                            cost_usd=m.get("cost_usd"),
                        )
                        for name, m in self._state.agent_metrics.items()
                    },
                    "error": (self._state.errors[0] if self._state.errors else str(e)),
                }
                try:
                    await workflow.execute_activity(
                        activities.finalize_summary, args=[act_input, summary],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=retry_for("standard"),
                    )
                except Exception:
                    pass  # finalize 自身失败不掩盖原异常;workflow 仍 FAILED,web _watch describe 兜底
            self._state.current_phase = None
            raise
        finally:
            if heartbeat_handle is not None:
                try:
                    heartbeat_handle.cancel()
                except Exception:
                    pass
            cleanup_settings()
            cleanup_auth_state_sync(workspace_path)
```

注意:`is_worker_path`、`heartbeat_handle`、`act_input`、`self._state`、`activities`、`workflow`、`timedelta`、`retry_for` 都是 `run()` 内已有变量/已 import,无需新增 import。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_session_status_sync.py -x -v`
Expected: 3 PASS

回归(确认不破现有 fail-fast / workflow 测试):
Run: `uv run pytest packages/whitebox/tests/test_workflow_gitnexus_failfast.py -x -v`
Expected: PASS(原锚点不破)

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_session_status_sync.py
git commit -m "fix(whitebox): workflow except Exception 调 finalize_summary 写 session.status=failed

根因:workflow 无 except Exception 分支,raise 时 finalize_summary 永不调 → session.json
status 永远 running → Web 幽灵卡住。本源修复:捕获后 worker_path 调 finalize(failed)+ raise。"
```

---

## Task 2: whitebox CLI worker 加 `except Exception` / `ScanCancelled` 兜底

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py:352-358`
- Test: `packages/whitebox/tests/test_session_status_sync.py` (append)

**Interfaces:**
- Consumes: `_build_final_summary(result, session, scan_start)`(`worker.py:110`,接受 `PipelineState`);`PipelineState`(import 自 `shannon_whitebox.pipeline.shared`);`session.log_workflow_complete(summary)`
- Produces: CLI 路径 workflow FAILED / cancelled 时 session 落盘

- [ ] **Step 1: 追加失败测试(AST 锚点)**

Append to `packages/whitebox/tests/test_session_status_sync.py`:

```python
def test_worker_has_except_exception_after_scancancelled():
    """whitebox CLI worker.py 内层 try 必须有 except Exception 兜底(抄 blackbox worker.py:201-211)."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    sc = src.find("except ScanCancelled:")
    assert sc != -1, "worker.py 须有 except ScanCancelled"
    ee = src.find("except Exception as e:", sc)
    assert ee != -1 and ee < src.find("finally:", sc), (
        "except Exception 必须紧跟 except ScanCancelled 之后(在 finally 之前),抄 blackbox 兜底模式")


def test_worker_except_branch_logs_failed_summary():
    """except Exception 分支必须调 session.log_workflow_complete 写 failed summary."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    sc = src.find("except ScanCancelled:")
    ee = src.find("except Exception as e:", sc)
    fin = src.find("finally:", ee)
    branch = src[ee:fin]
    assert "log_workflow_complete" in branch, (
        "except Exception 分支必须调 session.log_workflow_complete 落盘 failed 终态")
    assert "_build_final_summary" in branch, (
        "必须经 _build_final_summary 构造 summary(DRY,复用 cost/duration 数据源)")


def test_worker_scancancelled_logs_cancelled_summary():
    """except ScanCancelled 分支也必须落盘 cancelled(原版只 return,session 永远 running)."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    sc = src.find("except ScanCancelled:")
    ee = src.find("except Exception as e:", sc)
    branch = src[sc:ee]
    assert "log_workflow_complete" in branch, (
        "except ScanCancelled 必须调 session.log_workflow_complete 落盘 cancelled 终态")


def test_worker_imports_pipeline_state():
    """worker.py 必须能构造 PipelineState(failed/cancelled summary 需要它)."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    assert "PipelineState" in src, (
        "worker.py 必须引用 PipelineState(构造 failed/cancelled state 传给 _build_final_summary)")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/whitebox/tests/test_session_status_sync.py -x -v -k "worker"`
Expected: 4 个新测试 FAIL(`except Exception as e:` not found after ScanCancelled 等)

- [ ] **Step 3: 实现 CLI 兜底**

Modify `packages/whitebox/src/shannon_whitebox/worker.py`。先确认文件顶部已 import `PipelineState`;若无,在现有 `from shannon_whitebox.pipeline.shared import ...` 行补 `PipelineState`(若该 import 行不存在则加 `from shannon_whitebox.pipeline.shared import PipelineState`)。

改 line 352-358 区间(内层 try)。改动后:

```python
                    try:
                        # progress_type=None: Rich 仪表盘已负责进度展示，不重复 poll 打印。
                        result = await await_workflow_with_shutdown(
                            handle, ctrl, cancel_grace_seconds=15.0,
                        )
                    except ScanCancelled:
                        # session-status 同步:cancelled 落盘(原版只 return → session 永远 running)。
                        await session.log_workflow_complete(
                            await _build_final_summary(
                                PipelineState(status="cancelled"),
                                session, scan_start))
                        return {"status": "cancelled"}
                    except Exception as e:
                        # session-status 同步:workflow FAILED 兜底(workflow except 没跑到的场景,
                        # 如被 terminate / sandbox 崩)。抄 blackbox worker.py:201-211。
                        await session.log_workflow_complete(
                            await _build_final_summary(
                                PipelineState(status="failed", errors=[str(e)]),
                                session, scan_start))
                        raise
                finally:
                    clear_audit_session()
```

说明:`_build_final_summary` 已处理 `result.status` 不在合法集合时回落 `"failed"`(`worker.py:123-127`),传 `PipelineState(status="cancelled")` 会正确产出 `status="cancelled"` 的 summary;`completed_agents` 默认 `[]`、`agent_metrics` 默认 `{}`(shared.py:29-30),cost 仍从 `session.get_metrics()` 取(完整)。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/whitebox/tests/test_session_status_sync.py -x -v`
Expected: 全 PASS(含 Task 1 的 3 个 + 本 Task 的 4 个)

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_session_status_sync.py
git commit -m "fix(whitebox): CLI worker except Exception/ScanCancelled 兜底落盘 session 终态

抄 blackbox worker.py 模式;ScanCancelled 也补 log_workflow_complete(cancelled),
原版只 return 致 session 永远 running。"
```

---

## Task 3: blackbox workflow 加 `except Exception` 分支(防御性对齐)

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:426-430`
- Test: `packages/blackbox/tests/test_session_status_sync.py` (Create)

**Interfaces:**
- Consumes: `self._state`(`BlackboxPipelineState`,已有 `status`/`errors` 字段)
- Produces: blackbox workflow FAILED 时 state.status=failed(CLI worker.py:201-211 已据 state 写 session,故 session 自动落盘)

- [ ] **Step 1: 写失败测试(AST 锚点)**

Create `packages/blackbox/tests/test_session_status_sync.py`:

```python
"""session-status 同步:blackbox workflow 也要有 except Exception 分支(防御性对齐 whitebox).

blackbox CLI worker.py:201-211 已有 except Exception 兜底(写 session.failed),但 workflow
本身无 except Exception → state.status 不被设为 failed → 兜底 summary 的 status 回落逻辑
依赖 worker 层。本任务让 workflow 本源也设 state.status=failed,对齐 whitebox。
"""
import inspect


def _wf_src() -> str:
    from shannon_blackbox.pipeline import workflows
    return inspect.getsource(workflows)


def test_blackbox_workflow_has_except_exception_branch():
    """blackbox workflow.run 必须有 except Exception 分支(在 except CancelledError 之后)."""
    src = _wf_src()
    assert "except Exception as e:" in src, (
        "blackbox workflow 必须有 'except Exception as e:' 分支(对齐 whitebox)")


def test_blackbox_workflow_except_branch_sets_failed_state():
    """except Exception 分支必须设 state.status='failed' + return self._state(不调 activity)."""
    src = _wf_src()
    ee = src.find("except Exception as e:")
    assert ee != -1
    fin = src.find("finally:", ee)
    branch = src[ee:fin if fin != -1 else len(src)]
    assert '"failed"' in branch or "'failed'" in branch, (
        "except Exception 分支必须设 state.status='failed'")
    assert "return self._state" in branch, (
        "except Exception 分支必须 return self._state(让 CLI worker 拿到 failed state)")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/blackbox/tests/test_session_status_sync.py -x -v`
Expected: 2 FAIL

- [ ] **Step 3: 实现 `except Exception` 分支**

Modify `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`。在 `except CancelledError:`(line 427-430)之后、`finally:`(line 431)之前插入。改动后 line 426-432 区间:

```python
            self._state.current_phase = None
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            self._state.current_phase = None
            return self._state
        except Exception as e:
            # session-status 同步(对齐 whitebox):workflow-level 失败 → state.status=failed。
            # 不调 finalize_activity(规避 finalize_report 签名依赖);session 落盘靠
            # blackbox CLI worker.py:201-211 的 except Exception 兜底(已存在)。
            self._state.status = "failed"
            if not self._state.errors:
                self._state.errors.append(f"{type(e).__name__}: {e}")
            self._state.current_phase = None
            return self._state
        finally:
            cleanup_settings()
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/blackbox/tests/test_session_status_sync.py -x -v`
Expected: 2 PASS

回归:`uv run pytest packages/blackbox/tests/ -k "workflow" -x -v`(只跑 workflow 相关,避免全套 hang)Expected: 原有 PASS 不破

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py packages/blackbox/tests/test_session_status_sync.py
git commit -m "fix(blackbox): workflow except Exception 设 state.status=failed(对齐 whitebox)

防御性:session 落盘仍靠 CLI worker.py 兜底(已有);本源设 failed state 对齐 whitebox。"
```

---

## Task 4: web `_watch` 加 `handle.describe()` 轮询 + `_write_scan_end` 同步 session

**Files:**
- Modify: `packages/web/src/shannon_web/components/scan_manager.py:313-358`
- Test: `packages/web/tests/test_scan_manager_session_status.py` (Create)

**Interfaces:**
- Consumes: `self._handles[ws]`(`WorkflowHandle`,有 `.describe()` 返 `WorkflowExecutionDescription`,`.status` 为 `WorkflowExecutionStatus`);`SessionManager.update_session`;`self._workspaces_dir`
- Produces: workflow FAILED/TIMED_OUT/TERMINATED 时 `_watch` 自写 `scan_end(status="failed")` + `session.status="failed"`

- [ ] **Step 1: 写失败测试(mock describe)**

Create `packages/web/tests/test_scan_manager_session_status.py`:

```python
"""session-status 同步:_watch 周期 describe() 轮询,workflow FAILED 时写 scan_end+session.failed.

场景:worker 进程崩溃/容器死/被 terminate → workflow except 跑不到 → finalize_summary 不调
→ scan_end 不写,_watch 永等不到。describe() 轮询发现 FAILED 时 _watch 自行落盘。
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shannon_web.components.scan_manager import ScanManager


def _make_handle(status) -> MagicMock:
    """伪 WorkflowHandle:describe() 返回给定 status。"""
    handle = MagicMock()
    desc = MagicMock()
    desc.status = status  # temporalio.workflow.WorkflowExecutionStatus enum
    handle.describe = AsyncMock(return_value=desc)
    return handle


@pytest.mark.asyncio
async def test_watch_marks_session_failed_on_workflow_failed(tmp_path):
    """describe() 返 FAILED → session.json status=failed + scan_end 写入 + _handles 清理."""
    from temporalio.workflow import WorkflowExecutionStatus

    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws = "ghost"
    (workspaces / ws).mkdir()
    event_file = workspaces / ws / "events.ndjson"

    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock(), scan_timeout=0.0)
    handle = _make_handle(WorkflowExecutionStatus.FAILED)
    mgr._handles[ws] = handle

    await mgr._watch(ws, event_file)

    # session.json 落 failed
    from shannon_core.session import SessionManager
    data = SessionManager(workspaces).get_session_data(workspaces / ws)
    assert data["status"] == "failed", f"FAILED 后 session.status 应=failed, 实际={data.get('status')}"
    assert data.get("completed_at") is not None, "completed_at 应被写入"
    # scan_end 写 events.ndjson
    lines = event_file.read_text().splitlines()
    assert any(json.loads(l).get("type") == "scan_end" and json.loads(l).get("status") == "failed"
               for l in lines), "应写 scan_end(status=failed)"
    # _handles 清理
    assert ws not in mgr._handles


@pytest.mark.asyncio
async def test_watch_does_not_mark_failed_on_running(tmp_path):
    """describe() 返 RUNNING → 不触发 failed(继续 tail).用极短 scan_timeout 让循环退出."""
    from temporalio.workflow import WorkflowExecutionStatus

    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws = "live"
    (workspaces / ws).mkdir()
    event_file = workspaces / ws / "events.ndjson"

    # scan_timeout 极小让 _watch 快速走 timeout 兜底退出(不依赖 describe 触发)
    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock(), scan_timeout=0.05)
    handle = _make_handle(WorkflowExecutionStatus.RUNNING)
    mgr._handles[ws] = handle

    await mgr._watch(ws, event_file)

    from shannon_core.session import SessionManager
    data = SessionManager(workspaces).get_session_data(workspaces / ws)
    # RUNNING 时不应写 failed(timeout 兜底写 crashed,但 session.status 不该是 failed)
    assert data.get("status") != "failed", "RUNNING 时不应标 failed"


@pytest.mark.asyncio
async def test_write_scan_end_accepts_session_status(tmp_path):
    """_write_scan_end 接受 session_status 参数,命中时同步写 session.json."""
    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws = "x"
    (workspaces / ws).mkdir()
    event_file = workspaces / ws / "events.ndjson"
    mgr = ScanManager(workspaces_dir=workspaces, repos_dir=tmp_path / "repos",
                      config_store=MagicMock())

    await mgr._write_scan_end(event_file, "failed", -1, "worker crash",
                              session_status="failed", workspace_name=ws,
                              workspaces_dir=workspaces)

    from shannon_core.session import SessionManager
    data = SessionManager(workspaces).get_session_data(workspaces / ws)
    assert data["status"] == "failed"
```

注意:`test_watch_does_not_mark_failed_on_running` 用 `scan_timeout=0.05` 让 `_watch` 走 timeout 退出,避免无限轮询。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && cd - ` 不需要;后端测试直接:
`uv run pytest packages/web/tests/test_scan_manager_session_status.py -x -v`
Expected: 3 FAIL(`_watch` 不调 describe / `_write_scan_end` 不接 session_status)

- [ ] **Step 3: 实现 `_write_scan_end` 扩展 + `_watch` describe 轮询**

Modify `packages/web/src/shannon_web/components/scan_manager.py`。

**(a) 改 `_write_scan_end` 签名**(line 351-358),加可选 `session_status` / `workspace_name` / `workspaces_dir`:

```python
    async def _write_scan_end(self, event_file: Path, status: str,
                              returncode: int, stderr_tail: str,
                              session_status: str | None = None,
                              workspace_name: str | None = None,
                              workspaces_dir: Path | None = None) -> None:
        payload = {
            "ts": _now_iso(), "category": "CONTROL", "type": "scan_end",
            "status": status, "returncode": returncode, "stderr_tail": stderr_tail,
        }
        async with aiofiles.open(event_file, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        # session-status 同步:workflow FAILED 兜底时同步落 session.json
        if session_status and workspace_name and workspaces_dir is not None:
            from shannon_core.session import SessionManager
            import time as _time
            SessionManager(workspaces_dir).update_session(
                workspaces_dir / workspace_name,
                {"status": session_status, "completed_at": _time.time()},
            )
```

**(b) 改 `_watch`**(line 313-337),加 describe 轮询。改动后:

```python
    async def _watch(self, ws: str, event_file: Path) -> None:
        """C1: tail events.ndjson 直到 scan_end(worker finalize_summary 写)或超时.

        session-status 同步:周期 handle.describe() 轮询 workflow 状态,发现 FAILED /
        TIMED_OUT / TERMINATED 时(worker 进程崩溃/容器死/被 terminate,workflow except
        跑不到)自行写 scan_end + session.status=failed.
        """
        from temporalio.workflow import WorkflowExecutionStatus
        # describe() 见到的终态集合 → 标 failed
        _FAILED_STATES = {
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.TIMED_OUT,
            WorkflowExecutionStatus.TERMINATED,
        }
        try:
            deadline = (time.monotonic() + self._scan_timeout) if self._scan_timeout > 0 else None
            describe_tick = 0
            while not self._has_scan_end(event_file):
                if deadline is not None and time.monotonic() > deadline:
                    if not self._has_scan_end(event_file):
                        await self._write_scan_end(event_file, "timeout", -1, "web 超时收尾")
                    break
                # 每 ~15s describe 一次(0.5s sleep × 30 = 15s)
                describe_tick += 1
                if describe_tick >= 30:
                    describe_tick = 0
                    handle = self._handles.get(ws)
                    if handle is not None:
                        try:
                            desc = await handle.describe()
                            if desc.status in _FAILED_STATES:
                                await self._write_scan_end(
                                    event_file, "failed", -1,
                                    f"workflow {desc.status.name}",
                                    session_status="failed", workspace_name=ws,
                                    workspaces_dir=self._workspaces_dir,
                                )
                                break
                        except Exception:
                            pass  # temporal 断连等:忽略,下个 tick 重试
                await asyncio.sleep(0.5)
        finally:
            if not self._has_scan_end(event_file):
                await self._write_scan_end(event_file, "crashed", -1, "worker 未写 scan_end")
            self._handles.pop(ws, None)
            self._tasks.pop(ws, None)
            self._active_reqs.pop(ws, None)
```

说明:`describe_tick` 累计 0.5s sleep 到 30 次(15s)触发一次 `handle.describe()`;命中 FAILED/TIMED_OUT/TERMINATED 写 `scan_end(status="failed")` + `session.status="failed"` 并 break;temporal 断连等异常吞掉下轮重试。`scan_timeout=0`(默认)时 deadline 为 None,轮询靠 describe 发现终态或外部 cancel 收尾。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/web/tests/test_scan_manager_session_status.py -x -v`
Expected: 3 PASS

回归(现有 _watch / scan_manager 测试不破):
Run: `uv run pytest packages/web/tests/test_scan_manager.py -x -v`(若存在;否则跑 `packages/web/tests/ -k scan_manager`)
Expected: 原有 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/shannon_web/components/scan_manager.py packages/web/tests/test_scan_manager_session_status.py
git commit -m "fix(web): _watch describe 轮询 workflow 终态,FAILED 时自写 scan_end+session.failed

覆盖 worker 进程崩溃/容器死/被 terminate 场景(workflow except 跑不到);_write_scan_end
加 session_status 参数同步落 session.json。"
```

---

## Task 5: 全链路回归 + 文档

**Files:** 无新文件,跑测试 + 更新 memory

- [ ] **Step 1: 跑全部新增/改动测试**

Run:
```bash
uv run pytest packages/whitebox/tests/test_session_status_sync.py packages/blackbox/tests/test_session_status_sync.py packages/web/tests/test_scan_manager_session_status.py -v
```
Expected: 全 PASS(whitebox 7 + blackbox 2 + web 3 = 12)

- [ ] **Step 2: 跑邻近回归(守不破现有)**

Run:
```bash
uv run pytest packages/whitebox/tests/test_workflow_gitnexus_failfast.py packages/whitebox/tests/test_worker_registers_write_track_status_activity.py packages/whitebox/tests/test_worker.py -x -v
```
Expected: PASS(fail-fast / worker 注册 / worker 回归不破)

- [ ] **Step 3: tsc / 前端构建(若 web 改动影响类型)**

web 改动只在 Python 后端(scan_manager.py),不影响前端类型。跳过 tsc。

- [ ] **Step 4: 更新 memory**

在 `~/.claude/projects/-Users-mango-project-shannon-refactor-shannon-py/memory/` 新建 `session-status-sync-implemented.md`:

```markdown
---
name: session-status-sync-implemented
description: workflow FAILED 时 session.json status 同步 failed 的三层修复(2026-07-20)
metadata:
  type: project
---

TDD 实现(2026-07-20,feat/fork-py):workflow FAILED 时 session.json status 永远 running 致 Web 幽灵卡住(根因:无 except Exception 分支,finalize_summary 永不调)。

三层 defense-in-depth:
1. whitebox workflow except Exception → finalize_summary(failed)+ raise(workflows.py)
2. whitebox CLI worker except Exception/ScanCancelled 兜底(worker.py,抄 blackbox)
3. web scan_manager._watch 周期 handle.describe() 轮询,FAILED 时自写 scan_end+session.failed(scan_manager.py)

Temporal 铁律:except Exception 块可 execute_activity,except CancelledError/cancellation 不可 → cancelled 不靠 workflow,靠 web _mark_cancelled + CLI worker except。

blackbox workflow 同步加 except Exception(设 state,不调 activity;靠 CLI worker 兜底)。

关联:[[temporalio-activity-worker-registration]]、[[gitnexus-track-failfast-implemented]]。待 NodeGoat 真机冒烟(重跑确认幽灵不再)+ merge。
```

在 `MEMORY.md` 索引补一行:
```
- [session-status 同步](session-status-sync-implemented.md) — TDD done(2026-07-20,feat/fork-py 未 commit 待冒烟);workflow FAILED→session.failed 三层修复;待 NodeGoat 冒烟+merge
```

- [ ] **Step 5: 确认 commit 链**

memory 文件用 Write 工具直接写到 `~/.claude/projects/.../memory/`(不在 git repo,**不 git add**)。repo 侧确认 4 个实现 commit 都在:

```bash
git log --oneline -5  # 确认 Task 1-4 的 4 个 commit 都在
```

---

## Self-Review

**1. Spec coverage**:
- §3.2 workflow 本源(whitebox)→ Task 1 ✓
- §3.2 workflow 本源(blackbox)→ Task 3 ✓
- §3.2 CLI worker → Task 2 ✓
- §3.2 Web 兜底 → Task 4 ✓
- §3.3 status 写入路径复用 finalize_summary → Task 1 用 ✓
- §5 status 语义(failed/cancelled/interrupted)→ Task 1/2/4 实现 failed/cancelled;interrupted 不动(§7 YAGNI)✓
- §6 测试策略 → 每个 Task TDD + Task 5 回归 ✓
- §7 YAGNI(reconcile/数据/blackbox web C1)→ 均未触碰 ✓

**2. Placeholder scan**:无 TBD/TODO;每个 step 有完整代码或精确命令。✓

**3. Type consistency**:
- `_build_final_summary(result, session, scan_start)` 签名在 Task 2 与 `worker.py:110` 一致 ✓
- `PipelineState(status=..., errors=...)` 字段与 `shared.py:25-36` 一致 ✓
- `WorkflowSummary.status` Literal["completed","failed","cancelled"] 与传值一致 ✓
- `_write_scan_end` 新参数在 Task 4 测试与实现一致(session_status/workspace_name/workspaces_dir)✓
- `WorkflowExecutionStatus.FAILED/TIMED_OUT/TERMINATED` 是 temporalio 枚举 ✓

**4. 一处需实现者留意(非占位符)**:Task 2 Step 3 的 `PipelineState` import —— `worker.py` 可能已通过别的路径 import;实现者按"先查顶部 import,无则补"处理(代码注释已说明)。这是确定性动作,非歧义。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-session-status-sync.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个 task 派 fresh subagent,task 间 review,迭代快
2. **Inline Execution** — 本 session 用 executing-plans 批量执行,checkpoint review

Which approach?
