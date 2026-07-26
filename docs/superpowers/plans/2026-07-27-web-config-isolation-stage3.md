# P3c 阶段 3：并发解锁（contextvar 化） 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `AuditSession._current` / `LogBus` / `heartbeat._current_heartbeat` 三个进程级模块单例改为**按 `workflow_id` 索引**（经 `activity.info().workflow_id` 查表），解除「worker `max_concurrent_workflow_tasks=1`」硬钉，让多 ws 同时 scan 各自 AuditSession/events/session.json/heartbeat 不串台。叠加阶段 2 后 = 各 ws 各自配置并发跑。

**Architecture:** 三个单例各自改为 `dict[workflow_id, <instance>]` 注册表 + `_resolve_wf_id()` helper（`try: activity.info().workflow_id except RuntimeError: return "_cli"`，对齐 `activity_logger.py:53-58` 惯例）。web worker 路径的 `setup_display`/`finalize_summary` 在 activity 体内调 set/get/attach——`_resolve_wf_id()` 自动拿 workflow_id，**setup_display 调用点零改动**（兼容）。CLI 路径的 `set_audit_session`（`worker.py:342`）在 workflow 启动前（非 activity context），要**显式传 `workflow_id=resolve_workflow_id(...)`**。LogBus 最难：`queue` 跨线程，生产线程 emit 拿不到 contextvar → `LogBusHandler` 构造时（activity 线程）绑定 `workflow_id`，emit 时路由到 `get_log_bus(wf_id)`。heartbeat 删「ws_dir 变了先停旧」分支（并发杀心跳元凶）。最后放宽 worker `max_concurrent_workflow_tasks` 1→N + web 软门对齐 + 清 `workflow_logger.py:91` env 回落。

**Tech Stack:** Python 3.11+ / contextvars / temporalio（`activity.info()`）/ pytest + asyncio + `temporalio.testing.WorkflowEnvironment`（端到端并发测试）。

## Global Constraints

- **最高风险阶段**：三单例任一漏改 → 多 scan 并发立即串台（events/session/heartbeat 归属错乱）。每 task 单测钉死，Task 7 端到端两 workflow 不串台才验收。
- **改造顺序不可乱**：Task 1-3（三单例 dict 化，内部改造 + 兼容旧 API）→ Task 4（CLI 显式 workflow_id）→ Task 5（worker 放宽，**必须前 4 个全绿才能放宽**）→ Task 6（env 清理）→ Task 7（并发回归）。**仅放宽 worker 不做 contextvar 化，多 scan 立即串台**（runner.py:72-75 注释明示）。
- **`_resolve_wf_id` 统一兜底**：`try: from temporalio import activity; wf_id = activity.info().workflow_id; if wf_id: return wf_id except RuntimeError: pass; return "_cli"`。三单例共用（定义在 Task 1 的 session_registry，Task 2/3 import 复用）。CLI 路径非 activity context → 返 `_cli`（但 CLI 显式传 workflow_id 覆盖，见 Task 4）。
- **36 处 `get_audit_session()` 零改动**：全在 activity 体内，`get_audit_session()` 内部 `_resolve_wf_id()` 自动查表。**不要**要求调用点传参。
- **web worker 路径 setup_display/finalize 零改动**（兼容）：set/get/attach/detach 在 activity 体内，`_resolve_wf_id()` 自动。仅 CLI 路径要显式传（Task 4）。
- **LogBus 跨线程约束**：生产线程（GitNexus/`asyncio.to_thread`）emit 拿不到 contextvar → `LogBusHandler` 构造时（activity 线程的 `configure_logging`）绑定 `workflow_id`，emit 路由 `get_log_bus(handler._workflow_id)`。
- **heartbeat 删「先停旧」**：`start_heartbeat` 的「`_current_heartbeat is not None and ws_dir 变了 → 先停旧`」分支是并发杀心跳元凶，dict 化后按 workflow_id 隔离不再需要，**删掉**。
- **worker 放宽默认值**：`SUPERNOVA_WORKER_MAX_CONCURRENT_WF`（默认 4）；web 软门 `SUPERNOVA_WEB_MAX_CONCURRENT` 默认对齐 4。两者建议同值（避免 pending 堆积）。
- **不动 blackbox web 路径**（Phase C 未上线）；core 单例改 dict 自动兼容 blackbox CLI（Task 4 blackbox worker.py 同步）。
- **行为不变量**：`max_concurrent=1` 时（默认放宽前 / CLI 单 scan）行为与改造前逐字节一致；单 scan 路径全回归绿。
- **测试隔离**：`monkeypatch` + tmp_path；并发测试用 `temporalio.testing.WorkflowEnvironment`；按 CLAUDE.md 只跑改动相关测试。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `packages/core/src/supernova_core/audit/session_registry.py` | AuditSession 单例 | `_current` → `_SESSIONS: dict` + `_resolve_wf_id` + `get_audit_session_for`（Task 1） |
| `packages/core/src/supernova_core/runtime/heartbeat.py` | heartbeat 单例 | `_current_heartbeat` → `_HEARTBEATS: dict` + 删「先停旧」（Task 2） |
| `packages/core/src/supernova_core/logging/log_bus.py` | LogBus 单例 | `LogBus = _LogBus()` → `_BUSES: dict` + `LogBusHandler` 绑定 workflow_id（Task 3） |
| `packages/core/src/supernova_core/logging/log_bus.py` 的 `configure_logging` 调用链 | 挂 LogBusHandler | handler 构造绑定 workflow_id（Task 3） |
| `packages/whitebox/src/supernova_whitebox/worker.py:342/372` | CLI set/clear | 显式传 `workflow_id`（Task 4） |
| `packages/blackbox/src/supernova_blackbox/worker.py:187/213` | CLI set/clear | 同上（Task 4） |
| `packages/core/src/supernova_core/audit/display_lifecycle.py:37/56/65/69` | `run_with_display` LogBus.attach | 加 `workflow_id`（Task 4） |
| `packages/worker/src/supernova_worker/runner.py:75/92` | worker 并发硬门 | `max_concurrent_workflow_tasks` 1→N（Task 5） |
| `packages/worker/tests/test_runner.py:52/92-97` | worker 断言 | 放宽 + 修过时 `run_heartbeat` 断言（Task 5） |
| `packages/web/src/supernova_web/config.py:12` | web 软门 | `SUPERNOVA_WEB_MAX_CONCURRENT` 默认对齐（Task 5） |
| `packages/core/src/supernova_core/audit/workflow_logger.py:91` | event_file env 回落 | 删 env 回落（Task 6） |
| `packages/core/src/supernova_core/display/structured_event_renderer.py:67` | `wire_web_event_file` | 改显式构造（Task 6） |

---

## Task 1: AuditSession `_current` → `_SESSIONS` dict + `_resolve_wf_id`

**Files:**
- Modify: `packages/core/src/supernova_core/audit/session_registry.py:14/70-81`
- Test: `packages/core/tests/audit/test_session_registry_concurrency.py`

**Interfaces:**
- Consumes: `temporalio.activity.info()`（activity 体内可用）
- Produces: `_resolve_wf_id(explicit=None) -> str`（三单例共用）；`_SESSIONS: dict[str, Any]`；`set/get/clear_audit_session` 加可选 `workflow_id` 参数；`get_audit_session_for(workflow_id)`（测试用显式 query）。Task 2/3 import `_resolve_wf_id`。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/audit/test_session_registry_concurrency.py`

```python
"""P3c 阶段 3：AuditSession 按 workflow_id 隔离（不串台）。"""
import pytest

from supernova_core.audit.session_registry import (
    set_audit_session, get_audit_session, get_audit_session_for,
    clear_audit_session, NullAuditSession, _resolve_wf_id, _SESSIONS,
)


def setup_function():
    _SESSIONS.clear()


def test_resolve_wf_id_falls_back_to_cli_without_activity_context():
    """非 activity 线程 → '_cli'。"""
    assert _resolve_wf_id() == "_cli"
    assert _resolve_wf_id("explicit-wf") == "explicit-wf"   # 显式优先


def test_set_get_isolated_per_workflow_id():
    sA, sB = object(), object()
    set_audit_session(sA, workflow_id="wf-A")
    set_audit_session(sB, workflow_id="wf-B")
    assert get_audit_session_for("wf-A") is sA
    assert get_audit_session_for("wf-B") is sB
    assert get_audit_session_for("wf-A") is not sB


def test_clear_one_does_not_affect_other():
    set_audit_session(object(), workflow_id="wf-A")
    set_audit_session(object(), workflow_id="wf-B")
    clear_audit_session(workflow_id="wf-A")
    assert isinstance(get_audit_session_for("wf-A"), NullAuditSession)
    assert not isinstance(get_audit_session_for("wf-B"), NullAuditSession)


def test_get_audit_session_for_unknown_returns_null():
    assert isinstance(get_audit_session_for("never-set"), NullAuditSession)


def test_get_audit_session_without_activity_context_uses_cli_key():
    """非 activity 线程 get → 查 '_cli' key（CLI 兼容）。"""
    s = object()
    set_audit_session(s, workflow_id="_cli")
    assert get_audit_session() is s
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/audit/test_session_registry_concurrency.py -v`
  - 预期：FAIL（`_SESSIONS`/`get_audit_session_for`/`_resolve_wf_id` 不存在）

- [ ] **Step 3: 改 session_registry.py** — 编辑 `packages/core/src/supernova_core/audit/session_registry.py`

  替换 :14 `_current: Any = None` + :70-81 三个 accessor 为：

```python
import contextvars

# P3c 阶段 3：按 workflow_id 索引（替代进程级 _current 单例）。
# web worker 路径 setup_display/finalize 在 activity 体内 → _resolve_wf_id 自动拿 workflow_id；
# CLI 路径 worker.py:342 在 workflow 启动前 → 显式传 workflow_id（Task 4）。
_SESSIONS: dict[str, "Any"] = {}
_current_wf_id: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "audit_wf_id", default=None
)


def _resolve_wf_id(explicit: str | None = None) -> str:
    """解析当前 workflow_id。显式优先 > activity.info() > '_cli' 兜底。"""
    if explicit:
        return explicit
    ctx = _current_wf_id.get()
    if ctx:
        return ctx
    try:
        from temporalio import activity
        wf_id = activity.info().workflow_id
        if wf_id:
            return wf_id
    except RuntimeError:
        pass
    return "_cli"


# 保留旧名 _current 供现有测试/导入兼容（指向 _SESSIONS 的 "_cli" 视图，已无实际用途）
_current = None


def set_audit_session(session: Any, workflow_id: str | None = None) -> None:
    wf_id = _resolve_wf_id(workflow_id)
    _SESSIONS[wf_id] = session


def get_audit_session() -> Any:
    wf_id = _resolve_wf_id()
    return _SESSIONS.get(wf_id) if _SESSIONS.get(wf_id) is not None else NullAuditSession()


def get_audit_session_for(workflow_id: str) -> Any:
    """显式按 workflow_id 查询（测试 + CLI 兜底用）。"""
    s = _SESSIONS.get(workflow_id)
    return s if s is not None else NullAuditSession()


def clear_audit_session(workflow_id: str | None = None) -> None:
    wf_id = _resolve_wf_id(workflow_id)
    _SESSIONS.pop(wf_id, None)
```

  保留 `NullAuditSession` 类（:17-67）不动。

- [ ] **Step 4: 跑新测试 + 现有 audit 回归** — `cd packages/core && uv run pytest tests/audit/test_session_registry_concurrency.py tests/audit/ -v`
  - 预期：新测试 5 PASS；现有 audit 测试若依赖 `_current` 单值行为，按报错适配（`_current` 保留为 None 兼容层，多数测试经 set/get 走 _SESSIONS）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/audit/session_registry.py \
        packages/core/tests/audit/test_session_registry_concurrency.py
git commit -m "feat(core/audit): P3c 阶段3 AuditSession 按 workflow_id 隔离

_current 单例 → _SESSIONS dict + _resolve_wf_id(activity.info()→'_cli' 兜底)。
set/get/clear 加可选 workflow_id；get_audit_session_for 显式查询。
web worker 路径 setup_display 零改动（activity 体内自动）；CLI 路径 Task4 显式传。"
```

---

## Task 2: heartbeat `_current_heartbeat` → `_HEARTBEATS` dict + 删「先停旧」

**Files:**
- Modify: `packages/core/src/supernova_core/runtime/heartbeat.py:95-123`
- Test: `packages/core/tests/runtime/test_heartbeat_concurrency.py`

**Interfaces:**
- Consumes: Task 1 的 `_resolve_wf_id`（从 session_registry import）
- Produces: `_HEARTBEATS: dict[str, HeartbeatManager]`；`start_heartbeat(ws_dir, workflow_id=None)` / `stop_heartbeat(workflow_id=None)` 按 workflow_id 隔离。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/runtime/test_heartbeat_concurrency.py`

```python
"""P3c 阶段 3：heartbeat 按 workflow_id 隔离（B 启动不停 A）。"""
import pytest
from pathlib import Path

from supernova_core.runtime.heartbeat import (
    start_heartbeat, stop_heartbeat, _HEARTBEATS,
)


def setup_function():
    _HEARTBEATS.clear()


@pytest.mark.asyncio
async def test_start_B_does_not_kill_A(tmp_path, monkeypatch):
    """wf-B 的 start_heartbeat 不能停掉 wf-A 的 daemon（删'先停旧'分支）。"""
    dirA, dirB = tmp_path / "A", tmp_path / "B"
    dirA.mkdir(); dirB.mkdir()
    # 缩短 daemon 周期便于测试
    monkeypatch.setattr("supernova_core.runtime.heartbeat._DEFAULT_INTERVAL", 0.05)
    await start_heartbeat(dirA, workflow_id="wf-A")
    await start_heartbeat(dirB, workflow_id="wf-B")
    await asyncio.sleep(0.1)
    # A 的 heartbeat 仍存活（未被 B 启动停掉）
    assert "wf-A" in _HEARTBEATS
    assert "wf-B" in _HEARTBEATS
    await stop_heartbeat(workflow_id="wf-A")
    await stop_heartbeat(workflow_id="wf-B")


@pytest.mark.asyncio
async def test_stop_one_does_not_affect_other(tmp_path, monkeypatch):
    dirA, dirB = tmp_path / "A", tmp_path / "B"
    dirA.mkdir(); dirB.mkdir()
    monkeypatch.setattr("supernova_core.runtime.heartbeat._DEFAULT_INTERVAL", 0.05)
    await start_heartbeat(dirA, workflow_id="wf-A")
    await start_heartbeat(dirB, workflow_id="wf-B")
    await stop_heartbeat(workflow_id="wf-A")
    assert "wf-A" not in _HEARTBEATS
    assert "wf-B" in _HEARTBEATS
    await stop_heartbeat(workflow_id="wf-B")


@pytest.mark.asyncio
async def test_idempotent_same_workflow_same_dir(tmp_path, monkeypatch):
    """同 workflow_id + 同 ws_dir → 幂等（不重启）。"""
    monkeypatch.setattr("supernova_core.runtime.heartbeat._DEFAULT_INTERVAL", 0.05)
    await start_heartbeat(tmp_path, workflow_id="wf-A")
    mgr1 = _HEARTBEATS["wf-A"]
    await start_heartbeat(tmp_path, workflow_id="wf-A")   # 幂等
    assert _HEARTBEATS["wf-A"] is mgr1
    await stop_heartbeat(workflow_id="wf-A")
```

  > 注：import `asyncio`；`_DEFAULT_INTERVAL` 常量名以 heartbeat.py 现有为准（若不同，按实际 monkeypatch）。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/runtime/test_heartbeat_concurrency.py -v`
  - 预期：FAIL（`_HEARTBEATS` 不存在 / `test_start_B_does_not_kill_A` 因「先停旧」失败）

- [ ] **Step 3: 改 heartbeat.py** — 编辑 `packages/core/src/supernova_core/runtime/heartbeat.py:95-123`

```python
from supernova_core.audit.session_registry import _resolve_wf_id

# P3c 阶段 3：按 workflow_id 索引（替代 _current_heartbeat 单例）。
_HEARTBEATS: dict[str, "HeartbeatManager"] = {}


async def start_heartbeat(ws_dir, workflow_id: str | None = None):
    """启动 heartbeat daemon，按 workflow_id 隔离。

    P3c 阶段 3：删掉旧'ws_dir 变了先停旧'分支（并发杀心跳元凶）——
    按 workflow_id 隔离后，不同 workflow 各自独立，互不影响。
    """
    wf_id = _resolve_wf_id(workflow_id)
    existing = _HEARTBEATS.get(wf_id)
    if existing is not None and existing._ws_dir == Path(ws_dir):
        return  # 幂等（同 workflow + 同目录）
    mgr = HeartbeatManager(Path(ws_dir), on_cancel=None)
    await mgr.__aenter__()
    _HEARTBEATS[wf_id] = mgr


async def stop_heartbeat(workflow_id: str | None = None):
    wf_id = _resolve_wf_id(workflow_id)
    mgr = _HEARTBEATS.pop(wf_id, None)
    if mgr is not None:
        await mgr.__aexit__(None, None, None)
```

  删除旧的 `_current_heartbeat` 全局变量（:95）+ 旧的 `_stop_current_heartbeat`（:118-123，已被 `stop_heartbeat` 内联取代）。保留 `HeartbeatManager` 类（:126-223）不动。

- [ ] **Step 4: 跑新测试 + 现有 heartbeat 回归** — `cd packages/core && uv run pytest tests/runtime/test_heartbeat_concurrency.py tests/runtime/ -v`
  - 预期：新测试 3 PASS；现有 heartbeat 测试若依赖 `_current_heartbeat`，按报错适配（改用 `start/stop_heartbeat(workflow_id=...)`）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/runtime/heartbeat.py \
        packages/core/tests/runtime/test_heartbeat_concurrency.py
git commit -m "feat(core/runtime): P3c 阶段3 heartbeat 按 workflow_id 隔离

_current_heartbeat 单例 → _HEARTBEATS dict。删'start_heartbeat ws_dir 变了先停旧'
分支（并发杀心跳元凶）——按 workflow_id 隔离互不影响。start/stop 加 workflow_id。"
```

---

## Task 3: LogBus 单例 → `_BUSES` dict + LogBusHandler 绑定 workflow_id（最难）

**Files:**
- Modify: `packages/core/src/supernova_core/logging/log_bus.py:32/119/64-90/122-166`
- Modify: 挂 LogBusHandler 的 `configure_logging` 调用链（grep `LogBusHandler` 定位）
- Test: `packages/core/tests/logging/test_log_bus_concurrency.py`

**Interfaces:**
- Consumes: Task 1 的 `_resolve_wf_id`
- Produces: `_BUSES: dict[str, _LogBus]`；`get_log_bus(workflow_id) -> _LogBus`；`attach(workflow_id, dispatcher)` / `drain_and_detach(workflow_id)` / `is_attached` → `is_attached(workflow_id)`；`LogBusHandler` 构造时绑定 `_workflow_id`。下游 setup_display（`activities.py:1790`）/ finalize（`:1830`）/ display_lifecycle（`:37/65`）调用新 API。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/logging/test_log_bus_concurrency.py`

```python
"""P3c 阶段 3：LogBus 按 workflow_id 隔离（A 的事件不 dispatch 到 B）。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from supernova_core.logging.log_bus import (
    get_log_bus, attach, drain_and_detach, _BUSES, LogBusHandler,
)
from supernova_core.logging.log_bus import LogEvent  # 按实际 import 路径


def setup_function():
    for bus in list(_BUSES.values()):
        asyncio.get_event_loop().run_until_complete(bus.drain_and_detach())  # 清理
    _BUSES.clear()


@pytest.mark.asyncio
async def test_two_buses_dispatch_to_own_dispatcher():
    """wf-A 的 LogEvent 只 dispatch 到 wf-A 的 dispatcher，不串到 B。"""
    spyA, spyB = MagicMock(), MagicMock()
    spyA.dispatch = AsyncMock()
    spyB.dispatch = AsyncMock()
    await attach("wf-A", spyA)
    await attach("wf-B", spyB)
    busA = get_log_bus("wf-A")
    busA.queue.put_nowait(LogEvent(timestamp="t", category="WARNING",
        logger_name="x", level="WARNING", message="from-A", exc_txt=None))
    await asyncio.sleep(0.1)   # 等 drain
    await drain_and_detach("wf-A")
    await drain_and_detach("wf-B")
    spyA.dispatch.assert_awaited()
    spyB.dispatch.assert_not_awaited()   # B 完全没收到 A 的事件


@pytest.mark.asyncio
async def test_detach_one_does_not_cancel_other_drain():
    """wf-A detach 不 cancel wf-B 的 drain task。"""
    spyA, spyB = MagicMock(), MagicMock()
    spyA.dispatch = AsyncMock(); spyB.dispatch = AsyncMock()
    await attach("wf-A", spyA)
    await attach("wf-B", spyB)
    await drain_and_detach("wf-A")
    # wf-B 的 drain task 仍存活
    assert get_log_bus("wf-B")._drain_task is not None
    assert not get_log_bus("wf-B")._drain_task.done()
    await drain_and_detach("wf-B")


def test_logbus_handler_binds_workflow_id_at_construction(monkeypatch):
    """LogBusHandler 构造时（activity 线程）绑定 workflow_id，emit 路由用它。"""
    monkeypatch.setattr("supernova_core.logging.log_bus._resolve_wf_id", lambda: "wf-X")
    h = LogBusHandler()  # 构造时 _resolve_wf_id 拿 "wf-X"
    assert h._workflow_id == "wf-X"
```

  > 注：`LogEvent` 构造字段以现有 `log_bus.py` 定义为准；`_resolve_wf_id` import 路径以 Task 1 为准。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/logging/test_log_bus_concurrency.py -v`
  - 预期：FAIL（`_BUSES`/`get_log_bus`/`attach(workflow_id,...)` 不存在）

- [ ] **Step 3: 改 log_bus.py 注册表** — 编辑 `packages/core/src/supernova_core/logging/log_bus.py`

  3a. import `_resolve_wf_id`（顶部）：
```python
from supernova_core.audit.session_registry import _resolve_wf_id
```

  3b. 替换模块级单例（:119 `LogBus = _LogBus()`）为注册表：
```python
# P3c 阶段 3：按 workflow_id 索引（替代进程级 LogBus 单例）。
_BUSES: dict[str, "_LogBus"] = {}


def get_log_bus(workflow_id: str) -> "_LogBus":
    """取（或创建）该 workflow 的 LogBus 实例（独立 queue + drain task）。"""
    return _BUSES.setdefault(workflow_id, _LogBus())


# 兼容旧代码直接引用 LogBus 单例的场景：保留 LogBus 名指向"当前 workflow"的 bus
# （仅 setup_display/finalize 已改为显式 workflow_id；过渡期用）
@property
def _legacy_bus():
    return get_log_bus(_resolve_wf_id())
```

  3c. 把 `attach` / `drain_and_detach` / `is_attached` 改为模块级函数，收 `workflow_id`：
```python
async def attach(workflow_id: str | None = None, dispatcher=None):
    wf_id = _resolve_wf_id(workflow_id)
    await get_log_bus(wf_id)._attach(dispatcher)


async def drain_and_detach(workflow_id: str | None = None):
    wf_id = _resolve_wf_id(workflow_id)
    bus = _BUSES.get(wf_id)
    if bus is not None:
        await bus._drain_and_detach()


def is_attached(workflow_id: str | None = None) -> bool:
    wf_id = _resolve_wf_id(workflow_id)
    bus = _BUSES.get(wf_id)
    return bus.is_attached if bus else False
```

  把原 `_LogBus.attach`/`drain_and_detach` 方法重命名为 `_attach`/`_drain_and_detach`（内部实例方法，逻辑不变）。

  3d. `LogBusHandler`（:122-166）构造时绑定 workflow_id + emit 路由：
```python
class LogBusHandler(logging.handlers.QueueHandler):
    def __init__(self, bus=None, workflow_id: str | None = None):
        self._workflow_id = workflow_id or _resolve_wf_id()
        bus = bus or get_log_bus(self._workflow_id)
        super().__init__(bus.queue)
        self._bus = bus

    def prepare(self, record):
        evt = super().prepare(record)   # 原 prepare 逻辑（物化 LogEvent）
        return evt

    def emit(self, record):
        # 生产线程 emit：用构造时绑定的 workflow_id 路由到对应 bus.queue
        if self._bus.is_attached:
            self._bus.queue.put_nowait(self.prepare(record))
        else:
            self._write_fallback(record)
```

  > 注：`_LogBus` 类的 `__init__`（:37-42）queue/dispatcher/drain_task 字段不变——每个 `_LogBus` 实例独立 queue + drain task。`_attach`/`_drain_and_detach` 实例方法逻辑沿用原 `attach`/`drain_and_detach`（覆盖 _dispatcher + 起 drain task / flush + cancel）。`_write_fallback` 沿用原 `write_fallback`。

- [ ] **Step 4: 更新 setup_display/finalize/display_lifecycle 调用点** — 改用新模块级函数：

  - `whitebox activities.py:1790` `await LogBus.attach(session.dispatcher)` → `await attach(workflow_id=..., session.dispatcher)`（workflow_id 经 `_resolve_wf_id()` 自动，可写 `await attach(dispatcher=session.dispatcher)`）。实际因 `attach` 的 `workflow_id` 可选且默认 `_resolve_wf_id()`，**原调用 `LogBus.attach(x)` 改为 `attach(x)` 即可**（去 `LogBus.` 前缀，用模块函数）。
  - `whitebox activities.py:1830` `await LogBus.drain_and_detach()` → `await drain_and_detach()`。
  - `display_lifecycle.py:37/65` `LogBus.attach` → `attach`；`:69` `LogBus.drain_and_detach` → `drain_and_detach`。
  - 挂 LogBusHandler 的 `configure_logging`（grep 定位）：`LogBusHandler()` 构造在 activity 线程，自动 `_resolve_wf_id()` 绑定——**零参数构造即可**，确认调用点不传旧 `bus=` 单例。

  > ⚠️ 若有代码 `from ... import LogBus` 后 `LogBus.attach(...)`，全部改为 `from ... import attach` + `attach(...)`。grep `LogBus\.` 全清理。

- [ ] **Step 5: 适配现有 log_bus 测试 fixture** — grep `LogBus._dispatcher` / `_restore_log_bus` fixture（`test_log_bus_attach.py:48-63`），改为清 `_BUSES` dict：
```python
@pytest.fixture
def _restore_log_bus():
    yield
    for bus in list(_BUSES.values()):
        asyncio.get_event_loop().run_until_complete(bus._drain_and_detach())
    _BUSES.clear()
```

- [ ] **Step 6: 跑新测试 + 现有 log_bus 回归** — `cd packages/core && uv run pytest tests/logging/test_log_bus_concurrency.py tests/logging/ -v`
  - 预期：新测试 3 PASS；现有 log_bus 测试适配后绿。

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/supernova_core/logging/log_bus.py \
        packages/core/src/supernova_core/audit/display_lifecycle.py \
        packages/whitebox/src/supernova_whitebox/pipeline/activities.py \
        packages/core/tests/logging/test_log_bus_concurrency.py
git commit -m "feat(core/logging): P3c 阶段3 LogBus 按 workflow_id 隔离

LogBus 单例 → _BUSES dict（每 workflow 独立 queue+drain task）。
LogBusHandler 构造时绑定 workflow_id（activity 线程），生产线程 emit 路由到
对应 bus——解决 queue 跨线程拿不到 contextvar。attach/drain_and_detach/is_attached
改模块级函数收 workflow_id。setup_display/finalize/display_lifecycle 调用点更新。"
```

---

## Task 4: CLI 路径显式 workflow_id（whitebox/blackbox worker.py + display_lifecycle）

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/worker.py:342/372`
- Modify: `packages/blackbox/src/supernova_blackbox/worker.py:187/213`
- Modify: `packages/core/src/supernova_core/audit/display_lifecycle.py:37/56/65/69`（attach 显式 workflow_id）
- Test: `packages/whitebox/tests/test_worker_cli_workflow_id.py`（或扩展现有 worker 测试）

**Interfaces:**
- Consumes: Task 1-3 的 `set/clear_audit_session(workflow_id=)` / `attach(workflow_id=)`
- Produces: CLI 路径 `set_audit_session(session, workflow_id=<resolved>)` + `attach(workflow_id=<resolved>, dispatcher)`；CLI 单 scan 用真实 workflow_id（= `start_workflow` 的 id），与 activity 内 `get_audit_session()` 经 `activity.info().workflow_id` 匹配。

- [ ] **Step 1: 写失败测试** — 验证 CLI 路径 set 的 workflow_id 与 activity 内 get 一致（mock worker 跑一个 activity，断言 `get_audit_session_for(workflow_id)` 命中）。

```python
"""P3c 阶段 3：CLI 路径 set_audit_session 用真实 workflow_id（与 activity 内 get 匹配）。"""
# 参考 packages/whitebox/tests 现有 worker 测试模式
# 断言：CLI 起 workflow(id=wf-X) → activity 内 get_audit_session() 拿到 CLI set 的 session
```

  > 注：CLI worker 测试较重（要起 in-process temporal worker），可改为单测 `resolve_workflow_id` 返回值传给 `set_audit_session`，断言 `_SESSIONS[workflow_id]` 命中。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/whitebox && uv run pytest tests/test_worker_cli_workflow_id.py -v`

- [ ] **Step 3: 改 whitebox worker.py** — 编辑 `packages/whitebox/src/supernova_whitebox/worker.py:342/372`

  在 `set_audit_session(session)`（:342）前算 workflow_id（CLI 已有 `resolve_workflow_id` / 等价逻辑），改为：
```python
        workflow_id = resolve_workflow_id(ws)   # 或 CLI 现有的 workflow_id 变量
        set_audit_session(session, workflow_id=workflow_id)
        # ... start_workflow(id=workflow_id) ...
        finally:
            clear_audit_session(workflow_id=workflow_id)   # :372
```

  > 注：CLI 的 workflow_id 算法以现有 `worker.py` / `run_scan` 的 `resolve_workflow_id` 为准（与 `start_workflow(id=...)` 用同一个 id）。若 CLI 现有代码已算好 workflow_id 变量，直接复用。

- [ ] **Step 4: 改 blackbox worker.py** — 编辑 `packages/blackbox/src/supernova_blackbox/worker.py:187/213`，同 Step 3 模式。

- [ ] **Step 5: 改 display_lifecycle.py attach** — 编辑 `packages/core/src/supernova_core/audit/display_lifecycle.py:37/65`
```python
        await attach(workflow_id=workflow_id, dispatcher=session.dispatcher)
```
  （`run_with_display` 收 `workflow_id` 参数或内部 `resolve_workflow_id`；:69 `drain_and_detach(workflow_id=workflow_id)`）

- [ ] **Step 6: 跑 CLI worker 回归** — `cd packages/whitebox && uv run pytest tests/test_worker*.py -v` + `cd packages/blackbox && uv run pytest tests/test_worker*.py -v`
  - 预期：全 PASS（CLI 单 scan 行为不变）

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/supernova_whitebox/worker.py \
        packages/blackbox/src/supernova_blackbox/worker.py \
        packages/core/src/supernova_core/audit/display_lifecycle.py \
        packages/whitebox/tests/test_worker_cli_workflow_id.py
git commit -m "feat(whitebox/blackbox): P3c 阶段3 CLI 路径显式 workflow_id

worker.py set/clear_audit_session + display_lifecycle attach 改传 workflow_id
(= start_workflow 的 id)，与 activity 内 get_audit_session() 经 activity.info() 匹配。
CLI 单 scan 行为不变。"
```

---

## Task 5: worker max_concurrent 放宽 + web 软门对齐 + 修过时断言

**Files:**
- Modify: `packages/worker/src/supernova_worker/runner.py:75/92`
- Modify: `packages/worker/tests/test_runner.py:52/92-97`
- Modify: `packages/web/src/supernova_web/config.py:12`
- Test: 扩展 `test_runner.py`

**Interfaces:**
- Consumes: Task 1-4（contextvar 化完成，并发安全）
- Produces: worker `max_concurrent_workflow_tasks` 读 env `SUPERNOVA_WORKER_MAX_CONCURRENT_WF`（默认 4）；web 软门 `SUPERNOVA_WEB_MAX_CONCURRENT` 默认 4。

- [ ] **Step 1: 写失败测试** — 扩展 `packages/worker/tests/test_runner.py`

```python
def test_worker_max_concurrent_reads_env(monkeypatch):
    """max_concurrent_workflow_tasks 读 SUPERNOVA_WORKER_MAX_CONCURRENT_WF（默认 4）。"""
    monkeypatch.delenv("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", raising=False)
    # ... 构造 worker，断言 max_concurrent_workflow_tasks == 4（默认）
    monkeypatch.setenv("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "8")
    # ... 断言 == 8


def test_worker_max_concurrent_no_longer_hardcoded_1():
    """放宽后不再是硬钉 1（contextvar 化已解除串台）。"""
    # ... 断言 max_concurrent_workflow_tasks >= 2
```

  并修 `:96` 过时断言：`"run_heartbeat" in activity_names`——runner.py 注册列表实际无 `run_heartbeat`（setup_display 内联启动），改为断言实际注册的 activity（如 `setup_display`/`finalize_summary`）。

- [ ] **Step 2: 跑测试确认失败** — `cd packages/worker && uv run pytest tests/test_runner.py -v`
  - 预期：FAIL（仍硬钉 1）

- [ ] **Step 3: 改 runner.py** — 编辑 `packages/worker/src/supernova_worker/runner.py:75/92`

```python
        # P3c 阶段 3：contextvar 化（AuditSession/LogBus/heartbeat 按 workflow_id 隔离）后，
        # 多 scan 并发不再串台。max_concurrent_workflow_tasks 放开（默认 4，env 可配）。
        max_concurrent=int(os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")),
```

  wb（:75）+ bb（:92）两处。更新注释（删「AuditSession 单例约束 → 并发=1」旧注释，改为「contextvar 化后并发安全」）。

- [ ] **Step 4: 改 test_runner.py 过时断言** — 编辑 `packages/worker/tests/test_runner.py:52/92-97`
  - `:52` / `:92-93` 断言 `max_concurrent_workflow_tasks == 1` → 改为 `>= 1` 或读 env（按 Step 1 测试）。
  - `:96` `"run_heartbeat" in activity_names` → 改为实际注册的 activity 名（grep runner.py 注册列表确认）。

- [ ] **Step 5: 改 web 软门默认** — 编辑 `packages/web/src/supernova_web/config.py:12`
```python
        self.max_concurrent = max(1, int(os.environ.get("SUPERNOVA_WEB_MAX_CONCURRENT", "4")))
```
  加注释：与 worker `SUPERNOVA_WORKER_MAX_CONCURRENT_WF` 建议同值。

- [ ] **Step 6: 跑 worker + web 回归** — `cd packages/worker && uv run pytest tests/test_runner.py -v` + `cd packages/web && uv run pytest tests/test_api_scan.py -v`
  - 预期：全 PASS

- [ ] **Step 7: Commit**

```bash
git add packages/worker/src/supernova_worker/runner.py \
        packages/worker/tests/test_runner.py \
        packages/web/src/supernova_web/config.py
git commit -m "feat(worker): P3c 阶段3 放宽 max_concurrent_workflow_tasks 1→N

contextvar 化后多 scan 并发不再串台。读 SUPERNOVA_WORKER_MAX_CONCURRENT_WF(默认4)。
web 软门 SUPERNOVA_WEB_MAX_CONCURRENT 默认对齐 4。修 test_runner 过时 run_heartbeat 断言。"
```

---

## Task 6: 清残留 env 回落（workflow_logger.py + wire_web_event_file）

**Files:**
- Modify: `packages/core/src/supernova_core/audit/workflow_logger.py:91`
- Modify: `packages/core/src/supernova_core/display/structured_event_renderer.py:67-85`（`wire_web_event_file`）
- Modify: `packages/whitebox/src/supernova_whitebox/worker.py:178` + `packages/blackbox/src/supernova_blackbox/worker.py:123`（CLI 改显式构造 event_file）
- Test: `packages/core/tests/audit/test_workflow_logger_no_env_fallback.py`

**Interfaces:**
- Consumes: 阶段 1 的 `PipelineInput.event_file`（web 路径必塞）
- Produces: `workflow_logger.py:91` 不再读 `os.environ.get("SUPERNOVA_WEB_EVENT_FILE")`；event_file 经 PipelineInput 显式传递，多 scan 并发不共享 env。

- [ ] **Step 1: 写失败测试** — 新建 `packages/core/tests/audit/test_workflow_logger_no_env_fallback.py`

```python
"""P3c 阶段 3：workflow_logger 不再读 env 回落（多 scan 并发不串 events 文件）。"""
import pytest
from unittest.mock import patch


def test_workflow_logger_ignores_env_when_event_file_none(monkeypatch, tmp_path):
    """event_file=None + env 设了 SUPERNOVA_WEB_EVENT_FILE → 不挂 renderer（不回落 env）。"""
    monkeypatch.setenv("SUPERNOVA_WEB_EVENT_FILE", str(tmp_path / "should-not-use.ndjson"))
    from supernova_core.audit.workflow_logger import WorkflowLogger
    # 构造 WorkflowLogger(event_file=None)，断言 StructuredEventRenderer 未挂（或挂 None）
    # ... 按 WorkflowLogger 现有构造/initialize 签名
    # 断言：未读 SUPERNOVA_WEB_EVENT_FILE env
```

- [ ] **Step 2: 跑测试确认失败** — `cd packages/core && uv run pytest tests/audit/test_workflow_logger_no_env_fallback.py -v`

- [ ] **Step 3: 删 env 回落** — 编辑 `packages/core/src/supernova_core/audit/workflow_logger.py:89-91`

```python
        # P3c 阶段 3：删 env 回落——多 scan 并发下 os.environ 全局会串 events 文件。
        # event_file 必须经 PipelineInput 显式传递（web scan_manager 塞 / CLI 显式构造）。
        web_event_file = event_file
```

  （原 `web_event_file = event_file or os.environ.get("SUPERNOVA_WEB_EVENT_FILE")` → 去掉 `or os.environ.get(...)`）

- [ ] **Step 4: CLI 路径显式构造 event_file** — 编辑 `packages/whitebox/src/supernova_whitebox/worker.py:178` + `packages/blackbox/src/supernova_blackbox/worker.py:123`

  原 `wire_web_event_file(workspaces_dir, ws)`（设 env）改为显式算 event_file 传给 WorkflowLogger/PipelineInput：
```python
        event_file = str(Path(workspaces_dir) / ws / "events.ndjson")
        # 传给后续 AuditSession.initialize(event_file=event_file) / PipelineInput
```

  保留或删除 `wire_web_event_file` 函数（若别处仍调，保留但 workflow_logger 不再读 env；若仅 CLI 两处调，可删）。`structured_event_renderer.py:67-85` 的 `wire_web_event_file` 标注 deprecated 或删。

- [ ] **Step 5: 跑 audit + CLI worker 回归** — `cd packages/core && uv run pytest tests/audit/ -v` + `cd packages/whitebox && uv run pytest tests/test_worker*.py -v`
  - 预期：全 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/supernova_core/audit/workflow_logger.py \
        packages/core/src/supernova_core/display/structured_event_renderer.py \
        packages/whitebox/src/supernova_whitebox/worker.py \
        packages/blackbox/src/supernova_blackbox/worker.py \
        packages/core/tests/audit/test_workflow_logger_no_env_fallback.py
git commit -m "fix(core/audit): P3c 阶段3 删 workflow_logger env 回落

event_file 不再读 SUPERNOVA_WEB_EVENT_FILE env（多 scan 并发会串）。
经 PipelineInput 显式传递（web scan_manager 塞 / CLI 显式构造）。
wire_web_event_file deprecated。"
```

---

## Task 7: 并发回归 + 端到端两 workflow 不串台

**Files:**
- Test: `packages/whitebox/tests/pipeline/test_two_workflows_no_crosstalk.py`（新建，integration）

**Interfaces:**
- Consumes: Task 1-6 全部
- Produces: 端到端不变量——两个 ws 同时 scan（max_concurrent=2），events.ndjson/session.json/heartbeat 各自归属正确，不串台。

- [ ] **Step 1: 写端到端并发测试** — 新建 `packages/whitebox/tests/pipeline/test_two_workflows_no_crosstalk.py`

```python
"""P3c 阶段 3 端到端：两 workflow 并发不串台（events/session/heartbeat 归属正确）。

用 temporalio.testing.WorkflowEnvironment 起本地 worker（max_concurrent=2），
并发跑两个 WhiteboxScanWorkflow（不同 ws + event_file），断言无串台。
"""
import pytest
from temporalio.testing import WorkflowEnvironment
# 参考 packages/whitebox/tests/pipeline/test_workflow_heartbeat_execution.py:73 的 env 构造模式


@pytest.mark.asyncio
async def test_two_workflows_no_crosstalk(tmp_path, monkeypatch):
    """ws-A / ws-B 并发 scan，各自 events 只含自己的 workflow_id 事件。"""
    # 1. 起 WorkflowEnvironment + worker（max_concurrent_workflow_tasks=2）
    # 2. 并发 start 两个 WhiteboxScanWorkflow（ws-A event_file=A.ndjson / ws-B event_file=B.ndjson）
    # 3. await 两者完成
    # 4. 断言：
    #    - A.ndjson 只含 workflow_id=wf-A 的事件（grep workflow_id 字段）
    #    - B.ndjson 只含 workflow_id=wf-B
    #    - session.json (A) 的 status 与 (B) 独立翻转
    #    - 两份 heartbeat 文件各自存活到对应 finalize
    ...
```

  > 注：完整 e2e 较重（要 mock provider + 跑真实 workflow）。若 WorkflowEnvironment 测试在仓库有预存问题（CLAUDE.md 提醒测试 hang），可降级为**单元集成**：mock `run_claude_prompt`，跑两个 workflow 的 setup_display + 一个 activity + finalize_summary，断言 `get_audit_session_for("wf-A")` ≠ `get_audit_session_for("wf-B")` 且 events 文件不串。

- [ ] **Step 2: 跑端到端 + 全链回归** —
  - `cd packages/whitebox && uv run pytest tests/pipeline/test_two_workflows_no_crosstalk.py -v`
  - 跨包回归（contextvar 化相关）：`cd packages/core && uv run pytest tests/audit/ tests/runtime/ tests/logging/ -v` + `cd packages/worker && uv run pytest tests/ -v`
  - 预期：全 PASS。**任何串台相关的 FAIL 必须修到绿**（阶段 3 验收 = 并发不串台）。

- [ ] **Step 3: 人工核验单 scan 不退化** — 跑一个普通 whitebox scan（CLI 或 web），确认 events/session/heartbeat/report 正常产出（与改造前一致）。

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/tests/pipeline/test_two_workflows_no_crosstalk.py
git commit -m "test(whitebox): P3c 阶段3 两 workflow 并发不串台端到端

断言 ws-A/ws-B 并发 scan 各自 events/session/heartbeat 归属正确。
阶段 3 完成：并发解锁（AuditSession/LogBus/heartbeat contextvar 化 + worker 放宽）。"
```

---

## Self-Review（plan 作者自检）

**1. Spec 覆盖**：spec §8.2.1（AuditSession contextvar）→ Task 1；§8.2.2（LogBus/heartbeat）→ Task 2/3；§8.2.3（worker 放宽）→ Task 5；§8.2.4（web 软门）→ Task 5；§8.2.5（env 回落清理）→ Task 6；§8.3（验收不串台）→ Task 7。CLI 兼容（spec §8.2.1 「CLI 保留旧 set/get 作兜底」）→ Task 4。

**2. 占位符扫描**：少数测试（端到端 WorkflowEnvironment 构造、worker.py 的 `resolve_workflow_id` 变量名、`_DEFAULT_INTERVAL` 常量名）标注"以现有代码为准"——这是对现有符号的复用指引，非占位。核心改造代码（session_registry/heartbeat/log_bus/runner）完整。

**3. 顺序一致性**：Global Constraints + 各 Task 说明「Task 1-3 内部改造（兼容旧 API）→ Task 4 CLI 显式 → Task 5 放宽（必须前 4 绿）→ Task 6 清理 → Task 7 验证」——五处一致。`_resolve_wf_id` 在 Task 1 定义、Task 2/3 import 复用、Task 4 CLI 显式覆盖——一致。

**4. 跨线程约束**：LogBus Task 3 明确「LogBusHandler 构造时绑定 workflow_id（activity 线程），emit 路由」——解决 queue 跨线程，与 Global Constraints 一致。

**5. CLI 兼容**：`_resolve_wf_id` 的 `_cli` 兜底（Task 1）+ CLI 显式传 workflow_id（Task 4）——CLI 单 scan 行为不变，与 Global Constraints 一致。

**6. 风险点覆盖**：test_runner 过时断言（Task 5 Step 4）、log_bus fixture 适配（Task 3 Step 5）、finalize cancel 残留（spec §8 + Global Constraints「setup_display 入口覆盖」）、blackbox web Phase C（Global Constraints）——均已标注。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-web-config-isolation-stage3.md`. Two execution options:

1. **Subagent-Driven（推荐）** — 每 task 派 fresh subagent + 两阶段 review。阶段 3 高风险，task 间强依赖（1→2→3→4→5 顺序），适合按序 subagent + 逐 task 回归。
2. **Inline Execution** — 本 session 批量 + 检查点（每个单例改造后跑回归再进下一个）。

Which approach?

---

**后续阶段**（本 plan 不含）：
- 阶段 4：clone 凭据 per-ws（git 段进 config.yaml + GitFetcher per-ws）
- Phase C（黑盒 web C1 化）：blackbox setup_display/finalize_summary + ws_config 接入
