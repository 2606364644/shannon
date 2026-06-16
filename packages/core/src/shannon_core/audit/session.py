from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult, AgentLogDetails, ResumeInfo, WorkflowSummary

from .agent_logger import AgentLogger
from .metrics_tracker import MetricsTracker
from .utils import initialize_audit_structure
from .workflow_logger import WorkflowLogger

if TYPE_CHECKING:
    from rich.console import Console
    from shannon_core.display.live_dashboard import LiveDashboardRenderer


class AuditSession:
    """Facade coordinating AgentLogger, WorkflowLogger, and MetricsTracker."""

    def __init__(self, session_metadata: SessionMetadata, use_rich: bool = False,
                 console: Console | None = None,
                 dashboard: LiveDashboardRenderer | None = None):
        self._meta = session_metadata
        self._use_rich = use_rich
        self._console = console
        self._dashboard = dashboard
        self._agent_logger: AgentLogger | None = None
        self._workflow_logger: WorkflowLogger | None = None
        self._metrics_tracker: MetricsTracker | None = None
        self._lock = asyncio.Lock()
        self._current_agent_name: str | None = None

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Create directory structure and initialize all components."""
        initialize_audit_structure(self._meta)
        self._workflow_logger = WorkflowLogger(
            self._meta, use_rich=self._use_rich,
            console=self._console, dashboard=self._dashboard)
        await self._workflow_logger.initialize(workflow_id)
        self._metrics_tracker = MetricsTracker(self._meta)
        await self._metrics_tracker.initialize(workflow_id)

    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None:
        """Initialize an agent logger, save prompt, and log start events."""
        self._current_agent_name = agent_name
        self._agent_logger = AgentLogger(self._meta, agent_name, attempt)
        await self._agent_logger.initialize()
        await AgentLogger.save_prompt(self._meta, agent_name, prompt)

        if self._workflow_logger:
            await self._workflow_logger.log_agent(
                agent_name, "start", AgentLogDetails(attempt_number=attempt),
            )
        if self._metrics_tracker:
            self._metrics_tracker.start_agent(agent_name, attempt)

    async def log_event(self, event_type: str, event_data: Any) -> None:
        """Dispatch events to both agent log (JSON) and workflow log (human-readable)."""
        if self._agent_logger:
            await self._agent_logger.log_event(event_type, event_data)
        if self._workflow_logger:
            if event_type == "tool_start" and isinstance(event_data, dict):
                await self._workflow_logger.log_tool_start(
                    self._current_agent_name or "unknown",
                    event_data.get("toolName", "unknown"),
                    event_data.get("parameters", {}),
                )
            elif event_type == "llm_response" and isinstance(event_data, dict):
                await self._workflow_logger.log_llm_response(
                    self._current_agent_name or "unknown",
                    event_data.get("turn", 0),
                    event_data.get("content", ""),
                )

    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Close agent log, update metrics, and log end events."""
        if self._agent_logger:
            await self._agent_logger.log_event("agent_end", {
                "success": result.success,
                "duration_ms": result.duration_ms,
            })
            await self._agent_logger.close()
            self._agent_logger = None

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

        self._current_agent_name = None

    async def log_phase_start(self, phase: str, steps: tuple[str, ...] = (),
                              step_intents: tuple[str | None, ...] = ()) -> None:
        """Log a phase start event, optionally declaring the phase's unit names."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(
                phase, "start", steps=tuple(steps), step_intents=tuple(step_intents))

    async def log_phase_complete(self, phase: str) -> None:
        """Log a phase complete event."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(phase, "complete")

    async def log_step(self, name: str, phase: str, event: str,
                       duration_ms: int | None = None, error: str | None = None,
                       intent: str | None = None) -> None:
        """Log a deterministic sub-step start/complete event."""
        if self._workflow_logger:
            await self._workflow_logger.log_step(name, phase, event,
                                                 duration_ms=duration_ms, error=error,
                                                 intent=intent)

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        """Emit StepEvent start on enter, complete (with duration/error) on exit.

        Uses try/finally so the complete event is always emitted, even when the
        wrapped activity raises — keeps the dashboard's unit_status from getting
        stuck on 'running'.
        """
        start = time.monotonic()
        await self.log_step(name, phase, "start", intent=intent)
        err: str | None = None
        try:
            yield
        except Exception as e:  # re-raise after recording; caller decides handling
            err = str(e)
            raise
        finally:
            await self.log_step(name, phase, "complete",
                                duration_ms=int((time.monotonic() - start) * 1000), error=err,
                                intent=intent)

    async def log_workflow_complete(self, summary: WorkflowSummary) -> None:
        """Write the workflow summary and update session status."""
        if self._workflow_logger:
            await self._workflow_logger.log_workflow_complete(summary)
            await self._workflow_logger.close()
        if self._metrics_tracker:
            await self._metrics_tracker.update_session_status(summary.status)

    async def update_session_status(self, status: str) -> None:
        """Update the session status in session.json."""
        if self._metrics_tracker:
            await self._metrics_tracker.update_session_status(status)

    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None:
        """Record a resume attempt with lock-protected metrics update."""
        if self._metrics_tracker:
            async with self._lock:
                await self._metrics_tracker.reload()
                await self._metrics_tracker.add_resume_attempt(workflow_id, terminated, checkpoint)

    async def log_error(self, error: Exception, context: str | None = None) -> None:
        """Log an error to the workflow log (renders an [ERROR] line)."""
        if self._workflow_logger:
            await self._workflow_logger.log_error(error, context=context)

    async def log_resume_header(self, resume_info: ResumeInfo) -> None:
        """Write a resume header to the workflow log."""
        if self._workflow_logger:
            await self._workflow_logger.log_resume_header(resume_info)

    async def close(self) -> None:
        """Close the workflow logger's stream so buffered writes are flushed.

        Safe to call without initialize(); safe to call more than once.
        """
        if self._workflow_logger:
            await self._workflow_logger.close()

    async def get_metrics(self) -> dict:
        """Return the current metrics dict."""
        if self._metrics_tracker:
            return self._metrics_tracker.get_metrics()
        return {}
