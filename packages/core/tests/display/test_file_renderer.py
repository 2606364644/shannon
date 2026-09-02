from supernova_core.display.events import PhaseEvent, WorkflowHeader
from supernova_core.display.file_renderer import FileLogRenderer


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
    assert "Supernova Pentest - Workflow Log" in out
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


from supernova_core.display.events import AgentEvent, LlmTurnEvent, ToolCallEvent


async def test_agent_start_with_prefix():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="injection-vuln",
        event="start", attempt=2))
    assert "[AGENT] ▶ [Injection] injection-vuln started (attempt 2)\n" in renderer._writer.text


async def test_agent_start_no_prefix_for_unknown():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="pre-recon",
        event="start", attempt=1))
    assert "[AGENT] ▶ pre-recon started (attempt 1)\n" in renderer._writer.text


async def test_agent_end_completed_with_metrics():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    assert "[AGENT] ✓ [XSS] xss-vuln Completed (5.2s, $0.1500)\n" in renderer._writer.text


async def test_agent_end_failed():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=100, success=False, error="boom"))
    assert "[AGENT] ✗ [XSS] xss-vuln failed (100ms) — boom" in renderer._writer.text


async def test_tool_line_alignment():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ToolCallEvent(
        timestamp="t", category="TOOL", agent_name="injection-vuln",
        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._writer.text
    # [TOOL] 后 2 空格标签列 + 2 空格 agent 执行期缩进（2026-09-02 TOOL/LLM 行缩进一级）
    assert "[TOOL]    [Injection] injection-vuln: Bash: command=ls\n" in out


async def test_llm_line_alignment():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=1, content="Analyzing"))
    out = renderer._writer.text
    # [LLM] 后 3 空格标签列补齐 + 2 空格 agent 执行期缩进
    assert "[LLM]     [Injection] injection-vuln: Turn 1: Analyzing\n" in out


async def test_tool_llm_unknown_agent_full_name_with_indent():
    """未知 agent（chain-verdict-* 不在前缀表）TOOL/LLM 行显示全名 + 缩进一级。"""
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ToolCallEvent(
        timestamp="t", category="TOOL", agent_name="chain-verdict-xss-40",
        tool_name="Bash", parameters={"command": "ls"}))
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="chain-verdict-xss-40",
        turn=5, content="The sink is at 214:27"))
    out = renderer._writer.text
    assert "[TOOL]    chain-verdict-xss-40: Bash: command=ls\n" in out
    assert "[LLM]     chain-verdict-xss-40: Turn 5: The sink is at 214:27\n" in out


from supernova_core.display.events import AgentMetric, ErrorEvent, ResumeEvent, SummaryEvent


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
    assert "[ERROR] RuntimeError: x (context: during scan) [BillingError · 将重试]" in line


async def test_error_line_warning_category_uses_warning_tag():
    """attempt 级 retryable(category=WARNING)→ [WARNING] 标签,与终端一致,不再 [ERROR]。"""
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ErrorEvent(
        timestamp="t", category="WARNING", error_type="PentestError",
        message="Missing exploitation queue"))
    assert "[WARNING]" in renderer._writer.text
    assert "[ERROR]" not in renderer._writer.text


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


async def test_summary_cost_uses_currency_symbol_cny():
    """cost 定价(spec 2026-07-09): Total Cost + agent breakdown 按 cost_currency 显示 ¥。"""
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=100, total_cost_usd=0.0886, cost_currency="CNY",
        agents=[AgentMetric(name="recon", duration_ms=50, cost_usd=0.0443, cost_currency="CNY")]))
    out = renderer._writer.text
    assert "Total Cost:  ¥0.0886" in out
    assert "¥0.0443" in out  # agent breakdown 也按币种
    # 默认(USD)仍 $
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=1, total_cost_usd=0.0123, cost_currency="USD", agents=[]))
    assert "Total Cost:  $0.0123" in renderer._writer.text


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


from supernova_core.display.events import StepEvent


async def test_step_event_renders_step_line():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from supernova_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="complete", duration_ms=12000))
    out = "".join(w.lines)
    assert "[STEP ] ○ code-index\n" in out        # start: 符号 + name fallback，标签补齐 [STEP ]
    assert "[STEP ] ✓ code-index  12.0s\n" in out  # complete: 符号 + duration


async def test_step_file_line_includes_intent_when_present():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from supernova_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    from supernova_core.display.events import StepEvent
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start",
                             intent="构建调用图与代码索引"))
    out = "".join(w.lines)
    assert "[STEP ] ○ 构建调用图与代码索引\n" in out   # 符号 + intent，不再有 name:verb


async def test_header_renders_repo_and_monitor_when_offline():
    class _W:
        def __init__(self): self.lines = []
        async def write(self, s): self.lines.append(s)
    w = _W()
    from supernova_core.display.file_renderer import FileLogRenderer
    r = FileLogRenderer(w)
    await r.render(WorkflowHeader(
        timestamp="2026-06-16 13:49:44", category="HEADER", workflow_id="wf-1",
        target_url=None, repo_path="/root/code/prize_web", mode="offline (source code analysis)",
        web_ui_url="http://localhost:8233/namespaces/default/workflows/wf-1",
        logs_cmd="supernova-whitebox logs wf-1 --follow", workspace="wf-1"))
    out = "".join(w.lines)
    assert "Repository:" in out
    assert "/root/code/prize_web" in out
    assert "offline" in out
    assert "Monitor:" in out
    assert "8233" in out
    assert "Target URL:  N/A" not in out     # offline -> no N/A target line


async def test_file_summary_uses_ok_symbol_for_success():
    from supernova_core.display.events import AgentMetric, SummaryEvent
    from supernova_core.display.file_renderer import FileLogRenderer

    class _Buf:
        def __init__(self):
            self.s = ""

        async def write(self, s):
            self.s += s

    buf = _Buf()
    r = FileLogRenderer(buf)
    await r.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165)]))
    assert "✓ xss-vuln" in buf.s


async def test_file_renderer_info_event_info_level():
    from supernova_core.display.file_renderer import FileLogRenderer
    from supernova_core.display.events import InfoEvent
    from unittest.mock import AsyncMock
    writer = AsyncMock()
    await FileLogRenderer(writer).render(
        InfoEvent(timestamp="2026-06-28 12:00:00", category="INFO", message="hi", level="info"))
    written = writer.write.await_args.args[0]
    assert "[INFO ]" in written and "hi" in written and written.endswith("\n")  # tag() pad 到 LABEL_WIDTH，与 [STEP ]/[PHASE] 列对齐


async def test_file_renderer_info_event_warning_level():
    from supernova_core.display.file_renderer import FileLogRenderer
    from supernova_core.display.events import InfoEvent
    from unittest.mock import AsyncMock
    writer = AsyncMock()
    await FileLogRenderer(writer).render(
        InfoEvent(timestamp="t", category="INFO", message="careful", level="warning"))
    assert "[WARNING]" in writer.write.await_args.args[0]


async def test_phase_step_agent_labels_align_in_file():
    """file [PHASE]/[STEP ]/[AGENT] 标签列等宽 -> 正文起点同列。"""
    from supernova_core.display.events import StepEvent
    renderer = FileLogRenderer(FakeWriter())
    ts = "2026-06-23 00:42:39"
    await renderer.render(PhaseEvent(timestamp=ts, category="PHASE", phase="setup", event="start"))
    await renderer.render(StepEvent(timestamp=ts, category="STEP", name="preflight",
                                    phase="setup", event="start", intent="预检"))
    await renderer.render(AgentEvent(timestamp=ts, category="AGENT", agent_name="pre-recon",
                                     event="start", attempt=1))
    out = renderer._writer.text
    lines = [ln for ln in out.splitlines() if ln.strip()]
    phase_line = next(ln for ln in lines if "[PHASE]" in ln)
    step_line = next(ln for ln in lines if "[STEP ]" in ln)
    agent_line = next(ln for ln in lines if "[AGENT]" in ln)
    # 三行正文起点同列（标签列 [PHASE]/[STEP ]/[AGENT] 均为 7 字符等宽）
    p = phase_line.index("Starting")
    s = step_line.index("○")
    a = agent_line.index("▶")
    assert p == s == a, f"file 标签列未对齐: phase={p} step={s} agent={a}"


# --- GitnexusLlmEvent (归 LLM 族: [LLM]   [GitNexus], 对偶 _llm 的 [LLM]   [Agent]) ---

from supernova_core.display.events import GitnexusLlmEvent


def _gn_evt(kind, **kw):
    base = dict(timestamp="2026-07-01 14:32:05", category="GN-LLM",
                phase="sink-discovery", kind=kind, done=10, total=87, hits=3)
    base.update(kw)
    return GitnexusLlmEvent(**base)


async def _gn_render(e) -> str:
    """Render a single event through FileLogRenderer and return the written text."""
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(e)
    return renderer._writer.text


async def test_gitnexus_progress_line():
    out = await _gn_render(_gn_evt("progress"))
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  10/87  · 3 sinks\n")


async def test_gitnexus_hit_line():
    e = _gn_evt("hit", done=5, hits=1,
                detail="'pg.executeQuery' @ src/api/users.py:42 slot=args")
    out = await _gn_render(e)
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  ✓ 'pg.executeQuery' "
        "@ src/api/users.py:42 slot=args\n")


async def test_gitnexus_summary_line():
    e = _gn_evt("summary", done=87, hits=12,
                detail="12 soft sinks · 5 rule gaps · 2 timeouts")
    out = await _gn_render(e)
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  done 87/87 → "
        "12 soft sinks · 5 rule gaps · 2 timeouts\n")


async def test_gitnexus_progress_noun_varies_by_phase():
    e = _gn_evt("progress", phase="chain-verdict", hits=2, done=10, total=34)
    out = await _gn_render(e)
    assert "· 2 vulnerable" in out      # 去 so far


async def test_gitnexus_note_line():
    """note 行: per-skip timeout/error 诊断, 用 ⚠ 区别 hit 的 ✓(与 rich 一致)。"""
    e = _gn_evt("note", done=5, hits=1,
                detail="src/api/users.py:raw_query: timed out (>60s), skipped")
    out = await _gn_render(e)
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  ⚠ "
        "src/api/users.py:raw_query: timed out (>60s), skipped\n")
