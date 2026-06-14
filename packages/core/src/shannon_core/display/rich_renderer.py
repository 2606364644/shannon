"""RichConsoleRenderer — renders DisplayEvents to Rich live terminal output.

Uses Panel for per-agent grouping, Progress for parallel agent tracking, and
a category->style map for consistent color semantics.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from shannon_core.display.formatters import (
    agent_prefix, format_duration, humanize_tool_call,
)


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

    def _agent_panel_title(self, agent_name: str) -> str:
        pfx = agent_prefix(agent_name)
        if pfx == "[Agent]":
            return agent_name
        return f"{pfx} {agent_name}"

    def _render_agent(self, e) -> None:
        title = self._agent_panel_title(e.agent_name)
        if e.event == "start":
            self._console.print(f"[{e.timestamp}] [blue]AGENT[/]  ▶ {title} started (attempt {e.attempt})")
            return
        # end
        if e.success is False:
            dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
            self._console.print(f"[red]{title} failed ({dur}) — {e.error or ''}[/]")
            return
        parts = []
        if e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.cost_usd is not None:
            parts.append(f"${e.cost_usd:.4f}")
        metrics = f" ({', '.join(parts)})" if parts else ""
        self._console.print(f"[green]{title} Completed{metrics}[/]")

    def _render_tool(self, e) -> None:
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        self._console.print(f"[{e.timestamp}] [yellow]🔧 {e.tool_name}({params})[/]", highlight=False)

    def _render_llm(self, e) -> None:
        content = e.content[:200] + "..." if len(e.content) > 200 else e.content
        self._console.print(f"[{e.timestamp}] [magenta]💭 Turn {e.turn}: {content}[/]", highlight=False)
