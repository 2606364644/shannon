"""General dual-track merger for vulnerability queues.

The verdict mode merges LLM-track and GitNexus-track findings into the
exploitation queue consumed by reporting. The intel mode is a best-effort
dangerous-side merge for future recon wiring.
"""

from __future__ import annotations

import logging

from supernova_core.models.queue_schemas import Vulnerability

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


def _normalize_endpoint(endpoint: object) -> str | None:
    """Normalize an HTTP endpoint string for cross-track dedup.

    Uppercases the method and strips the query string + trailing slash, so the
    two tracks' phrasings of the same IDOR endpoint collapse to one key. Returns
    None when the endpoint is absent/blank (caller falls back to the strict key).
    """
    if not isinstance(endpoint, str):
        return None
    endpoint = endpoint.strip()
    if not endpoint:
        return None
    parts = endpoint.split(None, 1)
    if len(parts) == 2:
        method, raw_path = parts[0].upper(), parts[1]
    else:
        method, raw_path = "", parts[0]
    path = raw_path.split("?", 1)[0].rstrip("/") or "/"
    return f"{method} {path}".strip()


def _finding_key(finding: Vulnerability) -> tuple:
    """Build a cross-track dedup key from vulnerability type, location, and sink.

    Horizontal authz (IDOR) is deduped by normalized endpoint ALONE: the two
    tracks describe the same IDOR with different code locations (LLM points at
    the service layer; GitNexus at controller+service), so a location-bearing key
    would never collapse them. Other classes keep the strict type+location+sink
    key to avoid merging distinct problems. If a Horizontal finding lacks an
    endpoint, fall back to the strict key (cannot endpoint-dedup blindly).
    """
    vtype = getattr(finding, "vulnerability_type", None)
    if vtype == "Horizontal":
        norm = _normalize_endpoint(getattr(finding, "endpoint", None))
        if norm:
            return ("Horizontal", norm)
    loc = tuple(getattr(finding, field, None) for field in _LOCATION_FIELDS)
    sink = tuple(getattr(finding, field, None) for field in _SINK_FIELDS)
    return (vtype, loc, sink)


def _is_vulnerable(finding: Vulnerability) -> bool:
    verdict = getattr(finding, "verdict", None)
    if verdict is not None:
        return str(verdict).strip().lower() == "vulnerable"
    return bool(getattr(finding, "externally_exploitable", False))


_AUTHZ_VULN_TYPES = frozenset({"Horizontal", "Vertical", "Context_Workflow"})


def _authz_ee_or(llm: Vulnerability, gitnexus: Vulnerability) -> bool | None:
    """both 分支 ee OR, 仅限 authz 三类。

    authz 的 externally_exploitable 是 LLM 主观判 (prompt 未定义语义时易误判为
    "需登录 → 不可外部利用 → False"); 用 GitNexus 轨 OR 兜底纠错。非 authz 类返回
    None → 调用方不覆写, 保持 base ee (inj/xss/ssrf 跨服务可达性是客观事实, base
    权威, 不该被 OR 翻转 — 守 test_both_track_vulnerable_keeps_reachability_from_llm_base)。
    """
    if getattr(llm, "vulnerability_type", None) in _AUTHZ_VULN_TYPES:
        return bool(getattr(llm, "externally_exploitable", False)) or bool(
            getattr(gitnexus, "externally_exploitable", False)
        )
    return None


def _clone_with_merge_fields(
    finding: Vulnerability,
    *,
    merge_source: str,
    confidence: str,
    vulnerable: bool,
    evidence_chain: str | None = None,
    externally_exploitable_override: bool | None = None,
) -> Vulnerability:
    data = finding.model_dump()
    data["merge_source"] = merge_source
    data["confidence"] = confidence
    # Spec 改动 3′: externally_exploitable is a reachability tag (true =
    # public-internet; false = internal / cross-service), NOT part of the verdict
    # — do NOT recompute it from the verdict-OR. The both/llm-only branches use an
    # LLM-track finding as base (reachability authority); the gitnexus-only branch
    # uses the GitNexus finding. The `vulnerable` param still drives the `verdict`
    # rewrite below.
    # Spec 2026-08-04 改动 B (per-class): authz 三类 both 分支取 ee OR (LLM 主观判
    # 需 GitNexus 兜底, 见 _authz_ee_or); 非 authz 类不传 override → 保持 base。
    if externally_exploitable_override is not None:
        data["externally_exploitable"] = externally_exploitable_override
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
                    externally_exploitable_override=_authz_ee_or(llm, gitnexus),
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

    merged: list[dict] = list(by_seq.values())
    # pop internal `_gn_merged` guard so it never leaks into attack_chains.json output
    for chain in merged:
        chain.pop("_gn_merged", None)
    logger.info("merge_attack_chains: %d chain(s) (both/llm-only/gitnexus-only)", len(merged))
    return merged
