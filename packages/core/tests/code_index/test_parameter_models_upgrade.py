"""Spec A: TaintFlow / PropagationStep / ParameterPropagationGraph 升级契约测试。"""
import json

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SlotContext,
    TaintFlow,
)


def test_propagation_step_has_step_id_and_confidence():
    step = PropagationStep(
        step_id="s1",
        from_func_id="a.py:f:1", from_param="x",
        to_func_id="b.py:g:1", to_param="y",
        transformation="concat",
        code_location="a.py:3",
        confidence=0.8,
    )
    assert step.step_id == "s1"
    assert step.confidence == 0.8


def test_taint_flow_has_sink_call_site_id_and_slot_fields():
    flow = TaintFlow(
        flow_id="a.py:f:1->a.py:f:execute:2:4",
        entry_point_id="a.py:f:1",
        source_param="user_id",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="a.py:f:execute:2:4",
        sink_slot=SlotContext.SQL_VALUE,
        tainted_arg_index=0,
        confidence=0.7,
        has_sanitizer_hint=False,
    )
    assert flow.sink_call_site_id == "a.py:f:execute:2:4"
    assert flow.sink_slot == SlotContext.SQL_VALUE
    assert flow.tainted_arg_index == 0
    assert flow.confidence == 0.7


def test_taint_flow_legacy_fields_still_present():
    """旧字段 sink_func_id / sink_type 必须仍然存在（向后兼容）。
    新逻辑不应写入它们，但旧测试与序列化文件可能引用。"""
    flow = TaintFlow(
        entry_point_id="a.py:f:1",
        source_param="user_id",
        source_type=ParameterSource.QUERY_PARAM,
    )
    # 旧字段以默认值存在
    assert flow.sink_func_id == ""
    assert flow.sink_type is None
    # 新字段以默认值存在
    assert flow.sink_call_site_id == ""
    assert flow.has_sanitizer_hint is False
    assert flow.notes == ""


def test_parameter_propagation_graph_has_coverage_fields():
    pgraph = ParameterPropagationGraph(
        taint_flows=[],
        language_coverage=["python", "typescript"],
        skipped_languages=["go", "java", "php"],
    )
    assert pgraph.language_coverage == ["python", "typescript"]
    assert pgraph.skipped_languages == ["go", "java", "php"]


def test_pgraph_serializes_with_new_fields():
    """JSON 往返必须保留新字段。"""
    flow = TaintFlow(
        flow_id="f1",
        entry_point_id="a.py:f:1",
        source_param="x",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="a.py:f:execute:2:4",
        sink_slot=SlotContext.SQL_VALUE,
        tainted_arg_index=0,
        confidence=0.5,
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[flow],
        language_coverage=["python"],
        skipped_languages=[],
    )
    raw = json.loads(pgraph.model_dump_json())
    assert raw["language_coverage"] == ["python"]
    assert raw["taint_flows"][0]["sink_call_site_id"] == "a.py:f:execute:2:4"
    assert raw["taint_flows"][0]["sink_slot"] == "sql_value"
