"""Spec A: TaintFlow / PropagationStep / ParameterPropagationGraph 升级契约测试。"""
import json

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SlotContext,
    TaintFlow,
    TaintPath,
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


def test_taint_flow_has_needs_review_field():
    """spec 2026-07-10 §3.2: TaintFlow.needs_review(跟 SinkCallSite/SourcePoint 对称)。

    intra-first 产的 flow,source 是 llm-discovered-source 时标 needs_review=True,
    下游 chain_verdict 复核。默认 False(向后兼容)。
    """
    flow = TaintFlow(
        entry_point_id="a.py:f:1",
        source_param="x",
        source_type=ParameterSource.QUERY_PARAM,
        needs_review=True,
    )
    assert flow.needs_review is True
    default_flow = TaintFlow(
        entry_point_id="a.py:f:1",
        source_param="x",
        source_type=ParameterSource.QUERY_PARAM,
    )
    assert default_flow.needs_review is False


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


# --- Task 1: intermediate_vars / post_sanitized_concat 字段契约 ---


def test_propagation_step_defaults_intermediate_vars_empty():
    """PropagationStep 新字段 intermediate_vars 默认空(旧 json 兼容)。"""
    step = PropagationStep(from_func_id="f1", from_param="x", to_func_id="f2", to_param="y")
    assert step.intermediate_vars == []


def test_propagation_step_roundtrip_intermediate_vars():
    step = PropagationStep(
        from_func_id="f1", from_param="x", to_func_id="f2", to_param="y",
        intermediate_vars=["raw", "escaped"],
    )
    restored = PropagationStep.model_validate_json(step.model_dump_json())
    assert restored.intermediate_vars == ["raw", "escaped"]


def test_taint_path_defaults_post_sanitized_concat_false():
    """TaintPath 新字段 post_sanitized_concat 默认 False(旧 json 兼容)。"""
    path = TaintPath(source_param="x", sink_id="s1", sink_arg_index=0)
    assert path.post_sanitized_concat is False


def test_taint_path_old_json_without_post_concat_field_compat():
    """旧 json 无 post_sanitized_concat 字段 → 反序列化默认 False。"""
    old = {
        "source_param": "x", "sink_id": "s1", "sink_arg_index": 0,
        "intermediate_vars": [], "sanitized": False,
        "sanitizer_description": None, "confidence": 0.9,
    }
    path = TaintPath.model_validate(old)
    assert path.post_sanitized_concat is False
