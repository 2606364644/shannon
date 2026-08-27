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
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from supernova_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SinkCallSite,
    SinkCategory,
)
from supernova_core.code_index.models import EntryPoint
from supernova_core.code_index.sanitizer_library import annotate_sanitizers
from supernova_core.agents.llm_json import _extract_json_payload
from supernova_core.i18n import current_lang

# Structured output schema：chain-verdict 轻量 LLM 判定，JSON object 根。
# 对齐 ChainVerdict dataclass（verdict/witness_payload/evidence_chain/
# mismatch_reason/confidence）。经 output_format 通道强制合法 JSON，省事后 extract。
CHAIN_VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "witness_payload": {"type": ["string", "null"]},
        "evidence_chain": {"type": "string"},
        "mismatch_reason": {"type": ["string", "null"]},
        "confidence": {"type": "string"},
        "title": {"type": ["string", "null"]},
        # PoC 参数位的 Agent 判定（body|query|null）——builder 落 affected_parameters
        # 注记；缺失退 source_type 确定性注记（deterministic_param_location）。
        "source_param_location": {"type": ["string", "null"]},
    },
    # witness_payload 入 required（可 null）：openai_compatible 引擎不发 schema 时
    # 仅靠 prompt 约束，不在 required 里模型常直接省略 → 下游 PoC 无 witness 可用。
    "required": ["verdict", "witness_payload", "evidence_chain", "title"],
}

# source_type（source_rules.yml 匹配事实，与 method 无关——req.body.x 挂 GET 路由
# 照样是 body）→ PoC 参数位。path/header/cookie 等无 HTTP placement 对应 → None
# （不虚构位置，留给 method 启发式）。chain_verdict 失败时的确定性降级层。
_PARAM_LOCATION_BY_SOURCE_TYPE = {
    "body": "body", "form": "body", "query": "query",
}


def deterministic_param_location(source_type: str | None) -> str | None:
    """source_type → 'body'|'query'|None（builder 组装 affected_parameters 注记）。"""
    return _PARAM_LOCATION_BY_SOURCE_TYPE.get(
        str(source_type or "").strip().lower())


def placement_noted_params(chain, verdict) -> list[str] | None:
    """source_param → 带位置注记的 affected_parameters（'q (query)'）。

    Agent 判定（verdict.source_param_location）优先；None 退 source_type
    确定性注记（判定通道失败时仍有位置——PoC 参数位不再依赖文本启发式）；
    都无对应位（path/header/…）→ None（不虚构位置）。三个 taint builder 共用。"""
    loc = (getattr(verdict, "source_param_location", None)
           or deterministic_param_location(chain.source_type))
    return [f"{chain.source_param} ({loc})"] if loc else None


def _norm_location(val) -> str | None:
    """LLM 产的位置值归一：只认 body/query（大小写折叠），其余 → None。"""
    v = str(val or "").strip().lower()
    return v if v in ("body", "query") else None

logger = logging.getLogger(__name__)

_INJECTION_SLOTS = {"sql_value", "sql_identifier", "cmd_argument",
                    "file_path", "template_expr", "deserialize"}
_SSRF_SLOTS = {"url"}

_DIRECTION = {"injection": "backward", "xss": "backward", "ssrf": "backward"}

# title 指令按 narration 语言切换(zh 中文标题 / en 英文标题),与 vuln-*.txt 的 title
# 语言约束对齐——避免 GitNexus 轨产出与 LLM 轨语言不一致的漏洞标题。
_TITLE_DIRECTIVE = {
    "zh": (
        'Give a one-line descriptive "title" encoding the vulnerability category + where it lives '
        '(e.g., "SQL 注入：/shop/apply-coupon 的 coupon_code 进入原始查询"). Required for EVERY chain, '
        "vulnerable or safe (both enter the queue); never a bare category label. "
        "用简体中文撰写标题,漏洞类型/参数/路径/端点保留英文。"
    ),
    "en": (
        'Give a one-line descriptive "title" encoding the vulnerability category + where it lives '
        '(e.g., "SQL Injection via coupon_code in /shop/apply-coupon"). Required for EVERY chain, '
        "vulnerable or safe (both enter the queue); never a bare category label. "
        "Write the title in English."
    ),
}

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
- witness_payload = the MINIMAL concrete attack input that would trigger this sink
  (e.g. "' OR '1'='1" for a SQL value slot, "http://169.254.169.254/" for a url slot).
  Required when verdict is vulnerable; use null only when verdict is safe.
- source_param_location = where the source parameter is delivered in the HTTP
  request: "body" or "query". Judge from the source expression shown above
  (e.g. req.body.x → body, req.query.x / ?x= → query); use null only when
  genuinely indeterminable.
- {title_directive}

Respond with a compact JSON object ONLY:
{{"verdict":"safe|vulnerable","witness_payload":"<minimal concrete attack payload; null if safe>","evidence_chain":"<source->sink with sanitizer notes>","mismatch_reason":"<if vulnerable>","confidence":"high|medium|low","title":"<one-line descriptive name>","source_param_location":"body|query|null"}}
"""

# 多轮 agent 版 prompt（spec 2026-08-27 §3，逐条深判）：同一链快照与输出契约，
# 差异在判定形态——不再假设快照完备，agent 自主 grep/read 验证每一步
# （sanitize 实际实现、sink 实参构造、可达性）。内嵌常量对齐 _VERDICT_PROMPT
# 同模式（core 层无 prompt_manager；spec 所指 prompts/chain-verdict-agent.txt
# 落在此处，避免双份漂移）。
_VERDICT_PROMPT_AGENT = """You are a multi-turn chain-verdict agent for the {vuln_class} GitNexus track.
You are given ONE candidate source->sink chain with deterministic (best-effort) annotations.
Your job: VERIFY it in the actual repository, then judge whether it is vulnerable.

Candidate chain (leads to verify, NOT ground truth):
- source: {source_param} ({source_type})
- sink: {sink_call_site_id}
- slot/render_context: {sink_slot}
- sink arg expressions (deterministic layer's view of what reaches the slot): {sink_expressions}
- direction: {direction_hint}
- propagation steps: {steps_repr}
- sanitizer annotations (claimed, NOT judged for effectiveness): {sanitizers_repr}
- post-sanitize concatenation detected: {post_sanitize_concat}

Verification protocol (use your tools; the repo is your working directory):
1. Read the sink call site file/line: confirm the tainted expression actually flows into
   the dangerous slot as claimed, and note how the sink argument is built.
2. Grep/Read each claimed sanitizer: check its real implementation — does it actually
   encode/escape for THIS slot/render_context, and is it applied on the tainted path
   with no concatenation afterwards?
3. Read the entry point / handler: confirm reachability (route registered, param bound).
4. Only after verification, judge. Do NOT re-run a full analysis methodology — verify
   THIS chain's claims, that is all.

Rules (same as the single-pass caliber):
- post-sanitize concatenation = sanitizer considered INEFFECTIVE (tainted again) —
  but verify it: read the code between sanitizer and sink.
- A defense is effective ONLY if it matches the slot/render_context AND no concat after.
- Be decisive: return vulnerable OR safe. If after honest reading you still cannot
  decide (e.g. depends on runtime config you cannot see), return needs_review.
- witness_payload = the MINIMAL concrete attack input that would trigger this sink
  (e.g. "' OR '1'='1" for a SQL value slot, "http://169.254.169.254/" for a url slot).
  Required when verdict is vulnerable; use null only when verdict is safe.
- source_param_location = where the source parameter is delivered in the HTTP
  request: "body" or "query". Judge from the source expression you read
  (e.g. req.body.x → body, req.query.x / ?x= → query); use null only when
  genuinely indeterminable.
- evidence_chain must cite what you READ (file:line), not just the claims you were given.
- {title_directive}

Respond with a compact JSON object ONLY:
{{"verdict":"safe|vulnerable|needs_review","witness_payload":"<minimal concrete attack payload; null if safe>","evidence_chain":"<source->sink with file:line citations>","mismatch_reason":"<if vulnerable or needs_review>","confidence":"high|medium|low","title":"<one-line descriptive name>","source_param_location":"body|query|null"}}
"""

# unparseable 有界重试（spec O2 后半）：openai_compatible 引擎（GLM）端点不支持
# response_format=json_schema（发之 400），schema 只用于本地解析，模型仅受 prompt
# 约束——实测对 Markdown 输出合规率极低（2026-07-22 spec R5: 14/14 unparseable）。
# 重试链：先轻量转格式（对齐 providers_openai._lightweight_reparse 措辞，便宜），
# 再全量重发 + 加强 JSON-only 指令。耗尽才落保守分支（不丢报，witness=None）。
_JSON_ONLY_REMINDER = (
    "\n\nIMPORTANT: Your previous response was NOT valid JSON. "
    "Respond with ONLY the compact JSON object — no markdown fences, "
    "no explanation, no code blocks."
)


def _reformat_prompt(raw: str) -> str:
    """轻量转格式 prompt（不用 str.format：schema 里的 JSON 花括号会撞占位符）。"""
    return (
        "将以下分析结论转为符合 schema 的纯 JSON，只输出 JSON 本体，"
        "不要任何解释、前言或 markdown 代码围栏。schema 字段："
        '{"verdict":"safe|vulnerable","witness_payload":"<攻击载荷字符串或 null>",'
        '"evidence_chain":"<source->sink 证据链>",'
        '"mismatch_reason":"<字符串或 null>","confidence":"high|medium|low",'
        '"title":"<一句话标题>","source_param_location":"<body|query|null>"}\n'
        f"待转换文本：\n{raw[:4000]}"
    )


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
    title: str | None = None
    # PoC 参数位 Agent 判定（"body"|"query"|None）——None 时 builder 落
    # source_type 确定性注记（判定通道失败亦然，见 _norm_location 缺省）。
    source_param_location: str | None = None


def _slot_value(slot) -> str:
    return slot.value if hasattr(slot, "value") else str(slot)


def _parse_verdict_json(raw: object) -> dict | None:
    """LLM 原始输出 → verdict dict。

    extract 失败 / 非法 JSON / 非 object / verdict 值不合法 → None（调用方重试
    或落保守分支）。比旧裸 json.loads 多验一层 verdict 枚举：字段缺失/错值说明
    模型没按 schema 输出，与"解析崩"同 Treatment 走重试。
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = _extract_json_payload(raw)
        if payload is None:
            return None
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("verdict", "")).strip().lower() not in ("safe", "vulnerable"):
        return None
    return data


def _fallback_title(candidate: "CandidateChain") -> str:
    """Deterministic descriptive title when the LLM pass fails / returns no title.

    Best-effort: ``<vuln_class> -> <source_param> -> <sink>``
    (zh narration: ``<vuln_class>：<source> → <sink>``) — encodes category + where it lives
    so the finding is never title-less (the second-pass report cleanup can still rewrite it)."""
    if current_lang() == "zh":
        return f"{candidate.vuln_class}：{candidate.source_param} → {candidate.sink_call_site_id}"
    return f"{candidate.vuln_class} via {candidate.source_param} -> {candidate.sink_call_site_id}"


def _category_value(category) -> str:
    return category.value if hasattr(category, "value") else str(category)


def http_route_label(
    entry_point_id: str | None,
    entry_points: dict[str, EntryPoint] | None,
) -> str | None:
    """Join a chain's entry_point_id to its parsed HTTP route → "METHOD /path".

    entry_points maps EntryPoint.func_block_id → EntryPoint（route-bearing，
    由 pipeline activity 从 code_index.json 构建）。join miss / http_method
    未知时返回 None——调用方保持原字段值，PoC gap-fill LLM 兜底。与 authz 轨
    _endpoint_label 同构，但更严格：缺真实 method 的 label 匹配不上 PoC 的
    derive_method_path 正则，索性不发。
    """
    if not entry_point_id or not entry_points:
        return None
    ep = entry_points.get(entry_point_id)
    if ep is None or not ep.route or not ep.http_method:
        return None
    route = ep.route.strip()
    if not route.startswith("/"):
        route = f"/{route}"
    return f"{ep.http_method.strip().upper()} {route}"


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
    if "server" in s and "render" in s:
        # spec 2026-08-21 修复点 D 配套: 服务端模板渲染(res.render 类)——判定面是
        # 模板 autoescape/locals 转义,非 DOM innerHTML。
        return "server_template"
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


async def _retry_verdict_parse(
    prompt: str, raw: str, *, llm_client: Callable[..., Awaitable[str]],
) -> dict | None:
    """unparseable 后的有界重试：先轻量转格式（便宜），再全量重发+加强 JSON-only。

    env SUPERNOVA_CHAIN_VERDICT_RETRIES 控制总次数（默认 2；0 = 直接保守降级）。
    调用异常 / 全部尝试仍失败 → None（judge 落保守分支）。
    """
    max_retries = max(0, int(os.getenv("SUPERNOVA_CHAIN_VERDICT_RETRIES", "2")))
    retry_prompts: list[str] = []
    if raw.strip():
        retry_prompts.append(_reformat_prompt(raw))
    while len(retry_prompts) < max_retries:
        retry_prompts.append(prompt + _JSON_ONLY_REMINDER)
    for retry_prompt in retry_prompts[:max_retries]:
        try:
            raw = await llm_client(retry_prompt, output_format=CHAIN_VERDICT_SCHEMA)
        except Exception as exc:
            logger.warning("chain-verdict retry LLM call failed (%s); giving up", exc)
            return None
        data = _parse_verdict_json(raw)
        if data is not None:
            logger.info("chain-verdict: recovered parseable output after retry")
            return data
    return None


def unadjudicated_verdict(candidate: "CandidateChain", reason: str) -> ChainVerdict:
    """判定通道未跑/失败 → 保守 verdict（spec 2026-08-26 §5.7 + 2026-08-27 §3 护栏）。

    verdict=vulnerable（OR-friendly，不静默清空）+ confidence="unadjudicated"
    （通道失败 ≠ LLM 判了且低置信——渲染层显示「未判定」）。LLM 异常 /
    unparseable / 候选链超护栏 共用。
    """
    return ChainVerdict(
        verdict="vulnerable",
        witness_payload=None,
        evidence_chain=(f"{candidate.source_param} -> {candidate.sink_call_site_id} "
                        f"(unadjudicated, needs_review)"),
        mismatch_reason=reason,
        confidence="unadjudicated",
        title=_fallback_title(candidate),
    )


async def _resolve_agent_raw(verdict_agent, prompt: str, *,
                             agent_name: str | None = None) -> str:
    """多轮 agent 结果 → raw str（与 llm_client 契约归一）。

    structured_output（dict/list）→ JSON str；缺失回退 .text；success=False
    → raise（由 judge 的 except 接住走保守 unadjudicated——通道失败 ≠ 判非漏洞）。
    """
    result = await verdict_agent(
        prompt, output_format=CHAIN_VERDICT_SCHEMA, agent_name=agent_name)
    if result is None:
        raise RuntimeError("verdict agent returned None")
    if getattr(result, "success", True) is False:
        raise RuntimeError(
            f"verdict agent failed: {getattr(result, 'error', None) or 'unknown'}")
    so = getattr(result, "structured_output", None)
    if so is not None:
        import json as _json
        return _json.dumps(so, ensure_ascii=False)
    return getattr(result, "text", "") or ""


async def judge_chain_verdict(
    candidate: CandidateChain,
    *,
    llm_client: Callable[..., Awaitable[str]] | None = None,
    verdict_agent: Callable[..., Awaitable] | None = None,
    agent_name: str | None = None,
) -> ChainVerdict:
    """Judge one candidate chain -> verdict（spec 2026-08-27 §3 双形态）。

    - ``llm_client``：单次轻量路径（async (prompt, output_format=...) -> str），
      历史契约，测试 / 降级场景。
    - ``verdict_agent``：多轮 agent 路径（async (prompt, *, output_format,
      agent_name) -> ClaudeRunResult-like），生产主线——agent 自主 grep/read
      验证链快照后判定；structured_output 优先、text 兜底、success=False 走
      保守 unadjudicated。
    - ``agent_name``：多轮调用记账名（chain-verdict-{vc}-{i:02d}，防
      metrics.agents 同名覆盖，对齐 22269e4a）。

    Graceful on failure: never crash; return a conservative vulnerable
    verdict with confidence="unadjudicated" (spec 2026-08-26 §5.7 — the verdict
    CHANNEL failed, which is a different statement than the LLM judging with
    low confidence) so the merger still processes it (Plan 3 OR is
    conservative).
    """
    template = _VERDICT_PROMPT_AGENT if verdict_agent is not None else _VERDICT_PROMPT
    prompt = template.format(
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
        title_directive=_TITLE_DIRECTIVE[current_lang()],
    )

    try:
        if verdict_agent is not None:
            raw = await _resolve_agent_raw(
                verdict_agent, prompt, agent_name=agent_name)
        else:
            if llm_client is None:
                raise ValueError(
                    "judge_chain_verdict requires llm_client or verdict_agent")
            raw = await llm_client(prompt, output_format=CHAIN_VERDICT_SCHEMA)
    except Exception as exc:
        logger.warning("chain-verdict LLM pass failed (%s); marking unadjudicated", exc)
        return ChainVerdict(
            verdict="vulnerable",  # conservative: OR-friendly (do not silently clear)
            witness_payload=None,
            evidence_chain=f"{candidate.source_param} -> {candidate.sink_call_site_id} (llm-pass-failed, needs_review)",
            mismatch_reason="llm chain-verdict pass failed; needs human/LLM-track review",
            # spec 2026-08-26 §5.7：判定通道失败 ≠ LLM 判了且低置信——confidence 用
            # "unadjudicated" 显式化，不再用 low 冒充已判定（渲染层显示「未判定」）。
            confidence="unadjudicated",
            title=_fallback_title(candidate),
        )

    data = _parse_verdict_json(raw)
    if data is None:
        # retry 与首call同通道（agent 路径走 agent，单次路径走 llm_client）
        async def _retry_caller(p: str, **kw) -> str:
            if verdict_agent is not None:
                return await _resolve_agent_raw(
                    verdict_agent, p, agent_name=agent_name)
            return await llm_client(p, output_format=kw.get("output_format"))
        data = await _retry_verdict_parse(
            prompt, raw if isinstance(raw, str) else "", llm_client=_retry_caller)
    if data is None:
        # 空输出与非法输出分流：诊断时区分「调用层失败」与「模型不合规」。
        final_raw = raw if isinstance(raw, str) else ""
        if not final_raw.strip():
            reason = ("llm chain-verdict pass returned empty output "
                      "after all attempts; needs review")
        else:
            reason = ("llm chain-verdict pass returned unparseable output "
                      "after all attempts; needs review")
        logger.warning("chain-verdict LLM output unparseable after retries: %r",
                       final_raw[:200])
        return ChainVerdict(
            verdict="vulnerable",
            witness_payload=None,
            evidence_chain=f"{candidate.source_param} -> {candidate.sink_call_site_id} (unparseable-llm, needs_review)",
            mismatch_reason=reason,
            # 同上（spec 2026-08-26 §5.7）：unparseable 降级属判定通道失败。
            confidence="unadjudicated",
            title=_fallback_title(candidate),
        )

    title = data.get("title")
    return ChainVerdict(
        verdict=str(data.get("verdict", "safe")).strip().lower(),
        witness_payload=data.get("witness_payload"),
        evidence_chain=str(data.get("evidence_chain")
                           or f"{candidate.source_param} -> {candidate.sink_call_site_id}"),
        mismatch_reason=data.get("mismatch_reason"),
        confidence=str(data.get("confidence", "medium")).strip().lower(),
        title=str(title).strip() if isinstance(title, str) and title.strip() else None,
        source_param_location=_norm_location(data.get("source_param_location")),
    )
