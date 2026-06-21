# AuditSession 并发 agent 归因坍缩修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 5 个并发 vuln agent 的逐轮/工具事件各自携带正确 `agent_name`，修复 live 屏前缀、状态栏 turn 计数、`workflow.log` 的 `[LLM]`/`[TOOL]` 行、以及 per-agent JSON 审计日志的归因坍缩。

**Architecture:** 把 per-agent 状态（`agent_name` + `AgentLogger` 实例）从进程级单例 `AuditSession` 的共享字段下沉到 per-agent 的 `SessionToolAuditLogger`；`AuditSession` 退化为纯 facade（只保留本应全局共享的 workflow_logger / metrics / phase/step）。

**Tech Stack:** Python 3、asyncio、pytest（asyncio auto mode）、Temporal（activity）。规范见 `docs/superpowers/specs/2026-06-22-audit-session-agent-attribution-design.md`。

## Global Constraints

- **pytest 只跑子集，不跑全量**（memory `pytest-whitebox-hang`：全量卡 Temporal/网络慢测试）。每个任务给出具体测试文件路径。
- **不改**：display 层（`rich_renderer`/`dashboard_state`/`live_dashboard`）、`WorkflowLogger`、`DashboardState`、`MessageDispatcher`/`StreamCollector`、provider 接口、`workflow.log` 文件格式、dispatch、Temporal 接线。
- **`SessionToolAuditLogger` 对外方法签名不变**：`log_assistant_turn(turn, content)` / `log_tool_start(tool_name, parameters)` / `log_tool_end(result)` / `log_error(error, *, turn_count, duration_ms)` 保持原样（调用方 MessageDispatcher/StreamCollector 零影响）；只改 `__init__` + 新增 `initialize`/`close`。
- **`__init__` 新签名**：`SessionToolAuditLogger(session, agent_name, attempt=1)`，`agent_name` 必传。
- **双 race 一次修**：`_current_agent_name`（workflow/live 归因）+ `_agent_logger`（per-agent JSON 归因）同时根治。
- **失败路径必须 `close`**：activity 的每个 except 分支都要 `await tool_audit_logger.close(...)`，避免 per-agent JSON stream 泄漏。
- async 测试无需 `@pytest.mark.asyncio`（项目用 asyncio auto mode，见现有 `async def test_...`）。

---

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `packages/core/src/shannon_core/audit/session_tool_audit_logger.py` | per-agent 桥接器：持有 `agent_name`+`AgentLogger`，把 turn/tool 事件同时落 per-agent JSON + workflow log | 重写（新 `__init__`、持有 `AgentLogger`、`initialize`/`close`、各 log 方法自带 agent_name） |
| `packages/core/src/shannon_core/audit/session.py` | facade：workflow_logger + metrics + phase/step | 删 `_current_agent_name`/`_agent_logger`；`start_agent`/`end_agent` 瘦身；加 `log_llm_turn`/`log_tool_call`；删 `log_event` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_agent` activity | 构造点改新签名 + `initialize`/`close` 配对（含失败路径） |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | 4 个 activity（auth-validation/recon/exploit/report） | 4 处构造点改新签名 + `initialize`/`close` 配对 |
| `packages/whitebox/tests/test_session_tool_audit_logger.py` | 桥接器单 agent 测试 | 3 处构造改签名；新增 `initialize`/`close` 覆盖 |
| `packages/whitebox/tests/test_audit_session.py` | session 测试 | 删 4 个 `log_event`/agent_log 相关测试；改 `test_full_lifecycle` 用桥接器 |
| `packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py` | **并发回归锚点（新文件）** | 新建：5 agent 并发，验证归因不坍缩 |
| `packages/whitebox/tests/test_activity_display_wiring.py` | whitebox 端到端 wiring | 构造点改签名 |
| `packages/blackbox/tests/test_activity_display_wiring.py` | blackbox 端到端 wiring | 构造点改签名 |

> `packages/whitebox/src/shannon_whitebox/audit/session_tool_audit_logger.py` 是 re-export shim（`from shannon_core.audit.session_tool_audit_logger import *`），无需改。`packages/whitebox/src/shannon_whitebox/audit/session.py` 同理是 core 的 re-export（测试 `from shannon_whitebox.audit.session import AuditSession` 实际拿到 core 类）—— 本次改 core 即可。

---

## Task 1: core 重构 + 并发回归锚点

**Files:**
- Modify: `packages/core/src/shannon_core/audit/session_tool_audit_logger.py`（整文件重写）
- Modify: `packages/core/src/shannon_core/audit/session.py`（删字段/方法、瘦身、加新方法）
- Modify: `packages/whitebox/tests/test_session_tool_audit_logger.py`
- Modify: `packages/whitebox/tests/test_audit_session.py`
- Create: `packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py`

**Interfaces:**
- Consumes: `AgentLogger(session_metadata, agent_name, attempt)`（`agent_logger.py:19`，不变）、`WorkflowLogger.log_llm_response(agent_name, turn, content)` / `log_tool_start(agent_name, tool_name, parameters)`（`workflow_logger.py:120,113`，不变）、`AuditSession.log_error(error, context=None)`（不变）
- Produces:
  - `SessionToolAuditLogger(session, agent_name: str, attempt: int = 1)` —— 新签名
  - `SessionToolAuditLogger.initialize() -> None` —— 写 per-agent JSON header + agent_start
  - `SessionToolAuditLogger.close(success: bool, duration_ms: int) -> None` —— 写 agent_end + 关 stream
  - `AuditSession.log_llm_turn(agent_name: str, turn: int, content: str) -> None`
  - `AuditSession.log_tool_call(agent_name: str, tool_name: str, parameters: Any) -> None`
  - 移除：`AuditSession.log_event`、`AuditSession._current_agent_name`、`AuditSession._agent_logger`

- [ ] **Step 1: 写并发回归测试（TDD 锚点，此时应红）**

Create `packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py`:

```python
import asyncio
import json
from pathlib import Path

from shannon_core.display.formatters import agent_prefix
from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_concurrent_agents_keep_correct_workflow_attribution(tmp_path: Path):
    """5 agents concurrent via asyncio.gather; each must own exactly its 5 [LLM] lines.
    Regression anchor: under the old shared _current_agent_name, all turns collapsed
    onto the last-started agent and the other four got 0."""
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    names = ["injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"]

    async def run_one(name: str) -> None:
        lg = SessionToolAuditLogger(session, name, attempt=1)
        await session.start_agent(name, f"prompt-{name}", attempt=1)
        await lg.initialize()
        for turn in range(1, 6):
            await lg.log_assistant_turn(turn, f"{name} turn {turn}")
            await lg.log_tool_start("Read", {"path": f"{name}.js"})
        await lg.close(success=True, duration_ms=100)
        await session.end_agent(name, AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.0, attempt_number=1))

    await asyncio.gather(*(run_one(n) for n in names))
    await session.close()

    wf = (generate_audit_path(meta) / "workflow.log").read_text()
    for name in names:
        # FileLogRenderer._prefixed renders known agents as '[Prefix] name'
        # (formatters.agent_prefix); all 5 vuln names are in _AGENT_PREFIXES, so each
        # [LLM]/[TOOL] line carries its own prefix+name. Under the old shared-state race
        # every turn collapsed onto the last-started agent and the other four got 0.
        who = f"{agent_prefix(name)} {name}"
        assert wf.count(f"[LLM]   {who}: Turn") == 5, f"{name} [LLM] count != 5"
        assert wf.count(f"[TOOL]  {who}: Read:") == 5, f"{name} [TOOL] count != 5"


async def test_concurrent_agents_keep_correct_per_agent_json(tmp_path: Path):
    """Each agent's JSONL log contains only its own events (no _agent_logger race)."""
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    names = ["injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"]

    async def run_one(name: str) -> None:
        lg = SessionToolAuditLogger(session, name, attempt=1)
        await session.start_agent(name, f"prompt-{name}", attempt=1)
        await lg.initialize()
        await lg.log_assistant_turn(1, f"{name} content")
        await lg.close(success=True, duration_ms=0)
        await session.end_agent(name, AgentEndResult(
            success=True, duration_ms=0, cost_usd=0.0, attempt_number=1))

    await asyncio.gather(*(run_one(n) for n in names))
    await session.close()

    agents_dir = generate_audit_path(meta) / "agents"
    for name in names:
        log_files = list(agents_dir.glob(f"*_{name}_attempt-1.log"))
        assert len(log_files) == 1, f"expected 1 log for {name}, got {log_files}"
        content = log_files[0].read_text()
        assert f"Agent: {name}" in content
        events = [json.loads(line) for line in content.split("\n") if line.startswith("{")]
        llm = [e for e in events if e["type"] == "llm_response"]
        assert len(llm) == 1
        assert llm[0]["data"]["content"] == f"{name} content"
```

- [ ] **Step 2: 跑并发测试，确认它红**

Run: `uv run pytest packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py -v`
Expected: FAIL —— `SessionToolAuditLogger.__init__` 当前签名只接 `session`，传 3 个参数报 `TypeError`（或归因坍缩导致 count==0）。这验证 bug 存在并锁定期望行为。

- [ ] **Step 3: 重写 `SessionToolAuditLogger`（持有 per-agent 状态）**

Replace the entire contents of `packages/core/src/shannon_core/audit/session_tool_audit_logger.py`:

```python
"""SessionToolAuditLogger — bridges core ToolAuditLogger events to AuditSession.

Holds per-agent state (agent_name + AgentLogger) so concurrent agents never race
on shared session fields. The MessageDispatcher/StreamCollector call
log_assistant_turn/log_tool_start/etc.; this logger routes each event to its own
per-agent JSON log AND to the shared workflow log with explicit agent attribution.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shannon_core.agents.tool_audit_logger import ToolAuditLogger
from shannon_core.audit.agent_logger import AgentLogger

if TYPE_CHECKING:
    from .session import AuditSession


class SessionToolAuditLogger(ToolAuditLogger):
    def __init__(self, session: "AuditSession", agent_name: str, attempt: int = 1) -> None:
        self._session = session
        self._agent_name = agent_name
        self._agent_logger = AgentLogger(session._meta, agent_name, attempt)

    async def initialize(self) -> None:
        """Open the per-agent JSON log and write its header + agent_start event."""
        await self._agent_logger.initialize()

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._agent_logger.log_event("tool_start", {"toolName": tool_name, "parameters": parameters})
        await self._session.log_tool_call(self._agent_name, tool_name, parameters)

    async def log_tool_end(self, result: Any) -> None:
        # tool_end has no workflow.log surface; recorded to the per-agent JSON only.
        await self._agent_logger.log_event("tool_end", {"result": str(result)[:200]})

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        await self._agent_logger.log_event("llm_response", {"turn": turn, "content": content})
        await self._session.log_llm_turn(self._agent_name, turn, content)

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await self._session.log_error(
            RuntimeError(error), context=f"turn={turn_count}, {duration_ms}ms")

    async def close(self, success: bool, duration_ms: int) -> None:
        """Write agent_end to the per-agent JSON log and close its stream."""
        await self._agent_logger.log_event("agent_end", {"success": success, "duration_ms": duration_ms})
        await self._agent_logger.close()
```

- [ ] **Step 4: 改 `AuditSession` —— 删字段、瘦身 start/end、加 log_llm_turn/log_tool_call、删 log_event**

In `packages/core/src/shannon_core/audit/session.py`:

4a. 删除第 31 行 `self._agent_logger: AgentLogger | None = None` 和第 35 行 `self._current_agent_name: str | None = None`（保留第 34 行 `self._lock`）。

4b. 替换 `start_agent`（第 47-59 行）为：

```python
    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None:
        """Save prompt, log start events, and register with metrics.

        Per-agent JSON log creation moved to SessionToolAuditLogger.initialize
        (called by the activity after this), so concurrent agents no longer race
        on a shared _agent_logger field.
        """
        await AgentLogger.save_prompt(self._meta, agent_name, prompt)

        if self._workflow_logger:
            await self._workflow_logger.log_agent(
                agent_name, "start", AgentLogDetails(attempt_number=attempt),
            )
        if self._metrics_tracker:
            self._metrics_tracker.start_agent(agent_name, attempt)
```

4c. 替换 `log_event`（第 61-77 行，整个方法）为两个语义方法：

```python
    async def log_llm_turn(self, agent_name: str, turn: int, content: str) -> None:
        """Route an LLM turn to the workflow log with explicit agent attribution."""
        if self._workflow_logger:
            await self._workflow_logger.log_llm_response(agent_name, turn, content)

    async def log_tool_call(self, agent_name: str, tool_name: str, parameters: Any) -> None:
        """Route a tool call to the workflow log with explicit agent attribution."""
        if self._workflow_logger:
            await self._workflow_logger.log_tool_start(agent_name, tool_name, parameters)
```

4d. 替换 `end_agent`（第 79-104 行）为：

```python
    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Log end events and update metrics.

        Per-agent JSON log close moved to SessionToolAuditLogger.close
        (called by the activity before this).
        """
        if self._workflow_logger:
            details = AgentLogDetails(
                attempt_number=result.attempt_number,
                duration_ms=result.duration_ms,
                cost_usd=result.cost_usd,
                success=result.success,
                error=result.error,
            )
            await self._workflow_logger.log_agent(agent_name, "end", details)

        if self._metrics_tracker:
            async with self._lock:
                await self._metrics_tracker.reload()
                await self._metrics_tracker.end_agent(agent_name, result)
```

> `AgentLogger` import（session.py 顶部）保留 —— `start_agent` 仍用 `AgentLogger.save_prompt`。`Any` 已在 typing import。

- [ ] **Step 5: 改 `test_session_tool_audit_logger.py` —— 3 处构造改签名 + 加 initialize/close，新增 2 个测试承接迁移覆盖**

Replace the entire contents of `packages/whitebox/tests/test_session_tool_audit_logger.py`:

```python
from pathlib import Path

from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


def _read_log(tmp_path: Path) -> str:
    return (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()


async def test_tool_start_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.log_tool_start("Read", {"file_path": "/app/main.py"})
    await lg.close(success=True, duration_ms=0)
    await session.end_agent("recon", AgentEndResult(
        success=True, duration_ms=0, cost_usd=0.0, attempt_number=1))
    await session.close()
    assert "[TOOL]  recon: Read:" in _read_log(tmp_path)
    assert "file_path=/app/main.py" in _read_log(tmp_path)


async def test_assistant_turn_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.log_assistant_turn(2, "Found sinks")
    await lg.close(success=True, duration_ms=0)
    await session.end_agent("recon", AgentEndResult(
        success=True, duration_ms=0, cost_usd=0.0, attempt_number=1))
    await session.close()
    assert "[LLM]   recon: Turn 2:" in _read_log(tmp_path)


async def test_log_error_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.log_error("boom", turn_count=3, duration_ms=1000)
    await session.close()
    assert "[ERROR]" in _read_log(tmp_path)
    assert "boom" in _read_log(tmp_path)


async def test_initialize_creates_per_agent_log(tmp_path: Path):
    """initialize() writes the per-agent JSON header + agent_start (covers the
    migration of the old test_start_agent_creates_agent_log from test_audit_session)."""
    from shannon_whitebox.audit.utils import generate_audit_path
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await session.close()
    log_files = list((generate_audit_path(_make_meta(tmp_path)) / "agents").glob("*_recon_attempt-1.log"))
    assert len(log_files) == 1


async def test_close_writes_agent_end(tmp_path: Path):
    """close() writes the agent_end event to the per-agent JSON (covers the migration
    of the old test_end_agent_writes_agent_end_event from test_audit_session)."""
    import json
    from shannon_whitebox.audit.utils import generate_audit_path
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.close(success=True, duration_ms=5000)
    await session.close()
    agent_log = list((generate_audit_path(_make_meta(tmp_path)) / "agents").glob("*.log"))[0]
    events = [json.loads(l) for l in agent_log.read_text().split("\n") if l.startswith("{")]
    end_events = [e for e in events if e["type"] == "agent_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["success"] is True
```

- [ ] **Step 6: 改 `test_audit_session.py` —— 删 4 个失效测试，改 `test_full_lifecycle`**

In `packages/whitebox/tests/test_audit_session.py`:

6a. 删除这 4 个测试函数（它们依赖被删的 `log_event` 或被移走的 agent_log 创建/close）：
- `test_start_agent_creates_agent_log`（第 30-38 行）—— 覆盖已迁移到 `test_initialize_creates_per_agent_log`
- `test_log_event_dispatches_to_both_loggers`（第 50-66 行）—— 覆盖在 `test_tool_start_reaches_workflow_log`
- `test_log_event_dispatches_llm_response`（第 69-78 行）—— 覆盖在 `test_assistant_turn_reaches_workflow_log`
- `test_end_agent_writes_agent_end_event`（第 94-107 行）—— 覆盖已迁移到 `test_close_writes_agent_end`

6b. 替换 `test_full_lifecycle`（第 191-237 行）为（用桥接器替代 `session.log_event`）：

```python
async def test_full_lifecycle(tmp_path: Path):
    """End-to-end: initialize → start_agent → logger events → end_agent → complete."""
    from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-lifecycle")

    await session.log_phase_start("recon")
    await session.start_agent("recon", "Analyze the target application", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.log_tool_start("Read", {"file_path": "/app/main.py"})
    await lg.log_assistant_turn(1, "Identified SQL injection points")
    await lg.close(success=True, duration_ms=15000)
    await session.end_agent("recon", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.08))
    await session.log_phase_complete("recon")

    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=15000,
        total_cost_usd=0.08,
        completed_agents=["recon"],
        agent_metrics={"recon": AgentMetricsSummary(duration_ms=15000, cost_usd=0.08)},
    )
    await session.log_workflow_complete(summary)

    ad = _audit_dir(tmp_path)
    wf = (ad / "workflow.log").read_text()
    assert "Shannon Pentest - Workflow Log" in wf
    assert "Workflow ID: wf-lifecycle" in wf
    assert "[PHASE] Starting recon" in wf
    assert "[AGENT] recon: Starting" in wf
    assert "[TOOL]  recon: Read:" in wf
    assert "[LLM]   recon: Turn 1:" in wf
    assert "[AGENT] recon: Completed" in wf
    assert "[PHASE] Completed recon" in wf
    assert "Workflow COMPLETED" in wf

    data = json.loads((ad / "session.json").read_text())
    assert data["session"]["status"] == "completed"
    assert data["metrics"]["total_duration_ms"] == 15000
    assert data["metrics"]["agents"]["recon"]["success"] is True

    agent_log = list((ad / "agents").glob("*.log"))[0]
    agent_content = agent_log.read_text()
    assert "Agent: recon" in agent_content
    json_lines = [json.loads(l) for l in agent_content.split("\n") if l.startswith("{")]
    assert len(json_lines) == 4  # agent_start + tool_start + llm_response + agent_end
```

> `test_audit_session.py` 其余测试（initialize/saves_prompt/metrics/phase/workflow_complete/resume/track_step 等）不动 —— 它们不依赖被删的字段/方法。

- [ ] **Step 7: 跑 core 相关测试，确认全绿**

Run: `uv run pytest packages/whitebox/tests/test_session_tool_audit_logger.py packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py packages/whitebox/tests/test_audit_session.py packages/whitebox/tests/test_agent_logger.py -v`
Expected: PASS（包括并发回归锚点的两个测试）。若 `[LLM]`/`[TOOL]` 行格式断言失败，核对 `test_session_tool_audit_logger.py` 原断言的空格数（`[LLM]` 后 3 空格、`[TOOL]` 后 2 空格）并校正。

- [ ] **Step 8: 跑 display 回归，确认未碰 display 层**

Run: `uv run pytest packages/core/tests/display/ -v`
Expected: PASS（display 层未改）。

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/audit/session_tool_audit_logger.py \
        packages/core/src/shannon_core/audit/session.py \
        packages/whitebox/tests/test_session_tool_audit_logger.py \
        packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py \
        packages/whitebox/tests/test_audit_session.py
git commit -m "fix(audit): sink per-agent state into SessionToolAuditLogger

AuditSession was a process-global singleton; its shared _current_agent_name
and _agent_logger fields raced under concurrent vuln agents (last start_agent
wins), collapsing agent attribution in the live screen, workflow.log, and
per-agent JSON logs.

Move agent_name + AgentLogger ownership into the per-agent
SessionToolAuditLogger; AuditSession becomes a pure facade. Adds concurrent
regression tests as the anchor that would have caught this.

Activities (whitebox + blackbox) migrated in the next commit."
```

> 此时 whitebox/blackbox activities 仍用旧 `SessionToolAuditLogger(session)` 签名 → prod 代码暂时破，但本任务的测试子集不 import activities，故步骤 7/8 绿。Task 2 立即收尾。

---

## Task 2: 迁移 whitebox + blackbox 调用点 + wiring 测试

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（`run_agent`，第 74-125 行）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`（4 处：第 78、135、191、281 行）
- Modify: `packages/whitebox/tests/test_activity_display_wiring.py`（第 28 行构造点）
- Modify: `packages/blackbox/tests/test_activity_display_wiring.py`（第 23 行构造点）

**Interfaces:**
- Consumes: Task 1 产出的 `SessionToolAuditLogger(session, agent_name, attempt)` + `initialize()`/`close(success, duration_ms)`
- Produces: 所有 activity 用新签名构造桥接器，并在 `start_agent` 后 `initialize`、`end_agent` 前 `close`（含失败路径）

- [ ] **Step 1: 改 whitebox `run_agent`**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`：

1a. 第 82 行：
```python
# before
tool_audit_logger = SessionToolAuditLogger(session)
# after
tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
```

1b. 在 `await session.start_agent(...)`（第 90 行）之后、`metrics = await executor.execute(...)`（第 91 行）之前插入：
```python
        await tool_audit_logger.initialize()
```

1c. 在 `metrics = await executor.execute(...)` 之后、`await session.end_agent(...)`（第 102 行，成功路径）之前插入：
```python
        await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)
```

1d. 在两个 except 分支里，`await session.end_agent(...)` 之前各插入（计算 duration 后）：
```python
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
```
（`PentestError` 分支在第 111 行的 `end_agent` 之前；`Exception` 分支在第 120 行的 `end_agent` 之前。两处 `duration_ms` 表达式与该分支 `end_agent` 已用的完全一致。）

- [ ] **Step 2: 改 blackbox 4 处构造点 + initialize/close 配对**

`packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 的 4 个 activity 结构相同（都有 `agent_name`、`attempt = activity.info().attempt`、`agent_start = time.monotonic()`、双 except）。对每一个做与 Step 1 相同的 4 处改动：

- `run_blackbox_auth_validation`（构造点第 78 行；start 第 87 行；无 metrics 变量——成功路径在第 100 行 `end_agent` 前 `close` 用 `int((time.monotonic() - agent_start) * 1000)`）
- `run_recon`（构造点第 135 行；start 第 147 行；成功路径在第 157 行 `end_agent` 前 `close(success=True, duration_ms=metrics.duration_ms)`）
- `run_exploit_agent`（构造点第 191 行；start 第 202 行；成功路径在第 214 行 `end_agent` 前 `close(success=True, duration_ms=metrics.duration_ms)`）
- `run_report_agent`（构造点第 281 行；start 第 289 行；成功路径在第 300 行 `end_agent` 前 `close(success=True, duration_ms=metrics.duration_ms)`）

每个 activity 的两个 except 分支（`PentestError` / `Exception`）在各自 `end_agent` 之前插入：
```python
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
```

> `run_blackbox_auth_validation` 的成功路径没有 `metrics` 变量（它调 `validate_authentication` 返回 result），用 `int((time.monotonic() - agent_start) * 1000)` 作 `duration_ms`，与该函数 `end_agent` 已用表达式一致。

- [ ] **Step 3: 改 whitebox `test_activity_display_wiring.py` 的构造测试**

In `packages/whitebox/tests/test_activity_display_wiring.py`，把 `test_session_tool_audit_logger_feeds_workflow_log`（第 20-40 行）整体替换为（新签名 + initialize/close 配对；agent_name 为该测试既有的 `"injection-vuln"`）：

```python
async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    """L3: tool/llm events through SessionToolAuditLogger reach workflow.log
    via AuditSession -> WorkflowLogger -> dispatcher -> FileLogRenderer."""
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-vuln", "p", attempt=1)
        lg = SessionToolAuditLogger(session, "injection-vuln", attempt=1)
        await lg.initialize()
        await lg.log_tool_start("Bash", {"command": "rg -n eval"})
        await lg.log_assistant_turn(1, "found sinks")
        await lg.close(success=True, duration_ms=100)
        await session.end_agent("injection-vuln", AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.01, attempt_number=1))
    finally:
        clear_audit_session()
        await session.close()
    wf = (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()
    assert "[AGENT] [Injection] injection-vuln: Starting" in wf
    assert "[TOOL]  [Injection] injection-vuln: Bash:" in wf
    assert "[LLM]   [Injection] injection-vuln: Turn 1:" in wf
    assert "[AGENT] [Injection] injection-vuln: Completed" in wf
```

> 同文件的 `test_run_agent_failure_path_logs_end_and_error`（调真实 `run_agent`，第 43 行）无需手工改 —— Step 1 改完 `run_agent` 后它自动走新签名（失败路径含 `close(success=False, ...)`），断言 `[Injection] injection-vuln` + `boom` 仍成立。`test_step_intent_flows_end_to_end_from_registry` 不涉及桥接器，不动。

- [ ] **Step 4: 改 blackbox `test_activity_display_wiring.py` 的构造测试**

In `packages/blackbox/tests/test_activity_display_wiring.py`，把 `test_session_tool_audit_logger_feeds_workflow_log`（第 17-35 行）整体替换为（agent_name 为该测试既有的 `"injection-exploit"`）：

```python
async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-exploit", "p", attempt=1)
        lg = SessionToolAuditLogger(session, "injection-exploit", attempt=1)
        await lg.initialize()
        await lg.log_tool_start("Bash", {"command": "curl 'http://x/?q=<script>'"})
        await lg.log_assistant_turn(1, "confirmed reflected XSS")
        await lg.close(success=True, duration_ms=100)
        await session.end_agent("injection-exploit", AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.01, attempt_number=1))
    finally:
        clear_audit_session()
        await session.close()
    wf = (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()
    assert "[AGENT] [Injection] injection-exploit: Starting" in wf
    assert "[TOOL]  [Injection] injection-exploit: Bash:" in wf
    assert "[LLM]   [Injection] injection-exploit: Turn 1:" in wf
    assert "[AGENT] [Injection] injection-exploit: Completed" in wf
```

- [ ] **Step 5: 跑 whitebox wiring + audit 测试**

Run: `uv run pytest packages/whitebox/tests/test_activity_display_wiring.py packages/whitebox/tests/test_session_tool_audit_logger.py packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py packages/whitebox/tests/test_audit_session.py -v`
Expected: PASS。

- [ ] **Step 6: 跑 blackbox wiring 测试**

Run: `uv run pytest packages/blackbox/tests/test_activity_display_wiring.py packages/blackbox/tests/test_audit_injection.py -v`
Expected: PASS。

- [ ] **Step 7: 静态确认无遗漏的旧签名构造点**

Run: `grep -rn "SessionToolAuditLogger(session)" packages/`
Expected: 无输出（所有构造点都已带 agent_name）。若有残留，按 Task 2 Step 1 模式补齐。

- [ ] **Step 8: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py \
        packages/blackbox/src/shannon_blackbox/pipeline/activities.py \
        packages/whitebox/tests/test_activity_display_wiring.py \
        packages/blackbox/tests/test_activity_display_wiring.py
git commit -m "fix(audit): migrate whitebox+blackbox activities to per-agent logger

Update all 5 SessionToolAuditLogger construction sites (whitebox run_agent +
blackbox auth-validation/recon/exploit/report) to the new signature, with
initialize()/close() bracketing execute() — including failure paths — so
each concurrent agent owns its events end-to-end."
```

---

## Task 3: 全量回归 + 真机冒烟 + memory 回填

**Files:**
- 无代码改动；验证 + 文档。

- [ ] **Step 1: 跑 audit + display + agents 全套相关子集**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py packages/whitebox/tests/test_session_tool_audit_logger.py packages/whitebox/tests/test_session_tool_audit_logger_concurrency.py packages/whitebox/tests/test_activity_display_wiring.py packages/whitebox/tests/test_agent_logger.py packages/whitebox/tests/test_workflow_logger.py packages/blackbox/tests/test_activity_display_wiring.py packages/blackbox/tests/test_audit_injection.py packages/core/tests/display/ packages/core/tests/agents/ -v`
Expected: PASS（不跑全量 —— 避开 Temporal/网络慢测试，见 memory `pytest-whitebox-hang`）。

- [ ] **Step 2: 真机冒烟（人工，沙箱无法跑真实 Temporal 扫描）**

在真仓库执行 `uv run shannon-whitebox start -r <repo>`，到 `vulnerability-analysis` 阶段核对：
- 逐轮 `💭 [Injection]/[XSS]/[Auth]/[SSRF]/[Authz] Turn N` 前缀**各归各位**，不再全标同一个；
- 状态栏每个并发 agent 的 `tN` **各自递增**，不再 4 个停 `t0`；
- `shannon-whitebox logs <id> --follow` 的 `[LLM]`/`[TOOL]` 行 agent 归因正确；
- 5 个 agent 仍真并发完成（耗时不回归）；
- 每个 per-agent JSON log（`workspaces/.../agents/*_{name}_attempt-1.log`）只含该 agent 自己的事件。

- [ ] **Step 3: 回填 memory**

更新 memory：
- `audit-session-current-agent-race.md`：标记已修（附 commit hash）。
- `rich-display-visibility-status.md` / `whitebox-display-clarity-redesign.md`：标记真机冒烟完成、归因坍缩已修。

- [ ] **Step 4: 收尾 commit（若 memory 在仓库内则一并提交；否则仅记录）**

若 memory 文件在仓库内：
```bash
git add <memory-files-if-tracked>
git commit -m "docs(memory): mark audit attribution race fixed + smoke verified"
```
否则跳过（memory 在 `~/.claude` 不进仓库）。

---

## Self-Review（写计划后自检，已执行）

**1. Spec 覆盖：**
- §5.1 SessionToolAuditLogger 改造 → Task 1 Step 3 ✓
- §5.2 AuditSession 瘦身（删字段/start/end/log_event、加 log_llm_turn/log_tool_call）→ Task 1 Step 4 ✓
- §5.3 activity 时序（含失败路径 close）→ Task 2 Step 1-2 ✓
- §5.4 blackbox 同步 → Task 2 Step 2 ✓
- §9.1 并发回归锚点 → Task 1 Step 1-2 ✓
- §9.2 现有测试迁移（5 构造点 + test_audit_session 迁移）→ Task 1 Step 5-6 + Task 2 Step 3-4 ✓
- §10 验收冒烟 + memory 回填 → Task 3 ✓

**2. 占位符扫描：** 无 TODO/TBD。Task 2 Step 3-4 的 wiring 测试已读出实际 agent_name（whitebox `injection-vuln`、blackbox `injection-exploit`）并给出完整函数代码。并发回归测试的 `[LLM]`/`[TOOL]` 断言已对齐 `FileLogRenderer._prefixed` 的真实渲染格式（`[Prefix] name`，因为 5 个 vuln 名都在 `_AGENT_PREFIXES` 表内）。

**3. 类型一致：**
- `close(success: bool, duration_ms: int)` —— Task 1 Step 3 定义、Step 5/6 测试、Task 2 调用全部一致。
- `initialize() -> None` —— 定义与所有调用一致。
- `log_llm_turn`/`log_tool_call` —— session.py 定义（Step 4c）、session_tool_audit_logger.py 调用（Step 3）一致。
- `SessionToolAuditLogger(session, agent_name, attempt=1)` —— 所有构造点（Task 1 测试 + Task 2 activities + wiring）一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-22-audit-session-agent-attribution.md`.
