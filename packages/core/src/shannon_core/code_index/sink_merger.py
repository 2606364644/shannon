"""Merge deterministic sink detection results with LLM-discovered sinks.

Reads the LLM pre-recon deliverable text, extracts sink locations via
regex, deduplicates against deterministic SinkCallSite[] by (file_path, line),
and appends LLM-only sinks as new SinkCallSite instances with rule_id
"llm-sink-hunter" and needs_review=True.
"""

import re
import logging
from pydantic import BaseModel

from shannon_core.code_index.parameter_models import (
    SinkCallSite,
    SinkCategory,
)
    # NOTE: DangerousSlot, SlotContext are available but not needed here —
    # LLM sinks carry empty dangerous_slots since their taint slots are unknown.

logger = logging.getLogger(__name__)

# Regex to match file:line patterns in LLM reports.
# Matches backtick-wrapped paths like `src/db.py:42` or `templates/index.ejs:15`.
_FILE_LINE_RE = re.compile(r"`([^`]+):(\d+)`")

# Category inference keywords — first match in the LLM report section wins.
# Each entry: (keyword_pattern, SinkCategory).
# Ordered from most specific to least.
_CATEGORY_HINTS: list[tuple[re.Pattern, SinkCategory]] = [
    (re.compile(r"SQL\s*Injection|sql_raw|cursor\.execute|\.query\(", re.IGNORECASE), SinkCategory.SQL),
    (re.compile(r"Command\s*Injection|command_exec|system\(|exec\(|popen\(", re.IGNORECASE), SinkCategory.COMMAND),
    (re.compile(r"SSRF|server.side.request|fetch\(|requests\.", re.IGNORECASE), SinkCategory.SSRF),
    (re.compile(r"XSS|cross.site.scripting|innerHTML|document\.write", re.IGNORECASE), SinkCategory.XSS),
    (re.compile(r"Template|SSTI|render_template|<%-.*%>|{{\|safe}}", re.IGNORECASE), SinkCategory.TEMPLATE),
    (re.compile(r"Path\s*Traversal|LFI|RFI|file\s*include|fopen|readFile", re.IGNORECASE), SinkCategory.FILE),
    (re.compile(r"Deserializ|pickle\.loads?|unserialize|readObject", re.IGNORECASE), SinkCategory.DESERIALIZATION),
    (re.compile(r"Redirect|open.redirect|location\.href", re.IGNORECASE), SinkCategory.REDIRECT),
]

class LlmSinkCandidate(BaseModel):
    """A sink location extracted from an LLM report."""
    file_path: str
    line: int
    category: SinkCategory = SinkCategory.XSS  # conservative default; overridden by inference


def _infer_category(report_text: str, match_start: int) -> SinkCategory:
    """Infer SinkCategory from the LLM report text preceding a file:line match."""
    # Scope context to the nearest section heading (# ...) above the match,
    # so we only look at the relevant section rather than earlier sections.
    context_start = report_text.rfind("\n#", 0, match_start)
    if context_start == -1:
        context_start = 0
    else:
        context_start += 1  # skip the \n before #
    context = report_text[context_start:match_start]
    for pattern, category in _CATEGORY_HINTS:
        if pattern.search(context):
            return category
    # Fallback: also check the line after the match
    line_end = report_text.find("\n", match_start)
    if line_end == -1:
        line_end = len(report_text)
    after_line = report_text[match_start:line_end]
    for pattern, category in _CATEGORY_HINTS:
        if pattern.search(after_line):
            return category
    return SinkCategory.XSS  # conservative fallback — reviewed downstream via needs_review


def parse_llm_sinks(report_text: str) -> list[LlmSinkCandidate]:
    """Extract sink locations from an LLM free-text report.

    Looks for backtick-wrapped file:line patterns (e.g., `src/db.py:42`)
    and infers categories from surrounding context keywords.
    """
    if not report_text or not report_text.strip():
        return []

    candidates: list[LlmSinkCandidate] = []
    seen: set[tuple[str, int]] = set()

    for m in _FILE_LINE_RE.finditer(report_text):
        file_path = m.group(1)
        try:
            line = int(m.group(2))
        except ValueError:
            continue
        key = (file_path, line)
        if key in seen:
            continue
        seen.add(key)
        category = _infer_category(report_text, m.start())
        candidates.append(LlmSinkCandidate(file_path=file_path, line=line, category=category))

    return candidates


def merge_sink_reports(
    deterministic_sinks: list[SinkCallSite],
    llm_report_text: str,
) -> list[SinkCallSite]:
    """Merge deterministic sink detection results with LLM-discovered sinks.

    Deduplication: deterministic sinks win on (file_path, line) collision.
    LLM-only sinks are appended with rule_id="llm-sink-hunter" and
    needs_review=True.
    """
    # Build set of deterministic (file_path, line) pairs
    det_keys: set[tuple[str, int]] = {
        (s.file_path, s.line) for s in deterministic_sinks
    }

    # Parse LLM report
    llm_candidates = parse_llm_sinks(llm_report_text)

    # Build merged list: start with all deterministic
    merged = list(deterministic_sinks)

    # Append LLM-only sinks
    for cand in llm_candidates:
        if (cand.file_path, cand.line) in det_keys:
            logger.debug(
                "merge: LLM sink %s:%d already in deterministic results, skipping",
                cand.file_path, cand.line,
            )
            continue
        merged.append(SinkCallSite(
            # NOTE: id format deliberately differs from the deterministic
            # "{file}:{caller_func}:{callee}:{line}:{col}" contract.
            # LLM sinks lack caller/callee info; they use "llm:" prefix to
            # signal they need human review before joining taint-flow chains.
            id=f"llm:{cand.file_path}:{cand.line}",
            caller_id="",
            callee_name="",
            callee_receiver=None,
            category=cand.category,
            sink_subtype="llm_discovered",
            file_path=cand.file_path,
            line=cand.line,
            column=0,
            dangerous_slots=[],
            rule_id="llm-sink-hunter",
            needs_review=True,
        ))

    logger.info(
        "merge: %d deterministic + %d LLM-only = %d total sinks",
        len(deterministic_sinks),
        len(merged) - len(deterministic_sinks),
        len(merged),
    )
    return merged
