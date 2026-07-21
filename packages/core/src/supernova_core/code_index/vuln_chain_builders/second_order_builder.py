"""Second-order findings builder (spec §3.3). Reuses single-hop ``judge_chain_verdict``
on the read side; adds lightweight write-side tainted confirmation.

verdict = (write tainted) ∧ (read single-hop vulnerable).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from supernova_core.code_index.chain_verdict import (
    extract_candidate_chains,
    judge_chain_verdict,
)
from supernova_core.code_index.second_order_join import (
    extract_second_order_candidates,
)
from supernova_core.code_index.storage_models import StorageWritePoint
from supernova_core.code_index.progress import ProgressCb, ProgressEmitter
from supernova_core.models.queue_schemas import InjectionVulnerability

logger = logging.getLogger(__name__)


def _looks_user_tainted(written_expr: str | None) -> bool:
    """Lightweight write-side taint check.

    Pure literals (numbers, single quoted strings) are treated as NOT
    user-tainted. Anything else (variable, field access, concatenation,
    interpolation, ...) is considered tainted. This is intentionally a
    coarse heuristic; the authoritative judgment happens on the read side
    via ``judge_chain_verdict``.
    """
    e = (written_expr or "").strip()
    if not e:
        return False
    if e.isdigit():
        return False
    if len(e) >= 2 and e[0] in "\"'" and e[-1] == e[0]:
        return False
    return True


async def build_second_order_findings(
    writes: list[StorageWritePoint],
    pgraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
    sink_call_sites,
    reads_by_id: dict,
    progress_cb: ProgressCb = None,
) -> list[InjectionVulnerability]:
    """Emit second-order findings for storage taint pairs.

    Steps:
      1. Extract read-side single-hop candidates for both xss (stored XSS)
         and injection (second-order SQLi) vuln classes — each routes by
         its own sinkSlot / sinkCategory rules inside ``extract_candidate_chains``.
      2. Join writes × read-chains by (medium, literal-token) via
         ``extract_second_order_candidates`` (Task 6).
      3. For each candidate: ``judge_chain_verdict`` on the read side, and a
         lightweight tainted check on the write side's ``written_expr``.
         verdict = (write_tainted) ∧ (read_verdict == "vulnerable").
      4. Emit an ``InjectionVulnerability`` with ``source_track="gitnexus"``
         and ``ID="2ND-GN-NN"``.

    ``externally_exploitable=True`` is a reachability placeholder; the
    activity layer refines per-route at real-machine time. It is NOT a
    verdict overwrite (CLAUDE.md §1 铁律).
    """
    # 1. Read-side single-hop candidates — both vuln classes that can be
    #    reached by stored data (XSS for stored XSS, injection for 2nd-order SQLi).
    read_chains = extract_candidate_chains(
        pgraph, vuln_class="xss", sink_call_sites=sink_call_sites,
    )
    read_chains += extract_candidate_chains(
        pgraph, vuln_class="injection", sink_call_sites=sink_call_sites,
    )

    # 2. Bipartite join writes × read-chains by (medium, token).
    candidates = extract_second_order_candidates(
        writes, read_chains, reads_by_id=reads_by_id,
    )

    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[InjectionVulnerability] = []

    for i, cand in enumerate(candidates, start=1):
        read_verdict = await judge_chain_verdict(
            cand.read_side_chain, llm_client=llm_client,
        )
        write_tainted = _looks_user_tainted(cand.write.written_expr)
        is_vuln = write_tainted and (read_verdict.verdict == "vulnerable")

        detail = (
            f"2ND-GN-{i:02d} vulnerable: "
            f"write={cand.write.file_path}:{cand.write.line} "
            f"({cand.write.storage_token}) -> "
            f"read={cand.read.file_path}:{cand.read.line} "
            f"-> sink={cand.read_side_chain.sink_call_site_id}"
            if is_vuln else None
        )
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)

        if not is_vuln:
            continue

        vc = cand.read_side_chain.vuln_class
        findings.append(InjectionVulnerability(
            ID=f"2ND-GN-{i:02d}",
            vulnerability_type=f"second_order_{vc}",
            externally_exploitable=True,   # reachability tag; refined per-route in activity
            confidence=read_verdict.confidence,
            source=(
                f"storage read {cand.read.expression} "
                f"({cand.read.file_path}:{cand.read.line})"
            ),
            combined_sources=(
                f"write:{cand.write.file_path}:{cand.write.line} "
                f"({cand.write.storage_token}) + "
                f"read:{cand.read.file_path}:{cand.read.line}"
            ),
            path=read_verdict.evidence_chain,
            sink_call=cand.read_side_chain.sink_call_site_id,
            slot_type=cand.read_side_chain.sink_slot,
            verdict="vulnerable",
            mismatch_reason=(
                f"second-order: stored data from {cand.write.storage_token} "
                f"reaches {vc} sink without re-validation. "
                f"{read_verdict.mismatch_reason or ''}".rstrip()
            ),
            witness_payload=read_verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=read_verdict.evidence_chain,
        ))

    await emitter.finalize(
        f"{len(findings)} second-order vulnerable · "
        f"{len(candidates)} candidates judged"
    )
    logger.info(
        "second-order gitnexus-track: %d writes × %d read chains -> "
        "%d candidates -> %d findings",
        len(writes), len(read_chains), len(candidates), len(findings),
    )
    return findings
