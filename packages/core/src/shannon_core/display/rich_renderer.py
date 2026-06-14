"""RichConsoleRenderer — renders DisplayEvents to Rich live terminal output.

Uses Panel for per-agent grouping, Progress for parallel agent tracking, and
a category->style map for consistent color semantics.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from shannon_core.display.formatters import agent_prefix


class RichConsoleRenderer:
    STYLE_MAP = {
        "PHASE": "bold cyan",
        "AGENT": "blue",
        "TOOL": "yellow",
        "LLM": "magenta",
        "ERROR": "bold red",
        "RESUME": "dim yellow",
    }

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): self._render_header(event)
            case PhaseEvent(): self._render_phase(event)
            case AgentEvent(): self._render_agent(event)
            case ToolCallEvent(): self._render_tool(event)
            case LlmTurnEvent(): self._render_llm(event)
            case ErrorEvent(): self._render_error(event)
            case SummaryEvent(): self._render_summary(event)
            case ResumeEvent(): self._render_resume(event)

    def _render_header(self, e) -> None:
        body = (
            f"Workflow:  {e.workflow_id or 'N/A'}\n"
            f"Target:    {e.target_url or 'N/A'}\n"
            f"Started:   {e.timestamp}"
        )
        self._console.print(Panel(body, title="Shannon Pentest", border_style="cyan"))

    def _render_phase(self, e) -> None:
        verb = "Starting" if e.event == "start" else "Completed"
        self._console.print(
            f"[{e.timestamp}] [bold cyan]PHASE[/]  {verb} {e.phase} {'─' * 20}",
            highlight=False,
        )
