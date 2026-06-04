"""Degradation report — documents coverage gaps when GitNexus is unavailable."""

import json
from pydantic import BaseModel

from shannon_core.code_index.models import CoverageGap, DegradationLevel


class DegradationReport(BaseModel):
    """Report documenting degradation level and coverage gaps."""

    level: DegradationLevel
    gaps: list[CoverageGap]

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)


# Pre-defined gap lists for each degradation level

DEGRADED_GAPS: list[CoverageGap] = [
    CoverageGap(
        capability="cross_file_call_resolution",
        reason="BFS uses name matching, cannot distinguish same-name functions in different files",
        affected_phases=["Phase 0", "Phase 3"],
        estimated_coverage_loss="30-50% of cross-file calls",
    ),
    CoverageGap(
        capability="diamond_path_preservation",
        reason="BFS visited set prunes diamond paths (A→B→D and A→C→D)",
        affected_phases=["Phase 0"],
        estimated_coverage_loss="10-20% of multi-path scenarios",
    ),
    CoverageGap(
        capability="framework_route_detection",
        reason="No Framework Detection, only decorator/annotation patterns",
        affected_phases=["Phase 0", "Phase 1"],
        estimated_coverage_loss="20-40% of imperative routes",
    ),
    CoverageGap(
        capability="entry_point_scoring",
        reason="No EP Scoring, all candidates treated equally",
        affected_phases=["Phase 0", "Phase 1"],
        estimated_coverage_loss="increased false positives",
    ),
    CoverageGap(
        capability="process_tracing",
        reason="No Process Tracing, BFS only follows direct calls",
        affected_phases=["Phase 0"],
        estimated_coverage_loss="missing dynamic dispatch paths",
    ),
]

MINIMAL_GAPS: list[CoverageGap] = DEGRADED_GAPS + [
    CoverageGap(
        capability="any_static_call_graph",
        reason="No AST parsing, pure LLM analysis",
        affected_phases=["Phase 0", "Phase 1", "Phase 2", "Phase 3"],
        estimated_coverage_loss="60-80% overall",
    ),
]


def build_degradation_report(level: DegradationLevel) -> DegradationReport:
    """Build a degradation report for the given level."""
    if level == DegradationLevel.FULL:
        return DegradationReport(level=level, gaps=[])
    elif level == DegradationLevel.DEGRADED:
        return DegradationReport(level=level, gaps=list(DEGRADED_GAPS))
    else:
        return DegradationReport(level=level, gaps=list(MINIMAL_GAPS))
