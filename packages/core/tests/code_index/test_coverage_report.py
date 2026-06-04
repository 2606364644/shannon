import json
import pytest
from shannon_core.code_index.coverage_report import (
    AuditTierReport, CoverageReport, Phase0Coverage, Phase3Coverage,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore


class TestAuditTierReport:
    def test_empty_report(self):
        report = AuditTierReport(total_chains=0)
        assert report.tier3_count == 0
        assert report.tier2_count == 0
        assert report.tier1_count == 0

    def test_from_scores(self):
        scores = [
            ChainRiskScore(chain_id="a", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=5),  # total=33, tier 3
            ChainRiskScore(chain_id="b", sink_danger=7, taint_completeness=4,
                           auth_gap=8, depth=3),  # total=22, tier 2
            ChainRiskScore(chain_id="c", sink_danger=0, taint_completeness=0,
                           auth_gap=0, depth=1),  # total=1, tier 1
        ]
        report = AuditTierReport.from_scores(scores)
        assert report.total_chains == 3
        assert report.tier3_count == 1
        assert report.tier2_count == 1
        assert report.tier1_count == 1
        assert report.estimated_llm_calls == 5 + 2 + 1  # 8

    def test_json_serialization(self):
        report = AuditTierReport(total_chains=5, tier3_count=1,
                                  tier2_count=2, tier1_count=2,
                                  estimated_llm_calls=9,
                                  actual_llm_calls=8)
        data = json.loads(report.to_json())
        assert data["total_chains"] == 5
        assert data["tier3_count"] == 1


class TestPhase0Coverage:
    def test_file_coverage(self):
        cov = Phase0Coverage(
            total_source_files=100,
            indexed_source_files=95,
            total_template_files=20,
            scanned_template_files=18,
            total_config_files=10,
            scanned_config_files=10,
        )
        assert cov.source_file_coverage == 0.95
        assert cov.template_file_coverage == 0.90
        assert cov.config_file_coverage == 1.0


class TestPhase3Coverage:
    def test_chain_coverage(self):
        cov = Phase3Coverage(
            total_chains=50,
            tier3_chains_audited=5,
            tier2_chains_audited=20,
            tier1_chains_audited=25,
            total_findings=12,
            deduplicated_findings=8,
            llm_calls_used=85,
            llm_calls_budget=200,
        )
        assert cov.chain_audit_coverage == 1.0  # All 50 chains audited
        assert cov.budget_used_fraction == 85 / 200


class TestCoverageReport:
    def test_full_report(self):
        report = CoverageReport(
            phase0=Phase0Coverage(
                total_source_files=50, indexed_source_files=48,
                total_template_files=10, scanned_template_files=10,
                total_config_files=5, scanned_config_files=5,
            ),
            phase3=Phase3Coverage(
                total_chains=30,
                tier3_chains_audited=3, tier2_chains_audited=10,
                tier1_chains_audited=17,
                total_findings=5, deduplicated_findings=4,
                llm_calls_used=60, llm_calls_budget=200,
            ),
        )
        json_str = report.to_json()
        assert "phase0" in json_str
        assert "phase3" in json_str
        # Round-trip
        restored = CoverageReport.model_validate_json(json_str)
        assert restored.phase0.total_source_files == 50
