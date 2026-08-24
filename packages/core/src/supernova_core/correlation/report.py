from __future__ import annotations
import json
from pathlib import Path
from supernova_core.correlation.schemas import (
    CrossServiceTopology, TrustBoundary, CrossServiceFlow,
)


def write_correlation_deliverables(
    out_deliverables: Path,
    topology: CrossServiceTopology,
    boundaries: list[TrustBoundary],
    merged_queues: dict[str, list[dict]],
    report_md: str,
    flows: list[CrossServiceFlow] | None = None,
) -> None:
    out_deliverables.mkdir(parents=True, exist_ok=True)
    (out_deliverables / "cross-service-topology.json").write_text(
        topology.to_json(), encoding="utf-8")
    (out_deliverables / "trust-boundaries.json").write_text(
        json.dumps([json.loads(b.to_json()) for b in boundaries], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_deliverables / "correlation-report.md").write_text(report_md, encoding="utf-8")
    for vc, entries in merged_queues.items():
        (out_deliverables / f"{vc}_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    if flows is not None:
        (out_deliverables / "cross-service-flows.json").write_text(
            json.dumps([json.loads(f.to_json()) for f in flows],
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
