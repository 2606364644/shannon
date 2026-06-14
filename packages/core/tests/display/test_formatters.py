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
