"""Chain risk scorer — scores call chains and assigns audit tiers.

Each CallChain is scored across 4 dimensions (0-10 each):
  - sink_danger: Does the chain reach a high-danger sink?
  - taint_completeness: Does parameter propagation cover the sink?
  - auth_gap: Is authentication middleware missing?
  - depth: How deep is the call chain?

Tier assignment:
  - Tier 3 (total >= 30): Full depth audit, <=5 chains, 5 agents each
  - Tier 2 (total >= 15): Standard audit, <=20 chains, 2 agents each
  - Tier 1 (total < 15):  Lightweight scan, unlimited, 1 combined agent
"""

import logging
from pydantic import BaseModel

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import SinkType, TaintFlow

logger = logging.getLogger(__name__)

# Sink danger scores by type
SINK_DANGER_SCORES: dict[SinkType, int] = {
    SinkType.SQL_EXECUTION: 10,
    SinkType.COMMAND_EXEC: 10,
    SinkType.DESERIALIZATION: 9,
    SinkType.FILE_WRITE: 8,
    SinkType.TEMPLATE_RENDER: 7,
    SinkType.HTTP_REQUEST: 6,
    SinkType.LOG_WRITE: 3,
    SinkType.UNKNOWN: 0,
}


class ChainRiskScore(BaseModel):
    """Risk score for a single call chain."""
    chain_id: str
    sink_danger: int = 0
    taint_completeness: int = 0
    auth_gap: int = 0
    depth: int = 0

    @property
    def total(self) -> int:
        return self.sink_danger + self.taint_completeness + self.auth_gap + self.depth

    @property
    def tier(self) -> int:
        if self.total >= 30:
            return 3
        if self.total >= 15:
            return 2
        return 1

    @classmethod
    def score(
        cls,
        chain: CallChain,
        blocks_by_id: dict[str, FuncBlock],
        taint_flows: list[TaintFlow],
        auth_middleware_ids: set[str],
    ) -> "ChainRiskScore":
        """Score a call chain based on its risk characteristics."""
        chain_id = "→".join(chain.path[:4])  # Truncate for display

        # Sink danger: check the terminal function
        sink_node_id = chain.path[-1] if chain.path else None
        sink_danger = 0
        if sink_node_id:
            sink_block = blocks_by_id.get(sink_node_id)
            if sink_block:
                from shannon_core.code_index.taint_propagator import classify_sink
                sink_type = classify_sink(sink_block)
                sink_danger = SINK_DANGER_SCORES.get(sink_type, 0)

        # Taint completeness: how many flows reach the sink
        reaching = [f for f in taint_flows if f.sink_func_id == sink_node_id]
        taint_completeness = min(10, len(reaching) * 10)

        # Auth gap: does the chain pass through auth middleware?
        chain_has_auth = any(
            node_id in auth_middleware_ids for node_id in chain.path
        )
        auth_gap = 0 if chain_has_auth else 8

        # Depth: call chain length
        depth = min(10, len(chain.path))

        return cls(
            chain_id=chain_id,
            sink_danger=sink_danger,
            taint_completeness=taint_completeness,
            auth_gap=auth_gap,
            depth=depth,
        )


class AuditBudget(BaseModel):
    """Budget control for the tiered audit phase."""
    max_total_llm_calls: int = 200
    max_cost_usd: float = 50.0
    tier3_max_chains: int = 5
    tier2_max_chains: int = 20
    tier1_combined_agent: bool = True

    def estimate_calls(
        self,
        tier3_count: int = 0,
        tier2_count: int = 0,
        tier1_count: int = 0,
    ) -> int:
        """Estimate total LLM calls for the given chain distribution."""
        t3 = min(tier3_count, self.tier3_max_chains) * 5  # 5 agents each
        t2 = min(tier2_count, self.tier2_max_chains) * 2  # 2 agents each
        t1 = tier1_count * 1  # 1 combined agent
        return t3 + t2 + t1
