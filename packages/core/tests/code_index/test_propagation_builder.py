"""Spec A: propagation_builder 单元测试。"""
from pathlib import Path

import pytest

from shannon_core.code_index.models import (
    CallChain, CodeIndex, FuncBlock, ParameterSource, TypedParameter,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, SinkCallSite, SinkCategory, SlotContext,
    DangerousSlot,
)
from shannon_core.code_index.propagation_builder import (
    SANITIZER_HINTS,
    build_propagation_graph,
)


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "", language: str = "python",
           params: list[str] | None = None) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 10,
        source_code=source or f"def {name}(): pass",
        parameters=params or [],
        language=language,
    )


def _empty_index(blocks=None, language="python", chains=None,
                 sink_call_sites=None) -> CodeIndex:
    return CodeIndex(
        repository=".", language=language,
        total_blocks=len(blocks or []), total_entry_points=0, total_chains=0,
        blocks=blocks or [], edges=[], entry_points=[], chains=chains or [],
        sink_call_sites=sink_call_sites or [],
    )


class TestEmptyGraph:
    def test_no_blocks_returns_empty_graph_with_coverage(self):
        index = _empty_index(blocks=[], language="python", chains=[])
        pgraph = build_propagation_graph(index)
        assert isinstance(pgraph, ParameterPropagationGraph)
        assert pgraph.taint_flows == []
        assert "python" in pgraph.language_coverage

    def test_skipped_languages_recorded(self):
        """Go/Java/PHP 没有 typed param 提取，必须出现在 skipped_languages。"""
        for lang in ("go", "java", "php"):
            index = _empty_index(blocks=[], language=lang, chains=[])
            pgraph = build_propagation_graph(index)
            assert lang in pgraph.skipped_languages
            assert pgraph.taint_flows == []

    def test_sanitizer_hint_set_is_nonempty(self):
        # 集合至少要覆盖常见 sanitizer
        assert "escape" in SANITIZER_HINTS or any("escape" in s for s in SANITIZER_HINTS)
        assert any("sanitize" in s for s in SANITIZER_HINTS)


class TestSeedTaints:
    def test_seed_from_typed_params_excludes_internal(self):
        """TypedParameter.source != INTERNAL 才算 tainted。"""
        from shannon_core.code_index.propagation_builder import seed_taints
        block = _block("handler", "app.py", 1, params=["user_id", "logger"])
        typed = [
            TypedParameter(name="user_id", source=ParameterSource.QUERY_PARAM),
            TypedParameter(name="logger", source=ParameterSource.INTERNAL),
        ]
        seed = seed_taints(block, typed)
        assert "user_id" in seed
        assert "logger" not in seed

    def test_seed_falls_back_to_function_params_when_typed_empty(self):
        """没有 TypedParameter 信息时，把 FuncBlock.parameters 全部视作 tainted
        （保守偏 recall），并加 note。"""
        from shannon_core.code_index.propagation_builder import seed_taints
        block = _block("handler", "app.py", 1, params=["user_id", "limit"])
        seed = seed_taints(block, [])
        # 没 typed 信息 → 全部参数 tainted
        assert "user_id" in seed
        assert "limit" in seed

    def test_seed_includes_request_attr_patterns(self):
        """request.x / req.x 在 Python/TS 入口里是常见外部输入；seed 时把
        request 本身也加入（intra 阶段会展开 request.x 的字段过近似）。"""
        from shannon_core.code_index.propagation_builder import seed_taints
        block = _block("handler", "app.py", 1, params=["request"])
        typed = [
            TypedParameter(name="request", source=ParameterSource.UNKNOWN),
        ]
        seed = seed_taints(block, typed)
        assert "request" in seed
