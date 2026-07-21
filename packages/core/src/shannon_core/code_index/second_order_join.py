"""Second-order candidate assembly: bipartite join of storage writes × reads
by (medium, token). NOT a BFS — O(|W|×|R|) literal-token matching (spec §3.3).

Dynamic/concatenated tokens (storage_token == "unresolvable" or contains
markers such as "+" / "${") are skipped — conservative under-recall, covered
by the LLM track (CLAUDE.md §1 铁律).

Keying contract (shared with Task 7 builder / Task 8 activity):
  reads_by_id is keyed by ``SourcePoint.param_name`` and each CandidateChain's
  ``source_param`` equals its read's ``param_name`` (chain_verdict derives
  source_param from the source's param_name). Task 7/8 build reads_by_id as
  ``{s.param_name: s for s in source_points if s.source_type == STORAGE}``.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from shannon_core.code_index.storage_models import StorageWritePoint
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.chain_verdict import CandidateChain

# Markers that indicate a token is built from runtime composition / dynamic
# values and therefore cannot be matched literally across write/read sites.
_DYNAMIC_MARKERS = ("+", "${", "unresolvable")


def is_resolvable_token(token: str) -> bool:
    """True iff token is a literal that can be matched deterministically
    across write/read sites. ``""``, ``"unresolvable"`` and anything containing
    ``+`` / ``${`` → False (dynamic, left to the LLM track)."""
    if not token or token == "unresolvable":
        return False
    return not any(m in token for m in _DYNAMIC_MARKERS)


# Match a trailing string literal: optional quote, an identifier-like body,
# optional closing quote. Tolerates ``"users"`` / ``'users'`` / ``users``.
_LITERAL_RE = re.compile(r"""["']?([A-Za-z_][\w./-]*)["']?$""")


def _read_token(read_src: SourcePoint) -> str:
    """Best-effort literal token for a STORAGE source: prefer the literal in
    ``read_src.expression`` (e.g. ``"users"`` / ``'user_prefs'``), fall back to
    ``param_name`` when no literal can be parsed."""
    m = _LITERAL_RE.search(read_src.expression or "")
    return (m.group(1) if m else "") or read_src.param_name


@dataclass(frozen=True)
class SecondOrderCandidate:
    """One (write, read) pair joined by (medium, token). Feeds Task 7 builder
    which runs chain_verdict on the read side + confirms user-taint on the
    write side."""
    write: StorageWritePoint
    storage_token: tuple[str, str]        # (medium.value, token)
    read: SourcePoint
    read_side_chain: CandidateChain


def extract_second_order_candidates(
    writes: list[StorageWritePoint],
    read_chains: list[CandidateChain],
    *,
    reads_by_id: dict[str, SourcePoint],
) -> list[SecondOrderCandidate]:
    """Pair each resolvable-token write with every read chain whose source
    resolves to the SAME literal token. Same-token N writes × M reads →
    N*M cartesian product. Read medium is not carried on SourcePoint; pairing
    is by token (medium cross-check is advisory, kept on the candidate)."""
    # Index read chains by resolved literal token. Only STORAGE sources with
    # resolvable tokens participate; non-storage sources and dynamic tokens
    # are ignored (belong to single-hop track / LLM track respectively).
    by_token: dict[str, list[tuple[SourcePoint, CandidateChain]]] = {}
    for chain in read_chains:
        src = reads_by_id.get(chain.source_param)
        if src is None:
            continue
        if src.source_type.value != "storage":
            continue
        tok = _read_token(src)
        if not is_resolvable_token(tok):
            continue
        by_token.setdefault(tok, []).append((src, chain))

    out: list[SecondOrderCandidate] = []
    for w in writes:
        if not is_resolvable_token(w.storage_token):
            continue
        for src, chain in by_token.get(w.storage_token, []):
            out.append(SecondOrderCandidate(
                write=w,
                storage_token=(w.medium.value, w.storage_token),
                read=src,
                read_side_chain=chain,
            ))
    return out
