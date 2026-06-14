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


from shannon_core.display.events import AgentEvent, LlmTurnEvent, ToolCallEvent


async def test_agent_start_with_prefix():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="injection-vuln",
        event="start", attempt=2))
    assert "[AGENT] [Injection] injection-vuln: Starting (attempt 2)\n" in renderer._writer.text


async def test_agent_start_no_prefix_for_unknown():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="pre-recon",
        event="start", attempt=1))
    assert "[AGENT] pre-recon: Starting (attempt 1)\n" in renderer._writer.text


async def test_agent_end_completed_with_metrics():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    line = "[AGENT] [XSS] xss-vuln: Completed (5.2s, $0.1500)\n"
    assert line in renderer._writer.text


async def test_agent_end_failed():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=100, success=False, error="boom"))
    assert "[AGENT] [XSS] xss-vuln: Failed (100ms) - boom" in renderer._writer.text


async def test_tool_line_alignment():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ToolCallEvent(
        timestamp="t", category="TOOL", agent_name="injection-vuln",
        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._writer.text
    assert "[TOOL]  [Injection] injection-vuln: Bash: command=ls\n" in out  # two spaces after [TOOL]


async def test_llm_line_alignment():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=1, content="Analyzing"))
    out = renderer._writer.text
    assert "[LLM]   [Injection] injection-vuln: Turn 1: Analyzing\n" in out  # three spaces after [LLM]
