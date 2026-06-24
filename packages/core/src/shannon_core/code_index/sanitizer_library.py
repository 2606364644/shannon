"""Deterministic sanitizer/encoder library (best-effort, spec §5.4-5.6 shared).

Identifies KNOWN library-level defense functions appearing on a taint path.
This is annotation only -- it does NOT judge effectiveness. Effectiveness
(slot/context match, concat-after-sanitize) is judged by the LLM chain-verdict
pass (Task 3). This mirrors the existing ``has_sanitizer_hint`` semantics
(parameter_models.py:59 "仅提示，不判有效性").

Two entry points:
- SanitizerLibrary.match(language, callee, receiver_text, arg_expr) -> Annotation | None
- annotate_sanitizers(propagation_steps, language) -> list[Annotation]
"""

import re
from dataclasses import dataclass
from typing import Iterable

from shannon_core.code_index.parameter_models import PropagationStep


@dataclass(frozen=True)
class SanitizerRule:
    rule_id: str
    languages: tuple[str, ...]
    callee: str                         # function/method name
    receiver_pattern: re.Pattern | None  # None = bare call
    arg_hint: re.Pattern | None          # optional: arg text must match (e.g. placeholder)
    defense_type: str                    # sql_bind / shlex_quote / ... (see table)
    applies_to: str                      # SlotContext value or render_context value


@dataclass(frozen=True)
class SanitizerAnnotation:
    rule_id: str
    defense_type: str
    applies_to: str
    code_location: str = ""             # file:line where the defense appears
    matched_text: str = ""              # the callee / arg hint that matched


# --- Receiver regexes ---
_HTML_MOD = re.compile(r"^(html)$")
_DOMPURIFY = re.compile(r"^(DOMPurify|dompurify)$")
_PHP_HTMLSPECIAL = None  # bare function htmlspecialchars
_SHLEX = None
_SUBPROCESS = re.compile(r"^(subprocess)$")
_DB_CURSOR = re.compile(r"^(cursor|cnx|conn|db|database)$")
_PATHLIB = re.compile(r"^(Path)$")
_JINJA = re.compile(r"^(jinja2|flask)$")
_URLLIB = re.compile(r"^(urllib|parse)$")

# arg hints: detect SQL placeholder in the arg expression
_SQL_PLACEHOLDER = re.compile(r"(%s|%d|\?|:\w+|@\w+|\$\d+)")


DEFAULT_SANITIZER_RULES: tuple[SanitizerRule, ...] = (
    # --- SQL binds (parameterized query) ---
    SanitizerRule("py-sql-execute-bind", ("python",), "execute", _DB_CURSOR,
                  _SQL_PLACEHOLDER, "sql_bind", "sql_value"),
    SanitizerRule("py-sql-executemany-bind", ("python",), "executemany", _DB_CURSOR,
                  _SQL_PLACEHOLDER, "sql_bind", "sql_value"),
    SanitizerRule("php-mysqli-prepare", ("php",), "prepare", None,
                  None, "sql_bind", "sql_value"),
    SanitizerRule("java-preparedstatement", ("java",), "prepareStatement", None,
                  None, "sql_bind", "sql_value"),

    # --- Command execution ---
    SanitizerRule("py-shlex-quote", ("python",), "quote", re.compile(r"^(shlex)$"),
                  None, "shlex_quote", "cmd_argument"),
    # subprocess with list args + shell=False is detected via AST in sink_detector;
    # here we only annotate when the transformation text carries shell=False hint.
    # (No callee rule; handled in annotate_sanitizers via transformation text.)

    # --- Path ---
    SanitizerRule("py-path-resolve", ("python",), "resolve", _PATHLIB,
                  None, "path_resolve_boundary", "file_path"),
    SanitizerRule("py-path-absolute", ("python",), "absolute", _PATHLIB,
                  None, "path_resolve_boundary", "file_path"),

    # --- Template autoescape ---
    SanitizerRule("py-jinja-autoescape", ("python",), "get_template", _JINJA,
                  None, "template_autoescape", "template_expr"),

    # --- HTML entity encoding ---
    SanitizerRule("py-html-escape", ("python",), "escape", _HTML_MOD,
                  None, "html_entity_encode", "html_body"),
    SanitizerRule("php-htmlspecialchars", ("php",), "htmlspecialchars", None,
                  None, "html_entity_encode", "html_body"),
    SanitizerRule("php-htmlentities", ("php",), "htmlentities", None,
                  None, "html_entity_encode", "html_body"),
    SanitizerRule("ts-he-encode", ("typescript",), "encode", re.compile(r"^(he)$"),
                  None, "html_entity_encode", "html_body"),

    # --- Attribute encoding ---
    SanitizerRule("wp-esc-attr", ("php",), "esc_attr", None,
                  None, "attr_encode", "html_attribute"),

    # --- JS string escaping (json.dumps for JS context) ---
    SanitizerRule("py-json-dumps", ("python",), "dumps", re.compile(r"^(json)$"),
                  None, "js_string_escape", "javascript_string"),
    SanitizerRule("ts-json-stringify", ("typescript",), "stringify", re.compile(r"^(JSON)$"),
                  None, "js_string_escape", "javascript_string"),

    # --- URL encoding ---
    SanitizerRule("py-url-quote", ("python",), "quote", _URLLIB,
                  None, "url_encode", "url_param"),
    SanitizerRule("ts-encodeuricomponent", ("typescript",), "encodeURIComponent", None,
                  None, "url_encode", "url_param"),
    SanitizerRule("php-rawurlencode", ("php",), "rawurlencode", None,
                  None, "url_encode", "url_param"),

    # --- DOMPurify (client-side XSS sanitizer) ---
    SanitizerRule("ts-dompurify-sanitize", ("typescript", "javascript"), "sanitize", _DOMPURIFY,
                  None, "dom_purify", "html_body"),
)


class SanitizerLibrary:
    """Match a call site against the known defense-function library."""

    def __init__(self, rules: Iterable[SanitizerRule] = DEFAULT_SANITIZER_RULES):
        self._rules = tuple(rules)

    def match(
        self,
        *,
        language: str,
        callee: str,
        receiver_text: str | None,
        arg_expr: str | None = None,
    ) -> SanitizerAnnotation | None:
        lang = language.lower()
        for r in self._rules:
            if lang not in r.languages:
                continue
            if r.callee != callee:
                continue
            if r.receiver_pattern is not None:
                if receiver_text is None or not r.receiver_pattern.fullmatch(receiver_text):
                    continue
            if r.arg_hint is not None:
                if arg_expr is None or not r.arg_hint.search(arg_expr):
                    continue
            return SanitizerAnnotation(
                rule_id=r.rule_id, defense_type=r.defense_type,
                applies_to=r.applies_to, matched_text=f"{receiver_text or ''}.{callee}".lstrip("."),
            )
        return None


# transformation 字段里出现的 sanitizer 名片段 -> defense_type 映射
# (covers sanitize_hint:<name> patterns that llm_taint_analyzer / chain_propagator emit)
_TRANSFORMATION_FRAGMENTS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"shlex\.quote", re.I), "shlex_quote", "cmd_argument"),
    (re.compile(r"html\.escape|htmlspecialchars|htmlentities|DOMPurify", re.I),
     "html_entity_encode", "html_body"),
    (re.compile(r"encodeuricomponent|rawurlencode|urlencode|urllib.*quote", re.I),
     "url_encode", "url_param"),
    (re.compile(r"json\.(dumps|stringify)|JSON\.stringify", re.I),
     "js_string_escape", "javascript_string"),
    (re.compile(r"shell\s*=\s*False", re.I), "subprocess_array", "cmd_argument"),
    (re.compile(r"\.resolve\(\)|\.absolute\(\)", re.I),
     "path_resolve_boundary", "file_path"),
)


def annotate_sanitizers(
    propagation_steps: Iterable[PropagationStep],
    *,
    language: str = "python",
) -> list[SanitizerAnnotation]:
    """Annotate known defenses appearing in a taint path's propagation steps.

    Scans the ``transformation`` text of each step (which carries
    ``sanitize_hint:<name>`` per parameter_models.py:47, plus concat/encode/etc).
    Best-effort: returns annotations, does NOT judge effectiveness.
    """
    out: list[SanitizerAnnotation] = []
    for step in propagation_steps:
        tf = step.transformation or ""
        if not tf:
            continue
        for pat, defense_type, applies_to in _TRANSFORMATION_FRAGMENTS:
            if pat.search(tf):
                out.append(SanitizerAnnotation(
                    rule_id=f"xform:{defense_type}",
                    defense_type=defense_type, applies_to=applies_to,
                    code_location=step.code_location, matched_text=tf,
                ))
    return out
