from shannon_core.display.events import PhaseEvent, WorkflowHeader
from shannon_core.display.file_renderer import FileLogRenderer


class FakeWriter:
    def __init__(self):
        self.chunks: list[str] = []

    async def write(self, text: str) -> None:
        self.chunks.append(text)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


async def test_header_includes_workflow_id_and_target():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(WorkflowHeader(
        timestamp="2026-01-01 12:00:00", category="HEADER",
        workflow_id="wf-1", target_url="https://x.com"))
    out = renderer._writer.text
    assert "Shannon Pentest - Workflow Log" in out
    assert "Workflow ID: wf-1" in out
    assert "Target URL:  https://x.com" in out
    assert "Started:     2026-01-01 12:00:00" in out
    assert out.count("=" * 80) == 3


async def test_phase_start_prepends_blank_line():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(PhaseEvent(
        timestamp="2026-01-01 12:00:00", category="PHASE",
        phase="reconnaissance", event="start"))
    out = renderer._writer.text
    assert out.startswith("\n")
    assert "[PHASE] Starting reconnaissance" in out


async def test_phase_complete_no_blank_prefix():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="recon", event="complete"))
    out = renderer._writer.text
    assert "[PHASE] Completed recon" in out
    assert not out.startswith("\n")
