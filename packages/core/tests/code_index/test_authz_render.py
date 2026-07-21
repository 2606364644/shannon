# packages/core/tests/code_index/test_authz_render.py
"""Task 1 (spec-1a G3): render_authz_gitnexus_candidates 补 SourcePoint 参数列。

dominance 表格新增 "Params" 列，把命中 SourcePoint 的
`param_name(source_type): expression` 渲染进喂 LLM 的 prompt。
"""
from supernova_core.code_index.authz_gitnexus_track import (
    IDORCandidateChain,
    render_authz_gitnexus_candidates,
)
from supernova_core.code_index.models import EntryPoint
from supernova_core.code_index.parameter_models import SourcePoint


def _sp(i, name, st, expr):
    return SourcePoint(
        id=i,
        entry_point_id="ep1",
        param_name=name,
        source_type=st,
        expression=expr,
        file_path="a.py",
        line=1,
        rule_id="test-rule",
    )


def test_render_includes_sourcepoint_params():
    cand = IDORCandidateChain(
        endpoint_id="ep1",
        handler_id="h1",
        sink_id="s1",
        sink_step_idx=1,
        path=("h1", "s1"),
        guard_nodes_on_path=(),
        source_point_ids=("ep1::userId::5",),
    )
    sp = _sp("ep1::userId::5", "userId", "path", "req.params.userId")

    class _Blk:
        def __init__(self, bid, src):
            self.id = bid
            self.source_code = src

    class _Idx:
        blocks = [_Blk("h1", "handler()"), _Blk("s1", "db.update()")]
        source_points = [sp]

    ep = EntryPoint(
        func_block_id="ep1",
        entry_type="http_route",
        route="/x/:userId",
        http_method="GET",
        confidence=0.9,
        evidence="",
        needs_llm_review=False,
    )
    md = render_authz_gitnexus_candidates([cand], [], index=_Idx, entry_points=[ep])
    assert "userId" in md and "req.params.userId" in md and "path" in md
