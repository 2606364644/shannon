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

from supernova_core.i18n import Messages

logger = logging.getLogger(__name__)

# 攻击链模板双语文案（zh/en 可配，跟随 SUPERNOVA_AGENT_NARRATION_LANG）。
_M = Messages({
    "stored_xss_name": {
        "zh": "通过 {join} 的存储型 XSS：{w} → {x}",
        "en": "Stored XSS via {join}: {w} → {x}",
    },
    "stored_xss_desc": {
        "zh": "用户输入写入 {join}（注入），渲染时未转义（xss）。",
        "en": "User input written to {join} (injection) and rendered unescaped (xss).",
    },
    "step_write": {"zh": "写入 {join}：{ev}", "en": "write to {join}: {ev}"},
    "step_stored": {"zh": "已存储数据", "en": "stored data"},
    "step_render": {"zh": "渲染：{ev}", "en": "rendered: {ev}"},
    "idor_name": {
        "zh": "IDOR 链（{n} 个缺归属校验的对象 ID 端点）",
        "en": "IDOR chain ({n} object-id endpoints lacking ownership)",
    },
    "idor_desc": {
        "zh": "多个对象 ID 参数化端点缺少归属校验。",
        "en": "Multiple object-id-parameterized endpoints missing ownership validation.",
    },
    "step_missing_owner": {"zh": "缺少归属校验：{ev}", "en": "missing ownership: {ev}"},
})


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
                "name": _M.get("stored_xss_name", join=join,
                               w=_endpoint_of(write), x=_endpoint_of(xf)),
                "description": _M.get("stored_xss_desc", join=join),
                "vuln_type": "xss",
                "severity": "high",
                "confidence": "confirmed",
                "steps": [
                    {"order": 1, "phase": "input", "endpoint": _endpoint_of(write),
                     "method": "-",
                     "description": _M.get("step_write", join=join,
                                           ev=write.get('evidence_chain', ''))},
                    {"order": 2, "phase": "storage", "endpoint": join, "method": "-",
                     "description": _M.get("step_stored")},
                    {"order": 3, "phase": "render", "endpoint": _endpoint_of(xf),
                     "method": "-",
                     "description": _M.get("step_render", ev=xf.get('evidence_chain', ''))},
                ],
            })

    # IDOR chains: authz candidates with object-id params (single-step representation
    # of a chain entry; full A→B→C sequencing needs data-flow which GitNexus findings
    # carry in evidence_chain — left as probable unless multiple authz findings share an object)
    authz = gitnexus_findings_by_class.get("authz", [])
    if len(authz) >= 2:
        chains.append({
            "id": f"gn-idor-chain-{len(chains)+1}",
            "name": _M.get("idor_name", n=len(authz)),
            "description": _M.get("idor_desc"),
            "vuln_type": "authz",
            "severity": "high",
            "confidence": "probable",
            "steps": [
                {"order": i+1, "phase": "authorization", "endpoint": _endpoint_of(f),
                 "method": "-",
                 "description": _M.get("step_missing_owner", ev=f.get('evidence_chain', ''))}
                for i, f in enumerate(authz[:4])
            ],
        })

    logger.info("attack_chain_assembler: built %d chain(s) from GitNexus findings", len(chains))
    return chains
