import json
from unittest.mock import MagicMock

from shannon_core.agents.openai_result_mapper import map_run_result
from shannon_core.agents.pricing import compute_cost_usd
from shannon_core.agents.runner import ClaudeRunResult, TokenUsage


def _usage(inp, outp, cached=0):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = outp
    # 显式设 input_tokens_details：cached>0 时给带 cached_tokens 的 mock，否则 None
    # （避免 MagicMock 恒真污染 cache_read）
    if cached:
        u.input_tokens_details = MagicMock(cached_tokens=cached)
    else:
        u.input_tokens_details = None
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
    assert res.cost == compute_cost_usd("GLM-5.2[1m]", res.tokens)
    assert res.cost > 0.0  # glm-5.2 在价目表 → 不再恒 0


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


def test_map_extracts_cache_read():
    """_usage_from 提取 input_tokens_details.cached_tokens → cache_read；cache_creation=0。"""
    rr = _run_result("hi", _usage(1000, 500, cached=300))
    res = map_run_result(rr, duration_ms=10, model="glm-4.6", turns=1)
    assert res.tokens.cache_read_input_tokens == 300
    assert res.tokens.cache_creation_input_tokens == 0


def test_map_cost_nonzero_for_priced_model():
    rr = _run_result("hi", _usage(1_000_000, 0))
    res = map_run_result(rr, duration_ms=10, model="glm-4.6", turns=1)
    assert res.cost > 0.0
    assert res.cost == compute_cost_usd("glm-4.6", res.tokens)


def test_map_cost_zero_unknown_model_warning(caplog):
    """未知模型 → cost=0.0 + warning（spec §4.3）。"""
    from shannon_core.agents import openai_result_mapper as _m
    _m._WARNED_UNKNOWN_MODELS.clear()  # 隔离模块级去重状态
    rr = _run_result("hi", _usage(1000, 500))
    with caplog.at_level("WARNING", logger="shannon_core.agents.openai_result_mapper"):
        res = map_run_result(rr, duration_ms=10, model="mystery-model-xyz", turns=1)
    assert res.cost == 0.0
    assert any("未在价目表" in r.getMessage() for r in caplog.records)


def test_map_unknown_model_warning_dedup(caplog):
    """同模型进程内只 warning 一次（spec §4.3 去重）。"""
    from shannon_core.agents import openai_result_mapper as m
    m._WARNED_UNKNOWN_MODELS.clear()  # 隔离跨测试污染
    rr = _run_result("hi", _usage(1000, 500))
    with caplog.at_level("WARNING", logger="shannon_core.agents.openai_result_mapper"):
        map_run_result(rr, duration_ms=10, model="dedup-model-xyz", turns=1)
        map_run_result(rr, duration_ms=10, model="dedup-model-xyz", turns=1)
    assert sum(1 for r in caplog.records if "未在价目表" in r.getMessage()) == 1

