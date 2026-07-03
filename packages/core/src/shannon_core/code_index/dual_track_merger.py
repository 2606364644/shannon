"""General dual-track merger for vulnerability queues.

The verdict mode merges LLM-track and GitNexus-track findings into the
exploitation queue consumed by reporting. The intel mode is a best-effort
dangerous-side merge for future recon wiring.
"""

from __future__ import annotations

import logging

from shannon_core.models.queue_schemas import Vulnerability

logger = logging.getLogger(__name__)

_LOCATION_FIELDS = (
    "source",
    "endpoint",
    "source_endpoint",
    "vulnerable_code_location",
    "path",
)
_SINK_FIELDS = ("sink_call", "sink_function", "vulnerable_parameter")
_DANGER_KEYWORDS = ("none", "missing", "no ", "auto-generated", "absent")


def _finding_key(finding: Vulnerability) -> tuple:
    """Build a cross-class dedup key from vulnerability type, location, and sink."""
    loc = tuple(getattr(finding, field, None) for field in _LOCATION_FIELDS)
    sink = tuple(getattr(finding, field, None) for field in _SINK_FIELDS)
    return (getattr(finding, "vulnerability_type", None), loc, sink)


def _is_vulnerable(finding: Vulnerability) -> bool:
    verdict = getattr(finding, "verdict", None)
    if verdict is not None:
        return str(verdict).strip().lower() == "vulnerable"
    return bool(getattr(finding, "externally_exploitable", False))


def _clone_with_merge_fields(
    finding: Vulnerability,
    *,
    merge_source: str,
    confidence: str,
    vulnerable: bool,
    evidence_chain: str | None = None,
) -> Vulnerability:
    data = finding.model_dump()
    data["merge_source"] = merge_source
    data["confidence"] = confidence
    # Spec 改动 3′: do NOT overwrite externally_exploitable — it is a reachability
    # tag (true = public-internet; false = internal / cross-service), NOT part of
    # the verdict. Preserve the base finding's tag. The both/llm-only branches use
    # an LLM-track finding as base (reachability authority); the gitnexus-only
    # branch uses the GitNexus finding. The `vulnerable` param still drives the
    # `verdict` rewrite below.
    if evidence_chain and not data.get("evidence_chain"):
        data["evidence_chain"] = evidence_chain
    if data.get("verdict") is not None:
        data["verdict"] = "vulnerable" if vulnerable else "safe"
    return type(finding).model_validate(data)


def merge_dual_track_queues(
    llm_findings: list[Vulnerability],
    gitnexus_findings: list[Vulnerability],
    *,
    mode: str = "verdict",
) -> list[Vulnerability]:
    """Merge LLM-track and GitNexus-track findings.

    In verdict mode, the output is the deduped union of both tracks. Findings
    present in both tracks get `merge_source="both"` and `confidence="high"`;
    single-track findings get `needs_review`. Vulnerability status is an OR:
    any vulnerable track makes the merged finding vulnerable.
    """
    if mode == "intel":
        return _merge_intel(llm_findings, gitnexus_findings)
    if mode != "verdict":
        raise ValueError(f"unsupported dual-track merge mode: {mode}")

    llm_by_key: dict[tuple, Vulnerability] = {}
    for finding in llm_findings:
        llm_by_key.setdefault(_finding_key(finding), finding)

    gitnexus_by_key: dict[tuple, Vulnerability] = {}
    for finding in gitnexus_findings:
        gitnexus_by_key.setdefault(_finding_key(finding), finding)

    ordered_keys = list(llm_by_key)
    ordered_keys.extend(key for key in gitnexus_by_key if key not in llm_by_key)

    merged: list[Vulnerability] = []
    for key in ordered_keys:
        llm = llm_by_key.get(key)
        gitnexus = gitnexus_by_key.get(key)

        if llm is not None and gitnexus is not None:
            vulnerable = _is_vulnerable(llm) or _is_vulnerable(gitnexus)
            evidence_chain = getattr(llm, "evidence_chain", None) or getattr(
                gitnexus, "evidence_chain", None
            )
            merged.append(
                _clone_with_merge_fields(
                    llm,
                    merge_source="both",
                    confidence="high",
                    vulnerable=vulnerable,
                    evidence_chain=evidence_chain,
                )
            )
        elif llm is not None:
            merged.append(
                _clone_with_merge_fields(
                    llm,
                    merge_source="llm-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(llm),
                )
            )
        elif gitnexus is not None:
            merged.append(
                _clone_with_merge_fields(
                    gitnexus,
                    merge_source="gitnexus-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(gitnexus),
                )
            )

    logger.info(
        "dual-track merge: %d llm + %d gitnexus -> %d merged",
        len(llm_findings),
        len(gitnexus_findings),
        len(merged),
    )
    return merged


def _has_danger(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(keyword in lowered for keyword in _DANGER_KEYWORDS)


def _merge_intel(
    llm_findings: list[Vulnerability],
    gitnexus_findings: list[Vulnerability],
) -> list[Vulnerability]:
    """Best-effort dangerous-side merge for future recon/intel wiring."""
    llm_by_key = {_finding_key(finding): finding for finding in llm_findings}
    gitnexus_by_key = {_finding_key(finding): finding for finding in gitnexus_findings}

    ordered_keys = list(llm_by_key)
    ordered_keys.extend(key for key in gitnexus_by_key if key not in llm_by_key)

    merged: list[Vulnerability] = []
    for key in ordered_keys:
        llm = llm_by_key.get(key)
        gitnexus = gitnexus_by_key.get(key)
        if llm is not None and gitnexus is not None:
            data = llm.model_dump()
            data["merge_source"] = "both"
            data["confidence"] = "high"
            for field in ("notes", "role_context", "guard_evidence", "missing_defense"):
                gitnexus_value = getattr(gitnexus, field, None)
                if _has_danger(gitnexus_value) and not _has_danger(data.get(field)):
                    data[field] = gitnexus_value
            merged.append(type(llm).model_validate(data))
        elif llm is not None:
            merged.append(
                _clone_with_merge_fields(
                    llm,
                    merge_source="llm-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(llm),
                )
            )
        elif gitnexus is not None:
            merged.append(
                _clone_with_merge_fields(
                    gitnexus,
                    merge_source="gitnexus-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(gitnexus),
                )
            )
    return merged


def _chain_endpoint_sequence(chain: dict) -> tuple:
    """Dedup key: ordered tuple of step endpoints (normalized lower)."""
    steps = chain.get("steps", [])
    return tuple((s.get("endpoint", "") or "").strip().lower() for s in steps) + (chain.get("vuln_type", ""),)


def merge_attack_chains(
    llm_chains: list[dict],
    gitnexus_chains: list[dict],
) -> list[dict]:
    """Merge LLM-track and GitNexus-track attack chains by endpoint-sequence dedup.

    Unlike merge_dual_track_queues (which dedups Vulnerability by location+sink),
    attack chains dedup by the ordered endpoint sequence + vuln_type. merge_source:
    both / llm-only / gitnexus-only. When both tracks have the same chain, the
    GitNexus (evidence-driven) confidence wins if higher; the LLM (creative) fills
    coverage GitNexus misses.
    """
    merged: list[dict] = []
    by_seq: dict[tuple, dict] = {}

    for chain in llm_chains:
        chain = dict(chain)
        chain.pop("_source", None)
        seq = _chain_endpoint_sequence(chain)
        chain["merge_source"] = "llm-only"
        by_seq[seq] = chain

    for chain in gitnexus_chains:
        chain = dict(chain)
        chain.pop("_source", None)
        seq = _chain_endpoint_sequence(chain)
        if seq in by_seq:
            existing = by_seq[seq]
            existing["merge_source"] = "both"
            # evidence-driven confidence wins if higher
            rank = {"confirmed": 3, "probable": 2, "theoretical": 1}
            if rank.get(chain.get("confidence", ""), 0) > rank.get(existing.get("confidence", ""), 0):
                existing["confidence"] = chain["confidence"]
            # merge step descriptions (GitNexus adds file:line evidence)
            if chain.get("steps") and not existing.get("_gn_merged"):
                existing["gitnexus_evidence"] = chain.get("steps")
                existing["_gn_merged"] = True
        else:
            chain["merge_source"] = "gitnexus-only"
            by_seq[seq] = chain

    merged = list(by_seq.values())
    logger.info("merge_attack_chains: %d chain(s) (both/llm-only/gitnexus-only)", len(merged))
    return merged
