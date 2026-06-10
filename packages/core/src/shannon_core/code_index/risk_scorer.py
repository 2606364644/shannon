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

import re
import logging
from pydantic import BaseModel

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import (
    SinkCallSite, SinkCategory, SinkType, TaintFlow,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legacy classify_sink (inlined from deleted taint_propagator.py)
# ---------------------------------------------------------------------------
_SINK_PATTERNS: list[tuple[re.Pattern, SinkType]] = [
    (re.compile(r'(?:execute|query|cursor\.execute|raw\s*\()', re.IGNORECASE), SinkType.SQL_EXECUTION),
    (re.compile(r'(?:os\.system|subprocess|popen|exec\s*\(|eval\s*\()', re.IGNORECASE), SinkType.COMMAND_EXEC),
    (re.compile(r'(?:pickle\.loads?|yaml\.load|unserialize|deserialize)', re.IGNORECASE), SinkType.DESERIALIZATION),
    (re.compile(r'(?:open\s*\(|write\s*\(|file_put_contents|os\.path\.join.*\.\.)', re.IGNORECASE), SinkType.FILE_WRITE),
    (re.compile(r'(?:render_template|render\s*\(|\.innerHTML|Response\s*\()', re.IGNORECASE), SinkType.TEMPLATE_RENDER),
    (re.compile(r'(?:requests\.(?:get|post)|urllib|http\.Client|fetch\s*\()', re.IGNORECASE), SinkType.HTTP_REQUEST),
    (re.compile(r'(?:logger?\.(?:info|debug|warn|error)|log\.\w+|console\.log)', re.IGNORECASE), SinkType.LOG_WRITE),
]


def _classify_sink_legacy(block: FuncBlock) -> SinkType:
    """Regex-based sink classification (migrated from taint_propagator.classify_sink)."""
    source = block.source_code
    for pattern, sink_type in _SINK_PATTERNS:
        if pattern.search(source):
            return sink_type
    return SinkType.UNKNOWN

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

# Sink danger scores by category (Spec B). Used when SinkCallSite records are
# available; SINK_DANGER_SCORES (by SinkType) remains the classify_sink fallback.
SINK_CATEGORY_DANGER_SCORES: dict[SinkCategory, int] = {
    SinkCategory.SQL: 10,
    SinkCategory.COMMAND: 10,
    SinkCategory.DESERIALIZATION: 9,
    SinkCategory.FILE: 8,
    SinkCategory.TEMPLATE: 7,
    SinkCategory.SSRF: 6,
    SinkCategory.XSS: 7,        # code-level XSS, treated same as template render
    SinkCategory.REDIRECT: 5,
    SinkCategory.LOG: 3,
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
        sink_call_sites: list[SinkCallSite] | None = None,
    ) -> "ChainRiskScore":
        """Score a call chain based on its risk characteristics.

        Args:
            sink_call_sites: Spec B output. If provided, sink_danger is the max
                danger across all chain nodes that contain a SinkCallSite. If
                None or empty, falls back to legacy classify_sink regex on the
                terminal node.
        """
        chain_id = "→".join(chain.path[:4])  # Truncate for display

        sink_danger = cls._compute_sink_danger(
            chain, blocks_by_id, sink_call_sites,
        )

        # Taint completeness: Spec A 升级
        # 优先：如果传了 sink_call_sites，flow.sink_call_site_id 必须命中
        #       chain.path 上的某个 SinkCallSite.id。
        # 回退：没有 sink_call_sites → 用旧字段 flow.sink_func_id 命中 path[-1]。
        if sink_call_sites:
            chain_node_ids = set(chain.path)
            chain_site_ids = {s.id for s in sink_call_sites if s.caller_id in chain_node_ids}
            reaching = [f for f in taint_flows if f.sink_call_site_id in chain_site_ids]
        else:
            sink_node_id = chain.path[-1] if chain.path else None
            reaching = [f for f in taint_flows if f.sink_func_id == sink_node_id]
        taint_completeness = min(10, len(reaching) * 10)

        # Auth gap: does the chain pass through auth middleware?
        chain_has_auth = any(
            node_id in auth_middleware_ids for node_id in chain.path
        )
        auth_gap = 0 if chain_has_auth else 8

        # Depth: call chain length
        depth = min(10, len(chain.path))

        score = cls(
            chain_id=chain_id,
            sink_danger=sink_danger,
            taint_completeness=taint_completeness,
            auth_gap=auth_gap,
            depth=depth,
        )
        logger.debug("Scored chain %s: sink=%d taint=%d auth=%d depth=%d total=%d tier=%d",
                     chain_id, sink_danger, taint_completeness, auth_gap, depth,
                     score.total, score.tier)
        return score

    @staticmethod
    def _compute_sink_danger(
        chain: CallChain,
        blocks_by_id: dict[str, FuncBlock],
        sink_call_sites: list[SinkCallSite] | None,
    ) -> int:
        """Spec B upgrade: take max danger across all chain nodes' SinkCallSites.
        Falls back to classify_sink on terminal node if no SinkCallSites."""
        if sink_call_sites:
            chain_node_ids = set(chain.path)
            dangers = [
                SINK_CATEGORY_DANGER_SCORES.get(s.category, 0)
                for s in sink_call_sites
                if s.caller_id in chain_node_ids
            ]
            if dangers:
                return max(dangers)

        # Fallback: legacy classify_sink on terminal node
        sink_node_id = chain.path[-1] if chain.path else None
        if sink_node_id:
            sink_block = blocks_by_id.get(sink_node_id)
            if sink_block:
                sink_type = _classify_sink_legacy(sink_block)
                return SINK_DANGER_SCORES.get(sink_type, 0)
        return 0


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
