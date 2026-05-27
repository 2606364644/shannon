from shannon_core.models.deliverables import DeliverableType, DELIVERABLE_FILENAMES

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
