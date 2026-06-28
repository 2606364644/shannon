"""FileLogRenderer — renders DisplayEvents to plain text for workflow.log.

No ANSI codes (must stay grep-able and tail-friendly). Backward-compatible:
the summary block always contains a 'Workflow COMPLETED' or 'Workflow FAILED'
line, matching the COMPLETION_PATTERN in shannon_core.cli.logs.
"""
from __future__ import annotations

from shannon_core.display.formatters import (
    agent_body, agent_title, format_duration, format_error_block,
    humanize_tool_call, phase_body, step_body, tag,
)

from shannon_core.display.symbols import SUMMARY_FAIL, SUMMARY_OK


_SEP = "=" * 80


def _prefixed(agent_name: str) -> str:
    """Return '[Prefix] agentname' or just 'agentname' for unknown agents.

    Delegates to the shared agent_title so file/rich agree on agent display.
    仍供 _tool/_llm 行使用（本次不改这两类行）。
    """
    return agent_title(agent_name)


class FileLogRenderer:
    def __init__(self, writer) -> None:
        # writer satisfies shannon_core.display.types.LineWriter (async write(str))
        self._writer = writer

    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, InfoEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): await self._writer.write(self._header(event))
            case PhaseEvent(): await self._writer.write(self._phase(event))
            case StepEvent(): await self._writer.write(self._step(event))
            case AgentEvent(): await self._writer.write(self._agent(event))
            case ToolCallEvent(): await self._writer.write(self._tool(event))
            case LlmTurnEvent(): await self._writer.write(self._llm(event))
            case ErrorEvent(): await self._writer.write(self._error(event))
            case SummaryEvent(): await self._writer.write(self._summary(event))
            case InfoEvent(): await self._writer.write(self._info(event))
            case ResumeEvent(): await self._writer.write(self._resume(event))

    def _step(self, e) -> str:
        return f"[{e.timestamp}] [{tag('STEP')}] {step_body(e)}\n"

    def _info(self, e) -> str:
        label = "WARNING" if e.level == "warning" else "INFO"
        return f"[{e.timestamp}] [{tag(label)}] {e.message}\n"

    def _header(self, e) -> str:
        lines = [_SEP, "Shannon Pentest - Workflow Log", _SEP]
        if e.workflow_id:
            lines.append(f"Workflow ID: {e.workflow_id}")
        if getattr(e, "repo_path", None):
            lines.append(f"Repository:  {e.repo_path}")
        # Target line only when there is a real URL (offline scans show mode instead)
        if e.target_url:
            lines.append(f"Target URL:  {e.target_url}")
        mode = getattr(e, "mode", None)
        if mode and not e.target_url:
            lines.append(f"Mode:        {mode}")
        lines.append(f"Started:     {e.timestamp}")
        web_ui = getattr(e, "web_ui_url", None)
        logs_cmd = getattr(e, "logs_cmd", None)
        if web_ui or logs_cmd:
            lines.append("Monitor:")
            if web_ui:
                lines.append(f"  Web UI: {web_ui}")
            if logs_cmd:
                lines.append(f"  Logs:   {logs_cmd}")
        lines.append(_SEP)
        return "\n".join(lines) + "\n\n"

    def _phase(self, e) -> str:
        prefix = "\n" if e.event == "start" else ""
        return f"{prefix}[{e.timestamp}] [{tag('PHASE')}] {phase_body(e)}\n"

    def _agent(self, e) -> str:
        return f"[{e.timestamp}] [{tag('AGENT')}] {agent_body(e)}\n"

    def _tool(self, e) -> str:
        who = _prefixed(e.agent_name)
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        return f"[{e.timestamp}] [TOOL]  {who}: {e.tool_name}: {params}\n"

    def _llm(self, e) -> str:
        who = _prefixed(e.agent_name)
        content = e.content[:200] + "..." if len(e.content) > 200 else e.content
        return f"[{e.timestamp}] [LLM]   {who}: Turn {e.turn}: {content}\n"

    def _error(self, e) -> str:
        msg = f"[{e.timestamp}] [ERROR] {e.error_type}: {e.message}"
        if e.context:
            msg += f" (context: {e.context})"
        if e.classified:
            if e.display_retryable:
                suffix = (
                    f"将重试 {e.attempt}/{e.max_attempts}"
                    if (e.attempt and e.max_attempts) else "将重试"
                )
                msg += f" [{e.classified} · {suffix}]"
            else:
                msg += f" [{e.classified} · 不可重试]"
        if e.detail_path:
            msg += f"  (详细堆栈见 {e.detail_path})"
        return msg + "\n"

    def _summary(self, e) -> str:
        status = "COMPLETED" if e.status == "completed" else "FAILED"
        lines = [
            "",
            f"{_SEP}",
            f"Workflow {status}",
            "─" * 40,
            f"Status:      {e.status}",
            f"Duration:    {format_duration(e.total_duration_ms)}",
            f"Total Cost:  ${e.total_cost_usd:.4f}",
            f"Agents:      {len(e.agents)} completed",
            "",
            "Agent Breakdown:",
        ]
        for m in e.agents:
            mark = SUMMARY_OK if m.success else SUMMARY_FAIL
            cost = f", ${m.cost_usd:.4f}" if m.cost_usd is not None else ""
            lines.append(f"  {mark} {m.name} ({format_duration(m.duration_ms)}{cost})")
        if e.error:
            lines.append("")
            lines.append(format_error_block(e.error).rstrip("\n"))
        lines.append(f"{_SEP}")
        lines.append("")
        return "\n".join(lines)

    def _resume(self, e) -> str:
        return (
            f"\n[{e.timestamp}] [RESUME] Resuming workflow\n"
            f"  Previous Workflow ID: {e.previous_workflow_id}\n"
            f"  New Workflow ID:      {e.new_workflow_id}\n"
            f"  Checkpoint:           {e.checkpoint_hash}\n"
            f"  Completed Agents:     {', '.join(e.completed_agents)}\n\n"
        )
