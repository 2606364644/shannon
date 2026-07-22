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

# Raw-SQL table reference: FROM/INTO <table> (Task 3). Case-insensitive;
# table = the leading identifier after the keyword.
_SQL_TABLE_RE = re.compile(r"\b(?:FROM|INTO)\s+([A-Za-z_]\w*)", re.IGNORECASE)


def _resolve_read_table(read_src: SourcePoint) -> str:
    """Best-effort table name for a STORAGE read source (spec §3.3).

    1. Raw SQL: extract ``FROM <table>`` / ``INTO <table>`` from the
       expression (e.g. ``SELECT * FROM users`` → ``users``).
    2. Trailing literal: a quoted/identifier literal at the end of the
       expression (e.g. ``"users"``) — legacy single-token reads.
    3. Fall back to ``param_name`` (ORM ``findOneBy*`` reads carry no table;
       the property name is aligned to the write side via normalisation in
       Task 4).
    """
    expr = read_src.expression or ""
    m = _SQL_TABLE_RE.search(expr)
    if m:
        return m.group(1)
    lm = _LITERAL_RE.search(expr)
    if lm:
        return lm.group(1)
    return read_src.param_name


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

# Common English irregular plurals for table-name derivation (small allowlist;
# a wrong plural only ever causes under-recall, never a false join).
_PLURAL_IRREGULAR: dict[str, str] = {
    "person": "people", "child": "children", "man": "men", "woman": "women",
    "mouse": "mice", "goose": "geese", "foot": "feet", "tooth": "teeth",
    "ox": "oxen",
}


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


def _pluralize_word(word: str) -> str:
    """Best-effort English plural of a single lowercase word.

    Covers: irregulars (person→people), sibilants (box→boxes, brush→brushes),
    consonant+y (category→categories), and default +s. Conservative — a wrong
    plural only causes under-recall (the read side keeps its real table name),
    never a false join.
    """
    if not word:
        return word
    if word in _PLURAL_IRREGULAR:
        return _PLURAL_IRREGULAR[word]
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


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
    camelCase → snake_case + plural of the last word (``User`` → ``users``,
    ``UserProfile`` → ``user_profiles``, ``Category`` → ``categories``).
    Best-effort — a wrong plural only causes under-recall (保守), never a
    false join."""
    name = _strip_class_suffix(entity)
    if not name:
        return None
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    parts = snake.split("_")
    parts[-1] = _pluralize_word(parts[-1])
    return "_".join(parts)


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


def _normalize_token(token: str, entity_table_map: dict[str, str]) -> str:
    """Normalise a write/read token to a canonical table name for join
    matching (spec §3.4). Conservative by design: a token that cannot be
    confidently mapped is returned unchanged — under-recall goes to the LLM
    track; a wrong guess here would pair unrelated write↔read sites and
    produce a FALSE second-order finding (误连 > 漏报 更糟).

    Mapping strategy (map-only, deliberately):
      - explicit map hit (entity class → table, from @Table annotations) →
        map value wins;
      - every other token (table names, ORM property names, unmapped PascalCase)
        is returned unchanged.

    Write-side entity→table *naming-convention* (``User``→``users``) is handled
    upstream by :func:`_resolve_write_token`, not here — applying it on the read
    side would rewrite ORM property names (``Name``→``names``) and risk false
    joins. If real-machine recall proves too low, a guarded naming-convention
    step can be added here as a follow-up.
    """
    if not token or not is_resolvable_token(token):
        return token
    return entity_table_map.get(token, token)


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
    (same-file @Table / naming convention) and BOTH write & read tokens are
    normalised via :func:`_normalize_token` against a merged entity→table map
    built from the write files — this is what lets ``repo.save(u)`` (token
    "unresolvable") join a ``FROM users`` read. ``None`` → literal-token join
    only (Tasks 2-4 features inactive).
    """
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

    # Merged entity→table map across all write files (for normalising both
    # sides). Built once from cached sources; empty when no provider.
    merged_map: dict[str, str] = {}
    if source_provider is not None:
        for w in writes:
            _source_text_for(w)            # populate cache for each write file
        for st in src_cache.values():
            if st:
                merged_map.update(_build_entity_table_map(st))

    # Index read chains by normalised token. Only STORAGE sources with
    # resolvable tokens participate; non-storage sources and dynamic tokens
    # are ignored (belong to single-hop track / LLM track respectively).
    by_token: dict[str, list[tuple[SourcePoint, CandidateChain]]] = {}
    for chain in read_chains:
        src = reads_by_id.get(chain.source_param)
        if src is None:
            continue
        if src.source_type.value != "storage":
            continue
        tok = _normalize_token(_resolve_read_table(src), merged_map)
        if not is_resolvable_token(tok):
            continue
        by_token.setdefault(tok, []).append((src, chain))

    out: list[SecondOrderCandidate] = []
    for w in writes:
        w_token = w.storage_token
        if source_provider is not None:
            resolved = _resolve_write_token(w, _source_text_for(w))
            if is_resolvable_token(resolved):
                w_token = resolved
        w_token = _normalize_token(w_token, merged_map)
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
