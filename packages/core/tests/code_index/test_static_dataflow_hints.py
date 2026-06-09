"""Spec C: build_static_dataflow_hints —— 确定性数据流摘要渲染。"""
from shannon_core.code_index.audit_input_builder import (
    _coverage_disclaimer,
    _func_id_to_tier,
    _header,
    _sink_inventory,
    _taint_flows,
    build_static_dataflow_hints,
)
from shannon_core.code_index.models import CallChain, CodeIndex, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    ParameterPropagationGraph,
    PropagationStep,
    SinkCallSite,
    SinkCategory,
    SlotContext,
    TaintFlow,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore
from shannon_core.code_index.tiered_audit import AuditPlan


def _site(
    site_id: str, caller_id: str, *, category=SinkCategory.SQL,
    subtype="sql_raw", callee="execute", receiver="cursor",
    file_path="db.py", line=10, column=4, rule_id="py-db-cursor-execute",
    needs_review=False, slots=None,
) -> SinkCallSite:
    return SinkCallSite(
        id=site_id, caller_id=caller_id, callee_name=callee,
        callee_receiver=receiver, category=category, sink_subtype=subtype,
        file_path=file_path, line=line, column=column,
        dangerous_slots=slots or [DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE, expression="q", is_entry_hint=True)],
        rule_id=rule_id, needs_review=needs_review,
    )


def _index(chains, sites) -> CodeIndex:
    return CodeIndex(
        repository="demo", language="python",
        total_blocks=1, total_entry_points=1, total_chains=len(chains),
        blocks=[], edges=[], entry_points=[], chains=chains,
        sink_call_sites=sites,
    )


class TestFuncIdToTier:
    def test_maps_func_to_highest_tier_across_chains(self):
        # chain A: tier3（total=30），含 f1、f2
        # chain B: tier1（total=5），含 f2、f3
        chains = [
            CallChain(entry_point_id="a.py:f1:1", path=["a.py:f1:1", "a.py:f2:1"],
                      depth=1, has_unresolved=False),
            CallChain(entry_point_id="a.py:f2:1", path=["a.py:f2:1", "a.py:f3:1"],
                      depth=1, has_unresolved=False),
        ]
        scores = [
            ChainRiskScore(chain_id="a.py:f1:1→a.py:f2:1", sink_danger=10,
                           taint_completeness=10, auth_gap=8, depth=2),   # total=30 → tier3
            ChainRiskScore(chain_id="a.py:f2:1→a.py:f3:1", sink_danger=2,
                           taint_completeness=0, auth_gap=0, depth=2),    # total=4 → tier1
        ]
        plan = AuditPlan(total_chains=2, scores=scores)
        index = _index(chains, [])
        mapping = _func_id_to_tier(index, plan)
        assert mapping["a.py:f1:1"] == 3
        assert mapping["a.py:f3:1"] == 1
        # f2 同时在 tier3 链和 tier1 链上 → 取最高优先级 tier3
        assert mapping["a.py:f2:1"] == 3

    def test_func_not_in_any_chain_absent(self):
        chains = [CallChain(entry_point_id="a.py:f1:1", path=["a.py:f1:1"],
                            depth=0, has_unresolved=False)]
        plan = AuditPlan(total_chains=1, scores=[
            ChainRiskScore(chain_id="a.py:f1:1", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=1),  # tier3
        ])
        index = _index(chains, [])
        mapping = _func_id_to_tier(index, plan)
        assert "a.py:orphan:1" not in mapping


class TestHeader:
    def test_renders_coverage_and_disclaimer(self):
        text = _header(["python", "typescript"], ["go", "java", "php"])
        assert "Static Dataflow Hints" in text
        assert "python" in text and "typescript" in text
        assert "go" in text and "java" in text and "php" in text
        assert "线索" in text  # 线索非结论提醒

    def test_empty_skipped_languages(self):
        text = _header(["python"], [])
        assert "python" in text
        # 无未覆盖语言时不应崩
        assert "未覆盖语言" in text or "无" in text


class TestSinkInventory:
    def test_higher_tier_sinks_ranked_first(self):
        chains = [
            CallChain(entry_point_id="db.py:h3:1", path=["db.py:h3:1"],
                      depth=0, has_unresolved=False),
            CallChain(entry_point_id="db.py:h1:1", path=["db.py:h1:1"],
                      depth=0, has_unresolved=False),
        ]
        scores = [
            ChainRiskScore(chain_id="db.py:h3:1", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=2),   # total=30 → tier3
            ChainRiskScore(chain_id="db.py:h1:1", sink_danger=2, taint_completeness=2,
                           auth_gap=0, depth=1),    # total=5 → tier1
        ]
        plan = AuditPlan(total_chains=2, scores=scores)
        index = _index(chains, [
            _site("db.py:h3:execute:10:4", "db.py:h3:1"),
            _site("db.py:h1:execute:20:4", "db.py:h1:1"),
        ])
        func_to_tier = _func_id_to_tier(index, plan)
        text = _sink_inventory(index.sink_call_sites, func_to_tier)
        pos3 = text.index("Tier 3")
        pos1 = text.index("Tier 1")
        assert pos3 < pos1
        # tier3 的 sink 出现在 tier1 之前
        assert text.index("db.py:h3:execute:10:4") < text.index("db.py:h1:execute:20:4")

    def test_needs_review_marked(self):
        func_to_tier = {"db.py:h:1": 3}
        sites = [_site("db.py:h:execute:10:4", "db.py:h:1", needs_review=True)]
        text = _sink_inventory(sites, func_to_tier)
        assert "needs_review" in text

    def test_slot_and_rule_rendered(self):
        func_to_tier = {"db.py:h:1": 3}
        sites = [_site("db.py:h:execute:10:4", "db.py:h:1")]
        text = _sink_inventory(sites, func_to_tier)
        assert "sql_value" in text          # 槽位
        assert "py-db-cursor-execute" in text  # rule_id
        assert "cursor.execute" in text     # receiver.callee

    def test_orphan_sink_in_unranked_section(self):
        func_to_tier = {}  # 无 chain 关联
        sites = [_site("db.py:orphan:execute:99:4", "db.py:orphan:1")]
        text = _sink_inventory(sites, func_to_tier)
        # 未归类到任何 tier 的 sink 仍应出现（避免静默丢弃）
        assert "db.py:orphan:execute:99:4" in text


def _flow(
    *, entry="routes.py:getUser:10", source_param="uid",
    source_type=ParameterSource.QUERY_PARAM, sink_id="db.py:exec:execute:42:18",
    slot=SlotContext.SQL_VALUE, arg=0, confidence=0.6, sanitizer=False,
    notes="", steps=None,
) -> TaintFlow:
    return TaintFlow(
        flow_id=f"{entry}->{sink_id}",
        entry_point_id=entry, source_param=source_param, source_type=source_type,
        propagation_steps=steps or [], sink_call_site_id=sink_id,
        sink_slot=slot, tainted_arg_index=arg, confidence=confidence,
        has_sanitizer_hint=sanitizer, notes=notes,
    )


class TestTaintFlows:
    def test_renders_entry_to_sink_with_slot_and_arg(self):
        flows = [_flow(steps=[
            PropagationStep(step_id="s1", from_func_id="routes.py:getUser:10",
                            from_param="uid", to_func_id="db.py:exec:42",
                            to_param="sql", transformation="concat",
                            code_location="routes.py:12"),
        ])]
        text = _taint_flows(flows)
        assert "routes.py:getUser:10" in text
        assert "uid" in text
        assert "query" in text
        assert "sql_value" in text
        assert "arg0" in text or "arg=0" in text
        assert "concat" in text  # transformation 步骤

    def test_sanitizer_hint_flagged_as_not_effective(self):
        flows = [_flow(sanitizer=True, steps=[
            PropagationStep(step_id="s1", from_func_id="a.py:f:1", from_param="x",
                            to_func_id="b.py:g:1", to_param="y",
                            transformation="sanitize_hint:escape", code_location="a.py:3"),
        ])]
        text = _taint_flows(flows)
        assert "sanitize_hint" in text
        assert "不代表有效" in text  # 显式声明 sanitizer hint 非有效性

    def test_confidence_and_notes_rendered(self):
        flows = [_flow(confidence=0.4, notes="容器字段过近似")]
        text = _taint_flows(flows)
        assert "0.40" in text
        assert "容器字段过近似" in text

    def test_no_flows_message(self):
        text = _taint_flows([])
        assert "无" in text or "no" in text.lower()

    def test_flow_renders_sink_call_site_id_directly(self):
        # flow 的 sink_call_site_id 直接渲染进输出（无需额外反查）
        flows = [_flow(sink_id="db.py:missing:execute:1:1")]
        text = _taint_flows(flows)
        assert "db.py:missing:execute:1:1" in text


class TestCoverageDisclaimer:
    def test_lists_skipped_languages_and_caveats(self):
        pgraph = ParameterPropagationGraph(
            taint_flows=[], language_coverage=["python"], skipped_languages=["go", "java"],
        )
        text = _coverage_disclaimer(pgraph)
        assert "go" in text and "java" in text
        assert "needs_review" in text
        assert "sanitize_hint" in text
        assert "confidence" in text


class TestBuildStaticDataflowHints:
    def test_assembles_all_sections(self):
        chains = [CallChain(entry_point_id="db.py:h:1", path=["db.py:h:1"],
                            depth=0, has_unresolved=False)]
        plan = AuditPlan(total_chains=1, scores=[
            ChainRiskScore(chain_id="db.py:h:1", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=2),  # total=30 → tier3（depth 含入 total）
        ])
        index = _index(chains, [_site("db.py:h:execute:10:4", "db.py:h:1")])
        pgraph = ParameterPropagationGraph(
            taint_flows=[_flow(entry="db.py:h:1")],
            language_coverage=["python"], skipped_languages=["go"],
        )
        md = build_static_dataflow_hints(index, pgraph, plan)
        # 四段都在
        assert "# Static Dataflow Hints" in md
        assert "## Sink 调用点" in md
        assert "## 污点流" in md
        assert "## 边界与局限" in md
        # tier3 段标题
        assert "Tier 3" in md

    def test_empty_index_still_renders_disclaimer(self):
        index = _index([], [])
        pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=[],
                                           skipped_languages=["go", "java", "php"])
        plan = AuditPlan()
        md = build_static_dataflow_hints(index, pgraph, plan)
        assert "# Static Dataflow Hints" in md
        assert "## 边界与局限" in md
        assert "go" in md
