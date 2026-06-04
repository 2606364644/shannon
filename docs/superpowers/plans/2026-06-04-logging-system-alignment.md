# Logging System Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Align shannon-py's logging system with the original TypeScript implementation by adding phase-level metrics aggregation, a unified ActivityLogger abstraction, an AuditLogger bridge with Null Object pattern, and CLI progress enhancements.

**Architecture:** Four independent layers built bottom-up. Layer 1 extends MetricsTracker to aggregate metrics by pipeline phase. Layer 2 introduces an ActivityLogger ABC with Temporal and Console implementations. Layer 3 creates an AuditLogger bridge to eliminate null checks in agent execution. Layer 4 adds a terminal spinner (ProgressIndicator) and upgrades the `logs` command to tail workflow.log in real-time.

**Tech Stack:** Python 3.12+, asyncio, pydantic, aiofiles, click, temporalio, watchdog (new dependency)

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `packages/core/src/shannon_core/logging/__init__.py` | Package init, re-exports ActivityLogger, create_activity_logger |
| `packages/core/src/shannon_core/logging/activity_logger.py` | ActivityLogger ABC + TemporalActivityLogger + ConsoleActivityLogger + factory |
| `packages/whitebox/src/shannon_whitebox/audit/audit_logger.py` | AuditLogger ABC + RealAuditLogger + NullAuditLogger + factory |
| `packages/whitebox/src/shannon_whitebox/cli/progress.py` | ProgressIndicator spinner + NullProgressIndicator + factory |
| `packages/core/src/shannon_core/cli/__init__.py` | Package init for shared CLI utilities |
| `packages/core/src/shannon_core/cli/logs.py` | LogFileHandler + tail_workflow_log function |
| `packages/core/tests/test_activity_logger.py` | Tests for ActivityLogger factory and implementations |
| `packages/whitebox/tests/test_audit_logger.py` | Tests for AuditLogger bridge and Null Object |
| `packages/whitebox/tests/test_progress_indicator.py` | Tests for ProgressIndicator |
| `packages/core/tests/test_cli_logs.py` | Tests for log tailing and completion detection |

### Modified Files
| File | Change |
|------|--------|
| `packages/core/src/shannon_core/models/audit.py` | Add PhaseMetrics model |
| `packages/core/src/shannon_core/models/agents.py` | Add AGENT_PHASE_MAP constant |
| `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py` | Add phase aggregation in end_agent |
| `packages/whitebox/src/shannon_whitebox/audit/__init__.py` | Export AuditLogger, create_audit_logger |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Upgrade `logs` command to use tail, add --follow flag |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Upgrade `logs` command to use tail, add --follow flag |
| `packages/core/pyproject.toml` | Add watchdog dependency |

---

## Task 1: Add PhaseMetrics Model and AGENT_PHASE_MAP

**Files:**
- Modify: `packages/core/src/shannon_core/models/audit.py` (append after line 44)
- Modify: `packages/core/src/shannon_core/models/agents.py` (append after line 166)
- Test: `packages/core/tests/test_metrics.py`

- [x] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_metrics.py`:

```python
from shannon_core.models.audit import PhaseMetrics


def test_phase_metrics_defaults():
    pm = PhaseMetrics()
    assert pm.duration_ms == 0
    assert pm.duration_percentage == 0.0
    assert pm.cost_usd == 0.0
    assert pm.agent_count == 0


def test_phase_metrics_with_values():
    pm = PhaseMetrics(
        duration_ms=15000,
        duration_percentage=12.5,
        cost_usd=0.10,
        agent_count=1,
    )
    assert pm.duration_ms == 15000
    assert pm.duration_percentage == 12.5
    assert pm.cost_usd == 0.10
    assert pm.agent_count == 1
```

Also create `packages/core/tests/test_agent_phase_map.py`:

```python
from shannon_core.models.agents import AGENT_PHASE_MAP, AgentName


def test_all_agent_names_have_phase_mapping():
    """Every AgentName enum value should have a phase mapping."""
    for agent in AgentName:
        assert agent.value in AGENT_PHASE_MAP, f"Missing phase mapping for {agent.value}"


def test_phase_mapping_values():
    assert AGENT_PHASE_MAP["pre-recon"] == "pre-recon"
    assert AGENT_PHASE_MAP["recon"] == "recon"
    assert AGENT_PHASE_MAP["recon-blackbox"] == "recon"
    assert AGENT_PHASE_MAP["injection-vuln"] == "vulnerability-analysis"
    assert AGENT_PHASE_MAP["injection-exploit"] == "exploitation"
    assert AGENT_PHASE_MAP["report"] == "reporting"


def test_validate_auth_mapped():
    """validate-authentication is a preflight activity, maps to pre-recon."""
    assert AGENT_PHASE_MAP["validate-authentication"] == "pre-recon"


def test_misconfig_agents_mapped():
    assert AGENT_PHASE_MAP["misconfig-vuln"] == "vulnerability-analysis"
    assert AGENT_PHASE_MAP["misconfig-exploit"] == "exploitation"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_metrics.py::test_phase_metrics_defaults packages/core/tests/test_agent_phase_map.py -v`
Expected: FAIL — `ImportError: cannot import name 'PhaseMetrics'` and `ImportError: cannot import name 'AGENT_PHASE_MAP'`

- [x] **Step 3: Add PhaseMetrics model**

Append to `packages/core/src/shannon_core/models/audit.py` (after the `ResumeInfo` class, line 44):

```python


class PhaseMetrics(BaseModel):
    duration_ms: int = 0
    duration_percentage: float = 0.0
    cost_usd: float = 0.0
    agent_count: int = 0
```

- [x] **Step 4: Add AGENT_PHASE_MAP constant**

Append to `packages/core/src/shannon_core/models/agents.py` (after line 166, the `PLAYWRIGHT_SESSION_MAPPING` line):

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
    "validate-authentication": "pre-recon",
}
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_metrics.py::test_phase_metrics_defaults packages/core/tests/test_metrics.py::test_phase_metrics_with_values packages/core/tests/test_agent_phase_map.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/models/audit.py packages/core/src/shannon_core/models/agents.py packages/core/tests/test_metrics.py packages/core/tests/test_agent_phase_map.py
git commit -m "feat: add PhaseMetrics model and AGENT_PHASE_MAP constant"
```

---

## Task 2: Add Phase Aggregation to MetricsTracker

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py`
- Test: `packages/whitebox/tests/test_metrics_tracker.py`

- [x] **Step 1: Write the failing tests**

Append to `packages/whitebox/tests/test_metrics_tracker.py`:

```python
async def test_end_agent_populates_phases(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    data = _read_session_json(tmp_path)
    assert "phases" in data["metrics"]
    assert "recon" in data["metrics"]["phases"]
    assert data["metrics"]["phases"]["recon"]["duration_ms"] == 30000
    assert data["metrics"]["phases"]["recon"]["cost_usd"] == 0.20
    assert data["metrics"]["phases"]["recon"]["agent_count"] == 1


async def test_end_agent_accumulates_across_phases(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.10))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["phases"]["recon"]["duration_ms"] == 30000
    assert data["metrics"]["phases"]["recon"]["agent_count"] == 1
    assert data["metrics"]["phases"]["vulnerability-analysis"]["duration_ms"] == 15000
    assert data["metrics"]["phases"]["vulnerability-analysis"]["agent_count"] == 1


async def test_end_agent_calculates_duration_percentages(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.10))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["total_duration_ms"] == 45000
    assert data["metrics"]["phases"]["recon"]["duration_percentage"] == pytest.approx(66.67, abs=0.1)
    assert data["metrics"]["phases"]["vulnerability-analysis"]["duration_percentage"] == pytest.approx(33.33, abs=0.1)


async def test_end_agent_skips_failed_agents_in_phase_aggregation(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=30000, cost_usd=0.20))
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=False, duration_ms=1000, cost_usd=0.01, error="failed"))
    data = _read_session_json(tmp_path)
    # Failed agent should NOT be counted in phases
    assert "vulnerability-analysis" not in data["metrics"]["phases"]
    assert data["metrics"]["phases"]["recon"]["duration_percentage"] == 100.0


async def test_end_agent_multiple_agents_same_phase(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("injection-vuln", 1)
    await tracker.end_agent("injection-vuln", AgentEndResult(success=True, duration_ms=10000, cost_usd=0.10))
    tracker.start_agent("xss-vuln", 1)
    await tracker.end_agent("xss-vuln", AgentEndResult(success=True, duration_ms=8000, cost_usd=0.08))
    data = _read_session_json(tmp_path)
    phase = data["metrics"]["phases"]["vulnerability-analysis"]
    assert phase["duration_ms"] == 18000
    assert phase["cost_usd"] == pytest.approx(0.18)
    assert phase["agent_count"] == 2
    assert phase["duration_percentage"] == 100.0


async def test_initialize_creates_empty_phases(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    data = _read_session_json(tmp_path)
    assert data["metrics"]["phases"] == {}


async def test_phases_backward_compatible_missing_field(tmp_path: Path):
    """Reading a session.json without 'phases' should not crash."""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # Manually strip phases to simulate old format
    data = _read_session_json(tmp_path)
    del data["metrics"]["phases"]
    session_path = _audit_dir(tmp_path) / "session.json"
    session_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    await tracker.reload()
    metrics = tracker.get_metrics()
    # Should not crash; phases defaults to empty
    assert metrics.get("phases", {}) == {}
```

Also add the missing `import pytest` at the top of the test file if not already present. Check the existing imports and add `import pytest` if missing.

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_metrics_tracker.py::test_end_agent_populates_phases -v`
Expected: FAIL — phases dict will be empty because `end_agent` doesn't populate it yet

- [x] **Step 3: Implement phase aggregation in MetricsTracker**

Replace the `end_agent` method in `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py` (lines 52–70) with:

```python
    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Persist agent results, update running totals, and aggregate phase metrics."""
        agents = self._data["metrics"]["agents"]
        if agent_name not in agents:
            agents[agent_name] = {}
        agents[agent_name].update({
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "success": result.success,
            "attempt_number": result.attempt_number,
            "model": result.model,
        })
        if result.error:
            agents[agent_name]["error"] = result.error

        self._data["metrics"]["total_duration_ms"] += result.duration_ms
        self._data["metrics"]["total_cost_usd"] += result.cost_usd

        # Phase aggregation — only for successful agents
        if result.success:
            self._aggregate_phase(agent_name, result)

        await self._atomic_write()
```

Then add the `_aggregate_phase` and `_recalculate_phase_percentages` methods after `end_agent` (before `update_session_status`):

```python
    def _aggregate_phase(self, agent_name: str, result: AgentEndResult) -> None:
        """Accumulate metrics for the agent's phase and recalculate percentages."""
        from shannon_core.models.agents import AGENT_PHASE_MAP

        phase_name = AGENT_PHASE_MAP.get(agent_name)
        if phase_name is None:
            return

        phases = self._data["metrics"]["phases"]
        if phase_name not in phases:
            phases[phase_name] = {
                "duration_ms": 0,
                "duration_percentage": 0.0,
                "cost_usd": 0.0,
                "agent_count": 0,
            }

        phases[phase_name]["duration_ms"] += result.duration_ms
        phases[phase_name]["cost_usd"] += result.cost_usd
        phases[phase_name]["agent_count"] += 1

        self._recalculate_phase_percentages()

    def _recalculate_phase_percentages(self) -> None:
        """Recalculate duration_percentage for all phases based on total duration."""
        total = self._data["metrics"]["total_duration_ms"]
        phases = self._data["metrics"]["phases"]
        if total == 0:
            for phase_data in phases.values():
                phase_data["duration_percentage"] = 0.0
            return
        for phase_data in phases.values():
            phase_data["duration_percentage"] = round(
                phase_data["duration_ms"] / total * 100, 2
            )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_metrics_tracker.py -v`
Expected: All PASS (including existing tests)

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py packages/whitebox/tests/test_metrics_tracker.py
git commit -m "feat: add phase aggregation to MetricsTracker"
```

---

## Task 3: Create ActivityLogger Abstraction

**Files:**
- Create: `packages/core/src/shannon_core/logging/__init__.py`
- Create: `packages/core/src/shannon_core/logging/activity_logger.py`
- Create: `packages/core/tests/test_activity_logger.py`

- [x] **Step 1: Write the failing tests**

Create `packages/core/tests/test_activity_logger.py`:

```python
from unittest.mock import patch, MagicMock

from shannon_core.logging.activity_logger import (
    ActivityLogger,
    ConsoleActivityLogger,
    TemporalActivityLogger,
    create_activity_logger,
)


def test_activity_logger_is_abstract():
    """ActivityLogger cannot be instantiated directly."""
    import abc
    assert abc.ABC in ActivityLogger.__bases__


def test_console_activity_logger_info():
    """ConsoleActivityLogger delegates to stdlib logging."""
    logger = ConsoleActivityLogger()
    with patch.object(logger._logger, "info") as mock_info:
        logger.info("test message", key="value")
        mock_info.assert_called_once_with("test message", extra={"key": "value"})


def test_console_activity_logger_warn():
    logger = ConsoleActivityLogger()
    with patch.object(logger._logger, "warning") as mock_warn:
        logger.warn("warning msg", code=404)
        mock_warn.assert_called_once_with("warning msg", extra={"code": 404})


def test_console_activity_logger_error():
    logger = ConsoleActivityLogger()
    with patch.object(logger._logger, "error") as mock_error:
        logger.error("error msg")
        mock_error.assert_called_once_with("error msg", extra={})


def test_create_activity_logger_returns_console_outside_temporal():
    """Without a Temporal activity context, factory returns ConsoleActivityLogger."""
    with patch("shannon_core.logging.activity_logger.create_activity_logger.__code__", create_activity_logger.__code__):
        # Force the import to fail so we're outside Temporal context
        logger = create_activity_logger()
        assert isinstance(logger, ConsoleActivityLogger)


def test_create_activity_logger_returns_temporal_inside_activity():
    """When inside a Temporal activity context, factory returns TemporalActivityLogger."""
    mock_activity = MagicMock()
    mock_activity.info.return_value = None

    with patch("temporalio.activity.info") as mock_info:
        mock_info.return_value = MagicMock()  # simulate being inside activity
        logger = create_activity_logger()
        assert isinstance(logger, TemporalActivityLogger)


def test_temporal_activity_logger_info():
    """TemporalActivityLogger delegates to temporalio.activity.logger."""
    logger = TemporalActivityLogger()
    with patch("temporalio.activity.logger") as mock_logger:
        logger.info("test msg", agent="recon")
        mock_logger.info.assert_called_once_with("test msg", extra={"agent": "recon"})


def test_temporal_activity_logger_warn():
    logger = TemporalActivityLogger()
    with patch("temporalio.activity.logger") as mock_logger:
        logger.warn("warn msg")
        mock_logger.warning.assert_called_once_with("warn msg", extra={})


def test_temporal_activity_logger_error():
    logger = TemporalActivityLogger()
    with patch("temporalio.activity.logger") as mock_logger:
        logger.error("error msg")
        mock_logger.error.assert_called_once_with("error msg", extra={})
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_activity_logger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.logging'`

- [x] **Step 3: Create the logging package**

Create `packages/core/src/shannon_core/logging/__init__.py`:

```python
from .activity_logger import ActivityLogger, create_activity_logger

__all__ = ["ActivityLogger", "create_activity_logger"]
```

Create `packages/core/src/shannon_core/logging/activity_logger.py`:

```python
import logging
from abc import ABC, abstractmethod
from typing import Any


class ActivityLogger(ABC):
    """Unified Activity logging interface. Keeps service layer decoupled from Temporal."""

    @abstractmethod
    def info(self, message: str, **attrs: Any) -> None: ...

    @abstractmethod
    def warn(self, message: str, **attrs: Any) -> None: ...

    @abstractmethod
    def error(self, message: str, **attrs: Any) -> None: ...


class TemporalActivityLogger(ActivityLogger):
    """Bridges to Temporal activity context logger. Must be used within an activity context."""

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
    """Bridges to standard library logging. Used for local runs and tests."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("shannon.activity")

    def info(self, message: str, **attrs: Any) -> None:
        self._logger.info(message, extra=attrs)

    def warn(self, message: str, **attrs: Any) -> None:
        self._logger.warning(message, extra=attrs)

    def error(self, message: str, **attrs: Any) -> None:
        self._logger.error(message, extra=attrs)


def create_activity_logger() -> ActivityLogger:
    """Factory: returns TemporalActivityLogger inside activity context, ConsoleActivityLogger otherwise."""
    try:
        from temporalio import activity
        activity.info()
        return TemporalActivityLogger()
    except Exception:
        return ConsoleActivityLogger()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_activity_logger.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/logging/__init__.py packages/core/src/shannon_core/logging/activity_logger.py packages/core/tests/test_activity_logger.py
git commit -m "feat: add ActivityLogger abstraction with Temporal and Console implementations"
```

---

## Task 4: Create AuditLogger Bridge (Null Object Pattern)

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/audit_logger.py`
- Modify: `packages/whitebox/src/shannon_whitebox/audit/__init__.py`
- Create: `packages/whitebox/tests/test_audit_logger.py`

- [x] **Step 1: Write the failing tests**

Create `packages/whitebox/tests/test_audit_logger.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from shannon_whitebox.audit.audit_logger import (
    AuditLogger,
    RealAuditLogger,
    NullAuditLogger,
    create_audit_logger,
)


def test_create_audit_logger_returns_null_when_session_is_none():
    logger = create_audit_logger(None)
    assert isinstance(logger, NullAuditLogger)


def test_create_audit_logger_returns_real_when_session_provided():
    session = MagicMock()
    logger = create_audit_logger(session)
    assert isinstance(logger, RealAuditLogger)


@pytest.mark.asyncio
async def test_null_logger_log_llm_response():
    logger = NullAuditLogger()
    # Should not raise
    await logger.log_llm_response(turn=1, content="test")


@pytest.mark.asyncio
async def test_null_logger_log_tool_start():
    logger = NullAuditLogger()
    await logger.log_tool_start("Bash", {"command": "ls"})


@pytest.mark.asyncio
async def test_null_logger_log_tool_end():
    logger = NullAuditLogger()
    await logger.log_tool_end({"exit_code": 0})


@pytest.mark.asyncio
async def test_null_logger_log_error():
    logger = NullAuditLogger()
    await logger.log_error(RuntimeError("boom"), duration=5000, turns=3)


@pytest.mark.asyncio
async def test_real_logger_log_llm_response():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    await logger.log_llm_response(turn=2, content="hello")
    session.log_event.assert_called_once_with("llm_response", {"turn": 2, "content": "hello"})


@pytest.mark.asyncio
async def test_real_logger_log_tool_start():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    await logger.log_tool_start("Read", {"file_path": "/tmp/x"})
    session.log_event.assert_called_once_with("tool_start", {"toolName": "Read", "parameters": {"file_path": "/tmp/x"}})


@pytest.mark.asyncio
async def test_real_logger_log_tool_end():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    await logger.log_tool_end({"result": "ok"})
    session.log_event.assert_called_once_with("tool_end", {"result": "ok"})


@pytest.mark.asyncio
async def test_real_logger_log_error():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    err = ValueError("bad value")
    await logger.log_error(err, duration=3000, turns=5)
    session.log_event.assert_called_once_with("error", {
        "message": "bad value",
        "errorType": "ValueError",
        "duration": 3000,
        "turns": 5,
    })
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_audit_logger.py -v`
Expected: FAIL — `ImportError: cannot import name 'AuditLogger'`

- [x] **Step 3: Create AuditLogger bridge**

Create `packages/whitebox/src/shannon_whitebox/audit/audit_logger.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import AuditSession


class AuditLogger(ABC):
    """Audit logging bridge for the AI execution layer. Callers never need null checks."""

    @abstractmethod
    async def log_llm_response(self, turn: int, content: str) -> None: ...

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: Exception, duration: int, turns: int) -> None: ...


class RealAuditLogger(AuditLogger):
    """Bridges to AuditSession for actual logging."""

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
    """No-op implementation. All methods are safe to call without effect."""

    async def log_llm_response(self, turn: int, content: str) -> None: pass
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: pass
    async def log_tool_end(self, result: Any) -> None: pass
    async def log_error(self, error: Exception, duration: int, turns: int) -> None: pass


def create_audit_logger(audit_session: AuditSession | None) -> AuditLogger:
    """Factory: returns RealAuditLogger if session exists, NullAuditLogger otherwise."""
    if audit_session is not None:
        return RealAuditLogger(audit_session)
    return NullAuditLogger()
```

- [x] **Step 4: Update audit __init__.py exports**

Replace the entire content of `packages/whitebox/src/shannon_whitebox/audit/__init__.py` with:

```python
from .session import AuditSession
from .audit_logger import AuditLogger, create_audit_logger

__all__ = ["AuditSession", "AuditLogger", "create_audit_logger"]
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_audit_logger.py packages/whitebox/tests/test_audit_session.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/audit_logger.py packages/whitebox/src/shannon_whitebox/audit/__init__.py packages/whitebox/tests/test_audit_logger.py
git commit -m "feat: add AuditLogger bridge with Null Object pattern"
```

---

## Task 5: Create ProgressIndicator

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/cli/progress.py`
- Create: `packages/whitebox/tests/test_progress_indicator.py`

- [x] **Step 1: Write the failing tests**

Create `packages/whitebox/tests/test_progress_indicator.py`:

```python
import io
from unittest.mock import patch

from shannon_whitebox.cli.progress import (
    ProgressIndicator,
    NullProgressIndicator,
    create_progress_indicator,
)


def test_create_progress_indicator_returns_real_when_enabled():
    indicator = create_progress_indicator("Working...", enabled=True)
    assert isinstance(indicator, ProgressIndicator)


def test_create_progress_indicator_returns_null_when_disabled():
    indicator = create_progress_indicator("Working...", enabled=False)
    assert isinstance(indicator, NullProgressIndicator)


def test_progress_indicator_start_stop():
    indicator = ProgressIndicator("Testing...")
    indicator.start()
    assert indicator._running is True
    indicator.stop()
    assert indicator._running is False


def test_progress_indicator_finish_prints_complete():
    indicator = ProgressIndicator("Testing...")
    indicator.start()
    with patch("builtins.print") as mock_print:
        indicator.finish("All done")
        mock_print.assert_called_once_with("✓ All done")
    assert indicator._running is False


def test_progress_indicator_stop_when_not_started():
    """Calling stop() without start() should not raise."""
    indicator = ProgressIndicator("Idle")
    indicator.stop()
    assert indicator._running is False


def test_progress_indicator_start_idempotent():
    """Calling start() twice should not spawn a second thread."""
    indicator = ProgressIndicator("Double")
    indicator.start()
    thread1 = indicator._thread
    indicator.start()
    thread2 = indicator._thread
    assert thread1 is thread2
    indicator.stop()


def test_null_progress_indicator_is_noop():
    """All methods on NullProgressIndicator should execute without error."""
    indicator = NullProgressIndicator()
    indicator.start()
    indicator.stop()
    indicator.finish("done")


def test_progress_indicator_spinner_frames():
    indicator = ProgressIndicator("Loading")
    assert len(indicator._frames) == 10
    assert indicator._frames[0] == "⠋"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_progress_indicator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_whitebox.cli.progress'`

- [x] **Step 3: Create ProgressIndicator**

First, ensure the CLI package directory has an `__init__.py`. Check if `packages/whitebox/src/shannon_whitebox/cli/__init__.py` exists; if not, create it:

```python
```

Then create `packages/whitebox/src/shannon_whitebox/cli/progress.py`:

```python
import sys
import threading

from typing import Protocol


class ProgressIndicator:
    """Terminal spinner animation for long-running agent execution."""

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


class NullProgressIndicator:
    """No-op ProgressIndicator when spinner is disabled."""
    def start(self) -> None: pass
    def stop(self) -> None: pass
    def finish(self, message: str = "Complete") -> None: pass


def create_progress_indicator(message: str, enabled: bool = True) -> ProgressIndicator | NullProgressIndicator:
    """Factory: returns ProgressIndicator when enabled, NullProgressIndicator otherwise."""
    if enabled:
        return ProgressIndicator(message)
    return NullProgressIndicator()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_progress_indicator.py -v`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/progress.py packages/whitebox/src/shannon_whitebox/cli/__init__.py packages/whitebox/tests/test_progress_indicator.py
git commit -m "feat: add ProgressIndicator spinner with Null Object pattern"
```

---

## Task 6: Add watchdog Dependency and Create Log Tailing Module

**Files:**
- Modify: `packages/core/pyproject.toml`
- Create: `packages/core/src/shannon_core/cli/__init__.py`
- Create: `packages/core/src/shannon_core/cli/logs.py`
- Create: `packages/core/tests/test_cli_logs.py`

- [x] **Step 1: Add watchdog dependency**

Add `"watchdog>=4.0"` to the `dependencies` list in `packages/core/pyproject.toml`. The dependencies section should become:

```toml
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "aiofiles>=23.0",
    "python-dotenv>=1.0",
    "tree-sitter>=0.24",
    "tree-sitter-python>=0.23",
    "tree-sitter-typescript>=0.23",
    "tree-sitter-go>=0.23",
    "tree-sitter-java>=0.23",
    "tree-sitter-php>=0.23",
    "claude-agent-sdk>=0.2.87",
    "anthropic>=0.40",
    "openai>=1.50",
    "temporalio>=1.0",
    "watchdog>=4.0",
]
```

- [x] **Step 2: Install the new dependency**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv sync`

- [x] **Step 3: Write the failing tests**

Create `packages/core/src/shannon_core/cli/__init__.py`:

```python
```

Create `packages/core/tests/test_cli_logs.py`:

```python
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

from shannon_core.cli.logs import LogFileHandler, COMPLETION_PATTERN


def test_completion_pattern_matches_completed():
    assert COMPLETION_PATTERN.search("Workflow COMPLETED\n")


def test_completion_pattern_matches_failed():
    assert COMPLETION_PATTERN.search("Workflow FAILED\n")


def test_completion_pattern_no_match_in_progress():
    assert not COMPLETION_PATTERN.search("some intermediate log line")


def test_log_file_handler_flush_new_content(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("line 1\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    # First flush reads from position 0
    completed = handler.flush()
    assert completed is False
    assert handler._position == len("line 1\n")


def test_log_file_handler_flush_detects_completion(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("Workflow COMPLETED\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    completed = handler.flush()
    assert completed is True


def test_log_file_handler_flush_no_new_content(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("line 1\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    handler.flush()  # consume initial content
    # No new content
    completed = handler.flush()
    assert completed is False


def test_log_file_handler_flush_missing_file(tmp_path: Path):
    log_path = tmp_path / "nonexistent.log"
    handler = LogFileHandler(log_path)
    completed = handler.flush()
    assert completed is True  # missing file treated as completion


def test_log_file_handler_incremental_flush(tmp_path: Path):
    log_path = tmp_path / "workflow.log"
    log_path.write_text("line 1\n", encoding="utf-8")
    handler = LogFileHandler(log_path)
    handler.flush()
    # Append more content
    log_path.write_text("line 1\nline 2\n", encoding="utf-8")
    with patch("sys.stdout") as mock_stdout:
        completed = handler.flush()
        assert completed is False
        mock_stdout.write.assert_called_once_with("line 2\n")


def test_tail_workflow_log_missing_workspace(tmp_path: Path, capsys):
    from shannon_core.cli.logs import tail_workflow_log
    with patch.object(sys, "exit") as mock_exit:
        tail_workflow_log("nonexistent-workspace", workspaces_dir=str(tmp_path))
        mock_exit.assert_called_once_with(1)
    captured = capsys.readouterr()
    assert "Workflow log not found" in captured.err
```

- [x] **Step 4: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_cli_logs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.cli'`

- [x] **Step 5: Create the log tailing module**

Create `packages/core/src/shannon_core/cli/logs.py`:

```python
import re
import sys
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


COMPLETION_PATTERN = re.compile(r"^Workflow (COMPLETED|FAILED)$", re.MULTILINE)


class LogFileHandler(FileSystemEventHandler):
    """Watches a workflow.log file and outputs new content to stdout."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._position = 0

    def flush(self) -> bool:
        """Output new content since last read. Returns True if completion marker detected."""
        try:
            size = self._path.stat().st_size
            if size <= self._position:
                return False
            content = self._path.read_text(encoding="utf-8")
            new_content = content[self._position :]
            self._position = size
            sys.stdout.write(new_content)
            sys.stdout.flush()
            return bool(COMPLETION_PATTERN.search(new_content))
        except Exception:
            return True  # File deleted or unreadable, treat as complete

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                raise SystemExit(0)


def tail_workflow_log(workspace_id: str, workspaces_dir: str = "workspaces") -> None:
    """Tail a workflow.log in real-time, like tail -f. Auto-exits on Workflow COMPLETED/FAILED."""
    base = Path(workspaces_dir)

    # 1. Direct match
    log_path = base / workspace_id / "workflow.log"
    if not log_path.exists():
        # 2. Try stripping resume suffix
        stripped = re.sub(r"_resume_\d+$", "", workspace_id)
        if stripped != workspace_id:
            log_path = base / stripped / "workflow.log"
        if not log_path.exists():
            print(f"ERROR: Workflow log not found for: {workspace_id}", file=sys.stderr)
            sys.exit(1)

    handler = LogFileHandler(log_path)
    print(f"Tailing workflow log: {log_path}")

    # Output existing content
    if handler.flush():
        sys.exit(0)

    # Watch for changes
    observer = Observer()
    observer.schedule(handler, str(log_path.parent), recursive=False)
    observer.start()

    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
```

- [x] **Step 6: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_cli_logs.py -v`
Expected: All PASS

- [x] **Step 7: Commit**

```bash
git add packages/core/pyproject.toml packages/core/src/shannon_core/cli/__init__.py packages/core/src/shannon_core/cli/logs.py packages/core/tests/test_cli_logs.py
git commit -m "feat: add log tailing module with watchdog-based file watching"
```

---

## Task 7: Upgrade CLI `logs` Commands with --follow Flag

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py` (lines 105–118)
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py` (lines 133–146)

- [x] **Step 1: Write the failing test**

Add to `packages/whitebox/tests/test_cli.py` (or create a new test section if the file already has CLI tests):

```python
from click.testing import CliRunner
from shannon_whitebox.cli.main import cli


def test_logs_command_accepts_follow_flag(tmp_path, monkeypatch):
    """The logs command should accept a --follow flag."""
    runner = CliRunner()
    # Create a workspace with a workflow.log
    ws = tmp_path / "test-ws"
    ws.mkdir()
    (ws / "workflow.log").write_text("line 1\n")
    monkeypatch.chdir(tmp_path)
    # Just test that --follow is accepted as an option (it will error on missing watchdog setup in test, but the flag should parse)
    result = runner.invoke(cli, ["logs", "test-ws", "--follow"])
    # We expect it to either work or fail at runtime, not at argument parsing
    assert "--follow" not in (result.output or "")  # --follow shouldn't appear as an error about unknown option


def test_logs_command_shows_content_without_follow(tmp_path, monkeypatch):
    """Without --follow, logs command should cat the file."""
    runner = CliRunner()
    ws = tmp_path / "test-ws"
    ws.mkdir()
    (ws / "workflow.log").write_text("hello from log\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["logs", "test-ws"])
    assert result.exit_code == 0
    assert "hello from log" in result.output
```

Add the same pattern to `packages/blackbox/tests/test_cli.py`:

```python
from click.testing import CliRunner
from shannon_blackbox.cli.main import cli


def test_logs_command_accepts_follow_flag(tmp_path, monkeypatch):
    runner = CliRunner()
    ws = tmp_path / "test-ws"
    ws.mkdir()
    (ws / "workflow.log").write_text("line 1\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["logs", "test-ws", "--follow"])
    assert "--follow" not in (result.output or "")


def test_logs_command_shows_content_without_follow(tmp_path, monkeypatch):
    runner = CliRunner()
    ws = tmp_path / "test-ws"
    ws.mkdir()
    (ws / "workflow.log").write_text("hello from log\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["logs", "test-ws"])
    assert result.exit_code == 0
    assert "hello from log" in result.output
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py::test_logs_command_accepts_follow_flag -v`
Expected: FAIL — `No such option: --follow`

- [x] **Step 3: Update whitebox CLI logs command**

Replace the `logs` command in `packages/whitebox/src/shannon_whitebox/cli/main.py` (lines 105–118) with:

```python
@cli.command()
@click.argument("workspace_name")
@click.option("--follow", is_flag=True, help="Tail the log in real-time (auto-exits on completion)")
def logs(workspace_name, follow):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if not log_file.exists():
        click.echo("No logs found")
        return
    if follow:
        from shannon_core.cli.logs import tail_workflow_log
        tail_workflow_log(workspace_name)
    else:
        click.echo(log_file.read_text())
```

- [x] **Step 4: Update blackbox CLI logs command**

Replace the `logs` command in `packages/blackbox/src/shannon_blackbox/cli/main.py` (lines 133–146) with:

```python
@cli.command()
@click.argument("workspace_name")
@click.option("--follow", is_flag=True, help="Tail the log in real-time (auto-exits on completion)")
def logs(workspace_name, follow):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if not log_file.exists():
        click.echo("No logs found")
        return
    if follow:
        from shannon_core.cli.logs import tail_workflow_log
        tail_workflow_log(workspace_name)
    else:
        click.echo(log_file.read_text())
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py -v -k "logs"`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py
git commit -m "feat: upgrade logs command with --follow flag for real-time tailing"
```

---

## Task 8: Add Worker Progress Polling

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`
- Create: `packages/whitebox/tests/test_worker_progress.py`

- [x] **Step 1: Write the failing tests**

Create `packages/whitebox/tests/test_worker_progress.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_whitebox.worker import poll_workflow_progress


async def test_poll_workflow_progress_queries_and_prints():
    handle = MagicMock()
    progress = MagicMock()
    progress.elapsed_ms = 30000
    progress.current_phase = "recon"
    progress.current_agent = "recon"
    progress.completed_agents = ["preflight"]
    handle.query = AsyncMock(return_value=progress)

    with patch("builtins.print") as mock_print:
        task = asyncio.create_task(poll_workflow_progress(handle, interval_seconds=1))
        await asyncio.sleep(0.2)  # Let it run once
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        mock_print.assert_called()
        output = mock_print.call_args[0][0]
        assert "[30s]" in output
        assert "recon" in output
        assert "Completed: 1" in output


async def test_poll_workflow_progress_handles_query_error():
    handle = MagicMock()
    handle.query = AsyncMock(side_effect=RuntimeError("workflow not found"))

    with patch("builtins.print") as mock_print:
        task = asyncio.create_task(poll_workflow_progress(handle, interval_seconds=1))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Should not crash, just silently handle the error
        mock_print.assert_not_called()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_worker_progress.py -v`
Expected: FAIL — `ImportError: cannot import name 'poll_workflow_progress'`

- [x] **Step 3: Add progress polling to whitebox worker**

Add to `packages/whitebox/src/shannon_whitebox/worker.py`, after the `run_scan` function (before `def main()` at line 48):

```python
async def poll_workflow_progress(handle, interval_seconds: int = 30) -> None:
    """Periodically query workflow progress and print status to console."""
    while True:
        try:
            progress = await handle.query("PipelineProgress")
            elapsed = int(progress.elapsed_ms / 1000)
            phase = progress.current_phase or "unknown"
            agent = progress.current_agent or "none"
            completed = len(progress.completed_agents)
            print(f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/13")
        except Exception:
            pass  # Workflow may have completed
        await asyncio.sleep(interval_seconds)
```

Also update `run_scan` to spawn the poll task. Replace the `async with worker:` block (lines 38–45) with:

```python
    async with worker:
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            return result
        except Exception:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            raise
```

Note: This also changes from `execute_workflow` to `start_workflow` + `handle.result()` to get a handle we can query.

- [x] **Step 4: Add progress polling to blackbox worker**

Add the same `poll_workflow_progress` function to `packages/blackbox/src/shannon_blackbox/worker.py`, after the `run_scan` function:

```python
async def poll_workflow_progress(handle, interval_seconds: int = 30) -> None:
    """Periodically query workflow progress and print status to console."""
    while True:
        try:
            progress = await handle.query("PipelineProgress")
            elapsed = int(progress.elapsed_ms / 1000)
            phase = progress.current_phase or "unknown"
            agent = progress.current_agent or "none"
            completed = len(progress.completed_agents)
            print(f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/13")
        except Exception:
            pass  # Workflow may have completed
        await asyncio.sleep(interval_seconds)
```

Update the `run_scan` function in `packages/blackbox/src/shannon_blackbox/worker.py` to use `start_workflow` + polling. Replace lines 28–36:

```python
    async with worker:
        handle = await client.start_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            return result
        except Exception:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            raise
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/whitebox/tests/test_worker_progress.py -v`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/blackbox/src/shannon_blackbox/worker.py packages/whitebox/tests/test_worker_progress.py
git commit -m "feat: add workflow progress polling to worker entry points"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Section | Task |
|---|---|
| PhaseMetrics model | Task 1 |
| AGENT_PHASE_MAP constant | Task 1 |
| Phase aggregation in end_agent | Task 2 |
| Phase percentage recalculation | Task 2 |
| Backward compatibility (empty phases) | Task 2 (test + existing empty dict in initialize) |
| ActivityLogger ABC | Task 3 |
| TemporalActivityLogger | Task 3 |
| ConsoleActivityLogger | Task 3 |
| create_activity_logger factory | Task 3 |
| AuditLogger ABC | Task 4 |
| RealAuditLogger | Task 4 |
| NullAuditLogger | Task 4 |
| create_audit_logger factory | Task 4 |
| audit __init__.py exports | Task 4 |
| ProgressIndicator | Task 5 |
| NullProgressIndicator | Task 5 |
| create_progress_indicator factory | Task 5 |
| watchdog dependency | Task 6 |
| LogFileHandler | Task 6 |
| tail_workflow_log | Task 6 |
| Resume ID stripping | Task 6 |
| CLI --follow flag (whitebox) | Task 7 |
| CLI --follow flag (blackbox) | Task 7 |
| Worker progress polling (whitebox) | Task 8 |
| Worker progress polling (blackbox) | Task 8 |

### 2. Placeholder Scan

No TBD, TODO, "implement later", or "fill in details" found. All code blocks contain complete implementation code. All test blocks contain complete test code.

### 3. Type Consistency

- `create_audit_logger(audit_session: AuditSession | None)` → matches `RealAuditLogger.__init__(audit_session: AuditSession)` and `NullAuditLogger` (no args)
- `create_activity_logger()` → returns `ActivityLogger`, both implementations subclass it
- `create_progress_indicator(message: str, enabled: bool)` → returns `ProgressIndicator | NullProgressIndicator`, both have matching `start/stop/finish` methods
- `AGENT_PHASE_MAP` keys match `AgentName.value` strings exactly (verified in test)
- `PhaseMetrics` model fields match the dict structure created in `_aggregate_phase`
- `LogFileHandler.__init__(log_path: Path)` → `Path` type used consistently
- `poll_workflow_progress(handle, interval_seconds: int)` → `handle` is duck-typed (Temporal workflow handle)

**One note:** The spec mentions `PipelineProgress` query but this doesn't exist yet in the codebase. Task 8 uses `handle.query("PipelineProgress")` as a string query name. This will work once a `PipelineProgress` query handler is registered on the workflow — which is outside the scope of this logging alignment plan (it's a workflow-level concern). The polling function gracefully handles the query not being available via try/except.
