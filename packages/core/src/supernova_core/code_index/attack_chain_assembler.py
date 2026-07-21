"""GitNexus-track attack chain assembler.

Deterministically combines per-class GitNexus findings (already-judged single
source→sink chains in {vt}_gitnexus_queue.json) into multi-step attack chains by
cross-endpoint correlation. Evidence-driven (every step has a GitNexus finding backing it),
complementing the LLM-track's creative inference.

Degradation: if GitNexus findings are absent (GitNexus unavailable / timed out —
CLAUDE.md §3), returns [] — LLM track covers alone.

NOTE: reads GitNexus-track's OWN output (gitnexus_queue findings), NOT feeding it back
into LLM-track prompts (CLAUDE.md §1 ironclad rule). Output only enters attack_chains.json
via the merger.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _endpoint_of(finding: dict) -> str:
    """Best-effort extract the endpoint string from a finding."""
    return (
        finding.get("source_endpoint")
        or finding.get("endpoint")
        or finding.get("source")
        or finding.get("path")
        or ""
    )


def _link_key(finding: dict, *, sink_side: bool) -> str:
    """A storage/model token to join on. Heuristic: sink of a write == source of a read."""
    text = (finding.get("sink_call") if sink_side else finding.get("source", "")) or ""
    text = (text or "").lower()
    # crude storage token extraction (db insert/update profiles → "profiles")
    for marker in ("profiles", "users", "orders", "comments", "posts", "messages"):
        if marker in text:
            return marker
    return text


def assemble_attack_chains(
    gitnexus_findings_by_class: dict[str, list[dict]],
    logger: logging.Logger = logging.getLogger(__name__),
) -> list[dict]:
    """Assemble multi-step chains from per-class GitNexus findings.

    Args:
        gitnexus_findings_by_class: {"injection": [...], "xss": [...], "ssrf": [...],
            "authz": [...]} — each finding is a dict from {vt}_gitnexus_queue.json.

    Returns:
        list of AttackChain dicts (id/name/steps/vuln_type/severity/confidence).
        Empty if no cross-endpoint links found or GitNexus unavailable.
    """
    if not gitnexus_findings_by_class or not any(
        gitnexus_findings_by_class.get(c) for c in ("injection", "xss", "ssrf", "authz")
    ):
        logger.info("attack_chain_assembler: no GitNexus findings, returning [] (LLM track covers)")
        return []

    chains: list[dict] = []

    # Stored XSS: injection write (sink=storage) + xss render (source=storage)
    inj_writes = {
        _link_key(f, sink_side=True): f for f in gitnexus_findings_by_class.get("injection", [])
        if _link_key(f, sink_side=True)
    }
    for xf in gitnexus_findings_by_class.get("xss", []):
        join = _link_key(xf, sink_side=False)
        write = inj_writes.get(join)
        if write and _endpoint_of(write) and _endpoint_of(xf) and _endpoint_of(write) != _endpoint_of(xf):
            chains.append({
                "id": f"gn-stored-xss-{len(chains)+1}",
                "name": f"Stored XSS via {join}: {_endpoint_of(write)} → {_endpoint_of(xf)}",
                "description": f"User input written to {join} (injection) and rendered unescaped (xss).",
                "vuln_type": "xss",
                "severity": "high",
                "confidence": "confirmed",
                "steps": [
                    {"order": 1, "phase": "input", "endpoint": _endpoint_of(write),
                     "method": "-", "description": f"write to {join}: {write.get('evidence_chain','')}"},
                    {"order": 2, "phase": "storage", "endpoint": join, "method": "-",
                     "description": "stored data"},
                    {"order": 3, "phase": "render", "endpoint": _endpoint_of(xf),
                     "method": "-", "description": f"rendered: {xf.get('evidence_chain','')}"},
                ],
            })

    # IDOR chains: authz candidates with object-id params (single-step representation
    # of a chain entry; full A→B→C sequencing needs data-flow which GitNexus findings
    # carry in evidence_chain — left as probable unless multiple authz findings share an object)
    authz = gitnexus_findings_by_class.get("authz", [])
    if len(authz) >= 2:
        chains.append({
            "id": f"gn-idor-chain-{len(chains)+1}",
            "name": f"IDOR chain ({len(authz)} object-id endpoints lacking ownership)",
            "description": "Multiple object-id-parameterized endpoints missing ownership validation.",
            "vuln_type": "authz",
            "severity": "high",
            "confidence": "probable",
            "steps": [
                {"order": i+1, "phase": "authorization", "endpoint": _endpoint_of(f),
                 "method": "-", "description": f"missing ownership: {f.get('evidence_chain','')}"}
                for i, f in enumerate(authz[:4])
            ],
        })

    logger.info("attack_chain_assembler: built %d chain(s) from GitNexus findings", len(chains))
    return chains
