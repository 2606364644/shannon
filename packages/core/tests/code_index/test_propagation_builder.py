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


class TestIntraProcedural:
    def _make_sink(self, caller_id: str, callee: str = "execute",
                   file: str = "app.py", line: int = 3, col: int = 4,
                   arg_idx: int = 0,
                   slot: SlotContext = SlotContext.SQL_VALUE,
                   expression: str = "sql") -> SinkCallSite:
        return SinkCallSite(
            id=f"{file}:{caller_id.split(':')[1]}:{callee}:{line}:{col}",
            caller_id=caller_id,
            callee_name=callee,
            callee_receiver="cursor" if slot == SlotContext.SQL_VALUE else None,
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path=file,
            line=line,
            column=col,
            dangerous_slots=[DangerousSlot(
                arg_index=arg_idx, slot=slot,
                expression=expression, is_entry_hint=False,
            )],
            rule_id="py-db-cursor-execute",
        )

    def test_straight_assignment_to_sink(self):
        """user_id → sql → cursor.execute(sql)
        单函数体内一条赋值链命中 sink。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "handler", "app.py", 1,
            source=(
                "def handler(user_id):\n"
                "    sql = 'SELECT * FROM u WHERE id=' + user_id\n"
                "    cursor.execute(sql)\n"
            ),
            params=["user_id"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0)
        result = analyze_intra(
            block, seed={"user_id"},
            sinks_in_func=[sink],
        )
        assert sink.id in result.hits
        hit = result.hits[sink.id]
        assert hit.tainted_arg_index == 0
        # 至少能识别 sql 被污染 + 触达 sink 的 0 号槽
        assert any(s.to_param == "sql" or s.transformation == "concat"
                   for s in hit.local_steps)

    def test_transformation_concat_marked(self):
        """拼接 'SELECT ...' + user_input 应标 transformation='concat'。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    q = 'SELECT * FROM t WHERE id=' + user_input\n"
                "    cursor.execute(q)\n"
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0, expression="q")
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        hit = result.hits[sink.id]
        # 出现 concat transformation
        concat_steps = [s for s in hit.local_steps if s.transformation == "concat"]
        assert len(concat_steps) >= 1

    def test_no_hit_when_taint_never_reaches_sink_arg(self):
        """tainted 变量从未出现在 sink 的危险槽位 → 不命中。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    other = compute()\n"
                "    cursor.execute(other)\n"
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0)
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        assert sink.id not in result.hits

    def test_slot_constraint_excludes_safe_arg_index(self):
        """sink 的 dangerous_slots 是 arg_index=0，但 tainted 走的是 arg_index=1
        （例如 cursor.execute(safe, tainted)）→ 不算命中。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        # 危险槽位只声明在 0 号；tainted 走 1 号 → 不命中
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    cursor.execute('SAFE', user_input)\n"
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=2, arg_idx=0)  # 仅 0 号危险
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        assert sink.id not in result.hits

    def test_sanitizer_hint_does_not_block_taint(self):
        """路径上出现 escape(...) → 标 sanitize_hint:escape，但 taint 不阻断。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    safe = escape(user_input)\n"
                "    cursor.execute(safe)\n"
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0, expression="safe")
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        assert sink.id in result.hits
        hit = result.hits[sink.id]
        assert any(s.transformation and s.transformation.startswith("sanitize_hint:")
                   for s in hit.local_steps)
        assert hit.has_sanitizer_hint is True
