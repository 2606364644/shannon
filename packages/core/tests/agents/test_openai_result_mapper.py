import json
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
    assert res.cost == 0.0


def test_map_stop_reason_max_turns():
    """B1: max_turns → success=False + error_code=ExecutionLimitError + retryable=False。"""
    rr = _run_result("partial", _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=200, stop_reason="max_turns")
    assert res.stop_reason == "max_turns"
    assert res.success is False
    assert res.error_code == "ExecutionLimitError"
    assert res.retryable is False


def test_map_structured_output():
    rr = _run_result('{"k": "v"}', _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert res.structured_output == {"k": "v"}


def test_map_structured_output_dict_final():
    """B2: output_type 生效时 final_output 是已解析 dict（validate_json 返回），
    mapper 应直接采用，不二次 json.loads；text 走 json.dumps 序列化。"""
    rr = _run_result({"verdict": "allow"}, _usage(2, 3))
    res = map_run_result(
        rr,
        duration_ms=10,
        model="m",
        turns=1,
        output_format={"type": "object", "properties": {"verdict": {"type": "string"}}},
    )
    assert res.structured_output == {"verdict": "allow"}
    # dict final → text 经 json.dumps（保留中文，ensure_ascii=False）
    assert json.loads(res.text) == {"verdict": "allow"}


def test_map_structured_output_list_final():
    """B2: list final_output 同样走 dict/list 分支（isinstance 命中）。"""
    rr = _run_result([{"k": "v"}], _usage(1, 1))
    res = map_run_result(rr, duration_ms=10, model="m", turns=1, output_format={"type": "array"})
    assert res.structured_output == [{"k": "v"}]

