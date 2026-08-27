"""裁决批组织（spec 2026-08-27 §7.1）——发现驱动的阶段 B 输入编排，确定性纯函数。

批 = (service, vc) × 输入源（queue | dismissed）。dismissed_findings.json 是
单文件（每条含 vuln_class 字段），按字段过滤组织批；条目全量进批，
dismiss_reason 含可达性/暴露面的排批内前部（排序只影响优先级，不影响覆盖）。
批内 finding 数上限分片（防爆上下文）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# dismiss_reason 中提示"可达性/暴露面类否决"的关键词——这类是跨仓重审的
# 高优先翻案候选（spec §1 需求 3）。宽松匹配只影响批内顺序。
_REACHABILITY_HINTS = ("reach", "exposure", "可达", "暴露", "internal", "不可达")


@dataclass
class AdjudicationBatch:
    service: str
    vuln_class: str
    origin: str                     # "queue" | "dismissed"
    findings: list[dict] = field(default_factory=list)


def _reachability_rank(reason: str | None) -> int:
    r = (reason or "").lower()
    return 0 if any(h in r for h in _REACHABILITY_HINTS) else 1


def build_adjudication_batches(
    findings_by_service: dict[str, dict[str, list[dict]]],
    dismissed_by_service: dict[str, list[dict]],
    *,
    batch_limit: int = 15,
) -> list[AdjudicationBatch]:
    batches: list[AdjudicationBatch] = []
    for service, by_vc in findings_by_service.items():
        for vc, entries in by_vc.items():
            if entries:
                batches.extend(_shard(service, vc, "queue", list(entries),
                                      batch_limit))
    for service, entries in dismissed_by_service.items():
        by_vc: dict[str, list[dict]] = {}
        for e in entries:
            by_vc.setdefault(e.get("vuln_class", "unknown"), []).append(e)
        for vc, vc_entries in by_vc.items():
            vc_entries.sort(key=lambda e: _reachability_rank(e.get("dismiss_reason")))
            batches.extend(_shard(service, vc, "dismissed", vc_entries,
                                  batch_limit))
    return batches


def _shard(service: str, vc: str, origin: str,
           findings: list[dict], limit: int) -> list[AdjudicationBatch]:
    return [AdjudicationBatch(service=service, vuln_class=vc, origin=origin,
                              findings=findings[i:i + limit])
            for i in range(0, len(findings), limit)]
