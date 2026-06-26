"""LLM sink discovery for GitNexus track (spec §3.1, 方案 A 半 sink 精准).

规则库没命中的可疑 call(callee/receiver 命中 sink-ish 模式)→ 送 LLM 判定 →
软 SinkCallSite(rule_id="llm-discovered")。与 detect_sinks 独立遍历, 复用
parser.iter_calls / destructure_call / extract_arg_expressions, 接受双遍历
开销换 detect_sinks 零改动。LLM 不可用时 discover_sinks_llm 返回空(降级)。
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from shannon_core.code_index.sink_detector import (
    _RULE_INDEX,
    _make_id,
    _rule_matches,
    is_entry_hint,
)

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]


# sink-ish callee/receiver 模式(spec §3.1 初稿): 比规则库宽松, 精确判定交 LLM。
_SUSPICIOUS_CALLEE_RE = re.compile(
    r"(query|exec(ute)?|render|redirect|include|require|unserialize|"
    r"pickle|loads|system|popen|raw|where|format|template|open|fetch)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SuspiciousCall:
    block: "FuncBlock"
    callee: str
    receiver: str | None
    arg_exprs: list[str]
    file_path: str
    line: int
    column: int


def _is_rule_hit(language: str, callee: str, receiver: str | None) -> bool:
    """该 call 是否已被 detect_sinks 规则库命中(避免与规则 sink 重复)。"""
    candidates = _RULE_INDEX.get((language, callee), [])
    return any(_rule_matches(rule, receiver) for rule in candidates)


def collect_suspicious_calls(
    blocks: "list[FuncBlock]",
    parser: "BaseParser",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SuspiciousCall]:
    """遍历所有函数的 call, 收集『sink-ish 但规则未命中』的可疑 call。"""
    out: list[SuspiciousCall] = []
    for block in blocks:
        source = source_provider(block)
        if source is None:
            continue
        try:
            call_nodes = list(parser.iter_calls(block, source))
        except Exception:
            logger.debug("suspicious scan: iter_calls failed for %s", block.id, exc_info=True)
            continue
        for call in call_nodes:
            try:
                callee, receiver = parser.destructure_call(call)
            except Exception:
                continue
            if not callee:
                continue
            if _is_rule_hit(block.language, callee, receiver):
                continue  # 规则已命中, detect_sinks 会产 SinkCallSite, 不重复
            target = callee if receiver is None else f"{receiver}.{callee}"
            if not _SUSPICIOUS_CALLEE_RE.search(target):
                continue
            try:
                arg_exprs = parser.extract_arg_expressions(call, source)
            except Exception:
                arg_exprs = []
            out.append(SuspiciousCall(
                block=block, callee=callee, receiver=receiver, arg_exprs=arg_exprs,
                file_path=block.file_path, line=call.line, column=call.column,
            ))
    return out


# === Task 2: LLM 补召回 → 软 SinkCallSite + RuleGap 聚合 =======================

@dataclass(frozen=True)
class RuleGap:
    """一条规则缺口 —— 聚合同模式的 LLM 软 sink, 驱动规则库迭代(spec §3.1 层 2)。"""
    pattern: str            # "{callee}@{receiver}" 或 "{callee}"
    language: str
    category: str
    slot: str
    count: int
    sample_evidence: list[str] = field(default_factory=list)


_DISCOVERY_PROMPT_TMPL = """You are a security sink classifier for the GitNexus track.
Given ONE function and its suspicious call list (callee/receiver that look sink-ish
but were NOT matched by the deterministic rule library), judge whether each is a
real security sink.

## Function
{func_name} ({file}:{line})
Parameters: {params}

## Source
```
{source}
```

## Suspicious calls (judge each by call_ref)
{suspicious_repr}

## Task
For EACH call above, return a JSON array. One object per call:
{{"call_ref": "<callee>:<line>", "is_sink": true|false, "category": "sql|command|file|template|deserialization|ssrf|xss|redirect|log", "slot": "sql_value|sql_identifier|cmd_argument|file_path|template_expr|url|deserialize|generic", "arg_index": <0-based int or -1>, "rationale": "<one line>"}}
Return ONLY the JSON array, no prose."""


def _build_discovery_prompt(block, calls: list[SuspiciousCall]) -> str:
    lines = []
    for sc in calls:
        target = sc.callee if sc.receiver is None else f"{sc.receiver}.{sc.callee}"
        lines.append(f"- call_ref: {sc.callee}:{sc.line}  call: {target}  args: {sc.arg_exprs}")
    return _DISCOVERY_PROMPT_TMPL.format(
        func_name=block.function_name, file=block.file_path, line=block.start_line,
        params=list(block.parameters), source=block.source_code,
        suspicious_repr="\n".join(lines),
    )


def _parse_verdicts(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("discover_sinks_llm: failed to parse LLM JSON: %s", raw[:120])
        return []


# category / slot 字符串 → 枚举(容错: 不认识回落到安全中性值)
def _to_category(v: str) -> SinkCategory:
    try:
        return SinkCategory(v)
    except ValueError:
        return SinkCategory.SQL  # 最常见, 回落后 needs_review 仍会过 LLM 复核


def _to_slot(v: str) -> SlotContext:
    try:
        return SlotContext(v)
    except ValueError:
        return SlotContext.GENERIC


def _to_soft_sink(sc: SuspiciousCall, verdict: dict) -> SinkCallSite:
    arg_index = int(verdict.get("arg_index", -1))
    expr = sc.arg_exprs[arg_index] if 0 <= arg_index < len(sc.arg_exprs) else (
        sc.arg_exprs[0] if sc.arg_exprs else ""
    )
    slot = _to_slot(verdict.get("slot", "generic"))
    category = _to_category(verdict.get("category", "sql"))
    # 复用 _make_id 的格式契约(Spec A: TaintFlow.sink_call_site_id 须匹配)
    sink_id = f"{sc.file_path}:{sc.block.function_name}:{sc.callee}:{sc.line}:{sc.column}"
    return SinkCallSite(
        id=sink_id,
        caller_id=sc.block.id,
        callee_name=sc.callee,
        callee_receiver=sc.receiver,
        category=category,
        sink_subtype=verdict.get("subtype") or category.value,
        file_path=sc.file_path,
        line=sc.line,
        column=sc.column,
        dangerous_slots=[DangerousSlot(
            arg_index=arg_index, slot=slot, expression=expr,
            is_entry_hint=is_entry_hint(expr, sc.block),
        )],
        rule_id="llm-discovered",
        needs_review=True,
    )


def _aggregate_gaps(soft_sinks: list[SinkCallSite]) -> list[RuleGap]:
    buckets: dict[tuple, RuleGap] = {}
    for s in soft_sinks:
        pattern = s.callee_name if s.callee_receiver is None else f"{s.callee_name}@{s.callee_receiver}"
        slot = s.dangerous_slots[0].slot.value if s.dangerous_slots else "generic"
        # language 不在 SinkCallSite 上; 留空, 由调用方补(spec: gap 报告语言维度可选)
        key = (pattern, s.category.value, slot)
        evidence = f"{s.file_path}:{s.line}  {s.callee_name}"
        if key in buckets:
            b = buckets[key]
            buckets[key] = RuleGap(
                pattern=b.pattern, language=b.language, category=b.category,
                slot=b.slot, count=b.count + 1,
                sample_evidence=(b.sample_evidence + [evidence])[:5],
            )
        else:
            buckets[key] = RuleGap(
                pattern=pattern, language="", category=s.category.value,
                slot=slot, count=1, sample_evidence=[evidence],
            )
    return list(buckets.values())


async def discover_sinks_llm(
    suspicious: list[SuspiciousCall],
    llm_client: LLMClient | None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """对含可疑 call 的函数逐个调 LLM, 判定哪些是真 sink → 软 SinkCallSite + RuleGap。

    LLM 不可用(None / raise / 不可解析)→ 该函数跳过, 返回空(降级, spec §3.5)。
    调用粒度 = function 级(去重分组, 一函数一次 LLM 调用)。
    """
    if llm_client is None or not suspicious:
        return [], []
    by_func: dict[str, list[SuspiciousCall]] = defaultdict(list)
    for sc in suspicious:
        by_func[sc.block.id].append(sc)

    soft_sinks: list[SinkCallSite] = []
    for func_id, calls in by_func.items():
        block = calls[0].block
        prompt = _build_discovery_prompt(block, calls)
        try:
            raw = await llm_client(prompt)
        except Exception as exc:
            logger.warning("discover_sinks_llm failed for %s: %s", func_id, exc)
            continue  # 降级: 该函数跳过
        verdicts = _parse_verdicts(raw)
        # 按 call_ref(callee:line) 对回 SuspiciousCall
        vmap = {str(v.get("call_ref")): v for v in verdicts}
        for sc in calls:
            v = vmap.get(f"{sc.callee}:{sc.line}")
            if v is None or not v.get("is_sink"):
                continue
            soft_sinks.append(_to_soft_sink(sc, v))
    return soft_sinks, _aggregate_gaps(soft_sinks)
