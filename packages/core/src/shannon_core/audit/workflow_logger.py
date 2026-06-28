from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from shannon_core.display.dispatcher import DisplayDispatcher
from shannon_core.display.events import (
    AgentEvent, AgentMetric, ErrorEvent, InfoEvent, LlmTurnEvent, PhaseEvent,
    ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
)
from shannon_core.display.file_renderer import FileLogRenderer
from shannon_core.display.formatters import format_log_time
from shannon_core.logging.temporalio_redirect import install_temporalio_log_redirect
from shannon_core.models.audit import AgentLogDetails, ResumeInfo, WorkflowSummary
from shannon_core.models.metrics import SessionMetadata
from .log_stream import LogStream
from .utils import generate_workflow_log_path

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rich.console import Console
    from shannon_core.display.live_dashboard import LiveDashboardRenderer


def _web_ui_url(workflow_id: str | None) -> str | None:
    if not workflow_id:
        return None
    port = os.environ.get("TEMPORAL_WEB_UI_PORT", "8233")
    return f"http://localhost:{port}/namespaces/default/workflows/{workflow_id}"


def _logs_cmd(workspace: str | None) -> str | None:
    if not workspace:
        return None
    return f"shannon-whitebox logs {workspace} --follow"


class WorkflowLogger:
    """Emits DisplayEvents through a dispatcher.

    The dispatcher fans events to FileLogRenderer (writes workflow.log via the
    injected LogStream) and, optionally, a RichConsoleRenderer for live stdout.
    """

    def __init__(self, session_metadata: SessionMetadata, use_rich: bool = False,
                 console: Console | None = None,
                 dashboard: LiveDashboardRenderer | None = None) -> None:
        self._meta = session_metadata
        self._workflow_id: str | None = None
        self._stream: LogStream | None = None
        self._dispatcher: DisplayDispatcher | None = None
        self._use_rich = use_rich
        self._console = console
        self._dashboard = dashboard
        # Per-workspace failure-log redirect path; set by the redirect installer
        # (Part A / Task 5) during initialize(). None => detail_path=None.
        self._activity_failure_log_path: str | None = None

    async def initialize(self, workflow_id: str | None = None) -> None:
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()
        # Divert temporalio's verbose activity-failure tracebacks to a sibling
        # file (same dir as workflow.log) BEFORE renderers consume any events,
        # so the first ERROR can already point at detail_path. Degrades silently.
        self._install_failure_redirect()

        renderers: list = [FileLogRenderer(self._stream)]
        if self._console is not None:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(
                self._console,
                show_phase=True,                  # rich/plain 都显示 PHASE 分隔行（恢复结构感）
                show_steps=True,                 # rich: 放开 STEP 行
                show_tools=not self._use_rich,   # rich: 隐藏 🔧（仍写 workflow.log）
            ))
        if self._use_rich and self._dashboard is not None:
            renderers.append(self._dashboard)
        self._dispatcher = DisplayDispatcher(renderers)

        ws = workflow_id or self._meta.id
        mode = self._meta.web_url or "offline (source code analysis)"
        await self._dispatcher.dispatch(WorkflowHeader(
            timestamp=format_log_time(), category="HEADER",
            workflow_id=workflow_id, target_url=self._meta.web_url or None,
            repo_path=self._meta.repo_path,
            mode=mode,
            web_ui_url=_web_ui_url(workflow_id),
            logs_cmd=_logs_cmd(ws),
            workspace=ws,
        ))

    def _install_failure_redirect(self) -> None:
        """Install the temporalio.activity -> file redirect; set the detail_path hint.

        The redirect target is the same-source sibling of workflow.log
        (``<audit_dir>/activity_failures.log``), so the live display's ERROR
        line can hint at where the full traceback lives. On any install failure
        we degrade silently (tracebacks may appear on terminal) — never break
        the scan. ``log_error`` then emits with ``detail_path=None``.
        """
        try:
            failure_path: Path = generate_workflow_log_path(self._meta).with_name(
                "activity_failures.log")
            install_temporalio_log_redirect(failure_path)
            self._activity_failure_log_path = str(failure_path)
        except Exception:
            logger.warning(
                "temporalio log redirect install failed; "
                "activity tracebacks may appear on terminal", exc_info=True)
            self._activity_failure_log_path = None

    async def log_phase(self, phase: str, event: Literal["start", "complete"],
                        steps: tuple[str, ...] = (),
                        step_intents: tuple[str | None, ...] = ()) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(PhaseEvent(
            timestamp=format_log_time(), category="PHASE", phase=phase,
            event=event, steps=tuple(steps), step_intents=tuple(step_intents)))

    async def log_info(self, message: str, level: str = "info") -> None:
        """Emit a user-facing info/warning line.

        Routes through the dispatcher (not bare logging → stderr), so the line
        scrolls above the Live footer and is persisted to workflow.log — avoids
        the stderr/footer collision that bare ``logger.warning`` causes in the
        workflow sandbox thread.
        """
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(InfoEvent(
            timestamp=format_log_time(), category="INFO",
            message=message, level=level,
        ))

    async def log_step(self, name: str, phase: str, event: Literal["start", "complete"],
                       duration_ms: int | None = None, error: str | None = None,
                       intent: str | None = None) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(StepEvent(
            timestamp=format_log_time(), category="STEP", name=name, phase=phase,
            event=event, duration_ms=duration_ms, error=error, intent=intent))

    async def log_agent(self, agent_name: str, event: Literal["start", "end"],
                        details: AgentLogDetails | None = None) -> None:
        if self._dispatcher is None:
            return
        d = details or AgentLogDetails(attempt_number=1)
        await self._dispatcher.dispatch(AgentEvent(
            timestamp=format_log_time(), category="AGENT", agent_name=agent_name,
            event=event, attempt=d.attempt_number, duration_ms=d.duration_ms,
            cost_usd=d.cost_usd, success=d.success, error=d.error))

    async def log_tool_start(self, agent_name: str, tool_name: str, parameters: Any) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(ToolCallEvent(
            timestamp=format_log_time(), category="TOOL", agent_name=agent_name,
            tool_name=tool_name, parameters=parameters))

    async def log_llm_response(self, agent_name: str, turn: int, content: str) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(LlmTurnEvent(
            timestamp=format_log_time(), category="LLM", agent_name=agent_name,
            turn=turn, content=content))

    async def log_event(self, event_type: str, message: str) -> None:
        """Log a generic categorized event.

        Writes directly to the file stream (bypassing the dispatcher), so generic
        events appear in workflow.log but NOT on Rich stdout when use_rich=True.
        This is intentional: generic events are rare and don't warrant a dedicated
        DisplayEvent type. If Rich output of generic events becomes needed, introduce
        a GenericEvent type and route through the dispatcher.
        """
        if self._dispatcher is None:
            return
        await self._stream.write(f"[{format_log_time()}] [{event_type}] {message}\n")

    async def log_error(self, error: Exception, context: str | None = None,
                        *, attempt: int | None = None,
                        max_attempts: int | None = None) -> None:
        if self._dispatcher is None:
            return
        # Same-source classification as Temporal (models/errors.py) — keeps the
        # live display's error label identical to what drives retry decisions.
        from shannon_core.models.errors import classify_error_for_temporal
        etype, retryable = classify_error_for_temporal(error)
        await self._dispatcher.dispatch(ErrorEvent(
            timestamp=format_log_time(), category="ERROR",
            error_type=type(error).__name__, message=str(error), context=context,
            classified=etype, display_retryable=retryable,
            attempt=attempt, max_attempts=max_attempts,
            detail_path=self._activity_failure_log_path))

    async def log_workflow_complete(self, summary: WorkflowSummary) -> None:
        if self._dispatcher is None:
            return
        # AgentMetricsSummary has no success field; agents listed in a completed
        # summary are assumed to have succeeded. If per-agent failure state is needed
        # later, thread a success flag through AgentMetricsSummary first.
        agents = [
            AgentMetric(name=n, duration_ms=m.duration_ms, cost_usd=m.cost_usd, success=True)
            for n, m in summary.agent_metrics.items()
        ]
        await self._dispatcher.dispatch(SummaryEvent(
            timestamp=format_log_time(), category="SUMMARY", status=summary.status,
            total_duration_ms=summary.total_duration_ms, total_cost_usd=summary.total_cost_usd,
            agents=agents, error=summary.error))

    async def log_resume_header(self, resume_info: ResumeInfo) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(ResumeEvent(
            timestamp=format_log_time(), category="RESUME",
            previous_workflow_id=resume_info.previous_workflow_id,
            new_workflow_id=resume_info.new_workflow_id,
            checkpoint_hash=resume_info.checkpoint_hash,
            completed_agents=resume_info.completed_agents))

    async def close(self) -> None:
        if self._stream is not None:
            await self._stream.close()
            self._stream = None
        self._dispatcher = None  # all log_* methods check dispatcher and no-op after close
