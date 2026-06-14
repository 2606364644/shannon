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
