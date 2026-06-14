import io

from rich.console import Console

from shannon_core.display.events import PhaseEvent, WorkflowHeader
from shannon_core.display.rich_renderer import RichConsoleRenderer


def _renderer_with_capture() -> tuple[RichConsoleRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    return RichConsoleRenderer(console), buf


async def test_header_renders_workflow_id_and_target():
    renderer, buf = _renderer_with_capture()
    await renderer.render(WorkflowHeader(
        timestamp="2026-01-01 12:00:00", category="HEADER",
        workflow_id="wf-1", target_url="https://x.com"))
    out = renderer._console.export_text()
    assert "Shannon Pentest" in out
    assert "wf-1" in out
    assert "https://x.com" in out


async def test_phase_start_renders_phase_name():
    renderer, _ = _renderer_with_capture()
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="reconnaissance", event="start"))
    out = renderer._console.export_text()
    assert "reconnaissance" in out
    assert "Starting" in out or "started" in out


from shannon_core.display.events import AgentEvent, LlmTurnEvent, ToolCallEvent


async def test_agent_start_shows_prefix():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="injection-vuln",
        event="start", attempt=1))
    out = renderer._console.export_text()
    assert "Injection" in out
    assert "injection-vuln" in out


async def test_agent_end_completed_shows_metrics():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    out = renderer._console.export_text()
    assert "Completed" in out
    assert "5.2s" in out
    assert "0.15" in out


async def test_tool_renders_humanized():
    renderer, _ = _renderer_with_capture()
    await renderer.render(ToolCallEvent(
        timestamp="t", category="TOOL", agent_name="injection-vuln",
        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._console.export_text()
    assert "Bash" in out
    assert "command=ls" in out


async def test_llm_renders_turn():
    renderer, _ = _renderer_with_capture()
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=1, content="Analyzing code"))
    out = renderer._console.export_text()
    assert "Turn 1" in out
    assert "Analyzing code" in out
