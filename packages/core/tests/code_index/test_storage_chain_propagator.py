"""Task 2 (子项⑤): 验证 SourcePoint(source_type=STORAGE) 经 chain_propagator 零改动产 TaintFlow。

回归契约验证(spec §3.1 核心简化): 二阶存储 READ 点建模为
`SourcePoint(source_type=ParameterSource.STORAGE)`, 进 `produce_intra_first_taint_flows`
后必须与普通 source 一样产出 `TaintFlow(source_type=STORAGE)` 到达 sink.

核心洞察: `_source_points_matching` (chain_propagator.py:367-382) 按
`param_name in t` / `expression in t` / `t in expression` 做 substring 匹配,
**完全不检查 `source_type`**. 因此 STORAGE source 天然经现有单跳链路流到 sink,
chain_propagator 零实现改动 —— 本测试锁定该契约.

这是 contract-verification / regression-locking 测试, 不是经典 RED→GREEN:
GREEN-from-the-start 即为正确的证明 (强制 RED 或为本测试加入 source_type 分支
都会违反 "零改动复用" 契约).
"""
from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    IntraResult,
    PropagationStep,
    SinkCallSite,
    SinkCategory,
    SlotContext,
    SourcePoint,
)
from shannon_core.code_index.chain_propagator import produce_intra_first_taint_flows


# Handler FuncBlock id —— 同时是 sink.caller_id / source.entry_point_id / intra_results key
_HANDLER_ID = "ProfileController.java:getProfile:80"


def _handler_block() -> FuncBlock:
    """GET /profile/:id handler: 读 profile.bio (storage read) 后拼进 SQL query."""
    return FuncBlock(
        id=_HANDLER_ID,
        file_path="ProfileController.java",
        function_name="getProfile",
        start_line=80,
        end_line=95,
        source_code=(
            "String bio = profile.getBio();\n"
            "stmt.executeQuery(\"SELECT ... WHERE bio='\" + bio + \"'\");\n"
        ),
        parameters=["bio"],
        language="java",
    )


def _storage_read_source() -> SourcePoint:
    """二阶存储 READ 点: profile.bio 来自 DB, 建模为 STORAGE-typed SourcePoint.

    entry_point_id 必须等于含 sink 函数 id (H1), 这样 _source_points_matching
    (按 entry_id 过滤) 才会把它保留进候选.
    """
    return SourcePoint(
        id=f"{_HANDLER_ID}::bio::88",
        entry_point_id=_HANDLER_ID,
        param_name="bio",
        source_type=ParameterSource.STORAGE,
        expression="profile.bio",
        file_path="ProfileController.java",
        line=88,
        confidence=0.9,
        rule_id="java-storage-read",
    )


def _sql_sink_on_bio() -> SinkCallSite:
    """handler 内 stmt.executeQuery 把 bio 拼进 SQL value 位 → SQL 注入 sink.

    caller_id 必须等于 H1; dangerous_slots[0].slot=SQL_VALUE 会被透传进 TaintFlow.sink_slot.
    """
    sink_id = f"{_HANDLER_ID}::executeQuery::90:0"
    return SinkCallSite(
        id=sink_id,
        caller_id=_HANDLER_ID,
        callee_name="executeQuery",
        callee_receiver="stmt",
        category=SinkCategory.SQL,
        sink_subtype="sql_raw_query",
        file_path="ProfileController.java",
        line=90,
        column=0,
        dangerous_slots=[
            DangerousSlot(
                arg_index=0,
                slot=SlotContext.SQL_VALUE,
                expression='"SELECT ... WHERE bio=\'" + bio',
                is_entry_hint=True,
            ),
        ],
        rule_id="java-stmt-execute",
        needs_review=False,
    )


def _handler_intra(sink_id: str) -> IntraResult:
    """handler 函数内 taint 分析: bio 被污染, 命中 sink_id (confidence=0.9)."""
    return IntraResult(
        tainted_params={"bio"},
        hits={sink_id: 0.9},
        local_steps=[
            PropagationStep(
                from_func_id=_HANDLER_ID,
                from_param="bio",
                to_func_id=_HANDLER_ID,
                to_param=sink_id,
                code_location="ProfileController.java:90",
                confidence=0.9,
            ),
        ],
    )


def test_intra_first_links_storage_read_to_sql_sink():
    """契约: SourcePoint(source_type=STORAGE) 经 produce_intra_first_taint_flows
    必须产出 TaintFlow(source_type=STORAGE, sink_slot=SQL_VALUE) 到达 SQL sink.

    GREEN-from-the-start 是正确证明: _source_points_matching 按 substring 匹配
    `sp.param_name in t` (bio in bio=True), 不检查 source_type, 故 STORAGE source
    零改动复用单跳判定链路. 若本测试 FAIL 即契约破坏 (新引入了 source_type 分支).
    """
    handler = _handler_block()
    sink = _sql_sink_on_bio()
    storage_src = _storage_read_source()
    intra_results = {_HANDLER_ID: _handler_intra(sink.id)}

    flows = produce_intra_first_taint_flows(
        sink_call_sites=[sink],
        intra_results=intra_results,
        source_points=[storage_src],
        blocks=[handler],
    )

    # 至少产 1 条 flow
    assert flows, (
        "STORAGE source 必须与普通 source 一样产出 TaintFlow (零改动复用契约); "
        f"got flows={flows}"
    )

    # 找到 STORAGE-typed flow
    storage_flows = [f for f in flows if f.source_type == ParameterSource.STORAGE]
    assert storage_flows, (
        f"应存在 source_type==STORAGE 的 TaintFlow; got source_types="
        f"{[f.source_type for f in flows]}"
    )

    flow = storage_flows[0]

    # source_type 透传 (sp.source_type → TaintFlow.source_type)
    assert flow.source_type == ParameterSource.STORAGE
    # sink_slot 透传 (sink.dangerous_slots[0].slot → TaintFlow.sink_slot)
    assert flow.sink_slot == SlotContext.SQL_VALUE
    # 锚定到含 sink 的 handler
    assert flow.entry_point_id == _HANDLER_ID
    assert flow.sink_call_site_id == sink.id
    # source_param 是存储 read 的字段名
    assert flow.source_param == "bio"
    # intra-first 走的是 local_steps (不经 chain)
    assert flow.notes == "intra-first"
    # 规则 source (非 llm-discovered-source) → needs_review=False
    assert flow.needs_review is False
    # 单步 intra local_step 透传到 propagation_steps
    assert any(s.to_param == sink.id for s in flow.propagation_steps)


def test_storage_source_match_does_not_inspect_source_type():
    """白盒证明 _source_points_matching 不检查 source_type: 同一 fixture 把
    source_type 换成 BODY_FIELD 也应产 flow, 数量与 STORAGE 一致 (零分支).

    这等价于对 `_source_points_matching` 的 substring 匹配做属性不变量锁定:
    变 source_type 不变匹配结果.
    """
    handler = _handler_block()
    sink = _sql_sink_on_bio()
    intra_results = {_HANDLER_ID: _handler_intra(sink.id)}

    # STORAGE source
    storage_src = _storage_read_source()
    storage_flows = produce_intra_first_taint_flows(
        sink_call_sites=[sink], intra_results=intra_results,
        source_points=[storage_src], blocks=[handler],
    )

    # 同一 source, 仅 source_type 改成 BODY_FIELD
    body_src = storage_src.model_copy(update={"source_type": ParameterSource.BODY_FIELD})
    body_flows = produce_intra_first_taint_flows(
        sink_call_sites=[sink], intra_results=intra_results,
        source_points=[body_src], blocks=[handler],
    )

    # 匹配逻辑不看 source_type → 两条 source 产 flow 数量必须相等
    assert len(storage_flows) == len(body_flows) == 1, (
        f"source_type 不应影响 _source_points_matching 结果; "
        f"storage={len(storage_flows)} body={len(body_flows)}"
    )
    # 唯一差异: source_type 透传进 TaintFlow
    assert storage_flows[0].source_type == ParameterSource.STORAGE
    assert body_flows[0].source_type == ParameterSource.BODY_FIELD
