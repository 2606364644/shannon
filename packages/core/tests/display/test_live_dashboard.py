import io

from rich.console import Console

from shannon_core.display.events import PhaseEvent, AgentEvent, ToolCallEvent
from shannon_core.display.live_dashboard import LiveDashboardRenderer


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=100, force_terminal=True, color_system=None, force_interactive=True), buf


async def test_render_folds_event_into_snapshot():
    console, _ = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    assert r.snapshot.current_phase == "recon"


async def test_rich_console_renders_phase_and_agent_rows():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="vulnerability-analysis", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="injection-vuln",
                              event="start", attempt=1))
    await r.render(ToolCallEvent(timestamp="t", category="TOOL", agent_name="injection-vuln",
                                 tool_name="Bash", parameters={"command": "rg -n eval"}))
    console.print(r)
    out = buf.getvalue()
    assert "vulnerability-analysis" in out
    assert "injection-vuln" in out
    assert "Bash" in out or "command=rg -n eval" in out


async def test_done_agent_shows_checkmark_style():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="end",
                              attempt=1, duration_ms=4500, cost_usd=0.23, success=True))
    console.print(r)
    out = buf.getvalue()
    assert "auth-vuln" in out
    assert "4.5s" in out
