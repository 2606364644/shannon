"""AST-precise sink detector (Spec B).

Identifies dangerous-function call sites at call-point granularity using
tree-sitter AST nodes and a structured rule library. Produces SinkCallSite
records that downstream stages (Spec A propagation, Spec C LLM review)
consume as authoritative facts.

Design notes:
- Rule matching is qualified-name based: `receiver.method` or bare `function`.
- receiver_pattern is a regex; covers common DB cursor names (cursor/cnx/conn/db),
  HTTP clients, etc.
- dangerous_slots are (arg_index, SlotContext) pairs declared by the rule.
- needs_review_default=True for code-level XSS / dynamic sinks where static
  precision is impossible (the LLM in Spec C is told to double-check).

规则库外部化:DEFAULT_RULES 从 data/sink_rules.yml 加载(见 _rule_loader)。
旧版内联 tuple 已删除 —— 改规则改 YAML,不改本文件。
"""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from shannon_core.code_index._rule_loader import DATA_DIR, load_yaml

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SinkRule:
    """One rule in the sink rule library."""
    rule_id: str
    languages: tuple[str, ...]
    callee: str
    receiver_pattern: re.Pattern | None   # None = bare function call (no receiver)
    category: SinkCategory
    sink_subtype: str
    dangerous_slots: tuple[tuple[int, SlotContext], ...]
    needs_review_default: bool = False


def _build_sink_rules(raw: dict) -> "tuple[SinkRule, ...]":
    """YAML dict → tuple[SinkRule]。未知 category/slot 值 fail-fast(ValueError)。

    可信内部数据 —— 不像 sink_discovery_llm._to_category 那样回落(LLM 不可信输出才容错)。
    """
    rules: list[SinkRule] = []
    for item in raw.get("rules", []):
        rule_id = item["rule_id"]
        languages = tuple(item.get("languages") or ())
        callee = item["callee"]
        rp = item.get("receiver_pattern")
        receiver_pattern = None if rp is None else re.compile(rp)
        category = SinkCategory(item["category"])      # 未知 value → ValueError(fail-fast)
        sink_subtype = item["sink_subtype"]
        slots = tuple(
            (int(s["arg_index"]), SlotContext(s["slot"]))
            for s in (item.get("dangerous_slots") or [])
        )
        needs_review = bool(item.get("needs_review_default", False))
        rules.append(SinkRule(
            rule_id=rule_id, languages=languages, callee=callee,
            receiver_pattern=receiver_pattern, category=category,
            sink_subtype=sink_subtype, dangerous_slots=slots,
            needs_review_default=needs_review,
        ))
    return tuple(rules)


# ===== Default rule library(外部化:data/sink_rules.yml)=====
DEFAULT_RULES: tuple[SinkRule, ...] = _build_sink_rules(
    load_yaml(DATA_DIR / "sink_rules.yml"))


# ===== Detection algorithm =====


def _build_rule_index(
    rules: tuple[SinkRule, ...],
) -> dict[tuple[str, str], list[SinkRule]]:
    """Index rules by (language, callee) for O(1) lookup."""
    idx: dict[tuple[str, str], list[SinkRule]] = {}
    for r in rules:
        for lang in r.languages:
            idx.setdefault((lang, r.callee), []).append(r)
    return idx


# Module-level cache of the default rule index
_RULE_INDEX: dict[tuple[str, str], list[SinkRule]] = _build_rule_index(DEFAULT_RULES)


def is_entry_hint(expression: str, block: "FuncBlock") -> bool:
    """Lightweight heuristic: does this argument expression come straight from
    a known external input?

    Conservative — only returns True for clear cases:
      - The expression is exactly a function parameter name.
      - The expression starts with `request.` / `req.` (Flask / Express).
      - The expression starts with a PHP superglobal (`$_GET` etc.).

    Anything more complex (data.x, processed_id, ...) returns False. Spec A
    performs the real intraprocedural taint tracking; this is just a hint for
    downstream priority.
    """
    expr = expression.strip()

    # 1) Direct function parameter
    if expr in block.parameters:
        return True

    # 2) request.* / req.* (Flask / Express / similar)
    if expr.startswith("request.") or expr.startswith("req."):
        return True

    # 3) PHP superglobals
    if expr.startswith(("$_GET", "$_POST", "$_REQUEST", "$_COOKIE", "$_FILES")):
        return True

    return False


def detect_sinks(
    blocks: "list[FuncBlock]",
    parser: "BaseParser",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
    rules: tuple[SinkRule, ...] = DEFAULT_RULES,
) -> list[SinkCallSite]:
    """Detect sink call sites across all function blocks.

    Args:
        blocks: FuncBlocks to scan.
        parser: A parser whose iter_calls/destructure_call/extract_arg_expressions
            match the blocks' language.
        source_provider: Callable that returns source bytes for a given block
            (or None to skip). Caller is responsible for caching/reading files.
        rules: Rule library to use (defaults to DEFAULT_RULES).

    Returns:
        List of SinkCallSite in source order. No deduplication — one rule hit
        per call site, multiple rules with same callee can produce multiple
        SinkCallSites for one call (intentional).
    """
    rule_index = (
        _build_rule_index(rules) if rules is not DEFAULT_RULES else _RULE_INDEX
    )
    sites: list[SinkCallSite] = []

    for block in blocks:
        source = source_provider(block)
        if source is None:
            continue

        try:
            call_nodes = list(parser.iter_calls(block, source))
        except Exception:
            logger.debug("sink scan: iter_calls failed for %s", block.id, exc_info=True)
            continue

        for call in call_nodes:
            try:
                callee, receiver = parser.destructure_call(call)
            except Exception:
                logger.debug("sink scan: destructure_call failed for %s", block.id, exc_info=True)
                continue
            if not callee:
                continue

            candidates = rule_index.get((block.language, callee), [])
            if not candidates:
                continue

            for rule in candidates:
                if not _rule_matches(rule, receiver):
                    continue
                try:
                    args = parser.extract_arg_expressions(call, source)
                    dangerous = _build_dangerous_slots(rule, args, block)
                    # spec 改动 1.2 B: SQL sink whose arg is string-built
                    # (f-string / format / printf / concat) actually injects a
                    # dynamic identifier or DDL fragment — binds do NOT protect
                    # it. _build_dangerous_slots already rewrote such SQL_VALUE
                    # slots to SQL_IDENTIFIER; mirror that here so the site is
                    # flagged for Spec C review. Gated on SinkCategory.SQL so
                    # COMMAND/SSRF/etc. are untouched even if their arg text
                    # happens to look string-built.
                    force_review = rule.category == SinkCategory.SQL and any(
                        _looks_string_built(d.expression) for d in dangerous
                    )
                    site = SinkCallSite(
                        id=_make_id(block, callee, call),
                        caller_id=block.id,
                        callee_name=callee,
                        callee_receiver=receiver,
                        category=rule.category,
                        sink_subtype=rule.sink_subtype,
                        file_path=block.file_path,
                        line=call.line,
                        column=call.column,
                        dangerous_slots=dangerous,
                        rule_id=rule.rule_id,
                        needs_review=rule.needs_review_default or force_review,
                    )
                    sites.append(site)
                except Exception:
                    logger.debug("sink scan: skipping rule %s on call", rule.rule_id, exc_info=True)
                    continue

    return sites


def _rule_matches(rule: SinkRule, receiver: str | None) -> bool:
    """A rule matches if receiver_pattern is None (bare call) or receiver
    matches the pattern (qualified call)."""
    if rule.receiver_pattern is None:
        # Bare-function rule: only matches if there's no receiver.
        return receiver is None
    if receiver is None:
        return False
    return bool(rule.receiver_pattern.match(receiver))


# spec 改动 1.2 B: arg 表达式是否形如 string-built（f-string / format / printf / 拼接）。
# 当一个 SQL sink 的实参是动态字符串构建时，它实际注入的是一个标识符或 DDL 片段
# (table/column 名、ORDER BY 等)，参数绑定 / placeholder 并不能保护它 ——
# 因此这类槽位应改标 SQL_IDENTIFIER 而非 SQL_VALUE。
_STRING_BUILT_RE = re.compile(
    r"(^[fFrR]['\"])"          # f-string 前缀 (Python: f"...", r'...', etc.)
    r"|fmt\.Sprintf"            # Go fmt.Sprintf
    r"|\.format\("              # Python str.format
    r"|%\s*[sdbf]"              # printf 风格 %s/%d/%b/%f
    r"|['\"]\s*\+"              # 字符串字面量 + 拼接
)


def _looks_string_built(expr: str) -> bool:
    """best-effort: arg 表达式是否是动态字符串构建（暗示标识符/DDL 注入）。"""
    return bool(_STRING_BUILT_RE.search(expr or ""))


def _build_dangerous_slots(
    rule: SinkRule,
    arg_expressions: list[str],
    block: "FuncBlock",
) -> list[DangerousSlot]:
    slots: list[DangerousSlot] = []
    for idx, slot_ctx in rule.dangerous_slots:
        if idx == -1:  # variadic marker — emit a single hint
            slots.append(DangerousSlot(
                arg_index=-1,
                slot=slot_ctx,
                expression=",".join(arg_expressions),
                is_entry_hint=any(is_entry_hint(a, block) for a in arg_expressions),
            ))
            continue
        if idx < len(arg_expressions):
            expr = arg_expressions[idx]
            slots.append(DangerousSlot(
                arg_index=idx,
                slot=slot_ctx,
                expression=expr,
                is_entry_hint=is_entry_hint(expr, block),
            ))

    # spec 改动 1.2 B: SQL 类 sink 的 string-built 实参 → SQL_IDENTIFIER。
    # 绑定 / placeholder 只保护 value 槽；string-built arg 实际是动态标识符
    # 或 DDL 片段，必须经白名单校验。DangerousSlot 是 pydantic BaseModel，
    # 故用 model_copy(update=...) 重建（等价于 dataclasses.replace）。
    # 仅对 SinkCategory.SQL 生效 —— COMMAND/SSRF/TEMPLATE 等即使 arg 看似
    # string-built 也不应改标 SQL_IDENTIFIER。
    if rule.category == SinkCategory.SQL:
        slots = [
            d.model_copy(update={"slot": SlotContext.SQL_IDENTIFIER})
            if _looks_string_built(d.expression) else d
            for d in slots
        ]
    return slots


def _make_id(block: "FuncBlock", callee: str, call) -> str:
    """SinkCallSite.id format: '{file}:{caller_func}:{callee}:{line}:{col}'.

    This format is the Spec A contract: TaintFlow.sink_call_site_id must
    match it exactly.
    """
    return (
        f"{block.file_path}:{block.function_name}:{callee}:{call.line}:{call.column}"
    )
