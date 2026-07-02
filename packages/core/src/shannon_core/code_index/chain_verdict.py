"""GitNexus-track chain verdict infrastructure (spec §5.4-5.6 shared).

Three vuln classes (injection/xss/ssrf) share this framework; they differ
only in trace direction and the blind spots each fills. The LLM track already
runs the full methodology (vuln-*.txt prompts). This module is the LIGHT
GitNexus-track chain-verdict pass:

  parameter_graph.json (Plan 1) -> extract_candidate_chains(pgraph, vuln_class)
  -> deterministic sanitizer/encoder annotation (sanitizer_library, Task 1)
  -> post-sanitize-concat detection
  -> judge_chain_verdict(candidate, llm_client) -> verdict + witness + evidence

The merger (Plan 3) then does verdict OR against the LLM track. The GitNexus
track is a CROSS-VALIDATION / BLIND-SPOT FILL, never a constraint on the LLM
track's free analysis (spec §2 principle).

Routing note (2026-06-24 self-correction): SlotContext (parameter_models.py)
has no render-context members, and ParameterPropagationGraph carries no
SinkCallSite objects. So injection/ssrf route by sink_slot (a SlotContext
value), while xss routes by SinkCallSite.category == SinkCategory.XSS, which
requires the sink_call_sites collection (read from code_index.json by the
pipeline activity). render_context is derived from sink_subtype. Without
sink_call_sites, xss extraction yields no reflected candidates (Stored
synthesis in xss_builder still works off TaintFlow source_type/slot).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SinkCallSite,
    SinkCategory,
)
from shannon_core.code_index.sanitizer_library import annotate_sanitizers

logger = logging.getLogger(__name__)

_INJECTION_SLOTS = {"sql_value", "sql_identifier", "cmd_argument",
                    "file_path", "template_expr", "deserialize"}
_SSRF_SLOTS = {"url"}

_DIRECTION = {"injection": "backward", "xss": "backward", "ssrf": "backward"}

# LLM pass prompt template (lightweight; full methodology stays in vuln-*.txt).
_VERDICT_PROMPT = """You are a lightweight chain-verdict pass for the {vuln_class} GitNexus track.
Given ONE candidate source->sink chain with deterministic sanitizer annotations,
judge ONLY whether it is vulnerable. Do NOT re-run full analysis methodology.

Candidate chain:
- source: {source_param} ({source_type})
- sink: {sink_call_site_id}
- slot/render_context: {sink_slot}
- sink arg expressions (source code reaching the dangerous slot): {sink_expressions}
- direction: {direction_hint}
- propagation steps: {steps_repr}
- sanitizer annotations (best-effort, NOT judged for effectiveness): {sanitizers_repr}
- post-sanitize concatenation detected: {post_sanitize_concat}

Rules:
- post-sanitize concatenation = sanitizer considered INEFFECTIVE (tainted again).
- A defense is effective ONLY if it matches the slot/render_context AND no concat after.
- Inspect sink arg expressions to judge whether the sanitizer actually covers the tainted segment.
- Be decisive: return vulnerable OR safe.

Respond with a compact JSON object ONLY:
{{"verdict":"safe|vulnerable","witness_payload":"<minimal>","evidence_chain":"<source->sink with sanitizer notes>","mismatch_reason":"<if vulnerable>","confidence":"high|medium|low"}}
"""


@dataclass(frozen=True)
class CandidateChain:
    vuln_class: str
    flow_id: str
    entry_point_id: str
    source_param: str
    source_type: str
    sink_call_site_id: str
    sink_slot: str
    propagation_steps: list
    sanitizer_annotations: list
    direction_hint: str
    post_sanitize_concat: bool
    render_context: str = ""   # xss only; derived from SinkCallSite.sink_subtype
    sink_expressions: list[str] = field(default_factory=list)   # sink dangerous_slots 的实参源码表达式(供判定 LLM)


@dataclass(frozen=True)
class ChainVerdict:
    verdict: str
    witness_payload: str | None
    evidence_chain: str
    mismatch_reason: str | None
    confidence: str


def _slot_value(slot) -> str:
    return slot.value if hasattr(slot, "value") else str(slot)


def _category_value(category) -> str:
    return category.value if hasattr(category, "value") else str(category)


def _route_for(vuln_class: str, slot_value: str, sink_category: str | None = None) -> bool:
    """Does this TaintFlow belong to the vuln class?

    injection/ssrf: by sink_slot (SlotContext value, in enum).
    xss: by sink_category == SinkCategory.XSS.value ("xss"); sink_slot has no
    render context so it cannot disambiguate XSS sinks.
    """
    if vuln_class == "injection":
        return slot_value in _INJECTION_SLOTS
    if vuln_class == "xss":
        return sink_category == SinkCategory.XSS.value
    if vuln_class == "ssrf":
        return slot_value in _SSRF_SLOTS
    return False


def _render_context_for(sink_subtype: str) -> str:
    """Best-effort render context derivation from SinkCallSite.sink_subtype.

    SlotContext has no render-context members, so for XSS the render context is
    inferred from the code-level sink subtype (innerHTML/document.write/etc.).
    Defaults to html_body (the common DOM sink). The LLM pass judges the final
    effectiveness; this is annotation only.
    """
    s = (sink_subtype or "").lower()
    if "attribute" in s or "attr" in s:
        return "html_attribute"
    if "script" in s or "javascript" in s or "eval" in s:
        return "javascript_string"
    if "url" in s or "href" in s:
        return "url_param"
    if "style" in s or "css" in s:
        return "css_value"
    return "html_body"


def _detect_post_sanitize_concat(steps: list[PropagationStep]) -> bool:
    """True if a sanitizer is followed by re-tainting concatenation.

    两种形态都认:
    1. summary step 编码标记(transformation 含 '|post_concat',由 _intra_result_from_llm 产)
    2. 多 step 序列: sanitize/escape/encode/quote step 后跟 concat step(原逻辑,向后兼容)

    Mirrors spec §5.4.
    """
    seen_sanitizer = False
    for s in steps:
        tf = (s.transformation or "").lower()
        if "post_concat" in tf:          # summary step 标记(Task 2/3 产物)
            return True
        if "sanitize" in tf or "escape" in tf or "encode" in tf or "quote" in tf:
            seen_sanitizer = True
            continue
        if seen_sanitizer and tf == "concat":
            return True
    return False


def extract_candidate_chains(
    pgraph: ParameterPropagationGraph,
    *,
    vuln_class: str,
    sink_call_sites: dict[str, SinkCallSite] | None = None,
) -> list[CandidateChain]:
    """Extract candidate source->sink chains for a vuln class from the taint graph.

    injection/ssrf route by sink_slot (SlotContext value). xss routes by
    SinkCallSite.category == XSS via ``sink_call_sites`` (render context is
    not representable in SlotContext). Empty/None pgraph -> [] (graceful
    degradation when Plan 1 has not landed).
    """
    if pgraph is None:
        return []
    direction = _DIRECTION.get(vuln_class, "forward")
    language = pgraph.language_coverage[0] if pgraph.language_coverage else "python"
    chains: list[CandidateChain] = []
    for flow in pgraph.taint_flows:
        slot_value = _slot_value(flow.sink_slot)
        sink_site: SinkCallSite | None = None
        sink_category: str | None = None
        if vuln_class == "xss":
            if sink_call_sites is None:
                continue  # cannot resolve render context without sink call sites
            sink_site = sink_call_sites.get(flow.sink_call_site_id)
            if sink_site is None:
                continue
            sink_category = _category_value(sink_site.category)
        if not _route_for(vuln_class, slot_value, sink_category):
            continue
        annots = annotate_sanitizers(flow.propagation_steps, language=language)
        render_context = ""
        if vuln_class == "xss" and sink_site is not None:
            render_context = _render_context_for(sink_site.sink_subtype)
        # sink dangerous_slots 的实参表达式(inj/ssrf 也需 sink_call_sites 透传)
        sink_expressions: list[str] = []
        if sink_call_sites is not None:
            scs = sink_call_sites.get(flow.sink_call_site_id)
            if scs is not None:
                sink_expressions = [slot.expression for slot in scs.dangerous_slots if slot.expression]
        chains.append(CandidateChain(
            vuln_class=vuln_class,
            flow_id=flow.flow_id,
            entry_point_id=flow.entry_point_id,
            source_param=flow.source_param,
            source_type=_slot_value(flow.source_type),
            sink_call_site_id=flow.sink_call_site_id,
            sink_slot=slot_value,
            propagation_steps=list(flow.propagation_steps),
            sanitizer_annotations=annots,
            direction_hint=direction,
            post_sanitize_concat=_detect_post_sanitize_concat(flow.propagation_steps),
            render_context=render_context,
            sink_expressions=sink_expressions,
        ))
    return chains


async def judge_chain_verdict(
    candidate: CandidateChain,
    *,
    llm_client: Callable[..., Awaitable[str]],
) -> ChainVerdict:
    """Light LLM pass: judge one candidate chain -> verdict.

    Graceful on LLM failure: never crash; return a needs_review-flavored
    verdict so the merger still processes it (Plan 3 OR is conservative).
    """
    prompt = _VERDICT_PROMPT.format(
        vuln_class=candidate.vuln_class,
        source_param=candidate.source_param,
        source_type=candidate.source_type,
        sink_call_site_id=candidate.sink_call_site_id,
        sink_slot=candidate.render_context or candidate.sink_slot,
        sink_expressions="; ".join(candidate.sink_expressions) or "(none)",
        direction_hint=candidate.direction_hint,
        steps_repr="; ".join(
            f"{s.code_location}:{s.transformation or 'noop'}"
            + (f"|vars={','.join(s.intermediate_vars)}" if s.intermediate_vars else "")
            for s in candidate.propagation_steps
        ) or "(none)",
        sanitizers_repr="; ".join(
            f"{a.defense_type}@{a.applies_to}({a.code_location})"
            for a in candidate.sanitizer_annotations
        ) or "(none)",
        post_sanitize_concat=str(candidate.post_sanitize_concat),
    )

    try:
        raw = await llm_client(prompt)
    except Exception as exc:
        logger.warning("chain-verdict LLM pass failed (%s); marking needs_review", exc)
        return ChainVerdict(
            verdict="vulnerable",  # conservative: OR-friendly (do not silently clear)
            witness_payload=None,
            evidence_chain=f"{candidate.source_param} -> {candidate.sink_call_site_id} (llm-pass-failed, needs_review)",
            mismatch_reason="llm chain-verdict pass failed; needs human/LLM-track review",
            confidence="low",
        )

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("chain-verdict LLM returned non-JSON: %r", raw[:200])
        return ChainVerdict(
            verdict="vulnerable",
            witness_payload=None,
            evidence_chain=f"{candidate.source_param} -> {candidate.sink_call_site_id} (unparseable-llm, needs_review)",
            mismatch_reason="llm chain-verdict pass returned unparseable output; needs review",
            confidence="low",
        )

    return ChainVerdict(
        verdict=str(data.get("verdict", "safe")).strip().lower(),
        witness_payload=data.get("witness_payload"),
        evidence_chain=str(data.get("evidence_chain")
                           or f"{candidate.source_param} -> {candidate.sink_call_site_id}"),
        mismatch_reason=data.get("mismatch_reason"),
        confidence=str(data.get("confidence", "medium")).strip().lower(),
    )
