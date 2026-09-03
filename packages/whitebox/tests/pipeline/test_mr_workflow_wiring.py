"""MR 模式对 WhiteboxScanWorkflow 的消费点接线（spec 2026-09-03 §3.1/§5.2）。

类选择优先级、prompt 增量引导段、GN verdict 候选过滤——均为纯函数，先测行为。
"""

from supernova_whitebox.pipeline.mr_wiring import (
    build_mr_incremental_guidance, filter_flows_by_mr_scope, resolve_mr_vuln_classes,
)


def test_mr_vuln_classes_take_precedence_over_yaml_and_default():
    mr_meta = {"selected_vuln_classes": ["injection", "authz"]}
    assert resolve_mr_vuln_classes(None, mr_meta) == ["injection", "authz"]
    # YAML 配置也不敌 MR 启发式（diff 特征优先）
    assert resolve_mr_vuln_classes(None, mr_meta, cfg_classes=["xss"]) == ["injection", "authz"]


def test_explicit_user_override_beats_mr_heuristic():
    mr_meta = {"selected_vuln_classes": ["injection"]}
    assert resolve_mr_vuln_classes(["xss"], mr_meta) == ["xss"]


def test_no_mr_meta_returns_none_for_default_chain():
    # None = 走现有 select_vuln_classes 链（全量扫描零行为变化）
    assert resolve_mr_vuln_classes(None, None) is None


def test_incremental_guidance_contains_only_git_derived_info():
    guidance = build_mr_incremental_guidance(
        {"base_commit": "abc1234", "head_commit": "def5678"})

    # 只含 git 派生信息（ref/命令/路径提示），供 vuln prompt 尾拼
    assert "abc1234" in guidance and "def5678" in guidance
    assert "git diff" in guidance
    # 铁律锁定：不含任何确定性层产物字段（flow/sink/IncrementalScope）
    lowered = guidance.lower()
    for banned in ("verdict_flow", "sink_call_site", "incremental_scope",
                   "removed_protection", "parameter_graph"):
        assert banned not in lowered


def test_filter_flows_by_mr_scope_keeps_only_increment_candidates():
    from supernova_core.code_index.parameter_models import (
        ParameterPropagationGraph, TaintFlow,
    )

    flows = [
        TaintFlow(flow_id="f1", entry_point_id="e", source_param="q",
                  source_type="query", sink_call_site_id="s1"),
        TaintFlow(flow_id="f2", entry_point_id="e", source_param="q",
                  source_type="query", sink_call_site_id="s2"),
    ]
    pgraph = ParameterPropagationGraph(taint_flows=flows, language_coverage=["python"])

    filtered = filter_flows_by_mr_scope(pgraph, {"f2"})

    assert [f.flow_id for f in filtered.taint_flows] == ["f2"]
    # 语言覆盖等元数据保留
    assert filtered.language_coverage == ["python"]


def test_filter_flows_none_scope_returns_pgraph_untouched():
    from supernova_core.code_index.parameter_models import ParameterPropagationGraph

    pgraph = ParameterPropagationGraph(language_coverage=["go"])
    out = filter_flows_by_mr_scope(pgraph, None)

    assert out is pgraph            # 无 scope → 原对象直通（全量扫描零开销）
