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
    assert "Injection" in out                # running agent 行用短前缀（全名在滚动区 AGENT 行）


async def test_no_full_width_separator_in_footer():
    """footer 不含满宽 ─ 分隔线（鬼影根因之一：满宽横线每 tick 叠画不擦）。

    视觉兜底：rows 直接从状态行起，不再有顶部分隔线。"""
    console, buf = _console(width=80)
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    console.print(r)
    out = buf.getvalue()
    assert "─" * 80 not in out  # 无满宽分隔线


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
    assert "pre-recon" in out            # phase 名（状态行）
    assert "step 0/3" in out             # 0 completed of 3 units
    assert "running..." in out           # pre-recon agent 行：无 turn/tool 时 action 回退
    # 注：code-index 是非 agent 单元，当存在 running agent 时不在状态栏单独显示，
    # 其进度由状态行 "step 0/3" 体现。


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


async def test_pinned_row_shows_step_intent_and_latest_turn():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                              steps=("code-index", "pre-recon"),
                              step_intents=("构建调用图", "扫描架构与入口点")))
    from shannon_core.display.events import LlmTurnEvent
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="pre-recon",
                              event="start", attempt=1))
    await r.render(LlmTurnEvent(timestamp="t", category="LLM", agent_name="pre-recon",
                                turn=33, content="🔄 Read server/app/router.ts"))
    console.print(r)
    out = buf.getvalue()
    assert "step 0/2" in out                 # 状态行
    assert "扫描架构与入口点" in out          # 钉住行：步骤意图
    assert "t33" in out                      # agent 行：intent label + turn 号
    assert "🔄 Read server/app/router.ts" in out


async def test_pinned_row_falls_back_to_running_units_without_turns():
    # 无 LLM 轮时，钉住行退化为运行中单元名（保持既有可见性）
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                              steps=("code-index", "pre-recon")))
    await r.render(StepEvent(timestamp="t", category="STEP", name="code-index",
                             phase="pre-recon", event="start"))
    console.print(r)
    out = buf.getvalue()
    assert "code-index" in out


async def test_multiple_running_agents_each_get_a_row():
    """并行多个 running agent 时，状态栏应为每个 agent 渲染一行（短前缀），
    而非只显示最后一个。"""
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE",
                              phase="vulnerability-analysis", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="injection-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="xss-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="auth-vuln", event="start", attempt=1))
    console.print(r)
    out = buf.getvalue()
    assert "Injection" in out      # 三个并行 agent 各一行
    assert "XSS" in out
    assert "Auth" in out


async def test_agent_row_prefers_current_tool_over_turn_text():
    """每行优先显示当前工具（更实时），其次 turn 文本。"""
    from shannon_core.display.events import LlmTurnEvent, ToolCallEvent
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="vuln", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT",
                              agent_name="injection-vuln", event="start", attempt=1))
    await r.render(LlmTurnEvent(timestamp="t", category="LLM",
                                agent_name="injection-vuln", turn=4, content="Analyzing"))
    await r.render(ToolCallEvent(timestamp="t", category="TOOL",
                                 agent_name="injection-vuln",
                                 tool_name="Bash", parameters={"command": "rg -n eval"}))
    console.print(r)
    out = buf.getvalue()
    assert "rg -n eval" in out    # humanize 后的当前工具
    assert "t4" in out            # turn 号
