import io

from rich.console import Console

from shannon_core.display.events import StepEvent, PhaseEvent, AgentEvent
from shannon_core.display.live_dashboard import LiveDashboardRenderer


def _console(width: int = 100) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=width, force_terminal=True,
                   color_system=None, force_interactive=True), buf


async def test_render_folds_event_into_snapshot():
    console, _ = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    assert r.snapshot.current_phase == "recon"


async def test_status_line_shows_phase_counts_cost_and_running_agent():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="vulnerability-analysis", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="injection-vuln",
                              event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "vulnerability-analysis" in out   # phase in status line
    assert "0 done" in out                   # completed count (agent running, not done)
    assert "$0.0000" in out                  # accumulated cost
    assert "injection-vuln" in out           # running agent appended with spinner


async def test_separator_spans_full_console_width():
    console, buf = _console(width=80)
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    console.print(r)
    out = buf.getvalue()
    assert out.count("─") == 80              # rule width tracks options.max_width, not hardcoded


async def test_done_agent_increments_count_and_leaves_status_line():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="end",
                              attempt=1, duration_ms=4500, cost_usd=0.23, success=True))
    console.print(r)
    out = buf.getvalue()
    assert "1 done" in out                   # completed_count incremented
    assert "$0.2300" in out                  # cost accumulated into status line
    assert "auth-vuln" not in out            # done agent no longer "running" -> not in status line
    assert "4.5s" not in out                 # per-agent duration NOT shown in dashboard


async def test_status_line_shows_step_progress_and_running_units():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon",
                              event="start",
                              steps=("code-index", "pre-recon", "merge-sinks")))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="pre-recon",
                              event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "pre-recon" in out            # phase
    assert "step 0/3" in out             # 0 completed of 3 units
    assert "code-index" in out           # running unit
    assert "pre-recon" in out            # running unit (agent)


async def test_status_line_falls_back_when_phase_has_no_steps():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    # PhaseEvent without steps (legacy) -> no "step N/M", keep "N done"
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="recon",
                              event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "0 done" in out
    assert "step " not in out
