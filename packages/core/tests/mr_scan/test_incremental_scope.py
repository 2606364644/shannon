"""mr_scan.incremental_scope — 三来源增量范围合成（spec 2026-09-03 §4.2-4.5）。"""

from supernova_core.code_index.models import (
    CodeIndex, EntryPoint, FuncBlock,
)
from supernova_core.code_index.parameter_models import (
    ParameterPropagationGraph, PropagationStep, SinkCallSite, SinkCategory,
    SourcePoint, TaintFlow,
)
from supernova_core.mr_scan.diff_manifest import (
    DiffHunk, DiffLine, DiffManifest, DiffStats,
)
from supernova_core.mr_scan.incremental_scope import build_incremental_scope


_DIFF = DiffManifest(
    base_commit="b1", head_commit="h1",
    hunks=[
        DiffHunk(
            file_path="app/routes.py", old_start=10, old_lines=3, new_start=10, new_lines=5,
            added=[DiffLine(text="x", head_line_no=11), DiffLine(text="y", head_line_no=12)],
            removed=[DiffLine(text="old", base_line_no=11)],
        ),
    ],
    stats=DiffStats(files=1, insertions=2, deletions=1),
)


def _blk(fid, start, end):
    fp, fn, _ = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=start,
                     end_line=end, source_code="", parameters=[], language="python")


def _sink(sid, file, line):
    fp, fn, ln = sid.split(":")[:3]
    return SinkCallSite(id=sid, caller_id=f"{fp}:{fn}:{ln}", callee_name="db.execute",
                        callee_receiver=None, category=SinkCategory.SQL,
                        sink_subtype="sql_value", file_path=file, line=line, column=1,
                        dangerous_slots=[], rule_id="r")


def _flow(fid, entry, sink_id, steps=()):
    return TaintFlow(flow_id=fid, entry_point_id=entry, source_param="req.q",
                     source_type="query",
                     sink_call_site_id=sink_id,
                     propagation_steps=list(steps))


def _step(frm, to, loc):
    return PropagationStep(step_id="", from_func_id=frm, from_param="p",
                           to_func_id=to, to_param="p", code_location=loc)


# --- 索引 fixture：handler 在 routes.py，sink 分布在 added 内外 ---

_BLOCKS = [
    _blk("app/routes.py:handler:10", 10, 20),
    _blk("app/other.py:handler2:1", 1, 40),
]
_SINKS = [
    _sink("app/routes.py:handler:11:sink", "app/routes.py", 11),   # added 行内
    _sink("app/other.py:handler2:30:sink", "app/other.py", 30),    # 非 added 文件
    _sink("app/routes.py:handler:15:sink", "app/routes.py", 15),   # 同文件但非 added 行
]
_ENTRY = EntryPoint(func_block_id="app/routes.py:handler:10", entry_type="http_route",
                    route="/x", http_method="GET", confidence=1.0, evidence="e",
                    needs_llm_review=False)
_ENTRY2 = EntryPoint(func_block_id="app/other.py:handler2:1", entry_type="http_route",
                     route="/y", http_method="GET", confidence=1.0, evidence="e",
                    needs_llm_review=False)
_SOURCES = [
    SourcePoint(id="sp1", entry_point_id="app/routes.py:handler:10", param_name="q",
                source_type="query", expression="req.args.get", file_path="app/routes.py",
                line=12, column=1, validation="", confidence=1.0, rule_id="r"),
    SourcePoint(id="sp2", entry_point_id="app/other.py:handler2:1", param_name="q",
                source_type="query", expression="req.args.get", file_path="app/other.py",
                line=5, column=1, validation="", confidence=1.0, rule_id="r"),
]


def _index(flows):
    return CodeIndex(
        repository="r", language="python", total_blocks=len(_BLOCKS),
        total_entry_points=2, total_chains=0,
        blocks=_BLOCKS, edges=[], entry_points=[_ENTRY, _ENTRY2], chains=[],
        sink_call_sites=_SINKS, source_points=_SOURCES,
        parameter_graph=ParameterPropagationGraph(taint_flows=flows),
    )


def test_source_a_collects_flows_whose_sink_step_or_source_lands_on_added_lines():
    flows = [
        # A1：sink 在 added 行（routes.py:11）→ 入
        _flow("f1", "app/routes.py:handler:10", "app/routes.py:handler:11:sink"),
        # A2：sink 在非 added 文件，step 也不在 added → 不入
        _flow("f2", "app/other.py:handler2:1", "app/other.py:handler2:30:sink"),
        # A3：sink 非 added 行，但传播步 code_location 落在 added 行 → 入
        _flow("f3", "app/routes.py:handler:10", "app/routes.py:handler:15:sink",
              steps=[_step("app/routes.py:handler:10", "app/routes.py:helper:1",
                           "app/routes.py:12")]),
        # A4：sink/step 均不 added，但 entry 的 source_point 落在 added 行 → 入
        _flow("f4", "app/routes.py:handler:10", "app/other.py:handler2:30:sink"),
    ]
    # f4 的 entry 有 added 行上的 source_point（sp1 line=12 ∈ added）

    scope = build_incremental_scope(diff=_DIFF, index=_index(flows),
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[])

    assert set(scope.verdict_flow_ids) == {"f1", "f3", "f4"}


# 来源 B 专用 diff：added 行 {18} 落 handler 行范围（10-20）但不在 source/sink 行，
# 使 B 判定独立可观察（不与来源 A 的 sink/step/source 命中混淆）
_DIFF_B = DiffManifest(
    base_commit="b1", head_commit="h1",
    hunks=[
        DiffHunk(file_path="app/routes.py", old_start=17, old_lines=2, new_start=17,
                 new_lines=3, added=[DiffLine(text="decorator", head_line_no=18)]),
    ],
    stats=DiffStats(files=1, insertions=1, deletions=0),
)


def test_source_b_collects_all_flows_of_entry_touching_added_lines():
    flows = [
        # handler(10-20) 行范围含 added 18 → 新入口，其全部链入集（sink 不在 added 也入）
        _flow("fb1", "app/routes.py:handler:10", "app/routes.py:handler:15:sink"),
        # handler2 所在文件无 added → 不入
        _flow("fb2", "app/other.py:handler2:1", "app/other.py:handler2:30:sink"),
    ]

    scope = build_incremental_scope(diff=_DIFF_B, index=_index(flows),
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[])

    assert "fb1" in scope.verdict_flow_ids
    assert "fb2" not in scope.verdict_flow_ids
    assert scope.new_entry_point_ids == ["app/routes.py:handler:10"]


def test_source_b_entry_in_new_file_counts_as_new_entry():
    new_file_diff = DiffManifest(
        base_commit="b1", head_commit="h1",
        hunks=[
            DiffHunk(file_path="app/new_handler.py", old_start=0, old_lines=0,
                     new_start=1, new_lines=3, is_new_file=True,
                     added=[DiffLine(text="def nh(req):", head_line_no=1)]),
        ],
        stats=DiffStats(files=1, insertions=1, deletions=0),
    )
    blocks = _BLOCKS + [_blk("app/new_handler.py:nh:1", 1, 5)]
    entries = [_ENTRY, _ENTRY2, EntryPoint(func_block_id="app/new_handler.py:nh:1",
                                           entry_type="http_route", route="/z",
                                           http_method="POST", confidence=1.0, evidence="e",
                                           needs_llm_review=False)]
    index = CodeIndex(repository="r", language="python", total_blocks=len(blocks),
                      total_entry_points=3, total_chains=0, blocks=blocks, edges=[],
                      entry_points=entries, chains=[], sink_call_sites=_SINKS,
                      source_points=_SOURCES,
                      parameter_graph=ParameterPropagationGraph())
    flows = [_flow("fn1", "app/new_handler.py:nh:1", "app/routes.py:handler:15:sink")]

    scope = build_incremental_scope(diff=new_file_diff, index=index,
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[])

    assert "fn1" in scope.verdict_flow_ids
    assert scope.new_entry_point_ids == ["app/new_handler.py:nh:1"]


# --- 来源 C：删防护反向链（§4.4）——用 _DIFF_B（added {18}，A/B 均不命中 fixture flows）---

from supernova_core.code_index.models import CallChain  # noqa: E402
from supernova_core.mr_scan.incremental_scope import RemovedProtection  # noqa: E402


def _index_c(flows, chains):
    blocks = _BLOCKS + [_blk("app/utils.py:sanitize_input:40", 40, 50)]
    return CodeIndex(repository="r", language="python", total_blocks=len(blocks),
                     total_entry_points=2, total_chains=len(chains), blocks=blocks,
                     edges=[], entry_points=[_ENTRY, _ENTRY2], chains=chains,
                     sink_call_sites=_SINKS, source_points=_SOURCES,
                     parameter_graph=ParameterPropagationGraph(taint_flows=flows))


def test_source_c_locates_function_by_name_and_collects_direct_and_extended_flows():
    flows = [
        # fc1：传播步直接经过 sanitize_input → 直接级
        _flow("fc1", "app/routes.py:handler:10", "app/routes.py:handler:15:sink",
              steps=[_step("app/routes.py:handler:10", "app/utils.py:sanitize_input:40",
                           "app/routes.py:12")]),
        # fc2：steps 不含该函数，但 handler 的 CallChain 路径含它 → 扩展级
        _flow("fc2", "app/routes.py:handler:10", "app/other.py:handler2:30:sink"),
        # fc3：与被删防护函数无任何关联 → 不入
        _flow("fc3", "app/other.py:handler2:1", "app/other.py:handler2:30:sink"),
    ]
    chains = [
        CallChain(entry_point_id="app/routes.py:handler:10",
                  path=["app/routes.py:handler:10", "app/utils.py:sanitize_input:40",
                        "app/routes.py:helper:25"],
                  depth=2, has_unresolved=False),
    ]
    protection = RemovedProtection(
        file_path="app/utils.py", base_line_no=42, removed_text="q = sanitize(q)",
        function_name="sanitize_input", protection_kind="sanitize",
        rationale="escapes quotes", confidence=0.9,
    )

    scope = build_incremental_scope(diff=_DIFF_B, index=_index_c(flows, chains),
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[protection])

    assert "fc1" in scope.verdict_flow_ids          # 直接级
    assert "fc2" in scope.verdict_flow_ids          # 扩展级（chain.path 命中 → entry 全量）
    assert "fc3" not in scope.verdict_flow_ids
    assert len(scope.removed_protection_flows) == 1
    rp_flow = scope.removed_protection_flows[0]
    assert rp_flow.func_block_id == "app/utils.py:sanitize_input:40"
    assert set(rp_flow.flow_ids) == {"fc1", "fc2"}


# 区间映射专用 diff：old 区间 [40,50) 整体在 head 侧右移 2 行（old_start=40→new_start=42）
_DIFF_C_MAP = DiffManifest(
    base_commit="b1", head_commit="h1",
    hunks=[
        DiffHunk(file_path="app/utils.py", old_start=40, old_lines=10, new_start=42,
                 new_lines=12, added=[DiffLine(text="pad", head_line_no=42)]),
    ],
    stats=DiffStats(files=1, insertions=1, deletions=0),
)


def test_source_c_falls_back_to_line_range_mapping_when_no_function_name():
    # base_line 45（在 old 区间内）→ head 侧 47 → 落 sanitize_input(40-50)
    protection = RemovedProtection(file_path="app/utils.py", base_line_no=45,
                                   removed_text="q = sanitize(q)",
                                   function_name=None, protection_kind="sanitize")
    flows = [_flow("fc1", "app/routes.py:handler:10", "app/routes.py:handler:15:sink",
                   steps=[_step("app/routes.py:handler:10", "app/utils.py:sanitize_input:40",
                                "app/routes.py:12")])]

    scope = build_incremental_scope(diff=_DIFF_C_MAP, index=_index_c(flows, []),
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[protection])

    assert scope.removed_protection_flows[0].func_block_id == "app/utils.py:sanitize_input:40"


def test_source_c_deleted_function_records_without_chasing_flows():
    # added 行 81 不落任何 block 行范围 → A/B 均不命中，fc1 只可能因 C 入集
    diff_none = DiffManifest(
        base_commit="b1", head_commit="h1",
        hunks=[
            DiffHunk(file_path="app/other.py", old_start=80, old_lines=2, new_start=80,
                     new_lines=3, added=[DiffLine(text="x", head_line_no=81)]),
        ],
        stats=DiffStats(files=1, insertions=1),
    )
    # app/gone.py 在 head 索引中不存在（函数随文件删除）→ 不追链但保留记录
    protection = RemovedProtection(file_path="app/gone.py", base_line_no=3,
                                   removed_text="@login_required", function_name="gone_fn",
                                   protection_kind="authz_check")
    flows = [_flow("fc1", "app/routes.py:handler:10", "app/routes.py:handler:15:sink")]

    scope = build_incremental_scope(diff=diff_none, index=_index_c(flows, []),
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[protection])

    assert len(scope.removed_protection_flows) == 1
    assert scope.removed_protection_flows[0].func_block_id is None
    assert scope.removed_protection_flows[0].flow_ids == []
    assert "fc1" not in scope.verdict_flow_ids


def test_source_c_base_side_path_normalized_via_rename_mapping():
    # LLM 报出的旧路径（base 侧）经 rename 归一到新侧后匹配到函数
    rename_diff = DiffManifest(
        base_commit="b1", head_commit="h1",
        hunks=[DiffHunk(file_path="app/utils.py", old_start=0, old_lines=0, new_start=0,
                        new_lines=0, rename_from="app/legacy_utils.py")],
        stats=DiffStats(files=1),
    )
    protection = RemovedProtection(file_path="app/legacy_utils.py", base_line_no=42,
                                   removed_text="q = sanitize(q)",
                                   function_name="sanitize_input", protection_kind="sanitize")
    flows = [_flow("fc1", "app/routes.py:handler:10", "app/routes.py:handler:15:sink",
                   steps=[_step("app/routes.py:handler:10", "app/utils.py:sanitize_input:40",
                                "app/routes.py:12")])]

    scope = build_incremental_scope(diff=rename_diff, index=_index_c(flows, []),
                                    pgraph=ParameterPropagationGraph(taint_flows=flows),
                                    removed_protections=[protection])

    assert scope.removed_protection_flows[0].func_block_id == "app/utils.py:sanitize_input:40"
