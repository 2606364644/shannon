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


# ===== Helpers =====

_DB_CURSOR = re.compile(r"^(cursor|cnx|conn|db|database)$")
_REQUESTS_LIKE = re.compile(r"^(requests|httpx|urllib3)$")
_OS_LIKE = re.compile(r"^(os|commands)$")
_SUBPROCESS_LIKE = re.compile(r"^(subprocess)$")
_PICKLE_LIKE = re.compile(r"^(pickle|cPickle|marshal)$")
_YAML_LIKE = re.compile(r"^(yaml)$")
_TEMPLATE_LIKE = re.compile(r"^(flask|jinja2)$")
_PHP_DB_LIKE = re.compile(r"^(mysqli|pdo|db|DB)$")
_JAVA_RUNTIME = re.compile(r"^(Runtime|getRuntime)$")
_JAVA_HTTP = re.compile(r"^(HttpURLConnection|OkHttpClient|HttpClient)$")
_GO_HTTP = re.compile(r"^(http|net)$")
_GO_EXEC = re.compile(r"^(exec)$")


# ===== Default rule library (Spec B 附录 A) =====

DEFAULT_RULES: tuple[SinkRule, ...] = (
    # --- SQL ---
    SinkRule("py-db-cursor-execute", ("python",), "execute", _DB_CURSOR,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("py-db-cursor-executemany", ("python",), "executemany", _DB_CURSOR,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("ts-db-query", ("typescript",), "query", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),  # receiver unknown for `db.query`
    SinkRule("go-db-query", ("go",), "Query", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("java-stmt-executequery", ("java",), "executeQuery", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("java-stmt-execute", ("java",), "execute", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("php-mysqli-query", ("php",), "query", _PHP_DB_LIKE,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("php-db-select-static", ("php",), "select", re.compile(r"^(DB)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),

    # --- Command execution ---
    SinkRule("py-os-system", ("python",), "system", _OS_LIKE,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-os-popen", ("python",), "popen", _OS_LIKE,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-run", ("python",), "run", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-popen", ("python",), "Popen", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-call", ("python",), "call", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-checkoutput", ("python",), "check_output", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("ts-eval", ("typescript",), "eval", None,
             SinkCategory.COMMAND, "js_eval", ((0, SlotContext.GENERIC),)),
    SinkRule("ts-child-process-exec", ("typescript",), "exec", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),),
             needs_review_default=True),  # callee common; receiver check via 'child_process'
    SinkRule("go-exec-command", ("go",), "Command", _GO_EXEC,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),
    # Java's `Runtime.getRuntime().exec(cmd)` chains: the receiver of .exec()
    # is an arbitrarily-named Runtime instance (rt/runtime), so receiver text
    # matching is unreliable — mark needs_review for LLM confirmation.
    SinkRule("java-runtime-exec", ("java",), "exec", _JAVA_RUNTIME,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),),
             needs_review_default=True),
    SinkRule("php-shell-exec", ("php",), "shell_exec", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-system", ("php",), "system", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-passthru", ("php",), "passthru", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-proc-exec", ("php",), "exec", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),

    # --- Deserialization ---
    SinkRule("py-pickle-loads", ("python",), "loads", _PICKLE_LIKE,
             SinkCategory.DESERIALIZATION, "deser_pickle", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("py-pickle-load", ("python",), "load", _PICKLE_LIKE,
             SinkCategory.DESERIALIZATION, "deser_pickle", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("py-yaml-load", ("python",), "load", _YAML_LIKE,
             SinkCategory.DESERIALIZATION, "deser_yaml", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("php-unserialize", ("php",), "unserialize", None,
             SinkCategory.DESERIALIZATION, "deser_unserialize", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("java-objectinput-readobject", ("java",), "readObject", None,
             SinkCategory.DESERIALIZATION, "deser_java", ((0, SlotContext.DESERIALIZE_OBJ),),
             needs_review_default=True),

    # --- SSRF ---
    SinkRule("py-requests-get", ("python",), "get", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_http_client", ((0, SlotContext.URL),)),
    SinkRule("py-requests-post", ("python",), "post", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_http_client", ((0, SlotContext.URL),)),
    SinkRule("py-requests-put", ("python",), "put", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_http_client", ((0, SlotContext.URL),)),
    SinkRule("py-urllib-urlopen", ("python",), "urlopen", None,
             SinkCategory.SSRF, "ssrf_urllib", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("ts-fetch", ("typescript",), "fetch", None,
             SinkCategory.SSRF, "ssrf_fetch", ((0, SlotContext.URL),)),
    SinkRule("ts-axios-get", ("typescript",), "get", re.compile(r"^(axios)$"),
             SinkCategory.SSRF, "ssrf_axios", ((0, SlotContext.URL),)),
    SinkRule("go-http-get", ("go",), "Get", _GO_HTTP,
             SinkCategory.SSRF, "ssrf_http", ((0, SlotContext.URL),)),
    SinkRule("go-http-post", ("go",), "Post", _GO_HTTP,
             SinkCategory.SSRF, "ssrf_http", ((0, SlotContext.URL),)),
    SinkRule("java-httpclient-send", ("java",), "send", _JAVA_HTTP,
             SinkCategory.SSRF, "ssrf_java_http", ((0, SlotContext.URL),)),
    SinkRule("php-curl-exec", ("php",), "curl_exec", None,
             SinkCategory.SSRF, "ssrf_curl", ((0, SlotContext.URL),)),
    SinkRule("php-file-get-contents", ("php",), "file_get_contents", None,
             SinkCategory.SSRF, "ssrf_fgmc", ((0, SlotContext.URL),)),

    # --- SSTI / Template ---
    SinkRule("py-render-template-string", ("python",), "render_template_string", None,
             SinkCategory.TEMPLATE, "ssti_flask", ((0, SlotContext.TEMPLATE_EXPR),)),
    SinkRule("py-jinja-template-render", ("python",), "render", _TEMPLATE_LIKE,
             SinkCategory.TEMPLATE, "ssti_jinja", ((0, SlotContext.TEMPLATE_EXPR),)),

    # --- Code-level XSS (best-effort, needs_review=True) ---
    # Note: `innerHTML` is typically an assignment, not a call. We catch the
    # rare `element.innerHTML(...)` call shape here; assignment-shaped XSS is
    # handled by Spec C / LLM (tree-sitter parsing of assignments varies).
    # rule_id "ts-innerhtml" matches the TDD test contract (test_ts_innerhtml_rule_needs_review).
    SinkRule("ts-innerhtml", ("typescript",), "innerHTML", None,
             SinkCategory.XSS, "xss_dom", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-document-write", ("typescript",), "write", re.compile(r"^(document)$"),
             SinkCategory.XSS, "xss_dom", ((0, SlotContext.GENERIC),),
             needs_review_default=True),

    # --- File ---
    SinkRule("php-file-put-contents", ("php",), "file_put_contents", None,
             SinkCategory.FILE, "file_write", ((0, SlotContext.FILE_PATH),)),
    SinkRule("php-include", ("php",), "include", None,
             SinkCategory.FILE, "file_include", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("php-require", ("php",), "require", None,
             SinkCategory.FILE, "file_include", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),

    # --- Open redirect (needs_review: must combine with param source) ---
    SinkRule("ts-res-redirect", ("typescript",), "redirect", None,
             SinkCategory.REDIRECT, "open_redirect", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("py-flask-redirect", ("python",), "redirect", None,
             SinkCategory.REDIRECT, "open_redirect", ((0, SlotContext.URL),),
             needs_review_default=True),
)


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
                        needs_review=rule.needs_review_default,
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
    return slots


def _make_id(block: "FuncBlock", callee: str, call) -> str:
    """SinkCallSite.id format: '{file}:{caller_func}:{callee}:{line}:{col}'.

    This format is the Spec A contract: TaintFlow.sink_call_site_id must
    match it exactly.
    """
    return (
        f"{block.file_path}:{block.function_name}:{callee}:{call.line}:{call.column}"
    )
