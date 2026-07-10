"""LLM sink discovery for GitNexus track (spec §3.1, 方案 A 半 sink 精准).

规则库没命中的可疑 call(callee/receiver 命中 sink-ish 模式)→ 送 LLM 判定 →
软 SinkCallSite(rule_id="llm-discovered")。与 detect_sinks 独立遍历, 复用
parser.iter_calls / destructure_call / extract_arg_expressions, 接受双遍历
开销换 detect_sinks 零改动。LLM 不可用时 discover_sinks_llm 返回空(降级)。
"""
import json
import logging
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
from shannon_core.code_index._rule_loader import DATA_DIR, load_yaml
from shannon_core.code_index.llm_concurrency import (
    FileChunk,
    chunk_items_by_file,
    map_llm_with_bounds,
)
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
from shannon_core.agents.model_caps import get_chunk_token_threshold
from shannon_core.config.concurrency import get_max_concurrent, get_per_call_timeout

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]

# 文件级 prompt 更重(per-function → 文件级聚合后单次 prompt 含多函数), 单次响应更慢
# → 默认 120s(局部覆盖, 不动 concurrency 全局 60s; taint 仍 60s 够; spec §3.2)。
DEFAULT_DISCOVERY_PER_CALL_TIMEOUT = 120.0


@dataclass(frozen=True)
class SinkCandidateGroup:
    """一组 sink 候选模式(替换旧版 flat 子串正则;详见 data/sink_candidates.yml 顶部说明)。

    命中(且规则库未命中)→ 送轻量 LLM 判定。只决定「要不要送 LLM」,不产 SinkCallSite。
    - callees:精确 callee 名(loader 不动大小写;匹配时按 language 决定大小写策略)。
    - receivers_any:None = 任意 receiver 命中(含裸调用);tuple = receiver 必须 ∈ 集合。
    """
    languages: tuple[str, ...]
    callees: tuple[str, ...]
    receivers_any: tuple[str, ...] | None


# go/java 导出方法首字母大写是语义 → case-sensitive;其余语言不敏感。
_CASE_SENSITIVE_LANGS = frozenset({"go", "java"})


def _build_sink_candidates(raw: dict) -> tuple[SinkCandidateGroup, ...]:
    """YAML dict → tuple[SinkCandidateGroup]。"""
    groups: list[SinkCandidateGroup] = []
    for item in raw.get("candidates", []):
        groups.append(SinkCandidateGroup(
            languages=tuple(item.get("languages") or ()),
            callees=tuple(item.get("callees") or ()),
            receivers_any=tuple(item["receivers_any"]) if item.get("receivers_any") else None,
        ))
    return tuple(groups)


# Sink 候选模式表(外部化:data/sink_candidates.yml)。
_SINK_CANDIDATES: tuple[SinkCandidateGroup, ...] = _build_sink_candidates(
    load_yaml(DATA_DIR / "sink_candidates.yml"))


def _matches_candidate(language: str, callee: str, receiver: str | None) -> bool:
    """按语言查候选表:callee 精确比较(语言决定大小写)+ receiver 精确集合约束。

    - go/java:case-sensitive(大写导出方法)。
    - 其余:case-insensitive(lower() 后比较)。
    - receivers_any 省略 → 任意 receiver 命中(含裸);给定 → receiver 必须 ∈ 集合,
      receiver=None 不命中(收窄,防裸调用误触发,如裸 format/open)。
    """
    case_sensitive = language in _CASE_SENSITIVE_LANGS
    cmp_callee = callee if case_sensitive else callee.lower()
    for g in _SINK_CANDIDATES:
        if language not in g.languages:
            continue
        target = g.callees if case_sensitive else tuple(c.lower() for c in g.callees)
        if cmp_callee not in target:
            continue
        if g.receivers_any is None:
            return True
        if receiver is not None and receiver in g.receivers_any:
            return True
        # receivers_any 给定但 receiver 不匹配 → 此组不命中,继续看下一组
    return False


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
            if not _matches_candidate(block.language, callee, receiver):
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
Given a FILE with one or more functions and their suspicious calls (callee/receiver
that look sink-ish but were NOT matched by the deterministic rule library), judge
whether each suspicious call is a real security sink.

## File
{file_path}

## Functions
{functions_repr}

## Suspicious calls (judge each by call_ref)
{suspicious_repr}

## Task
For EACH call above, return a JSON array. One object per call:
{{"call_ref": "<callee>:<line>", "is_sink": true|false, "category": "sql|command|file|template|deserialization|ssrf|xss|redirect|log", "slot": "sql_value|sql_identifier|cmd_argument|file_path|template_expr|url|deserialize|generic", "arg_index": <0-based int or -1>, "rationale": "<one line>"}}
Return ONLY the JSON array, no prose."""


def _build_discovery_prompt(chunk: FileChunk) -> str:
    """文件级 prompt: 该 chunk 所有可疑函数源码(去重) + 全部可疑 call 列表(spec §3.1)。

    chunk.blocks 已按 block.id 去重保序(同 block 多 call 只列一次源码); chunk.items
    是这些函数的全部可疑 call, 按 call_ref 归位。
    """
    func_parts: list[str] = []
    for b in chunk.blocks:
        func_parts.append(
            f"### {b.function_name} ({b.file_path}:{b.start_line})\n"
            f"Parameters: {list(b.parameters)}\n"
            f"```\n{b.source_code}\n```"
        )
    call_lines: list[str] = []
    for sc in chunk.items:
        target = sc.callee if sc.receiver is None else f"{sc.receiver}.{sc.callee}"
        call_lines.append(f"- call_ref: {sc.callee}:{sc.line}  call: {target}  args: {sc.arg_exprs}")
    return _DISCOVERY_PROMPT_TMPL.format(
        file_path=chunk.file_path,
        functions_repr="\n\n".join(func_parts),
        suspicious_repr="\n".join(call_lines),
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
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
    token_threshold: int | None = None,
    model: str | None = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """对含可疑 call 的函数并发调 LLM, 判定哪些是真 sink → 软 SinkCallSite + RuleGap。

    调用粒度 = **文件级**(spec 2026-07-10 §3.1): 同文件所有可疑 call → 一个 chunk →
    一次 LLM 调用(大幅减调用次数: N 函数 → 文件 chunk 数)。大文件按 token 贪心拆 chunk
    (token_threshold, 默认由 get_chunk_token_threshold(model) 派生: 按当前模型 context
    自适应, glm-5.2 走 750K / 默认 96K)防 prompt 爆 LLM context。

    LLM 不可用(None / raise / 超时 / 不可解析)→ 该 chunk 跳过, 返回空(降级, spec §3.4)。
    并发由 concurrency(Semaphore)限, 默认 get_max_concurrent()(SHANNON_MAX_CONCURRENT);
    单次调用超过 per_call_timeout(默认 DEFAULT_DISCOVERY_PER_CALL_TIMEOUT=120s, 文件级
    prompt 更重; 显式传值优先)→ 该 chunk 降级跳过。大仓 N chunk 并发跑, 防串行累加
    拖垮 activity 的 start_to_close_timeout。

    progress_cb: best-effort 进度上报(每 chunk 一 tick + 一次 finalize 汇总);
    cb=None 全程 no-op。
    """
    if llm_client is None or not suspicious:
        return [], []
    effective_threshold = (token_threshold if token_threshold is not None
                           else get_chunk_token_threshold(model))
    chunks: list[FileChunk] = chunk_items_by_file(
        suspicious,
        block_of=lambda sc: sc.block,
        token_threshold=effective_threshold,
    )

    emitter = ProgressEmitter("sink-discovery", len(chunks), progress_cb)

    async def _discover_one(chunk: FileChunk) -> list[SinkCallSite]:
        prompt = _build_discovery_prompt(chunk)
        raw = await llm_client(prompt)
        verdicts = _parse_verdicts(raw)
        vmap = {str(v.get("call_ref")): v for v in verdicts}
        out: list[SinkCallSite] = []
        for sc in chunk.items:
            v = vmap.get(f"{sc.callee}:{sc.line}")
            if v is None or not v.get("is_sink"):
                continue
            # per-item 防护: 单条 verdict 字段 malformed(arg_index=null 等)只跳过该
            # sink, 不让 int()/解析异常带垮整文件 chunk(spec §3.1 文件级容错, 防影响面
            # 从「丢一个函数」放大到「丢整文件」)。
            try:
                out.append(_to_soft_sink(sc, v))
            except Exception:
                logger.debug("discover_sinks_llm: skip malformed verdict %s:%s",
                             sc.callee, sc.line, exc_info=True)
                continue
        detail = None
        if out:
            s0 = out[0]
            slot = (s0.dangerous_slots[0].slot.value
                    if s0.dangerous_slots else "generic")
            detail = f"'{s0.callee_name}' @ {s0.file_path}:{s0.line} slot={slot}"
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    # spec §3.2: SHANNON_LLM_PER_CALL_TIMEOUT env 可覆盖; 文件级 prompt 更重故取
    # max(env, 120) —— env 未设(60)→120、env=200→200, 显式传值仍优先。旧版恒 120
    # 绕过 env(违反 spec「均可经 env 覆盖」+ concurrency.py docstring), 已修。
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(), DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))

    async def _on_skip(idx, message):
        # idx → 文件路径: 文件级聚合后诊断单位是文件 chunk; 经 emitter.note 走 progress_cb
        # → GitnexusLlmEvent note 行 → dispatcher → Rich Live 协调正确换行。
        chunk = chunks[idx]
        await emitter.note(f"{chunk.file_path}: {message}")

    per_chunk = await map_llm_with_bounds(
        chunks, _discover_one,
        concurrency=conc, per_call_timeout=effective_timeout, label="discover_sinks_llm",
        on_skip=_on_skip,
    )
    soft_sinks: list[SinkCallSite] = [s for chunk_sinks in per_chunk for s in chunk_sinks]
    skipped = len(chunks) - len(per_chunk)   # 超时/失败被 map_llm_with_bounds 丢弃
    await emitter.finalize(
        f"{len(soft_sinks)} soft sinks · {len(_aggregate_gaps(soft_sinks))} rule gaps"
        f" · {skipped} timeouts")
    return soft_sinks, _aggregate_gaps(soft_sinks)
