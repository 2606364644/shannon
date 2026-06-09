import pytest
from shannon_core.code_index.tiered_audit import (
    TieredAuditPlanner, AuditPlan,
)
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import TaintFlow, SinkType, SlotContext
from shannon_core.code_index.risk_scorer import ChainRiskScore, AuditBudget


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "") -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 5,
        source_code=source or f"def {name}(): pass",
        parameters=[], language="python",
    )


def _chain(path: list[str], depth: int) -> CallChain:
    return CallChain(
        entry_point_id=path[0], path=path,
        depth=depth, has_unresolved=False,
    )


class TestTieredAuditPlanner:
    def test_empty_chains(self):
        planner = TieredAuditPlanner(
            chains=[], blocks_by_id={}, taint_flows_by_chain={},
            auth_middleware_ids=set(), budget=AuditBudget(),
        )
        plan = planner.plan()
        assert plan.total_chains == 0
        assert plan.tier3_chains == []
        assert plan.tier2_chains == []
        assert plan.tier1_chains == []

    def test_sorts_by_risk(self):
        """Chains are sorted by risk score descending within each tier."""
        blocks = {
            "a.py:high:1": _block("high", "a.py", 1,
                                   source="def high(x): db.query(x)"),
            "a.py:med:5": _block("med", "a.py", 5,
                                  source="def med(x): render(x)"),
            "a.py:low:10": _block("low", "a.py", 10),
        }
        chains = [
            _chain(["a.py:low:10"], 0),
            _chain(["a.py:med:5"], 1),
            _chain(["a.py:high:1"], 1),
        ]
        # Provide taint flows for high-risk chain
        flows_by_chain = {
            "a.py:high:1": [TaintFlow(
                entry_point_id="a.py:high:1", source_param="x",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[], sink_func_id="a.py:high:1",
                sink_type=SinkType.SQL_EXECUTION,
                # 显式补 Spec A 字段（兼容字段之外）
                sink_call_site_id="a.py:high:query:1:0",
                sink_slot=SlotContext.SQL_VALUE,
                tainted_arg_index=0,
                confidence=0.7,
            )],
            "a.py:med:5": [TaintFlow(
                entry_point_id="a.py:med:5", source_param="x",
                source_type=ParameterSource.BODY_FIELD,
                propagation_steps=[], sink_func_id="a.py:med:5",
                sink_type=SinkType.TEMPLATE_RENDER,
                # 显式补 Spec A 字段（兼容字段之外）
                sink_call_site_id="a.py:med:render:5:0",
                sink_slot=SlotContext.TEMPLATE_EXPR,
                tainted_arg_index=0,
                confidence=0.7,
            )],
            "a.py:low:10": [],
        }

        planner = TieredAuditPlanner(
            chains=chains, blocks_by_id=blocks,
            taint_flows_by_chain=flows_by_chain,
            auth_middleware_ids=set(), budget=AuditBudget(),
        )
        plan = planner.plan()
        assert plan.total_chains == 3
        # High-risk chain should be in a higher tier
        assert len(plan.scores) == 3
        # Verify scores are computed
        high_score = next(s for s in plan.scores if "high" in s.chain_id)
        low_score = next(s for s in plan.scores if "low" in s.chain_id)
        assert high_score.total > low_score.total

    def test_budget_limits_tier3(self):
        """Tier 3 is capped at tier3_max_chains."""
        blocks = {}
        chains = []
        flows_by_chain = {}
        for i in range(10):
            fid = f"a.py:f{i}:{i}"
            blocks[fid] = _block(f"f{i}", "a.py", i,
                                  source="def f(x): cursor.execute(x)")
            chains.append(_chain([fid], 0))
            flows_by_chain[fid] = [TaintFlow(
                entry_point_id=fid, source_param="x",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[], sink_func_id=fid,
                sink_type=SinkType.SQL_EXECUTION,
                # 显式补 Spec A 字段（兼容字段之外）
                sink_call_site_id=f"{fid}:query:{i}:0",
                sink_slot=SlotContext.SQL_VALUE,
                tainted_arg_index=0,
                confidence=0.7,
            )]

        budget = AuditBudget(tier3_max_chains=3)
        planner = TieredAuditPlanner(
            chains=chains, blocks_by_id=blocks,
            taint_flows_by_chain=flows_by_chain,
            auth_middleware_ids=set(), budget=budget,
        )
        plan = planner.plan()
        assert len(plan.tier3_chains) <= 3

    def test_estimated_calls_within_budget(self):
        blocks = {}
        chains = []
        flows_by_chain = {}
        for i in range(10):
            fid = f"a.py:f{i}:{i}"
            blocks[fid] = _block(f"f{i}", "a.py", i,
                                  source="def f(x): cursor.execute(x)")
            chains.append(_chain([fid], 0))
            flows_by_chain[fid] = [TaintFlow(
                entry_point_id=fid, source_param="x",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[], sink_func_id=fid,
                sink_type=SinkType.SQL_EXECUTION,
                # 显式补 Spec A 字段（兼容字段之外）
                sink_call_site_id=f"{fid}:query:{i}:0",
                sink_slot=SlotContext.SQL_VALUE,
                tainted_arg_index=0,
                confidence=0.7,
            )]

        budget = AuditBudget(max_total_llm_calls=50, tier3_max_chains=2,
                              tier2_max_chains=5)
        planner = TieredAuditPlanner(
            chains=chains, blocks_by_id=blocks,
            taint_flows_by_chain=flows_by_chain,
            auth_middleware_ids=set(), budget=budget,
        )
        plan = planner.plan()
        assert plan.estimated_llm_calls <= budget.max_total_llm_calls


class TestAuditPlan:
    def test_tier_distribution(self):
        scores = [
            ChainRiskScore(chain_id="a", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=5),
            ChainRiskScore(chain_id="b", sink_danger=7, taint_completeness=4,
                           auth_gap=8, depth=3),
            ChainRiskScore(chain_id="c", sink_danger=0, taint_completeness=0,
                           auth_gap=0, depth=1),
        ]
        plan = AuditPlan(
            total_chains=3,
            scores=scores,
            tier3_chains=[scores[0]],
            tier2_chains=[scores[1]],
            tier1_chains=[scores[2]],
            estimated_llm_calls=8,
        )
        assert plan.tier3_count == 1
        assert plan.tier2_count == 1
        assert plan.tier1_count == 1

    def test_json_serialization(self):
        plan = AuditPlan(
            total_chains=0, scores=[], tier3_chains=[],
            tier2_chains=[], tier1_chains=[], estimated_llm_calls=0,
        )
        json_str = plan.to_json()
        assert "total_chains" in json_str
