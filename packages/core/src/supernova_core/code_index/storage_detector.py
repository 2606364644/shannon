"""Deterministic storage read/write point detection (spec §3.2/§3.3).

Mirrors `source_detector.py` structure: rule dataclass → `_build_*_rules` from
YAML → `detect_*` over entry blocks → `_dedup_*`.

- Reads → ``SourcePoint(source_type=STORAGE)`` feeding chain_propagator
  (single-hop reuse, per Task 2's verified contract). Stays in the existing
  source-point channel — chain_verdict consumes STORAGE sources with zero
  changes.
- Writes → ``StorageWritePoint`` (independent type, NOT in sink_call_sites).
  Anchors the WRITE end of second-order storage taint; a later LLM hunter
  (Task 4) joins read+write on the same literal token.

Token must be a literal (named group "tok" or regex group(1)); dynamic or
concatenated tokens are recorded as "unresolvable" for the LLM hunter.

API symmetry with `detect_sources`: both `detect_storage_*` take a keyword-only
``source_provider: Callable[[FuncBlock], bytes | None]`` and resolve text the
same way (`source_provider(block)` → bytes decode, else `block.source_code`).
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from supernova_core.code_index.models import ParameterSource
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.storage_models import StorageWritePoint, StorageMedium
from supernova_core.code_index._rule_loader import DATA_DIR, load_yaml

if TYPE_CHECKING:
    from supernova_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageReadRule:
    """一条 storage 读规则。pattern 的 group("tok") 或 group(1) = literal token。"""
    rule_id: str
    languages: tuple[str, ...]
    medium: StorageMedium
    pattern: re.Pattern
    param_of: str = "tok"      # group name carrying the read var (default "tok")


@dataclass(frozen=True)
class StorageWriteRule:
    """一条 storage 写规则。written_arg = 第几个实参携带被写的表达式(0-based)。"""
    rule_id: str
    languages: tuple[str, ...]
    medium: StorageMedium
    pattern: re.Pattern
    written_arg: int = 0


def _build_read_rules(raw: dict) -> "tuple[StorageReadRule, ...]":
    """YAML dict → tuple[StorageReadRule]。未知 medium fail-fast(ValueError)。"""
    rules: list[StorageReadRule] = []
    for item in raw.get("storage_reads", []):
        rules.append(StorageReadRule(
            rule_id=item["rule_id"],
            languages=tuple(item.get("languages") or ()),
            medium=StorageMedium(item["medium"]),
            pattern=re.compile(item["pattern"]),
            param_of=item.get("param_of", "tok"),
        ))
    return tuple(rules)


def _build_write_rules(raw: dict) -> "tuple[StorageWriteRule, ...]":
    rules: list[StorageWriteRule] = []
    for item in raw.get("storage_writes", []):
        rules.append(StorageWriteRule(
            rule_id=item["rule_id"],
            languages=tuple(item.get("languages") or ()),
            medium=StorageMedium(item["medium"]),
            pattern=re.compile(item["pattern"]),
            written_arg=int(item["written_arg"]),
        ))
    return tuple(rules)


# ===== Default rule library(外部化:data/storage_rules.yml)=====
_RAW_RULES = load_yaml(DATA_DIR / "storage_rules.yml")
DEFAULT_READ_RULES: tuple[StorageReadRule, ...] = _build_read_rules(_RAW_RULES)
DEFAULT_WRITE_RULES: tuple[StorageWriteRule, ...] = _build_write_rules(_RAW_RULES)


def _line_of(text: str, offset: int) -> int:
    """offset(0-based) 所在的 1-based 行号(相对于文本起始)。与 source_detector 对齐。"""
    return text.count("\n", 0, offset) + 1


def _is_dynamic(token: str) -> bool:
    """best-effort: detect dynamic/concatenated tokens (unresolvable statically).

    These get recorded as "unresolvable" and left for the LLM hunter (Task 4).
    """
    return "+" in token or "${" in token or (token.endswith(")") and "(" in token)


def _arg_expr_at(call_text: str, idx: int) -> str:
    """best-effort: extract the idx-th argument expression from a call's text.

    Mirrors the brief's spec: simple comma split (no nested-call awareness).
    Used to capture the expression being written (e.g. `save(u)` → "u").
    """
    open_p = call_text.find("(")
    close_p = call_text.rfind(")")
    if open_p == -1 or close_p == -1 or close_p < open_p:
        return call_text
    inside = call_text[open_p + 1: close_p].strip()
    if not inside:
        return call_text
    parts = [p.strip() for p in inside.split(",")]
    return parts[idx] if idx < len(parts) else (parts[0] if parts else inside)


def _full_call_text(text: str, match: re.Match) -> str:
    """Extend a write-rule match to cover the whole call incl. args + ')'.

    Write patterns end at the opening ``(`` (or just past a literal token), so
    ``match.group(0)`` never contains the arguments or the closing paren —
    which would leave ``_arg_expr_at`` with a truncated call and a garbage
    ``written_expr``. This scans forward from the call's ``(`` with a simple
    balanced-paren counter and returns the full ``name(...)`` slice.

    Best-effort: if parens never rebalance (truncated source) it returns the
    text from the match start to end-of-input — never worse than the old
    truncated behaviour.
    """
    g0 = match.group(0)
    rel_open = g0.find("(")
    if rel_open == -1:
        return g0
    open_pos = match.start() + rel_open
    depth = 0
    i = open_pos
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[match.start():i + 1]
        i += 1
    return text[match.start():]


def detect_storage_reads(
    blocks: "list[FuncBlock]",
    parser,
    entry_point_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourcePoint]:
    """对 entry handler 扫描函数体,识别 storage READ 取用点 → SourcePoint(STORAGE)。

    只对 ``block.id ∈ entry_point_ids`` 的函数跑(与 detect_sources 同口径)。
    Token 必须字面量(group "tok" 或 group(1));未命中 tok 的规则跳过该 match
    (留给 LLM hunter Task 4)。
    """
    out: list[SourcePoint] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        lang = block.language
        for rule in DEFAULT_READ_RULES:
            if lang not in rule.languages:
                continue
            for m in rule.pattern.finditer(text):
                gd = m.groupdict()
                tok = gd.get(rule.param_of) or (m.group(1) if m.groups() else "")
                if not tok:
                    continue   # no literal token resolvable → skip (LLM hunter)
                rel_line = _line_of(text, m.start())
                abs_line = block.start_line + rel_line - 1
                out.append(SourcePoint(
                    id=f"{block.id}::{tok}::{abs_line}",
                    entry_point_id=block.id,
                    param_name=tok,
                    source_type=ParameterSource.STORAGE,
                    expression=m.group(0),
                    file_path=block.file_path,
                    line=abs_line,
                    rule_id=rule.rule_id,
                ))
    return _dedup_sources(out)


def detect_storage_writes(
    blocks: "list[FuncBlock]",
    parser,
    entry_point_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[StorageWritePoint]:
    """对 entry handler 扫描函数体,识别 storage WRITE 点 → StorageWritePoint。

    Writes 不是 sink(DB 写本身不危险),是二阶存储 taint 的 WRITE 端锚点。
    Dynamic/concatenated token 记为 "unresolvable" 以便 LLM hunter 跟进。
    """
    out: list[StorageWritePoint] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        lang = block.language
        for rule in DEFAULT_WRITE_RULES:
            if lang not in rule.languages:
                continue
            for m in rule.pattern.finditer(text):
                gd = m.groupdict()
                tok = gd.get("tok")
                if not tok:
                    token = "unresolvable"
                elif _is_dynamic(tok):
                    token = "unresolvable"
                else:
                    token = tok
                callee = m.group(0).split("(", 1)[0].split(".")[-1]
                receiver = gd.get("receiver")
                written = _arg_expr_at(_full_call_text(text, m), rule.written_arg)
                rel_line = _line_of(text, m.start())
                abs_line = block.start_line + rel_line - 1
                out.append(StorageWritePoint(
                    id=f"{block.id}::{rule.rule_id}::{abs_line}",
                    caller_id=block.id,
                    callee_name=callee,
                    callee_receiver=receiver,
                    medium=rule.medium,
                    storage_token=token,
                    written_expr=written,
                    file_path=block.file_path,
                    line=abs_line,
                    rule_id=rule.rule_id,
                ))
    return _dedup_writes(out)


def _dedup_sources(points: list[SourcePoint]) -> list[SourcePoint]:
    """按 (entry_point_id, param_name, source_type) 去重,保留首个。

    Mirrors source_detector._dedup shape (key shape per Trap 2 / task brief).
    """
    seen: set[tuple] = set()
    out: list[SourcePoint] = []
    for sp in points:
        key = (sp.entry_point_id, sp.param_name, sp.source_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
    return out


def _dedup_writes(ws: list[StorageWritePoint]) -> list[StorageWritePoint]:
    """按 (caller_id, rule_id, line) 去重,保留首个。"""
    seen: set[tuple] = set()
    out: list[StorageWritePoint] = []
    for w in ws:
        key = (w.caller_id, w.rule_id, w.line)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out
