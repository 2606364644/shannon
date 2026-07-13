from shannon_core.display.formatters import format_duration, format_log_time, format_timestamp


def test_format_duration_milliseconds():
    assert format_duration(23) == "23ms"


def test_format_duration_seconds():
    assert format_duration(1500) == "1.5s"


def test_format_duration_minutes():
    assert format_duration(150000) == "2m 30s"


def test_format_timestamp_is_iso8601_with_z():
    ts = format_timestamp(1700000000123 / 1000)
    assert ts.endswith("Z")
    assert "T" in ts


def test_format_log_time_format():
    # format_log_time uses local now; just assert shape YYYY-MM-DD HH:MM:SS
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", format_log_time())


from shannon_core.display.formatters import agent_prefix


def test_agent_prefix_known_vuln_agents():
    assert agent_prefix("injection-vuln") == "[Injection]"
    assert agent_prefix("xss-vuln") == "[XSS]"
    assert agent_prefix("ssrf-vuln") == "[SSRF]"
    assert agent_prefix("auth-vuln") == "[Auth]"
    assert agent_prefix("authz-vuln") == "[Authz]"


def test_agent_prefix_exploit_variants_share_prefix():
    assert agent_prefix("injection-exploit") == "[Injection]"
    assert agent_prefix("authz-exploit") == "[Authz]"
    assert agent_prefix("auth-exploit") == "[Auth]"


def test_agent_prefix_unknown_falls_back():
    assert agent_prefix("pre-recon") == "[Agent]"
    assert agent_prefix("totally-unknown") == "[Agent]"


from shannon_core.display.formatters import format_error_block, summarize_todo


def test_summarize_todo_shows_latest_completed():
    params = {"todos": [
        {"status": "completed", "content": "step one"},
        {"status": "completed", "content": "step two"},
        {"status": "in_progress", "content": "step three"},
    ]}
    assert summarize_todo(params) == "✅ step two"


def test_summarize_todo_shows_in_progress_when_none_completed():
    params = {"todos": [
        {"status": "in_progress", "content": "current"},
    ]}
    assert summarize_todo(params) == "🔄 current"


def test_summarize_todo_returns_none_when_empty():
    assert summarize_todo({"todos": []}) is None
    assert summarize_todo({}) is None


def test_format_error_block_pipe_delimited():
    result = format_error_block("phase context|ErrorType|message|Hint: retry")
    lines = result.split("\n")
    assert lines[0] == "Error:       phase context"
    assert lines[1] == "             ErrorType"
    assert lines[2] == "             message"
    assert lines[3] == "             Hint: retry"


def test_format_error_block_single_segment():
    assert format_error_block("just one error") == "Error:       just one error\n"


from shannon_core.display.formatters import humanize_tool_call, maybe_browser_action


def test_humanize_task_launch():
    result = humanize_tool_call("Task", {"description": "deep analysis"})
    assert result == "🚀 Launching deep analysis"


def test_humanize_todowrite_uses_summarize():
    result = humanize_tool_call("TodoWrite", {"todos": [
        {"status": "completed", "content": "done thing"},
    ]})
    assert result == "✅ done thing"


def test_humanize_todowrite_none_returns_placeholder():
    # summarize_todo can return None; humanize falls back to a generic line
    result = humanize_tool_call("TodoWrite", {"todos": []})
    assert result == "TodoWrite"


def test_humanize_bash_browser_action():
    result = humanize_tool_call("Bash", {"command": "playwright-cli navigate https://x.com"})
    assert "🌐" in result
    assert "x.com" in result


def test_humanize_bash_non_browser():
    result = humanize_tool_call("Bash", {"command": "ls -la"})
    assert "command=ls -la" in result


def test_humanize_unknown_tool_default_params():
    result = humanize_tool_call("Read", {"file_path": "/tmp/x"})
    assert "file_path=/tmp/x" in result


def test_maybe_browser_action_navigate():
    assert maybe_browser_action({"command": "playwright-cli goto https://a.com"}) == "🌐 Navigating to a.com"


def test_maybe_browser_action_click():
    assert maybe_browser_action({"command": "playwright-cli click #submit"}) == "🖱️ Clicking #submit"


def test_maybe_browser_action_non_browser_returns_none():
    assert maybe_browser_action({"command": "ls -la"}) is None


def test_maybe_browser_action_agent_browser_navigate():
    assert maybe_browser_action(
        {"command": "agent-browser --session s1 open https://a.com"}
    ) == "🌐 Navigating to a.com"


def test_maybe_browser_action_agent_browser_click():
    assert maybe_browser_action(
        {"command": "agent-browser --session s1 click @e5"}
    ) == "🖱️ Clicking @e5"


def test_maybe_browser_action_agent_browser_snapshot():
    assert maybe_browser_action(
        {"command": "agent-browser --session s1 snapshot"}
    ) == "📸 Taking page snapshot"


from shannon_core.display.formatters import first_nonempty_line


def test_first_nonempty_line_single_line():
    assert first_nonempty_line("🔄 Read router.ts") == "🔄 Read router.ts"


def test_first_nonempty_line_picks_first_non_blank():
    assert first_nonempty_line("\n\n  🔄 Read router.ts  \nnext") == "🔄 Read router.ts"


def test_first_nonempty_line_empty_returns_empty():
    assert first_nonempty_line("") == ""
    assert first_nonempty_line("   \n  ") == ""


from shannon_core.display.formatters import pad_rule, PHASE_RULE_WIDTH


def test_pad_rule_constant_exists():
    assert PHASE_RULE_WIDTH == 36


def test_pad_rule_ascii():
    # cell_len("Starting setup") == 14 -> 36 - 14 = 22 个 ─
    result = pad_rule("Starting setup")
    assert result.startswith("Starting setup ")
    assert result.count("─") == 22


def test_pad_rule_cjk_counts_double_width():
    # cell_len("预检") == 4（中文双宽）-> 36 - 4 = 32 个 ─
    assert pad_rule("预检").count("─") == 32


def test_pad_rule_overflow_floors_at_two():
    # 文字超长时兜底至少 2 个 ─
    assert pad_rule("a" * 40).count("─") == 2


def test_pad_rule_same_col_aligns_right_edge():
    # 同一 col 调用，显示宽度恒定 -> 右端对齐
    from rich.cells import cell_len
    a = pad_rule("Starting setup")
    b = pad_rule("Completed pre-recon")
    assert cell_len(a) == cell_len(b)


from shannon_core.display.formatters import tag, LABEL_WIDTH


def test_tag_pads_short_label_to_width():
    assert tag("STEP") == "STEP "          # 4 -> 5


def test_tag_no_pad_when_already_full_width():
    assert tag("PHASE") == "PHASE"
    assert tag("AGENT") == "AGENT"


def test_tag_all_core_labels_equal_width():
    assert {len(tag(l)) for l in ("PHASE", "STEP", "AGENT")} == {LABEL_WIDTH}
    assert LABEL_WIDTH == 5


from shannon_core.display.formatters import step_body
from shannon_core.display.events import StepEvent


def test_step_body_start_uses_pending_and_intent():
    e = StepEvent(timestamp="t", category="STEP", name="code-index", phase="pre-recon",
                  event="start", intent="构建调用图与代码索引")
    assert step_body(e) == "○ 构建调用图与代码索引"


def test_step_body_start_falls_back_to_name_when_no_intent():
    e = StepEvent(timestamp="t", category="STEP", name="code-index", phase="pre-recon",
                  event="start")
    assert step_body(e) == "○ code-index"


def test_step_body_complete_with_duration():
    e = StepEvent(timestamp="t", category="STEP", name="code-index", phase="pre-recon",
                  event="complete", duration_ms=4100, intent="构建调用图与代码索引")
    assert step_body(e) == "✓ 构建调用图与代码索引  4.1s"


def test_step_body_complete_without_duration():
    e = StepEvent(timestamp="t", category="STEP", name="x", phase="p", event="complete")
    assert step_body(e) == "✓ x"


def test_step_body_error_uses_cross_and_error():
    e = StepEvent(timestamp="t", category="STEP", name="x", phase="p",
                  event="complete", error="索引构建超时", intent="构建调用图")
    assert step_body(e) == "✗ 构建调用图  — 索引构建超时"


from shannon_core.display.formatters import phase_body, agent_title, agent_body
from shannon_core.display.events import PhaseEvent, AgentEvent


def test_phase_body_start():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="setup", event="start")
    assert phase_body(e) == "Starting setup"


def test_phase_body_complete():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="complete")
    assert phase_body(e) == "Completed pre-recon"


def test_agent_title_known_prefix():
    assert agent_title("injection-vuln") == "[Injection] injection-vuln"
    assert agent_title("xss-vuln") == "[XSS] xss-vuln"


def test_agent_title_unknown_is_bare_name():
    assert agent_title("pre-recon") == "pre-recon"


def test_agent_body_start_with_prefix():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="injection-vuln",
                   event="start", attempt=1)
    assert agent_body(e) == "▶ [Injection] injection-vuln started (attempt 1)"


def test_agent_body_start_unknown_agent():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="pre-recon",
                   event="start", attempt=1)
    assert agent_body(e) == "▶ pre-recon started (attempt 1)"


def test_agent_body_end_completed_with_metrics():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="xss-vuln",
                   event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True)
    assert agent_body(e) == "✓ [XSS] xss-vuln Completed (5.2s, $0.1500)"


def test_agent_body_end_failed():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="xss-vuln",
                   event="end", attempt=1, duration_ms=100, success=False, error="boom")
    assert agent_body(e) == "✗ [XSS] xss-vuln failed (100ms) — boom"


from shannon_core.display.formatters import gitnexus_body, gitnexus_hits_noun
from shannon_core.display.events import GitnexusLlmEvent


def _gn_evt(kind, **kw):
    base = dict(timestamp="t", category="GN-LLM", phase="sink-discovery",
                kind=kind, done=10, total=87, hits=3)
    base.update(kw)
    return GitnexusLlmEvent(**base)


def test_gitnexus_hits_noun_known_phases():
    assert gitnexus_hits_noun("sink-discovery") == "sinks"
    assert gitnexus_hits_noun("source-discovery") == "sources"
    assert gitnexus_hits_noun("taint-analysis") == "taint_flows"
    assert gitnexus_hits_noun("chain-verdict") == "vulnerable"


def test_gitnexus_hits_noun_unknown_falls_back():
    assert gitnexus_hits_noun("mystery-phase") == "hits"


def test_gitnexus_body_progress_includes_noun_no_so_far():
    assert gitnexus_body(_gn_evt("progress")) == "sink-discovery  10/87  · 3 sinks"


def test_gitnexus_body_progress_noun_varies_by_phase():
    e = _gn_evt("progress", phase="chain-verdict", hits=2, done=10, total=34)
    assert gitnexus_body(e) == "chain-verdict  10/34  · 2 vulnerable"


def test_gitnexus_body_hit_shows_checkmark_and_detail():
    e = _gn_evt("hit", detail="'pg.executeQuery' @ src/api/users.py:42 slot=args")
    assert gitnexus_body(e) == "sink-discovery  ✓ 'pg.executeQuery' @ src/api/users.py:42 slot=args"


def test_gitnexus_body_summary_shows_done_arrow_detail():
    e = _gn_evt("summary", done=87, hits=12,
                detail="12 soft sinks · 5 rule gaps · 2 timeouts")
    assert gitnexus_body(e) == "sink-discovery  done 87/87 → 12 soft sinks · 5 rule gaps · 2 timeouts"


def test_gitnexus_body_note_shows_warn_and_detail():
    e = _gn_evt("note", detail="src/api/users.py: timed out (>60s), skipped")
    assert gitnexus_body(e) == "sink-discovery  ⚠ src/api/users.py: timed out (>60s), skipped"


from shannon_core.display.formatters import wrap_body, truncate_action, LOG_INDENT


def test_log_indent_constant_is_label_column_offset():
    # [YYYY-MM-DD HH:MM:SS]=19 + " "=1 + LABEL_WIDTH=5 + "  "=2 -> body 起点 27
    assert LOG_INDENT == 27


def test_wrap_body_short_returns_single_line_unchanged():
    assert wrap_body("hi", 80) == ["hi"]


def test_wrap_body_long_wraps_with_indented_continuation():
    parts = wrap_body("a" * 100, 80)
    assert parts[0] == "a" * 53                       # avail = width - LOG_INDENT = 80 - 27
    assert len(parts) > 1
    assert all(p.startswith(" " * LOG_INDENT) for p in parts[1:])  # 续行缩进到标签列后


def test_wrap_body_width_le_indent_floors_avail_no_loop():
    # width(20) < indent(27) -> avail floor at 1，逐字符换行不死循环
    parts = wrap_body("abc", 20)
    assert isinstance(parts, list)
    assert parts and parts[0].startswith("a")


def test_wrap_body_empty_returns_single_empty_line():
    assert wrap_body("", 80) == [""]


def test_truncate_action_short_unchanged():
    assert truncate_action("running...") == "running..."


def test_truncate_action_long_truncated_with_ellipsis_within_width():
    from rich.cells import cell_len
    result = truncate_action("x" * 100, max_width=60)
    assert result.endswith("…")
    assert cell_len(result) <= 60


def test_truncate_action_cjk_counts_double_display_width():
    from rich.cells import cell_len
    result = truncate_action("预" * 40, max_width=60)   # 显示宽 80 > 60
    assert result.endswith("…")
    assert cell_len(result) <= 60
