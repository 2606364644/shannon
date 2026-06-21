"""L2 gate: AuditSession -> WorkflowLogger -> dispatcher -> renderers actually
reaches the terminal. If this is empty, the pipeline is not wired (the failure
mode of the prior logging-display-optimization plan)."""
import io
from pathlib import Path

from rich.console import Console

from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_core.display.live_dashboard import LiveDashboardRenderer
from shannon_whitebox.audit.session import AuditSession


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="gate", web_url="https://example.com", output_path=str(tmp_path))


async def test_audit_session_reaches_console_and_dashboard(tmp_path: Path):
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True, color_system=None, force_interactive=True)
    dashboard = LiveDashboardRenderer(console)
    session = AuditSession(_make_meta(tmp_path), use_rich=True, console=console, dashboard=dashboard)
    await session.initialize(workflow_id="wf-gate")

    await session.log_phase_start("vulnerability-analysis")
    await session.start_agent("injection-vuln", "p", attempt=1)
    await session.log_event("tool_start", {"toolName": "Bash", "parameters": {"command": "rg -n eval"}})
    await session.log_event("llm_response", {"turn": 2, "content": "found sinks"})
    await session.end_agent("injection-vuln", AgentEndResult(
        success=True, duration_ms=5200, cost_usd=0.15, attempt_number=1))

    # Render the dashboard once into the same buffer
    console.print(dashboard)
    await session.close()

    out = buf.getvalue()
    # Scrolling-log lines (RichConsoleRenderer printed each event). color_system=None
    # strips Rich markup like "[blue]AGENT[/]" to the bare word "AGENT". After the
    # rich-display-visibility change, rich mode shows the PHASE line and the AGENT
    # start/end lines, prefixes the llm_response line with the agent tag, but
    # suppresses the tool_start (🔧) line (show_tools=False). Each assertion pins a
    # distinct event the renderer can only have produced if the dispatcher wired the
    # event through.
    assert "AGENT" in out and "injection-vuln" in out           # agent start line
    assert "▶ [Injection] injection-vuln started" in out        # agent start (full)
    assert "💭 [Injection] Turn 2: found sinks" in out          # llm_response line (agent-prefixed)
    assert "🔧" not in out                                      # tool_start suppressed in rich mode
    # Phase is shown in the scrolling log in rich mode (show_phase=True): the
    # PHASE line renders, and color_system=None strips "[bold cyan]PHASE[/]" to
    # the bare word "PHASE". Its presence proves the WorkflowLogger wired
    # show_phase=True through to the RichConsoleRenderer.
    assert "PHASE" in out                                       # scrolling phase line shown
    assert "Starting vulnerability-analysis" in out
    # Dashboard status line carries the phase name + completed count.
    assert "vulnerability-analysis" in out                      # phase in status line
    assert "1 done" in out                                      # completed_count
