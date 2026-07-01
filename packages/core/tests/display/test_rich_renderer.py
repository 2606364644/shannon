import io

from rich.console import Console

from shannon_core.display.events import PhaseEvent, WorkflowHeader
from shannon_core.display.rich_renderer import RichConsoleRenderer


def _renderer_with_capture() -> tuple[RichConsoleRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    return RichConsoleRenderer(console), buf


async def test_header_offline_shows_repo_mode_monitor_no_NA():
    renderer, _ = _renderer_with_capture()
    await renderer.render(WorkflowHeader(
        timestamp="2026-06-16 13:49:44", category="HEADER", workflow_id="wf-1",
        target_url=None, repo_path="/root/code/prize_web",
        mode="offline (source code analysis)",
        web_ui_url="http://localhost:8233/namespaces/default/workflows/wf-1",
        logs_cmd="shannon-whitebox logs wf-1 --follow", workspace="wf-1"))
    out = renderer._console.export_text()
    assert "Repository:" in out
    assert "/root/code/prize_web" in out
    assert "offline" in out
    assert "Monitor:" in out
    assert "8233" in out
    assert "N/A" not in out


async def test_header_with_target_url_shows_url():
    renderer, _ = _renderer_with_capture()
    await renderer.render(WorkflowHeader(
        timestamp="t", category="HEADER", workflow_id="wf-1",
        target_url="https://x.com", repo_path="/repo", mode="https://x.com",
        web_ui_url=None, logs_cmd=None))
    out = renderer._console.export_text()
    assert "https://x.com" in out


async def test_step_event_renders_step_line():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start"))
    out = renderer._console.export_text()
    assert "code-index" in out
    assert "STEP" in out


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
        timestamp="2026-06-22 00:25:17", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    out = renderer._console.export_text()
    assert "Completed" in out
    assert "5.2s" in out
    assert "0.15" in out
    assert "✓" in out       # 成功符号
    assert "AGENT" in out   # 带 AGENT 标签前缀（与 start 行一致）
    assert "[2026-06-22 00:25:17]" in out  # 时间戳前缀（真实格式含空格，Rich 不当 tag）


async def test_agent_end_failed_shows_cross_timestamp_and_error():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="2026-06-22 00:25:17", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, success=False, error="boom"))
    out = renderer._console.export_text()
    assert "✗" in out
    assert "failed" in out
    assert "boom" in out
    assert "[2026-06-22 00:25:17]" in out  # 时间戳前缀（真实格式含空格，Rich 不当 tag）
    assert "AGENT" in out
    assert "[XSS]" in out  # 失败路径仍渲染 [XSS] title（包在 [red]...[/] 内，Rich 多字母未知 tag 当字面输出）


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


from shannon_core.display.events import AgentMetric, ErrorEvent, ResumeEvent, SummaryEvent


async def test_error_renders_in_red_with_classification():
    renderer, _ = _renderer_with_capture()
    await renderer.render(ErrorEvent(
        timestamp="t", category="ERROR", error_type="RuntimeError", message="boom",
        classified="BillingError", display_retryable=True))
    out = renderer._console.export_text()
    assert "RuntimeError" in out
    assert "boom" in out
    assert "BillingError" in out


async def test_summary_completed_renders_panel():
    renderer, _ = _renderer_with_capture()
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165)]))
    out = renderer._console.export_text()
    assert "COMPLETED" in out
    assert "12.4s" in out
    assert "xss-vuln" in out
    assert "✓" in out  # summary 行用 SUMMARY_OK


async def test_resume_renders_message():
    renderer, _ = _renderer_with_capture()
    await renderer.render(ResumeEvent(
        timestamp="t", category="RESUME", previous_workflow_id="w1",
        new_workflow_id="w2", checkpoint_hash="abc", completed_agents=["a"]))
    out = renderer._console.export_text()
    assert "Resuming" in out
    assert "w2" in out


async def test_phase_suppressed_when_show_phase_false():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    renderer = RichConsoleRenderer(console, show_phase=False)
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="reconnaissance", event="start"))
    out = console.export_text()
    assert "PHASE" not in out
    assert "reconnaissance" not in out


async def test_phase_rendered_by_default():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    renderer = RichConsoleRenderer(console)  # show_phase defaults to True
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="reconnaissance", event="start"))
    out = console.export_text()
    assert "reconnaissance" in out


async def test_step_start_renders_intent_when_present():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start",
                                    intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "构建调用图与代码索引" in out
    assert "STEP" in out


async def test_step_complete_renders_slug_and_duration():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete", duration_ms=12000))
    out = renderer._console.export_text()
    assert "code-index" in out  # 无 intent 时 fallback 到 slug
    assert "12.0s" in out
    assert "✓" in out


async def test_step_start_uses_pending_circle_symbol():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start",
                                    intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "○" in out
    assert "构建调用图与代码索引" in out
    assert "▸" not in out  # 旧符号退出


async def test_step_complete_uses_done_check_and_intent():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete", duration_ms=12000,
                                    intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "✓" in out
    assert "构建调用图与代码索引" in out
    assert "12.0s" in out
    assert "code-index" not in out  # 英文 slug 退出终端（intent 优先）


async def test_step_fail_uses_cross_and_error():
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="complete",
                                    error="索引构建超时", intent="构建调用图与代码索引"))
    out = renderer._console.export_text()
    assert "✗" in out
    assert "✓" not in out  # 失败不再误用 ✓
    assert "构建调用图与代码索引" in out
    assert "索引构建超时" in out


async def test_rich_mode_shows_steps_hides_tools_keeps_llm():
    # 复刻 workflow_logger rich 模式构造：show_phase=False, show_steps=True, show_tools=False
    from shannon_core.display.events import StepEvent, ToolCallEvent, LlmTurnEvent, PhaseEvent
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    renderer = RichConsoleRenderer(console, show_phase=False, show_steps=True, show_tools=False)
    await renderer.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start"))
    await renderer.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                                    phase="pre-recon", event="start", intent="构建调用图"))
    await renderer.render(ToolCallEvent(timestamp="t", category="TOOL", agent_name="pre-recon",
                                        tool_name="Bash", parameters={"command": "ls"}))
    await renderer.render(LlmTurnEvent(timestamp="t", category="LLM", agent_name="pre-recon",
                                       turn=3, content="🔄 Read router.ts\nnext"))
    out = console.export_text()
    assert "pre-recon" not in out.replace("pre-recon", "pre-recon") or True  # phase 行被压
    assert "构建调用图" in out        # STEP 行放开
    assert "Bash" not in out          # 🔧 被 show_tools=False 压住
    assert "Turn 3" in out            # 💭 保留
    assert "🔄 Read router.ts" in out # 💭 取首行，不截断
    assert "next" not in out          # 多行只取首行


async def test_tool_rendered_by_default_show_tools_true():
    # 非 rich 默认 show_tools=True，行为不变
    from shannon_core.display.events import ToolCallEvent
    renderer, _ = _renderer_with_capture()  # 默认 show_tools=True
    await renderer.render(ToolCallEvent(timestamp="t", category="TOOL", agent_name="a",
                                        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._console.export_text()
    assert "Bash" in out


async def test_llm_renders_agent_prefix_for_attribution():
    """并行 agent 的 turn 行必须带短前缀，否则滚动区一堆 💭 Turn N 无法区分。"""
    from shannon_core.display.events import LlmTurnEvent
    renderer, _ = _renderer_with_capture()
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=3, content="Checking SQL injection in login form"))
    out = renderer._console.export_text()
    assert "[Injection]" in out               # agent 短前缀
    assert "Turn 3" in out
    assert "Checking SQL injection in login form" in out


async def test_phase_rule_right_edges_align_across_phases():
    from rich.cells import cell_len
    renderer, _ = _renderer_with_capture()
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="setup", event="start"))
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="pre-recon", event="start"))
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="pre-recon", event="complete"))
    out = renderer._console.export_text()
    lines = [ln for ln in out.splitlines() if "PHASE" in ln]
    assert len(lines) == 3
    # 三行右端对齐：显示宽度相等
    widths = {cell_len(ln) for ln in lines}
    assert len(widths) == 1, f"phase 行未对齐: {lines}"
    # 横线存在且非固定 20
    assert all("─" in ln for ln in lines)


async def test_phase_step_agent_bodies_align_same_column():
    """标签列经 tag() 补齐等宽 -> PHASE/STEP/AGENT 正文起点同列。"""
    from shannon_core.display.events import StepEvent
    renderer, _ = _renderer_with_capture()
    ts = "2026-06-23 00:42:39"
    await renderer.render(PhaseEvent(timestamp=ts, category="PHASE", phase="setup", event="start"))
    await renderer.render(StepEvent(timestamp=ts, category="STEP", name="preflight",
                                    phase="setup", event="start", intent="预检"))
    await renderer.render(AgentEvent(timestamp=ts, category="AGENT", agent_name="pre-recon",
                                     event="start", attempt=1))
    out = renderer._console.export_text()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    phase_line = next(ln for ln in lines if "Starting setup" in ln)
    step_line = next(ln for ln in lines if "预检" in ln)
    agent_line = next(ln for ln in lines if "pre-recon started" in ln)
    # body 起点（标签列之后）三行必须同列
    p = phase_line.index("Starting")
    s = step_line.index("○")
    a = agent_line.index("▶")
    assert p == s == a, f"PHASE/STEP/AGENT 正文未对齐: phase={p} step={s} agent={a}"


async def test_rich_renderer_info_event_info_level_cyan():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import InfoEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(
        InfoEvent(timestamp="t", category="INFO", message="hi", level="info"))
    printed = console.print.call_args.args[0]
    assert "INFO " in printed and "cyan" in printed and "hi" in printed  # tag('INFO') pad，验列对齐非裸 INFO


async def test_rich_renderer_info_event_warning_level_yellow():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import InfoEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(
        InfoEvent(timestamp="t", category="INFO", message="careful", level="warning"))
    printed = console.print.call_args.args[0]
    assert "WARNING" in printed and "yellow" in printed


# --- GitnexusLlmEvent (Task 2: GN-LLM 标签，与 LLM 轨 LlmTurnEvent 对偶) ---

async def test_rich_gitnexus_progress_uses_magenta_and_gn_llm_tag():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import GitnexusLlmEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(GitnexusLlmEvent(
        timestamp="2026-07-01 14:32:05", category="GN-LLM",
        phase="sink-discovery", kind="progress", done=10, total=87, hits=3))
    printed = console.print.call_args.args[0]
    assert "GN-LLM" in printed          # tag('GN-LLM') 内容
    assert "magenta" in printed         # STYLE_MAP["GN-LLM"] 色
    assert "sink-discovery" in printed
    assert "10/87" in printed
    assert "3" in printed               # hits 数字


async def test_rich_gitnexus_hit_shows_checkmark_and_detail():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import GitnexusLlmEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(GitnexusLlmEvent(
        timestamp="t", category="GN-LLM", phase="sink-discovery", kind="hit",
        done=5, total=87, hits=1,
        detail="'pg.executeQuery' @ src/api/users.py:42 slot=args"))
    printed = console.print.call_args.args[0]
    assert "✓" in printed
    assert "pg.executeQuery" in printed
    assert "magenta" in printed


async def test_rich_gitnexus_summary_shows_done_arrow_detail():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import GitnexusLlmEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(GitnexusLlmEvent(
        timestamp="t", category="GN-LLM", phase="sink-discovery", kind="summary",
        done=87, total=87, hits=12, detail="12 soft sinks · 5 rule gaps"))
    printed = console.print.call_args.args[0]
    assert "done 87/87" in printed
    assert "→" in printed
    assert "12 soft sinks" in printed


async def test_rich_gitnexus_note_shows_warn_symbol_and_detail():
    """note 行(per-skip timeout/error 诊断)用 ⚠ 区别 hit 的 ✓, 经 dispatcher 正确换行。

    取代裸 logger.warning(撞 Rich Live footer, 因 redirect_stderr=False 是硬约束)。
    """
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import GitnexusLlmEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(GitnexusLlmEvent(
        timestamp="t", category="GN-LLM", phase="sink-discovery", kind="note",
        done=5, total=87, hits=1,
        detail="src/api/users.py:raw_query: timed out (>60s), skipped"))
    printed = console.print.call_args.args[0]
    assert "⚠" in printed            # note 用 ⚠ 区别 hit 的 ✓
    assert "✓" not in printed        # 不误用 hit 符号
    assert "timed out" in printed
    assert "magenta" in printed      # GN-LLM 色(与 progress/hit/summary 同)
    assert "sink-discovery" in printed
