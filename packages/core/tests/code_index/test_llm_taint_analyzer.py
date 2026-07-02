"""llm_taint_analyzer 单元测试 — LLM 逐函数 taint 分析。"""
import json
import logging

import pytest

from shannon_core.code_index.models import FuncBlock, TypedParameter, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    IntraResult,
    SinkCallSite,
    SinkCategory,
    SlotContext,
    TaintAnalysisResult,
    TaintPath,
)
from shannon_core.code_index.llm_taint_analyzer import (
    _deterministic_intra_fallback,
    _intra_result_from_llm,
    _is_literal_expression,
    analyze_taint_llm,
    build_taint_prompt,
    parse_llm_response,
    truncate_source,
)


def _block(
    name: str = "handler",
    file: str = "app.py",
    line: int = 1,
    source: str = "",
    params: list[str] | None = None,
) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 10,
        source_code=source or f"def {name}(): pass",
        parameters=params or [],
        language="python",
    )


def _sink(func_id: str, sink_id: str = "sink_1") -> SinkCallSite:
    return SinkCallSite(
        id=sink_id,
        caller_id=func_id,
        callee_name="cursor.execute",
        callee_receiver="cursor",
        category=SinkCategory.SQL,
        sink_subtype="execute",
        file_path="app.py",
        line=4,
        column=0,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE, expression="query", is_entry_hint=False)],
        rule_id="sql-execute",
        needs_review=False,
    )


class FakeLLMClient:
    """Fake LLM client returning a fixed TaintAnalysisResult."""

    def __init__(self, response: TaintAnalysisResult | None = None):
        self._response = response

    async def __call__(self, prompt: str, **kwargs):
        if self._response is None:
            raise RuntimeError("LLM timeout")
        return json.dumps(self._response.model_dump())


class TestTruncateSource:
    def test_short_source_unchanged(self):
        src = "line 1\nline 2\nline 3"
        assert truncate_source(src, []) == src

    def test_long_source_truncated_with_sink_context(self):
        lines = [f"line {i}" for i in range(1500)]
        src = "\n".join(lines)
        result = truncate_source(src, sink_lines=[1200], max_lines=1200, prefix_lines=1000, context_lines=30)
        result_lines = result.split("\n")
        assert len(result_lines) <= 1200
        assert "line 1200" in result

    def test_no_sink_lines_keeps_prefix(self):
        lines = [f"line {i}" for i in range(1500)]
        src = "\n".join(lines)
        result = truncate_source(src, sink_lines=[], max_lines=1200, prefix_lines=1000)
        result_lines = result.split("\n")
        assert len(result_lines) == 1000


class TestBuildTaintPrompt:
    def test_includes_function_info(self):
        block = _block(
            source="def handler(user_input):\n    cursor.execute(user_input)",
            params=["user_input"],
        )
        sinks = [_sink(block.id)]
        prompt = build_taint_prompt(block, sinks)
        assert "handler" in prompt
        assert "user_input" in prompt
        assert "cursor.execute" in prompt
        assert "tainted_params" in prompt

    def test_includes_typed_params(self):
        block = _block(params=["user_input"])
        typed = [
            TypedParameter(
                name="user_input",
                source=ParameterSource.QUERY_PARAM,
                type_annotation="str",
            ),
        ]
        prompt = build_taint_prompt(block, [], typed_params=typed)
        assert "query" in prompt  # ParameterSource.QUERY_PARAM.value


class TestParseLLMResponse:
    def test_valid_json_returns_result(self):
        data = TaintAnalysisResult(
            tainted_params=["user_input"],
            propagation_paths=[
                TaintPath(
                    source_param="user_input",
                    sink_id="sink_1",
                    sink_arg_index=0,
                    confidence=0.9,
                ),
            ],
        )
        result = parse_llm_response(json.dumps(data.model_dump()))
        assert "user_input" in result.tainted_params
        assert len(result.propagation_paths) == 1

    def test_invalid_json_returns_conservative(self):
        result = parse_llm_response("not json at all")
        assert isinstance(result, TaintAnalysisResult)

    def test_empty_response(self):
        result = parse_llm_response("{}")
        assert result.tainted_params == []
        assert result.propagation_paths == []


class TestAnalyzeTaintLLM:
    @pytest.mark.asyncio
    async def test_returns_intra_result_with_hits(self):
        block = _block(
            source="def handler(user_input):\n    cursor.execute(user_input)",
            params=["user_input"],
        )
        sinks = [_sink(block.id)]
        llm_response = TaintAnalysisResult(
            tainted_params=["user_input"],
            propagation_paths=[
                TaintPath(
                    source_param="user_input",
                    sink_id="sink_1",
                    sink_arg_index=0,
                    confidence=0.9,
                ),
            ],
        )
        llm_client = FakeLLMClient(response=llm_response)
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=sinks,
            llm_client=llm_client,
        )
        assert isinstance(result, IntraResult)
        assert "user_input" in result.tainted_params
        assert "sink_1" in result.hits
        assert result.hits["sink_1"] == 0.9

    @pytest.mark.asyncio
    async def test_llm_failure_returns_conservative(self):
        block = _block(params=["user_input", "config"])
        llm_client = FakeLLMClient(response=None)  # raises RuntimeError
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=[],
            llm_client=llm_client,
        )
        assert "user_input" in result.tainted_params
        assert "config" in result.tainted_params

    @pytest.mark.asyncio
    async def test_no_params_returns_empty(self):
        block = _block(params=[])
        llm_client = FakeLLMClient()
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=[],
            llm_client=llm_client,
        )
        assert len(result.tainted_params) == 0

    @pytest.mark.asyncio
    async def test_llm_failure_fallback_tiers_sink_hits(self):
        """spec 改动: LLM 失败时走确定性 fallback。_sink() 默认
        expression="query" / is_entry_hint=False → 非字面量变量 → hits @ 0.5
        (不再是旧的 1.0 全标);tainted_params 保守全保。"""
        block = _block(params=["user_input"])
        sink = _sink(block.id, sink_id="sink_1")
        llm_client = FakeLLMClient(response=None)  # raises
        result = await analyze_taint_llm(
            block=block, sinks_in_func=[sink], llm_client=llm_client,
        )
        assert result.tainted_params == {"user_input"}      # 全参数保守
        assert result.hits["sink_1"] == 0.5                 # 非字面量变量 → 0.5


class TestIsLiteralExpression:
    def test_quoted_string(self):
        assert _is_literal_expression("'SELECT * FROM users'") is True
        assert _is_literal_expression('"hello"') is True

    def test_integer(self):
        assert _is_literal_expression("42") is True
        assert _is_literal_expression("-7") is True
        assert _is_literal_expression("+3") is True

    def test_float(self):
        assert _is_literal_expression("3.14") is True
        assert _is_literal_expression("-0.5") is True

    def test_boolean_and_null(self):
        for lit in ("true", "false", "null", "None", "True", "False"):
            assert _is_literal_expression(lit) is True

    def test_empty(self):
        assert _is_literal_expression("") is True
        assert _is_literal_expression("   ") is True

    def test_variable_is_not_literal(self):
        assert _is_literal_expression("user_input") is False
        assert _is_literal_expression("processed") is False
        assert _is_literal_expression("request.body") is False
        assert _is_literal_expression("data.x") is False

    def test_concatenation_not_literal(self):
        # I-1 修复:首尾恰好同引号但含拼接操作符 → 不是字面量
        # (防止 GitNexus 轨把拼接 sink 误过滤致漏报)
        assert _is_literal_expression('"SELECT " + col + " FROM"') is False
        assert _is_literal_expression("'a','b'") is False
        assert _is_literal_expression("'x' + user") is False
        # 纯字面量仍判 True(回归保护)
        assert _is_literal_expression("'SELECT * FROM users'") is True
        assert _is_literal_expression('"hello"') is True


def _sink_hint(
    func_id: str, expression: str, is_hint: bool, sink_id: str = "sink_1",
) -> SinkCallSite:
    """构造带指定 dangerous_slot(is_entry_hint/expression)的 sink。"""
    return SinkCallSite(
        id=sink_id,
        caller_id=func_id,
        callee_name="cursor.execute",
        callee_receiver="cursor",
        category=SinkCategory.SQL,
        sink_subtype="execute",
        file_path="app.py",
        line=4,
        column=0,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.SQL_VALUE,
            expression=expression, is_entry_hint=is_hint,
        )],
        rule_id="sql-execute",
        needs_review=False,
    )


class TestDeterministicIntraFallback:
    def test_direct_param_sink_high_confidence(self):
        block = _block(params=["user_input"])
        sink = _sink_hint(block.id, "user_input", is_hint=True)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.hits["sink_1"] == 0.9

    def test_request_object_sink_high_confidence(self):
        block = _block(params=[])
        sink = _sink_hint(block.id, "request.body", is_hint=True)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.hits["sink_1"] == 0.9

    def test_local_var_sink_low_confidence(self):
        block = _block(params=["user_input"])
        sink = _sink_hint(block.id, "processed", is_hint=False)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.hits["sink_1"] == 0.5

    def test_literal_sink_filtered_out(self):
        block = _block(params=[])
        sink = _sink_hint(block.id, "'SELECT * FROM users'", is_hint=False)
        result = _deterministic_intra_fallback(block, [sink])
        assert "sink_1" not in result.hits

    def test_preserves_all_tainted_params(self):
        block = _block(params=["user_input", "config", "limit"])
        sink = _sink_hint(block.id, "user_input", is_hint=True)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.tainted_params == {"user_input", "config", "limit"}

    def test_empty_sinks_returns_empty_hits(self):
        block = _block(params=["user_input"])
        result = _deterministic_intra_fallback(block, [])
        assert result.hits == {}
        assert result.tainted_params == {"user_input"}


class TestNoClientSilentFallback:
    """llm_client=None(本就无 LLM,预期降级)应静默 fallback — 不打 warning,
    避免 SHANNON_GITNEXUS_LLM_ENABLED=0 时每个函数一条 "LLM taint analysis failed" 刷屏。
    真 LLM 失败(client 有但调用 raise)仍保留 warning(运维需知)。"""

    async def test_no_client_is_silent(self, caplog):
        block = _block("handler", params=["req"])
        sink = _sink(block.id)
        with caplog.at_level(
            logging.WARNING, logger="shannon_core.code_index.llm_taint_analyzer"
        ):
            result = await analyze_taint_llm(
                block, [sink], typed_params=None, llm_client=None)
        # 返回 deterministic fallback(保守标记所有 params tainted)
        assert isinstance(result, IntraResult)
        assert "req" in result.tainted_params
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [], (
            f"None client 应静默 fallback, 实得 warning: "
            f"{[r.getMessage() for r in warnings]}")

    async def test_real_failure_keeps_warning(self, caplog):
        """client 有但调用失败 → 仍打 warning(真故障, 非预期降级)。"""
        block = _block("handler", params=["req"])
        sink = _sink(block.id)
        failing = FakeLLMClient(response=None)  # __call__ raises RuntimeError
        with caplog.at_level(
            logging.WARNING, logger="shannon_core.code_index.llm_taint_analyzer"
        ):
            await analyze_taint_llm(block, [sink], typed_params=None, llm_client=failing)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, (
            f"真失败应 1 条 warning, 实得 {len(warnings)}: "
            f"{[r.getMessage() for r in warnings]}")
        assert "failed" in warnings[0].getMessage().lower()


# ---------------------------------------------------------------------------
# Task 2: _intra_result_from_llm sanitizer info → local_steps
# ---------------------------------------------------------------------------


def _blk():
    return FuncBlock(
        id="app.py:handler", function_name="handler", file_path="app.py",
        start_line=10, end_line=20, parameters=["q"], source_code="def handler(q): ...",
        language="python",
    )


def _sink2(sid="app.py:handler:db.execute:15:0", line=15):
    return SinkCallSite(
        id=sid, caller_id="app.py:handler", callee_name="execute",
        callee_receiver="db", category=SinkCategory.SQL, sink_subtype="sql_raw_query",
        file_path="app.py", line=line, column=8, dangerous_slots=[], rule_id="py-sql-execute",
    )


def test_intra_result_preserves_sanitizer_in_local_steps():
    """sanitized=True 的 path → local_steps summary step 携带 sanitize_hint。"""
    llm_result = TaintAnalysisResult(
        tainted_params=["q"],
        propagation_paths=[TaintPath(
            source_param="q", sink_id="app.py:handler:db.execute:15:0", sink_arg_index=0,
            intermediate_vars=["raw"], sanitized=True,
            sanitizer_description="html.escape", post_sanitized_concat=True,
            confidence=0.9,
        )],
    )
    result = _intra_result_from_llm(_blk(), llm_result, [_sink2()])
    assert isinstance(result, IntraResult)
    assert len(result.local_steps) == 1
    step = result.local_steps[0]
    assert step.transformation is not None
    assert "sanitize_hint:html.escape" in step.transformation
    assert "post_concat" in step.transformation          # post_sanitized_concat 编码进 transformation
    assert step.intermediate_vars == ["raw"]
    assert step.to_param == "app.py:handler:db.execute:15:0"   # 指向 sink
    assert step.code_location == "app.py:15"
    # tainted_params / hits 仍保留(不回归)
    assert result.tainted_params == {"q"}
    assert "app.py:handler:db.execute:15:0" in result.hits


def test_intra_result_unsanitized_path_has_null_transformation():
    """sanitized=False 的 path → summary step transformation=None(无防护标注)。"""
    llm_result = TaintAnalysisResult(
        tainted_params=["q"],
        propagation_paths=[TaintPath(
            source_param="q", sink_id="app.py:handler:db.execute:15:0", sink_arg_index=0,
            sanitized=False, sanitizer_description=None, confidence=0.8,
        )],
    )
    result = _intra_result_from_llm(_blk(), llm_result, [_sink2()])
    assert len(result.local_steps) == 1
    assert result.local_steps[0].transformation is None


def test_intra_result_skips_invalid_sink_or_param():
    """sink_id / source_param 不在已知集合 → 跳过(不进 local_steps/hits)。"""
    llm_result = TaintAnalysisResult(
        tainted_params=["q"],
        propagation_paths=[TaintPath(
            source_param="evil",   # 非函数参数
            sink_id="app.py:handler:db.execute:15:0", sink_arg_index=0, confidence=0.9,
        )],
    )
    result = _intra_result_from_llm(_blk(), llm_result, [_sink2()])
    assert result.local_steps == []
