"""Coverage report models for audit tier distribution and phase coverage.

Tracks:
- Phase 0: File/function/entry point coverage from code indexing
- Phase 3: Chain audit coverage, tier distribution, finding counts, budget usage
- Overall: Combined report with JSON serialization
"""

from __future__ import annotations

from pydantic import BaseModel

from shannon_core.code_index.risk_scorer import ChainRiskScore


class AuditTierReport(BaseModel):
    """Statistics about the tiered audit distribution."""
    total_chains: int = 0
    tier3_count: int = 0
    tier2_count: int = 0
    tier1_count: int = 0
    estimated_llm_calls: int = 0
    actual_llm_calls: int = 0

    @classmethod
    def from_scores(cls, scores: list[ChainRiskScore]) -> "AuditTierReport":
        """Build tier distribution from a list of scored chains."""
        tier3 = sum(1 for s in scores if s.tier == 3)
        tier2 = sum(1 for s in scores if s.tier == 2)
        tier1 = sum(1 for s in scores if s.tier == 1)
        est = tier3 * 5 + tier2 * 2 + tier1 * 1

        return cls(
            total_chains=len(scores),
            tier3_count=tier3,
            tier2_count=tier2,
            tier1_count=tier1,
            estimated_llm_calls=est,
        )

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)


class Phase0Coverage(BaseModel):
    """Coverage metrics for Phase 0 (code indexing)."""
    total_source_files: int = 0
    indexed_source_files: int = 0
    total_template_files: int = 0
    scanned_template_files: int = 0
    total_config_files: int = 0
    scanned_config_files: int = 0
    total_entry_points: int = 0
    resolved_entry_points: int = 0

    @property
    def source_file_coverage(self) -> float:
        if self.total_source_files == 0:
            return 1.0
        return self.indexed_source_files / self.total_source_files

    @property
    def template_file_coverage(self) -> float:
        if self.total_template_files == 0:
            return 1.0
        return self.scanned_template_files / self.total_template_files

    @property
    def config_file_coverage(self) -> float:
        if self.total_config_files == 0:
            return 1.0
        return self.scanned_config_files / self.total_config_files


class Phase3Coverage(BaseModel):
    """Coverage metrics for Phase 3 (tiered audit)."""
    total_chains: int = 0
    tier3_chains_audited: int = 0
    tier2_chains_audited: int = 0
    tier1_chains_audited: int = 0
    tier1_upgraded: int = 0
    total_findings: int = 0
    deduplicated_findings: int = 0
    llm_calls_used: int = 0
    llm_calls_budget: int = 200

    @property
    def chain_audit_coverage(self) -> float:
        audited = (self.tier3_chains_audited + self.tier2_chains_audited +
                   self.tier1_chains_audited)
        if self.total_chains == 0:
            return 1.0
        return audited / self.total_chains

    @property
    def budget_used_fraction(self) -> float:
        if self.llm_calls_budget == 0:
            return 0.0
        return self.llm_calls_used / self.llm_calls_budget


class CoverageReport(BaseModel):
    """Combined coverage report across all phases."""
    phase0: Phase0Coverage = Phase0Coverage()
    phase3: Phase3Coverage = Phase3Coverage()

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
