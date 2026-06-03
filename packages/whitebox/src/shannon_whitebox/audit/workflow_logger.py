from typing import Any, Literal

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentLogDetails, WorkflowSummary, ResumeInfo
from shannon_whitebox.audit.log_stream import LogStream
from shannon_whitebox.audit.utils import (
    format_duration,
    format_log_time,
    generate_workflow_log_path,
)


def _format_tool_params(tool_name: str, parameters: Any) -> str:
    """Per-tool smart truncation for readable workflow log lines."""
    if not isinstance(parameters, dict):
        return str(parameters)

    tool_key_map: dict[str, str] = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Grep": "pattern",
        "Glob": "pattern",
    }

    key = tool_key_map.get(tool_name)
    if key and key in parameters:
        val = str(parameters[key])
        if len(val) > 80:
            val = val[:77] + "..."
        return f"{key}={val}"

    items = list(parameters.items())[:2]
    parts = [f"{k}={str(v)[:40]}" for k, v in items]
    result = ", ".join(parts)
    if len(parameters) > 2:
        result += ", ..."
    return result


class WorkflowLogger:
    """Human-readable workflow log with category-tagged lines."""

    def __init__(self, session_metadata: SessionMetadata):
        self._meta = session_metadata
        self._stream: LogStream | None = None
        self._workflow_id: str | None = None

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Open the log file and write the header block."""
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()

        sep = "=" * 80
        header = f"{sep}\nShannon Pentest - Workflow Log\n{sep}\n"
        if workflow_id:
            header += f"Workflow ID: {workflow_id}\n"
        header += (
            f"Target URL:  {self._meta.web_url or 'N/A'}\n"
            f"Started:     {format_log_time()}\n"
            f"{sep}\n\n"
        )
        await self._stream.write(header)

    async def log_phase(self, phase: str, event: Literal["start", "complete"]) -> None:
        """Log a phase transition."""
        if self._stream is None:
            return
        verb = "started" if event == "start" else "completed"
        await self._stream.write(f"[{format_log_time()}] [PHASE] {phase} {verb}\n")

    async def log_agent(self, agent_name: str, event: Literal["start", "end"], details: AgentLogDetails | None = None) -> None:
        """Log an agent lifecycle event."""
        if self._stream is None:
            return
        verb = "started" if event == "start" else "ended"
        msg = f"[{format_log_time()}] [AGENT] {agent_name} {verb}"
        if details:
            parts: list[str] = []
            if details.attempt_number > 1:
                parts.append(f"attempt {details.attempt_number}")
            if details.duration_ms is not None:
                parts.append(f"duration: {format_duration(details.duration_ms)}")
            if details.cost_usd is not None:
                parts.append(f"cost: ${details.cost_usd:.4f}")
            if details.success is not None:
                parts.append("✓" if details.success else "✗")
            if details.error:
                parts.append(f"error: {details.error}")
            if parts:
                msg += " (" + ", ".join(parts) + ")"
        await self._stream.write(msg + "\n")

    async def log_tool_start(self, agent_name: str, tool_name: str, parameters: Any) -> None:
        """Log a tool invocation."""
        if self._stream is None:
            return
        formatted = _format_tool_params(tool_name, parameters)
        await self._stream.write(f"[{format_log_time()}] [TOOL] {agent_name} → {tool_name}({formatted})\n")

    async def log_llm_response(self, agent_name: str, turn: int, content: str) -> None:
        """Log an LLM response (truncated to 200 chars)."""
        if self._stream is None:
            return
        truncated = content[:200] + "..." if len(content) > 200 else content
        await self._stream.write(f"[{format_log_time()}] [LLM] {agent_name} turn {turn}: {truncated}\n")

    async def log_event(self, event_type: str, message: str) -> None:
        """Log a generic categorized event."""
        if self._stream is None:
            return
        await self._stream.write(f"[{format_log_time()}] [{event_type}] {message}\n")

    async def log_error(self, error: Exception, context: str | None = None) -> None:
        """Log an error with optional context."""
        if self._stream is None:
            return
        msg = f"[{format_log_time()}] [ERROR] {type(error).__name__}: {error}"
        if context:
            msg += f" (context: {context})"
        await self._stream.write(msg + "\n")

    async def log_workflow_complete(self, summary: WorkflowSummary) -> None:
        """Write the final summary block (single write)."""
        if self._stream is None:
            return
        sep = "=" * 80
        dash = "─" * 40
        lines = [
            f"\n{sep}\n",
            "Workflow COMPLETED\n",
            f"{dash}\n",
            f"Workflow ID: {self._workflow_id or 'N/A'}\n",
            f"Status:      {summary.status}\n",
            f"Duration:    {format_duration(summary.total_duration_ms)}\n",
            f"Total Cost:  ${summary.total_cost_usd:.4f}\n",
            f"Agents:      {len(summary.completed_agents)} completed\n",
            "\n",
            "Agent Breakdown:\n",
        ]
        for name in summary.completed_agents:
            metrics = summary.agent_metrics.get(name)
            if metrics:
                cost_str = f", ${metrics.cost_usd:.4f}" if metrics.cost_usd is not None else ""
                lines.append(f"  - {name} ({format_duration(metrics.duration_ms)}{cost_str})\n")
            else:
                lines.append(f"  - {name}\n")
        if summary.error:
            lines.append(f"\nError: {summary.error}\n")
        lines.append(f"{sep}\n")
        await self._stream.write("".join(lines))

    async def log_resume_header(self, resume_info: ResumeInfo) -> None:
        """Write a resume header block."""
        if self._stream is None:
            return
        header = (
            f"\n[{format_log_time()}] [RESUME] Resuming workflow\n"
            f"  Previous Workflow ID: {resume_info.previous_workflow_id}\n"
            f"  New Workflow ID:      {resume_info.new_workflow_id}\n"
            f"  Checkpoint:           {resume_info.checkpoint_hash}\n"
            f"  Completed Agents:     {', '.join(resume_info.completed_agents)}\n\n"
        )
        await self._stream.write(header)

    async def close(self) -> None:
        """Flush and close the underlying stream."""
        if self._stream is not None:
            await self._stream.close()
            self._stream = None
