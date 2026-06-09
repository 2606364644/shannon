"""Spec C: build_static_dataflow_hints —— 确定性数据流摘要渲染。"""
from shannon_core.code_index.audit_input_builder import (
    _func_id_to_tier,
    _header,
    _sink_inventory,
)
from shannon_core.code_index.models import CallChain, CodeIndex
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    ParameterPropagationGraph,
    SinkCallSite,
    SinkCategory,
    SlotContext,
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
