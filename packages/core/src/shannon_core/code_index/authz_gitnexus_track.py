"""vuln-authz GitNexus deterministic track (spec §5.7, ⭐ net-progress direction).

Produces IDOR candidate chains: handler→sink call paths where no ownership
guard dominates the sink. The "guard must dominate the sink" is approximated
heuristically (spec §8: not a full dominance proof) — we flag a path as an
IDOR candidate when the handler segment carries no ownership predicate (ORM
`where { userId }` / `findByOwner` etc.) AND the path reaches a side-effect
sink (DB write / ORM mutation / file / state). This is conservative: we
over-report candidates (宁过报不漏报, spec §2 principle 4) and let the LLM
chain-judgement pass (Task 4) confirm or reject each.

Ownership/auth detection reuses Plan 6's scan_endpoint_security machinery
(_OWNERSHIP_PREDICATE_RE etc.) — imported, not reimplemented.

This is the GitNexus TRACK of the dual-track merge. The LLM track (authz
agent, vuln-authz.txt) is untouched (spec principle 2: no anchoring). The
two tracks merge via Plan 3's merge_dual_track_queues (verdict OR by
endpoint).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock

logger = logging.getLogger(__name__)


# Side-effect sinks: DB writes, ORM mutations, file writes, state updates.
# Matched against a node's source_code OR function_name (some sinks are
# named update/save/remove/destroy directly). Cross-language.
_SIDE_EFFECT_SINK_RE = re.compile(
    r"(?i)("
    r"\b(update|save|create|destroy|delete|remove|insert|upsert|patch)\b\s*[(<]"
    r"|\.update\s*\(|\.save\s*\(|\.create\s*\(|\.remove\s*\(|\.destroy\s*\("
    r"|\.delete\s*\(|\.insert\s*\(|\.upsert\s*\(|\.patch\s*\("
    r"|\b(exec|query|run)\s*\(\s*['\"]?(update|insert|delete|drop)"
    r"|model\s*\.\s*(save|update|remove|destroy)\b"
    r"|repo\s*\.\s*(save|update|remove|destroy)\b"
    r")"
)


@dataclass(frozen=True)
class IDORCandidateChain:
    """A handler→sink path flagged as a potential IDOR (no ownership guard)."""
    endpoint_id: str          # EntryPoint.func_block_id of the handler
    handler_id: str           # FuncBlock.id of the handler (= endpoint_id here)
    sink_id: str              # FuncBlock.id of the side-effect sink
    path: tuple[str, ...]     # ordered FuncBlock.id list, handler→sink
    guard_nodes_on_path: tuple[str, ...]  # ownership-guard node ids on path (empty=none)


def _is_side_effect_sink(block: FuncBlock | None) -> bool:
    """True if the block performs a DB/ORM/file/state side-effect."""
    if block is None:
        return False
    if _SIDE_EFFECT_SINK_RE.search(block.source_code):
        return True
    return bool(_SIDE_EFFECT_SINK_RE.search(block.function_name + "("))


def _handler_has_ownership_guard(handler: FuncBlock) -> bool:
    """Reuse Plan 6's ownership predicate detection.

    Plan 6 (recon §4.2) ships `scan_endpoint_security` with
    `_OWNERSHIP_PREDICATE_RE`/`_detect_ownership`. We reuse the predicate
    regex directly (imported lazily so Plan 6 is a soft dependency — if it
    has not landed, we degrade to a local copy).
    """
    try:
        from shannon_core.code_index.recon_gitnexus_track import (
            _OWNERSHIP_PREDICATE_RE,
        )
    except ImportError:
        # Plan 6 not landed yet — local fallback (kept in sync with Plan 6).
        _OWNERSHIP_PREDICATE_RE = re.compile(
            r"(?i)("
            r"where\s*[:({]?\s*['\"]?\s*(user_?id|owner_?id|owner|creator_?id|author_?id)\b"
            r"|where\s*\(\s*['\"]?(user_?id|owner_?id|owner|creator_?id|author_?id)['\"]?\s*[,=]"
            r"|\bfind(First|One|All)?\s*\(\s*\{[^}]*?(user_?id|owner|creator)"
            r"|\b(owner|currentUser|req\.user|ctx\.state\.user)\s*\.\s*id\b"
            r"|\b(user_?id|owner_?id)\s*=\s*(req|ctx|currentUser)"
            r")"
        )
    return _OWNERSHIP_PREDICATE_RE.search(handler.source_code) is not None


def find_unguarded_sink_paths(
    index: CodeIndex,
    *,
    max_paths_per_endpoint: int = 20,
) -> list[IDORCandidateChain]:
    """Find handler→sink paths lacking an ownership guard (IDOR candidates).

    Heuristic (spec §8): for each HTTP EntryPoint's handler, walk the
    CallChains rooted at it; for any chain whose tail is a side-effect sink
    AND whose handler carries no ownership predicate, emit a candidate.
    Conservative — flags any path reaching a sink without an ownership check
    in the handler, even if some OTHER path has one (dominance under-approx:
    a guard that doesn't cover every path still yields a candidate).

    Dedup by (endpoint_id, sink_id). Capped per endpoint to bound the
    judge-LLM cost.
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    http_eps = [ep for ep in index.entry_points
                if ep.entry_type == "http_route" and ep.route is not None]

    candidates: list[IDORCandidateChain] = []
    seen: set[tuple[str, str]] = set()  # (endpoint_id, sink_id)

    for ep in http_eps:
        handler = blocks_by_id.get(ep.func_block_id)
        if handler is None:
            continue  # unresolved handler — Plan 6 surfaces "unknown"; skip here
        # Dominance short-circuit: if the handler itself has an ownership
        # predicate, it dominates the sink for all paths through it — no IDOR
        # candidate from this handler (guard present at the entry).
        if _handler_has_ownership_guard(handler):
            continue
        count_for_ep = 0
        for chain in index.chains:
            if chain.entry_point_id != ep.func_block_id or not chain.path:
                continue
            sink_id = chain.path[-1]
            key = (ep.func_block_id, sink_id)
            if key in seen:
                continue
            if not _is_side_effect_sink(blocks_by_id.get(sink_id)):
                continue
            # Path reached a side-effect sink with no ownership guard in the
            # handler → IDOR candidate.
            seen.add(key)
            candidates.append(IDORCandidateChain(
                endpoint_id=ep.func_block_id,
                handler_id=ep.func_block_id,
                sink_id=sink_id,
                path=tuple(chain.path),
                guard_nodes_on_path=(),  # handler guard absent → no guards
            ))
            count_for_ep += 1
            if count_for_ep >= max_paths_per_endpoint:
                break

    logger.info(
        "authz GitNexus track: %d HTTP endpoints, %d IDOR candidate chains",
        len(http_eps), len(candidates),
    )
    return candidates
