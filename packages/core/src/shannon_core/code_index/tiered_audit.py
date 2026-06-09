"""Tiered audit orchestrator — plans and executes per-chain audits.

Responsibilities:
1. Score all call chains using ChainRiskScore
2. Sort chains by risk score within each tier
3. Apply budget limits (max chains per tier, max total LLM calls)
4. Produce an AuditPlan with the chain distribution

The actual LLM agent dispatch is done by the workflow layer,
not by this module. This module produces the plan.
"""

import logging
from pydantic import BaseModel

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import SinkCallSite, TaintFlow
from shannon_core.code_index.risk_scorer import ChainRiskScore, AuditBudget

logger = logging.getLogger(__name__)


class AuditPlan(BaseModel):
    """The planned audit distribution across tiers."""
    total_chains: int = 0
    scores: list[ChainRiskScore] = []
    tier3_chains: list[ChainRiskScore] = []
    tier2_chains: list[ChainRiskScore] = []
    tier1_chains: list[ChainRiskScore] = []
    estimated_llm_calls: int = 0

    @property
    def tier3_count(self) -> int:
        return len(self.tier3_chains)

    @property
    def tier2_count(self) -> int:
        return len(self.tier2_chains)

    @property
    def tier1_count(self) -> int:
        return len(self.tier1_chains)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)


class TieredAuditPlanner:
    """Plans the tiered audit distribution.

    Usage:
        planner = TieredAuditPlanner(chains, blocks, flows, auth_ids, budget)
        plan = planner.plan()
        # plan.tier3_chains, plan.tier2_chains, plan.tier1_chains

    sink_call_sites（Spec A）: 可选。传入后 ChainRiskScore.taint_completeness
        会按 sink_call_site_id 命中 chain 上的 SinkCallSite 算分；不传则回退
        sink_func_id（向后兼容旧 json / 旧 flow）。
    """

    def __init__(
        self,
        chains: list[CallChain],
        blocks_by_id: dict[str, FuncBlock],
        taint_flows_by_chain: dict[str, list[TaintFlow]],
        auth_middleware_ids: set[str],
        budget: AuditBudget,
        sink_call_sites: list[SinkCallSite] | None = None,
    ):
        self.chains = chains
        self.blocks_by_id = blocks_by_id
        self.taint_flows_by_chain = taint_flows_by_chain
        self.auth_middleware_ids = auth_middleware_ids
        self.budget = budget
        self.sink_call_sites = sink_call_sites

    def plan(self) -> AuditPlan:
        """Score all chains and produce the tiered audit plan."""
        if not self.chains:
            return AuditPlan()

        # Score all chains
        scores: list[ChainRiskScore] = []
        for chain in self.chains:
            entry_id = chain.path[0] if chain.path else ""
            flows = self.taint_flows_by_chain.get(entry_id, [])
            score = ChainRiskScore.score(
                chain, self.blocks_by_id, flows, self.auth_middleware_ids,
                sink_call_sites=self.sink_call_sites,
            )
            scores.append(score)

        # Sort by total score descending
        scores.sort(key=lambda s: -s.total)

        # Assign tiers with budget limits
        tier3 = sorted(
            [s for s in scores if s.tier == 3],
            key=lambda s: -s.total,
        )[:self.budget.tier3_max_chains]

        tier2 = sorted(
            [s for s in scores if s.tier == 2],
            key=lambda s: -s.total,
        )[:self.budget.tier2_max_chains]

        tier1 = [s for s in scores if s.tier == 1]

        # Calculate estimated LLM calls
        estimated = (
            len(tier3) * 5 +   # 5 agents per Tier 3 chain
            len(tier2) * 2 +   # 2 agents per Tier 2 chain
            len(tier1) * 1     # 1 combined agent per Tier 1 chain
        )

        # If over budget, trim from the bottom (Tier 1 first)
        while estimated > self.budget.max_total_llm_calls and tier1:
            tier1.pop()
            estimated -= 1

        logger.info(
            "Audit plan: %d chains → Tier3=%d, Tier2=%d, Tier1=%d, "
            "estimated_calls=%d (budget=%d)",
            len(scores), len(tier3), len(tier2), len(tier1),
            estimated, self.budget.max_total_llm_calls,
        )

        return AuditPlan(
            total_chains=len(scores),
            scores=scores,
            tier3_chains=tier3,
            tier2_chains=tier2,
            tier1_chains=tier1,
            estimated_llm_calls=estimated,
        )
