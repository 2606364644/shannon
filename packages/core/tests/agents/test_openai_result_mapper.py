from unittest.mock import MagicMock

from shannon_core.agents.openai_result_mapper import map_run_result
from shannon_core.agents.runner import ClaudeRunResult, TokenUsage


def _usage(inp, outp):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = outp
    return u


def _run_result(final_output, usage):
    rr = MagicMock()
    rr.final_output = final_output
    rr.context_wrapper = MagicMock()
    rr.context_wrapper.usage = usage
    return rr


def test_map_plain_text():
    rr = _run_result("hello", _usage(10, 5))
    res = map_run_result(rr, duration_ms=123, model="GLM-5.2[1m]", turns=1)
    assert isinstance(res, ClaudeRunResult)
    assert res.text == "hello"
    assert res.success is True
    assert res.duration == 123
    assert res.turns == 1
    assert res.model == "GLM-5.2[1m]"
    assert res.tokens.input_tokens == 10
    assert res.tokens.output_tokens == 5


def test_map_stop_reason_max_turns():
    rr = _run_result("partial", _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=200, stop_reason="max_turns")
    assert res.stop_reason == "max_turns"


def test_map_structured_output():
    rr = _run_result('{"k": "v"}', _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"k": "v"}
