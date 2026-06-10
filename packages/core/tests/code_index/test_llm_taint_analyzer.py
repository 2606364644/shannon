"""llm_taint_analyzer 单元测试 — LLM 逐函数 taint 分析。"""
import json
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
