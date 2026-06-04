import json
import pytest
from shannon_core.code_index.degradation import (
    DegradationReport, build_degradation_report,
    DEGRADED_GAPS, MINIMAL_GAPS,
)
from shannon_core.code_index.models import DegradationLevel


class TestDegradationReport:
    def test_full_mode_no_gaps(self):
        report = build_degradation_report(DegradationLevel.FULL)
        assert report.level == DegradationLevel.FULL
        assert report.gaps == []

    def test_degraded_mode_has_known_gaps(self):
        report = build_degradation_report(DegradationLevel.DEGRADED)
        assert report.level == DegradationLevel.DEGRADED
        assert len(report.gaps) == len(DEGRADED_GAPS)
        capabilities = [g.capability for g in report.gaps]
        assert "cross_file_call_resolution" in capabilities
        assert "diamond_path_preservation" in capabilities

    def test_minimal_mode_has_more_gaps(self):
        report = build_degradation_report(DegradationLevel.MINIMAL)
        assert report.level == DegradationLevel.MINIMAL
        assert len(report.gaps) > len(DEGRADED_GAPS)

    def test_json_serialization(self):
        report = build_degradation_report(DegradationLevel.DEGRADED)
        data = json.loads(report.to_json())
        assert data["level"] == "degraded"
        assert len(data["gaps"]) == len(DEGRADED_GAPS)

    def test_full_report_has_no_gaps_json(self):
        report = build_degradation_report(DegradationLevel.FULL)
        data = json.loads(report.to_json())
        assert data["gaps"] == []
