"""RichConsoleRenderer — renders DisplayEvents to Rich live terminal output.

Uses Panel for per-agent grouping, Progress for parallel agent tracking, and
a category->style map for consistent color semantics.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from shannon_core.display.formatters import (
    agent_prefix, format_duration, format_error_block, humanize_tool_call,
    first_nonempty_line, pad_rule,
)

from shannon_core.display.symbols import (
    AGENT_DONE, AGENT_FAIL, AGENT_START,
    STEP_DONE, STEP_FAIL, STEP_PENDING,
    SUMMARY_OK, SUMMARY_FAIL,
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

    def __init__(self, console: Console | None = None, show_phase: bool = True,
                 show_steps: bool = True, show_tools: bool = True) -> None:
        self._console = console or Console()
        self._show_phase = show_phase
        self._show_steps = show_steps
        self._show_tools = show_tools

    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): self._render_header(event)
            case PhaseEvent():
                if self._show_phase:
                    self._render_phase(event)
            case StepEvent():
                if self._show_steps:
                    self._render_step(event)
            case AgentEvent(): self._render_agent(event)
            case ToolCallEvent():
                if self._show_tools:
                    self._render_tool(event)
            case LlmTurnEvent(): self._render_llm(event)
            case ErrorEvent(): self._render_error(event)
            case SummaryEvent(): self._render_summary(event)
            case ResumeEvent(): self._render_resume(event)

    def _render_header(self, e) -> None:
        lines = []
        if getattr(e, "repo_path", None):
            lines.append(f"Repository: {e.repo_path}")
        if e.target_url:
            lines.append(f"Target:     {e.target_url}")
        mode = getattr(e, "mode", None)
        if mode and not e.target_url:
            lines.append(f"Mode:       {mode}")
        lines.append(f"Started:    {e.timestamp}")
        web_ui = getattr(e, "web_ui_url", None)
        logs_cmd = getattr(e, "logs_cmd", None)
        if web_ui or logs_cmd:
            lines.append("")
            lines.append("Monitor:")
            if web_ui:
                lines.append(f"  Web UI: {web_ui}")
            if logs_cmd:
                lines.append(f"  Logs:   {logs_cmd}")
        body = "\n".join(lines)
        self._console.print(Panel(body, title="Shannon Pentest", border_style="cyan"))

    def _render_step(self, e) -> None:
        label = e.intent or e.name
        if e.event == "start":
            self._console.print(
                f"[{e.timestamp}] [cyan]STEP[/]  {STEP_PENDING} {label}", highlight=False)
            return
        if e.error:
            self._console.print(
                f"[{e.timestamp}] [cyan]STEP[/]  {STEP_FAIL} {label}  — {e.error}",
                highlight=False)
            return
        suffix = f"  {format_duration(e.duration_ms)}" if e.duration_ms is not None else ""
        self._console.print(
            f"[{e.timestamp}] [cyan]STEP[/]  {STEP_DONE} {label}{suffix}", highlight=False)

    def _render_phase(self, e) -> None:
        verb = "Starting" if e.event == "start" else "Completed"
        body = pad_rule(f"{verb} {e.phase}")
        self._console.print(
            f"[{e.timestamp}] [bold cyan]PHASE[/]  {body}",
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
            self._console.print(
                f"[{e.timestamp}] [blue]AGENT[/]  {AGENT_START} {title} started (attempt {e.attempt})")
            return
        # end
        if e.success is False:
            dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
            err = f" — {e.error}" if e.error else ""
            self._console.print(
                f"[{e.timestamp}] [blue]AGENT[/]  [red]{AGENT_FAIL} {title} failed ({dur}){err}[/]")
            return
        parts = []
        if e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.cost_usd is not None:
            parts.append(f"${e.cost_usd:.4f}")
        metrics = f" ({', '.join(parts)})" if parts else ""
        self._console.print(
            f"[{e.timestamp}] [blue]AGENT[/]  [green]{AGENT_DONE} {title} Completed{metrics}[/]")

    def _render_tool(self, e) -> None:
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        self._console.print(f"[{e.timestamp}] [yellow]🔧 {e.tool_name}({params})[/]", highlight=False)

    def _render_llm(self, e) -> None:
        line = first_nonempty_line(e.content) or "(无文本)"
        self._console.print(
            f"[{e.timestamp}] [magenta]💭 {agent_prefix(e.agent_name)} "
            f"Turn {e.turn}: {line}[/]", highlight=False)

    def _render_error(self, e) -> None:
        line = f"[{e.timestamp}] [bold red]ERROR[/]  {e.error_type}: {e.message}"
        if e.context:
            line += f" (context: {e.context})"
        if e.classified:
            flag = "retryable" if e.display_retryable else "non-retryable"
            line += f" [{e.classified} · {flag}]"
        self._console.print(line, highlight=False)

    def _render_summary(self, e) -> None:
        from rich.table import Table
        status = e.status.upper()
        self._console.print(Panel.fit(
            f"Workflow [bold]{status}[/]\n"
            f"Duration: {format_duration(e.total_duration_ms)}    "
            f"Total Cost: ${e.total_cost_usd:.4f}",
            border_style="green" if e.status == "completed" else "red",
        ))
        if e.agents:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Status")
            table.add_column("Agent")
            table.add_column("Duration")
            table.add_column("Cost")
            for m in e.agents:
                mark = SUMMARY_OK if m.success else SUMMARY_FAIL
                cost = f"${m.cost_usd:.4f}" if m.cost_usd is not None else "—"
                table.add_row(mark, m.name, format_duration(m.duration_ms), cost)
            self._console.print(table)
        if e.error:
            self._console.print(f"[red]{format_error_block(e.error)}[/]", highlight=False)

    def _render_resume(self, e) -> None:
        self._console.print(
            f"[dim yellow][{e.timestamp}] [RESUME] Resuming workflow[/]\n"
            f"  Previous: {e.previous_workflow_id}    New: {e.new_workflow_id}",
            highlight=False,
        )
