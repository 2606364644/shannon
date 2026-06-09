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
