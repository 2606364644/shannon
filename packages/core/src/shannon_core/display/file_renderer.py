"""FileLogRenderer — renders DisplayEvents to plain text for workflow.log.

No ANSI codes (must stay grep-able and tail-friendly). Backward-compatible:
the summary block always contains a 'Workflow COMPLETED' or 'Workflow FAILED'
line, matching the COMPLETION_PATTERN in shannon_core.cli.logs.
"""
from __future__ import annotations

from shannon_core.display.formatters import agent_prefix, format_duration, humanize_tool_call


_SEP = "=" * 80


def _prefixed(agent_name: str) -> str:
    """Return '[Prefix] agentname' or just 'agentname' for unknown agents."""
    pfx = agent_prefix(agent_name)
    if pfx == "[Agent]":
        return agent_name
    return f"{pfx} {agent_name}"


class FileLogRenderer:
    def __init__(self, writer) -> None:
        # writer satisfies shannon_core.display.types.LineWriter (async write(str))
        self._writer = writer

    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): await self._writer.write(self._header(event))
            case PhaseEvent(): await self._writer.write(self._phase(event))
            case AgentEvent(): await self._writer.write(self._agent(event))
            case ToolCallEvent(): await self._writer.write(self._tool(event))
            case LlmTurnEvent(): await self._writer.write(self._llm(event))
            case ErrorEvent(): await self._writer.write(self._error(event))
            case SummaryEvent(): await self._writer.write(self._summary(event))
            case ResumeEvent(): await self._writer.write(self._resume(event))

    def _header(self, e) -> str:
        target = e.target_url if e.target_url else "N/A"
        lines = [_SEP, "Shannon Pentest - Workflow Log", _SEP]
        if e.workflow_id:  # omit the line entirely when None (matches old behavior)
            lines.append(f"Workflow ID: {e.workflow_id}")
        lines.append(f"Target URL:  {target}")
        lines.append(f"Started:     {e.timestamp}")
        lines.append(_SEP)
        return "\n".join(lines) + "\n\n"

    def _phase(self, e) -> str:
        verb = "Starting" if e.event == "start" else "Completed"
        prefix = "\n" if e.event == "start" else ""
        return f"{prefix}[{e.timestamp}] [PHASE] {verb} {e.phase}\n"

    def _agent(self, e) -> str:
        who = _prefixed(e.agent_name)
        if e.event == "start":
            return f"[{e.timestamp}] [AGENT] {who}: Starting (attempt {e.attempt})\n"
        # end
        if e.success is False:
            dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
            err = f" - {e.error}" if e.error else ""
            return f"[{e.timestamp}] [AGENT] {who}: Failed ({dur}){err}\n"
        parts = []
        if e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.cost_usd is not None:
            parts.append(f"${e.cost_usd:.4f}")
        metrics = f" ({', '.join(parts)})" if parts else ""
        return f"[{e.timestamp}] [AGENT] {who}: Completed{metrics}\n"

    def _tool(self, e) -> str:
        who = _prefixed(e.agent_name)
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        return f"[{e.timestamp}] [TOOL]  {who}: {e.tool_name}: {params}\n"

    def _llm(self, e) -> str:
        who = _prefixed(e.agent_name)
        content = e.content[:200] + "..." if len(e.content) > 200 else e.content
        return f"[{e.timestamp}] [LLM]   {who}: Turn {e.turn}: {content}\n"
