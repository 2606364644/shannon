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


from shannon_core.display.events import AgentMetric, ErrorEvent, ResumeEvent, SummaryEvent


async def test_error_line_basic():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ErrorEvent(
        timestamp="t", category="ERROR", error_type="ValueError", message="boom"))
    assert "[ERROR] ValueError: boom\n" in renderer._writer.text


async def test_error_line_with_context_and_classification():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ErrorEvent(
        timestamp="t", category="ERROR", error_type="RuntimeError", message="x",
        context="during scan", classified="BillingError", display_retryable=True))
    line = renderer._writer.text
    assert "[ERROR] RuntimeError: x (context: during scan) [BillingError · retryable]" in line


async def test_summary_completed_has_completion_marker():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165, success=True)]))
    out = renderer._writer.text
    assert "Workflow COMPLETED" in out  # COMPLETION_PATTERN must match
    assert "Status:      completed" in out
    assert "Duration:    12.4s" in out
    assert "Total Cost:  $0.3450" in out
    assert "✓ xss-vuln" in out


async def test_summary_failed_has_failure_marker():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="failed",
        total_duration_ms=1000, total_cost_usd=0.0, agents=[], error="something|went|wrong"))
    out = renderer._writer.text
    assert "Workflow FAILED" in out
    assert "Error:       something" in out


async def test_resume_block():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ResumeEvent(
        timestamp="t", category="RESUME", previous_workflow_id="w1",
        new_workflow_id="w2", checkpoint_hash="abc", completed_agents=["a", "b"]))
    out = renderer._writer.text
    assert "[RESUME] Resuming workflow" in out
    assert "Previous Workflow ID: w1" in out
    assert "New Workflow ID:      w2" in out


from shannon_core.display.events import StepEvent


async def test_step_event_renders_step_line():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from shannon_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="complete", duration_ms=12000))
    out = "".join(w.lines)
    assert "[STEP]" in out
    assert "code-index" in out
    assert "Starting" in out
    assert "Completed" in out


async def test_header_renders_repo_and_monitor_when_offline():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from shannon_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    await r.render(WorkflowHeader(
        timestamp="2026-06-16 13:49:44", category="HEADER", workflow_id="wf-1",
        target_url=None, repo_path="/root/code/prize_web", mode="offline (source code analysis)",
        web_ui_url="http://localhost:8233/namespaces/default/workflows/wf-1",
        logs_cmd="shannon-whitebox logs wf-1 --follow", workspace="wf-1"))
    out = "".join(w.lines)
    assert "Repository:" in out
    assert "/root/code/prize_web" in out
    assert "offline" in out
    assert "Monitor:" in out
    assert "8233" in out
    assert "Target URL:  N/A" not in out     # offline -> no N/A target line
