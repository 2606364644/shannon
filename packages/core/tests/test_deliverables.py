import fnmatch

from supernova_core.models.deliverables import DeliverableType, DELIVERABLE_FILENAMES

def test_deliverable_type_values():
    assert DeliverableType.CODE_ANALYSIS == "CODE_ANALYSIS"
    assert DeliverableType.RECON == "RECON"
    assert DeliverableType.INJECTION_ANALYSIS == "INJECTION_ANALYSIS"

def test_deliverable_filenames_complete():
    for dt in DeliverableType:
        assert dt in DELIVERABLE_FILENAMES, f"Missing filename for {dt}"

def test_deliverable_filenames_match_ts():
    assert DELIVERABLE_FILENAMES[DeliverableType.CODE_ANALYSIS] == "pre_recon_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.RECON] == "recon_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.INJECTION_ANALYSIS] == "injection_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.XSS_ANALYSIS] == "xss_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTH_ANALYSIS] == "auth_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTHZ_ANALYSIS] == "authz_analysis_deliverable.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.SSRF_ANALYSIS] == "ssrf_analysis_deliverable.md"

def test_blackbox_deliverable_type_values():
    assert DeliverableType.INJECTION_EVIDENCE == "INJECTION_EVIDENCE"
    assert DeliverableType.XSS_EVIDENCE == "XSS_EVIDENCE"
    assert DeliverableType.AUTH_EVIDENCE == "AUTH_EVIDENCE"
    assert DeliverableType.AUTHZ_EVIDENCE == "AUTHZ_EVIDENCE"
    assert DeliverableType.SSRF_EVIDENCE == "SSRF_EVIDENCE"
    assert DeliverableType.REPORT == "REPORT"

def test_evidence_filenames_match_ts():
    assert DELIVERABLE_FILENAMES[DeliverableType.INJECTION_EVIDENCE] == "injection_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.XSS_EVIDENCE] == "xss_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTH_EVIDENCE] == "auth_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.AUTHZ_EVIDENCE] == "authz_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.SSRF_EVIDENCE] == "ssrf_exploitation_evidence.md"
    assert DELIVERABLE_FILENAMES[DeliverableType.REPORT] == "comprehensive_security_assessment_report.md"


class TestIntermediateFilePatterns:
    """spec 2026-08-18 tier SSOT：中间产物文件名模式清单 + tier 判定。"""

    def test_patterns_cover_known_intermediates(self):
        from supernova_core.models.deliverables import INTERMEDIATE_FILE_PATTERNS
        for name in (
            "code_index.json", "entry_points.json", "code_index_summary.md",
            "parameter_graph.json", "attack_chains.json", "attack_chains_llm_queue.json",
            "route_chains.json", "framework_analysis.json", "frontend_mapping.json",
            "injection_llm_queue.json", "injection_gitnexus_queue.json",
            "injection_exploitation_queue.json", "injection_exploit_verdicts.json",
            "endpoint_verify.json", "rule_gap_report.json", "source_gap_report.json",
            "storage_gap_report.json", "gitnexus_track_status.json", "audit_plan.json",
            ".poc_checkpoint.json",
        ):
            assert any(fnmatch.fnmatch(name, p) for p in INTERMEDIATE_FILE_PATTERNS), name

    def test_patterns_exclude_deliverables(self):
        from supernova_core.models.deliverables import INTERMEDIATE_FILE_PATTERNS
        for name in (
            "comprehensive_security_assessment_report.md",
            "injection_analysis_deliverable.md", "injection_findings.md",
            "auth_findings.md", "authz_findings.md", "recon_deliverable.md",
            "pre_recon_deliverable.md", "exploitable_poc_collection.md",
            "injection_exploitation_evidence.md",
        ):
            assert not any(fnmatch.fnmatch(name, p) for p in INTERMEDIATE_FILE_PATTERNS), name


class TestClassifyTier:
    def test_intermediate_dir_segment_wins(self):
        from supernova_core.models.deliverables import classify_tier
        assert classify_tier("whitebox/intermediate/code_index.json") == "intermediate"
        # 目录判据优先于模式兜底：intermediate/ 内即使名字不在清单也是 intermediate
        assert classify_tier("whitebox/intermediate/whatever_new.json") == "intermediate"

    def test_pattern_fallback_for_legacy_layout(self):
        from supernova_core.models.deliverables import classify_tier
        assert classify_tier("whitebox/injection_exploitation_queue.json") == "intermediate"
        assert classify_tier("injection_llm_queue.json") == "intermediate"

    def test_deliverable_when_no_match(self):
        from supernova_core.models.deliverables import classify_tier
        assert classify_tier("whitebox/comprehensive_security_assessment_report.md") == "deliverable"
        assert classify_tier("whitebox/injection_findings.md") == "deliverable"
