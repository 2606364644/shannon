"""L2 gate: blackbox AuditSession -> WorkflowLogger -> dispatcher -> renderers
actually reaches the terminal. Empty capture => pipeline not wired.

This is the §3.4 anti-regression check the prior logging-display work lacked:
it drives a REAL blackbox AuditSession (the core shannon_core.audit.session
implementation used by the blackbox pipeline) through the full
WorkflowLogger -> DisplayDispatcher -> FileLogRenderer + RichConsoleRenderer +
LiveDashboardRenderer path against a capturing Console. If the session isn't
wired to the renderers, buf is empty and every assertion goes red.
"""
import io
from pathlib import Path

from rich.console import Console

from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_core.display.live_dashboard import LiveDashboardRenderer
from shannon_core.audit.session import AuditSession
from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="gate", web_url="https://example.com", output_path=str(tmp_path))


async def test_audit_session_reaches_console_and_dashboard(tmp_path: Path):
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True, color_system=None, force_interactive=True)
    dashboard = LiveDashboardRenderer(console)
    session = AuditSession(_make_meta(tmp_path), use_rich=True, console=console, dashboard=dashboard)
    await session.initialize(workflow_id="wf-gate")

    await session.log_phase_start("exploitation")
    await session.start_agent("injection-exploit", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "injection-exploit", attempt=1)
    await lg.initialize()
    await lg.log_tool_start("Bash", {"command": "curl 'http://x/?q=<script>'"})
    await lg.log_assistant_turn(2, "reflected XSS confirmed")
    await lg.close(success=True, duration_ms=5200)
    await session.end_agent("injection-exploit", AgentEndResult(
        success=True, duration_ms=5200, cost_usd=0.15, attempt_number=1))

    # Render the dashboard once into the same buffer
    console.print(dashboard)
    await session.close()

    out = buf.getvalue()
    # Scrolling-log lines (RichConsoleRenderer printed each event). color_system=None
    # strips Rich markup like "[blue]AGENT[/]" to the bare word "AGENT". After the
    # rich-display-visibility change, rich mode shows the PHASE line and the AGENT
    # start/end lines, prefixes the llm_response line with the agent tag, but
    # suppresses the tool_start (🔧) line (show_tools=False). The FileLogRenderer
    # still writes [TOOL]/[LLM] to workflow.log, but that stream is not the
    # capturing console. Each assertion pins a distinct event the renderer can only
    # have produced if the dispatcher wired the event through.
    assert "AGENT" in out and "injection-exploit" in out              # agent start line
    assert "▶ [Injection] injection-exploit started" in out           # agent start (full)
    assert "💭 [Injection] Turn 2: reflected XSS confirmed" in out    # llm_response line (agent-prefixed)
    assert "🔧" not in out                                            # tool_start suppressed in rich mode
    # Phase is shown in the scrolling log in rich mode (show_phase=True): the
    # PHASE line renders, and color_system=None strips "[bold cyan]PHASE[/]" to
    # the bare word "PHASE". Its presence proves the WorkflowLogger wired
    # show_phase=True through to the RichConsoleRenderer.
    assert "PHASE" in out                                             # scrolling phase line shown
    assert "Starting exploitation" in out
    assert "exploitation" in out                                      # phase in status line
    assert "1 done" in out                                            # completed_count
