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
from collections.abc import Callable
from dataclasses import dataclass

from supernova_core.code_index.storage_models import StorageWritePoint
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.chain_verdict import CandidateChain

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


# ===== Task 2 (2026-07-22): write-side table-name resolution (spec §3.2) =====
# ORM saves (``repo.save(u)``) carry no literal table token at the call site
# — storage_token is "unresolvable". These helpers resolve it from same-file
# context: @Table/@TableName/@Document annotations → entity-class map, then
# naming convention (camelCase→snake_case + plural) from the receiver. When
# context is ambiguous or absent the original token is kept (保守漏召 > 误连).

# @Table(name="x") / @TableName("x") / @Document("x"|collection="x") followed
# (after the annotation's ')') by an optional modifier list and `class <Name>`.
# Group 1 = table, group 2 = class.
_ANNOTATION_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'@Table\s*\(\s*name\s*=\s*["\']([^"\']+)["\'][^)]*\)\s*(?:[\w]+\s+)*class\s+(\w+)'),
    re.compile(r'@TableName\s*\(\s*["\']([^"\']+)["\'][^)]*\)\s*(?:[\w]+\s+)*class\s+(\w+)'),
    re.compile(r'@Document\s*\(\s*(?:collection\s*=\s*)?["\']([^"\']+)["\'][^)]*\)\s*(?:[\w]+\s+)*class\s+(\w+)'),
)

# ORM handles with no entity semantics (lowercase generic variables).
_GENERIC_RECEIVERS: frozenset[str] = frozenset({
    "repo", "repository", "session", "db", "tx", "cache", "redis",
    "em", "entityManager", "dao", "mapper", "service", "manager",
    "store", "storage",
})

# Suffixes stripped from a repository/service receiver to recover the entity
# (checked longest-first so "Repository" wins over "Repo").
_ENTITY_SUFFIXES: tuple[str, ...] = ("Repository", "Repo", "Service", "Manager", "Dao", "Mapper")
# Suffixes stripped from an entity class name before table-name derivation.
_CLASS_SUFFIXES: tuple[str, ...] = ("Entity", "Model", "DTO", "VO", "DO", "PO")


def _build_entity_table_map(source_text: str) -> dict[str, str]:
    """Scan one file's source for @Table/@TableName/@Document annotations and
    bind each to its following ``class <Name>`` → {class_name: table_name}."""
    mapping: dict[str, str] = {}
    for pat in _ANNOTATION_PATTERNS:
        for m in pat.finditer(source_text):
            mapping[m.group(2)] = m.group(1)
    return mapping


def _strip_class_suffix(name: str) -> str:
    for suf in _CLASS_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            return name[: -len(suf)]
    return name


def _entity_from_receiver(receiver: str) -> str | None:
    """Best-effort entity-class hint from a write's callee_receiver.

    - PascalCase receiver (PHP ``User::create``, or a typed repo) → the class
      itself (after stripping Entity/Model/…).
    - camelCase receiver ending in a repository/service suffix
      (``userRepository``) → strip suffix, capitalise → ``User``.
    - Generic lowercase ORM handle (``repo``/``session``/``db``) → None.
    """
    if not receiver:
        return None
    if receiver.lower() in _GENERIC_RECEIVERS:
        return None
    # PHP-style static call / PascalCase type reference → class name.
    if receiver[:1].isupper() and receiver.isidentifier():
        return _strip_class_suffix(receiver)
    # repository/service suffix → entity
    for suf in _ENTITY_SUFFIXES:
        if receiver.endswith(suf) and len(receiver) > len(suf):
            base = receiver[: -len(suf)]
            return base[:1].upper() + base[1:]
    return None


def _entity_to_table(entity: str) -> str | None:
    """Entity class name → table name via Spring/JPA naming convention:
    camelCase → snake_case + naive plural (``User`` → ``users``,
    ``UserProfile`` → ``user_profiles``). Best-effort — wrong pluralisation
    only causes under-recall (保守), never a false join."""
    name = _strip_class_suffix(entity)
    if not name:
        return None
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return snake + "s"


def _resolve_write_token(write: StorageWritePoint, source_text: str | None) -> str:
    """Resolve a write's storage_token to a joinable table name (spec §3.2).

    Three-level fallback, same-file only:
      1. Explicit annotation — @Table/@TableName/@Document → entity-class map;
         match the receiver-derived entity, the receiver itself (PHP static),
         or (single-entity file only) the file's sole entity.
      2. Naming convention — receiver ``userRepository`` → ``User`` → ``users``.
      3. Conservative — generic receiver / no context → keep original token
         (never fabricate; under-recall goes to the LLM track).

    Literal tokens (cache keys, ``DB::table("users")``) pass through unchanged.
    """
    original = write.storage_token
    receiver = (write.callee_receiver or "").strip()
    entity = _entity_from_receiver(receiver)

    ent_map = _build_entity_table_map(source_text) if source_text else {}
    if ent_map:
        if entity and entity in ent_map:
            return ent_map[entity]
        if receiver in ent_map:            # PHP User::create, receiver == class
            return ent_map[receiver]
        # Single-entity file + ORM-style unresolvable write → assume it targets
        # the file's only entity (common monolith layout). Ambiguous (≥2
        # entities) → do not guess (保守).
        if len(ent_map) == 1 and not is_resolvable_token(original):
            return next(iter(ent_map.values()))

    if entity:
        table = _entity_to_table(entity)
        if table:
            return table

    return original


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
    source_provider: "Callable[[StorageWritePoint], bytes | None] | None" = None,
) -> list[SecondOrderCandidate]:
    """Pair each resolvable-token write with every read chain whose source
    resolves to the SAME literal token. Same-token N writes × M reads →
    N*M cartesian product. Read medium is not carried on SourcePoint; pairing
    is by token (medium cross-check is advisory, kept on the candidate).

    ``source_provider`` (optional, default None): lazy loader returning a write
    point's file source (bytes). When supplied, each ORM-style write's
    ``storage_token`` is resolved to a table name via :func:`_resolve_write_token`
    (same-file @Table / naming convention) before matching — this is what lets
    ``repo.save(u)`` (token "unresolvable") join a ``FROM users`` read.
    ``None`` → writes keep their literal ``storage_token``; read-side
    normalisation is added in Task 4.
    """
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

    # Per-file source cache: avoid re-reading the same file for each write.
    src_cache: dict[str, str | None] = {}

    def _source_text_for(w: StorageWritePoint) -> str | None:
        if source_provider is None:
            return None
        fp = w.file_path or ""
        if fp not in src_cache:
            raw = source_provider(w)
            src_cache[fp] = raw.decode("utf-8", errors="replace") if raw else None
        return src_cache[fp]

    out: list[SecondOrderCandidate] = []
    for w in writes:
        w_token = w.storage_token
        if source_provider is not None:
            resolved = _resolve_write_token(w, _source_text_for(w))
            if is_resolvable_token(resolved):
                w_token = resolved
        if not is_resolvable_token(w_token):
            continue
        for src, chain in by_token.get(w_token, []):
            out.append(SecondOrderCandidate(
                write=w,
                storage_token=(w.medium.value, w_token),
                read=src,
                read_side_chain=chain,
            ))
    return out
