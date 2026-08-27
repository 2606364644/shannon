"""LLM sink discovery for GitNexus track (spec §3.1, 方案 A 半 sink 精准).

规则库没命中的可疑 call(callee/receiver 命中 sink-ish 模式)→ 送 LLM 判定 →
软 SinkCallSite(rule_id="llm-discovered")。与 detect_sinks 独立遍历, 复用
parser.iter_calls / destructure_call / extract_arg_expressions, 接受双遍历
开销换 detect_sinks 零改动。LLM 不可用时 discover_sinks_llm 返回空(降级)。
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from supernova_core.code_index.parameter_models import (
    DangerousSlot,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from supernova_core.code_index.sink_detector import (
    _RULE_INDEX,
    _make_id,
    _rule_matches,
    is_entry_hint,
)
from supernova_core.code_index._rule_loader import DATA_DIR, load_yaml
from supernova_core.code_index.llm_concurrency import (
    FileChunk,
    chunk_items_by_file,
    map_llm_with_bounds,
)
from supernova_core.code_index.progress import ProgressCb, ProgressEmitter
from supernova_core.agents.model_caps import get_chunk_token_threshold
from supernova_core.agents.llm_json import _extract_json_payload

# Structured output schema：LLM sink 判定器（discover_sinks_llm），JSON array 根。
# 经 run_claude_prompt output_format 通道强制模型吐合法 JSON，省去事后 extract 的不确定性。
_SINK_VERDICT_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "call_ref": {"type": "string"},
            "is_sink": {"type": "boolean"},
            "category": {"type": "string"},
            "slot": {"type": "string"},
            "arg_index": {"type": "integer"},
            "rationale": {"type": "string"},
        },
        "required": ["call_ref", "is_sink"],
    },
}

# Structured output schema：LLM sink hunter（discover_sinks_by_entry），JSON array 根。
_SINK_HUNTER_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "sink": {"type": "string"},
            "category": {"type": "string"},
            "slot": {"type": "string"},
            "dangerous_arg": {"type": "string"},
            "line": {"type": "integer"},
            "is_sink": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["sink", "is_sink"],
    },
}
from supernova_core.config.concurrency import (
    get_chunk_max_calls,
    get_max_concurrent,
    get_per_call_timeout,
)

if TYPE_CHECKING:
    from supernova_core.code_index.models import FuncBlock
    from supernova_core.code_index.parsers.base import BaseParser

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
    - context_patterns(deepsec §3 吸收):可选;调用周边文本窗口须含其一(子串,lower)。
      用于收窄「callee 太常见、危险取决于上下文」的组(如 NoSQL $where/$regex)。
    - arg_patterns:可选;某参数位表达式须命中其一(子串,lower)。收窄 path 拼接等。
    - exclude_patterns:可选;周边文本命中任一即排除(降噪)。
    """
    languages: tuple[str, ...]
    callees: tuple[str, ...]
    receivers_any: tuple[str, ...] | None
    context_patterns: tuple[re.Pattern, ...] = ()
    arg_patterns: tuple[re.Pattern, ...] = ()
    exclude_patterns: tuple[re.Pattern, ...] = ()


# go/java 导出方法首字母大写是语义 → case-sensitive;其余语言不敏感。
_CASE_SENSITIVE_LANGS = frozenset({"go", "java"})


def _compile_patterns(items) -> tuple[re.Pattern, ...]:
    """YAML 字符串列表 → 编译正则元组(IGNORECASE+多行,子串匹配)。空/缺省 → ()。"""
    if not items:
        return ()
    return tuple(re.compile(p, re.IGNORECASE) for p in items)


def _build_sink_candidates(raw: dict) -> tuple[SinkCandidateGroup, ...]:
    """YAML dict → tuple[SinkCandidateGroup]。"""
    groups: list[SinkCandidateGroup] = []
    for item in raw.get("candidates", []):
        groups.append(SinkCandidateGroup(
            languages=tuple(item.get("languages") or ()),
            callees=tuple(item.get("callees") or ()),
            receivers_any=tuple(item["receivers_any"]) if item.get("receivers_any") else None,
            context_patterns=_compile_patterns(item.get("context_patterns")),
            arg_patterns=_compile_patterns(item.get("arg_patterns")),
            exclude_patterns=_compile_patterns(item.get("exclude_patterns")),
        ))
    return tuple(groups)


# Sink 候选模式表(外部化:data/sink_candidates.yml)。
_SINK_CANDIDATES: tuple[SinkCandidateGroup, ...] = _build_sink_candidates(
    load_yaml(DATA_DIR / "sink_candidates.yml"))


def _matches_candidate(
    language: str,
    callee: str,
    receiver: str | None,
    arg_exprs: list[str] | None = None,
    context: str | None = None,
) -> bool:
    """按语言查候选表:callee 精确比较(语言决定大小写)+ receiver 精确集合约束
    + 可选 context_patterns/arg_patterns/exclude_patterns 收窄。

    - go/java:case-sensitive(大写导出方法)。
    - 其余:case-insensitive(lower() 后比较)。
    - receivers_any 省略 → 任意 receiver 命中(含裸);给定 → receiver 必须 ∈ 集合,
      receiver=None 不命中(收窄,防裸调用误触发,如裸 format/open)。
    - context_patterns 给定 → context 窗口须含其一(防 NoSQL 海量误报);
      arg_patterns 给定 → 某 arg_exprs 须命中其一;
      exclude_patterns 给定 → context 命中任一即排除。
    旧调用点(只传 language/callee/receiver)向后兼容:arg_exprs/context 为 None 时
    跳过对应收窄检查,行为同改前。
    """
    case_sensitive = language in _CASE_SENSITIVE_LANGS
    cmp_callee = callee if case_sensitive else callee.lower()
    for g in _SINK_CANDIDATES:
        if language not in g.languages:
            continue
        target = g.callees if case_sensitive else tuple(c.lower() for c in g.callees)
        if cmp_callee not in target:
            continue
        if g.receivers_any is not None:
            if receiver is None or receiver not in g.receivers_any:
                continue
        # context_patterns 收窄(需 context 窗口)
        if g.context_patterns and context is not None:
            if not any(p.search(context) for p in g.context_patterns):
                continue
        elif g.context_patterns and context is None:
            # 候选组要求 context 但调用点没传 → 保守放行(不误杀,交 LLM 判)
            pass
        # arg_patterns 收窄(需 arg_exprs)
        if g.arg_patterns and arg_exprs:
            if not any(p.search(a) for a in arg_exprs for p in g.arg_patterns):
                continue
        # exclude_patterns 排除
        if g.exclude_patterns and context is not None:
            if any(p.search(context) for p in g.exclude_patterns):
                continue
        return True
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


@dataclass(frozen=True)
class SinkHunterCandidate:
    """entry handler 函数,待 LLM 探测器自由找 sink(对称 source 的 SourceCandidate)。

    与 collect_suspicious_calls(候选表筛选→判定器)互补:本候选不依赖候选表,
    LLM 在 entry handler 源码内自由识别框架特有 sink(fastjson.parseObject 等)。
    """
    block: "FuncBlock"


def collect_entry_handler_blocks(
    blocks: "list[FuncBlock]",
    *,
    entry_point_ids: "set[str]",
    sink_func_ids: "set[str]",
) -> list[SinkHunterCandidate]:
    """收集 entry handler 中**已有 sink(规则+判定器软 sink)之外**的函数,送 LLM 探测器。

    排除 sink_func_ids 中的函数(规则/判定器已覆盖,避免重复);只留 entry handler。
    对称 collect_source_candidates 的收集职责,但目标是 sink 探测(整函数送 LLM)。
    """
    out: list[SinkHunterCandidate] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        if block.id in sink_func_ids:
            continue  # 已有 sink,规则路径覆盖
        out.append(SinkHunterCandidate(block=block))
    return out


def _is_rule_hit(language: str, callee: str, receiver: str | None) -> bool:
    """该 call 是否已被 detect_sinks 规则库命中(避免与规则 sink 重复)。"""
    candidates = _RULE_INDEX.get((language, callee), [])
    return any(_rule_matches(rule, receiver) for rule in candidates)


def _byte_offset(text: str, line: int) -> int:
    """1-based line number → 该行起始的 char offset(用于取 context 窗口)。

    行号超出范围时返回 len(text)(保守,窗口退化为尾部)。
    """
    if line <= 1:
        return 0
    pos = 0
    for _ in range(line - 1):
        nl = text.find("\n", pos)
        if nl == -1:
            return len(text)
        pos = nl + 1
    return pos


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
        text = source.decode("utf-8", errors="replace")
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
            try:
                arg_exprs = parser.extract_arg_expressions(call, source)
            except Exception:
                arg_exprs = []
            # context 窗口:call 附近 ±256 字符(deepsec §3 context_patterns 收窄用)
            ctx_start = max(0, _byte_offset(text, call.line) - 256)
            ctx_end = min(len(text), _byte_offset(text, call.line) + 256)
            context = text[ctx_start:ctx_end]
            if not _matches_candidate(block.language, callee, receiver,
                                      arg_exprs=arg_exprs, context=context):
                continue
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

## File(s)
{file_paths}

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

    跨文件合并后(spec 2026-07-10)一个 chunk 可含多文件: {file_paths} join 全部文件
    (如 "a.go, b.go"); 各函数仍 per-block 标注 (file_path:line), 判定语义不变。
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
    call_lines = _call_lines(chunk)
    return _DISCOVERY_PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        functions_repr="\n\n".join(func_parts),
        suspicious_repr="\n".join(call_lines),
    )


# 多轮 agent 版 prompt（spec 2026-08-27 §5）：瘦身——只给 call 清单与函数定位，
# 不塞源码快照；agent 自主 Read 源码、跨文件追 callee 定义与框架包装后判定。
_DISCOVERY_AGENT_PROMPT_TMPL = """You are a multi-turn security sink-discovery agent for the GitNexus track.
You are given suspicious calls (callee/receiver that look sink-ish but were NOT matched
by the deterministic rule library). VERIFY each call in the actual repository, then
judge whether it is a real security sink. The repo is your working directory.

## File(s)
{file_paths}

## Suspicious calls (judge each by call_ref)
{suspicious_repr}

## Verification protocol (use your tools)
1. Read the file/line around each call: see how the call is built and what flows in.
2. If the callee is a local/project function or framework wrapper, Grep/Read its
   definition — judge the underlying operation (raw SQL? command exec? file write?
   template render? outbound fetch?), not just the name.
3. Judge each call on its own merits.

## Task
For EACH call above, return a JSON array. One object per call:
{{"call_ref": "<callee>:<line>", "is_sink": true|false, "category": "sql|command|file|template|deserialization|ssrf|xss|redirect|log", "slot": "sql_value|sql_identifier|cmd_argument|file_path|template_expr|url|deserialize|generic", "arg_index": <0-based int or -1>, "rationale": "<one line citing what you read>"}}
Return ONLY the JSON array, no prose."""


def _call_lines(chunk: FileChunk) -> list[str]:
    call_lines: list[str] = []
    for sc in chunk.items:
        target = sc.callee if sc.receiver is None else f"{sc.receiver}.{sc.callee}"
        call_lines.append(f"- call_ref: {sc.callee}:{sc.line}  call: {target}  args: {sc.arg_exprs}")
    return call_lines


def _build_discovery_agent_prompt(chunk: FileChunk) -> str:
    """agent 版 prompt：call 清单（含函数定位提示）无源码快照——agent 自己 Read。"""
    locs = [f"- {b.function_name} ({b.file_path}:{b.start_line}-{b.end_line})"
            for b in chunk.blocks]
    return _DISCOVERY_AGENT_PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        suspicious_repr="\n".join(_call_lines(chunk) + ["", "Function locations:"] + locs),
    )


def _parse_verdicts(raw: str) -> list[dict]:
    payload = _extract_json_payload(raw) if isinstance(raw, str) else None
    try:
        if payload is None:
            raise json.JSONDecodeError("no JSON payload found", raw or "", 0)
        data = json.loads(payload)
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
    discovery_agent=None,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
    token_threshold: int | None = None,
    model: str | None = None,
    max_calls: int | None = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """对含可疑 call 的函数并发调 LLM, 判定哪些是真 sink → 软 SinkCallSite + RuleGap。

    判定通道双形态（spec 2026-08-27 §5）：
    - ``discovery_agent``：多轮 agent（生产主线）——每 chunk 一个 agent，prompt 只给
      call 清单（瘦身），agent 自主 Read 源码 / Grep 追 callee 定义后判定；
      agent_name=gn-discovery-sink-NNN（记账唯一名）。
    - ``llm_client``：单次路径（历史契约，chunk 全源码快照），测试 / 兼容。

    调用粒度 = **文件级**(spec 2026-07-10 §3.1): 同文件所有可疑 call → 一个 chunk →
    一次判定(大幅减调用次数: N 函数 → 文件 chunk 数)。大文件按 token 贪心拆 chunk
    (token_threshold, 默认由 get_chunk_token_threshold(model) 派生: 按当前模型 context
    自适应, glm-5.2 走 750K / 默认 96K)防 prompt 爆 LLM context。

    LLM 不可用(None / raise / 超时 / 不可解析)→ 该 chunk 跳过, 返回空(降级, spec §3.4)。
    并发由 concurrency(Semaphore)限, 默认 get_max_concurrent()(SUPERNOVA_MAX_CONCURRENT);
    单次调用超过 per_call_timeout(默认 DEFAULT_DISCOVERY_PER_CALL_TIMEOUT=120s, 文件级
    prompt 更重; 显式传值优先)→ 该 chunk 降级跳过。大仓 N chunk 并发跑, 防串行累加
    拖垮 activity 的 start_to_close_timeout。

    progress_cb: best-effort 进度上报(每 chunk 一 tick + 一次 finalize 汇总);
    cb=None 全程 no-op。
    """
    if (llm_client is None and discovery_agent is None) or not suspicious:
        return [], []
    effective_threshold = (token_threshold if token_threshold is not None
                           else get_chunk_token_threshold(model))
    effective_max_calls = (max_calls if max_calls is not None
                           else get_chunk_max_calls())
    chunks: list[FileChunk] = chunk_items_by_file(
        suspicious,
        block_of=lambda sc: sc.block,
        token_threshold=effective_threshold,
        max_calls=effective_max_calls,
    )

    emitter = ProgressEmitter("sink-discovery", len(chunks), progress_cb)

    async def _discover_one(item) -> list[SinkCallSite]:
        idx, chunk = item
        if discovery_agent is not None:
            result = await discovery_agent(
                _build_discovery_agent_prompt(chunk),
                output_format=_SINK_VERDICT_SCHEMA,
                agent_name=f"gn-discovery-sink-{idx + 1:03d}")
            if getattr(result, "success", True) is False:
                raise RuntimeError(
                    f"discovery agent failed: {getattr(result, 'error', None)}")
            so = getattr(result, "structured_output", None)
            raw = json.dumps(so, ensure_ascii=False) if so is not None else (
                getattr(result, "text", "") or "")
        else:
            prompt = _build_discovery_prompt(chunk)
            raw = await llm_client(prompt, output_format=_SINK_VERDICT_SCHEMA)
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
    # spec §3.2: SUPERNOVA_LLM_PER_CALL_TIMEOUT env 可覆盖; 文件级 prompt 更重故取
    # max(env, 120) —— env 未设(60)→120、env=200→200, 显式传值仍优先。旧版恒 120
    # 绕过 env(违反 spec「均可经 env 覆盖」+ concurrency.py docstring), 已修。
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(), DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))
    if discovery_agent is not None:
        # 多轮 agent 超时地板（spec 2026-08-27 §5）：单次档 60/120s 对自主
        # Read/Grep 的多轮形态不够，取 max(现值, agent 地板默认 300s)。
        from supernova_core.config.concurrency import get_gn_discovery_agent_timeout
        effective_timeout = max(effective_timeout, get_gn_discovery_agent_timeout())

    async def _on_skip(idx, message):
        # idx → 文件路径: 跨文件合并后诊断单位是 chunk(可多文件); file_paths join 标注。
        # 经 emitter.note 走 progress_cb → GitnexusLlmEvent note 行 → dispatcher →
        # Rich Live 协调正确换行。
        chunk = chunks[idx]
        await emitter.note(f"{', '.join(chunk.file_paths)}: {message}")

    per_chunk = await map_llm_with_bounds(
        list(enumerate(chunks)), _discover_one,
        concurrency=conc, per_call_timeout=effective_timeout, label="discover_sinks_llm",
        on_skip=_on_skip,
    )
    soft_sinks: list[SinkCallSite] = [s for chunk_sinks in per_chunk for s in chunk_sinks]
    skipped = len(chunks) - len(per_chunk)   # 超时/失败被 map_llm_with_bounds 丢弃
    await emitter.finalize(
        f"{len(soft_sinks)} soft sinks · {len(_aggregate_gaps(soft_sinks))} rule gaps"
        f" · {skipped} timeouts")
    return soft_sinks, _aggregate_gaps(soft_sinks)


# === 子项③: sink 探测器(entry-driven, 对称 source 探测器)=====================

_SINK_HUNTER_PROMPT_TMPL = """You are a security sink detector for the GitNexus track.
Given a FILE with one or more entry handler functions, identify ALL security sinks
WITHIN each function. Rule-based detection already covered common sinks (raw SQL
execute, Runtime.exec, ObjectInputStream.readObject, HttpClient.send); you handle
the unconventional ones — framework-specific deserialization (fastjson
JSON.parseObject, Jackson enableDefaultTyping), custom URL builders followed by
HTTP execute, template engines, reflection.

## File(s)
{file_paths}

## Functions
{functions_repr}

## Task
Return a JSON array. One object per sink found (omit functions with no sink):
{{"sink":"<call expression>","category":"sql|command|file|template|deserialization|ssrf|xss|redirect|log","slot":"sql_value|sql_identifier|cmd_argument|file_path|template_expr|url|deserialize|generic","dangerous_arg":"<expression reaching the sink>","line":<int>,"is_sink":true,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. `line` is the FILE-absolute line number of the sink call."""


def _build_sink_hunter_prompt(chunk: "FileChunk") -> str:
    func_parts: list[str] = []
    for b in chunk.blocks:
        func_parts.append(
            f"### {b.function_name} ({b.file_path}:{b.start_line})\n"
            f"Parameters: {list(b.parameters)}\n"
            f"```\n{b.source_code}\n```"
        )
    return _SINK_HUNTER_PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        functions_repr="\n\n".join(func_parts),
    )


def _parse_sink_verdicts(raw: str) -> list[dict]:
    payload = _extract_json_payload(raw) if isinstance(raw, str) else None
    try:
        if payload is None:
            raise json.JSONDecodeError("no JSON payload found", raw or "", 0)
        data = json.loads(payload)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("discover_sinks_by_entry: failed to parse LLM JSON: %s", raw[:120])
        return []


def _resolve_block_for_line(chunk: "FileChunk", line: int | None):
    """verdict.line 反查所属 block(文件级 chunk 含多函数)。line 缺失/越界 → 首个 block。

    对称 source_discovery_llm._resolve_block_for_line: 用 parser 已填充的 end_line
    判界 b.start_line <= line <= b.end_line(而非 source_code.count(b"\\n") —— 后者
    一则在 str 字段上调 bytes pattern 会 TypeError, 二则 end_line 是 parser 权威产物)。
    """
    if line is None:
        return chunk.blocks[0]
    for b in chunk.blocks:
        if b.start_line <= line <= b.end_line:
            return b
    return chunk.blocks[0]


# category → slot 兜底映射(hunter 探测器 LLM 漏 slot 时按 category 派生, C1 修复 B)。
# 对称 judge 路径:_to_soft_sink 直读 verdict.slot(判定器 prompt 必给 slot);
# hunter LLM 可能漏 slot —— 此时按 category 派生保证路由正确(deserialization→deserialize
# 过 _INJECTION_SLOTS, 否则恒 GENERIC 被 extract_candidate_chains 滤掉)。
_CATEGORY_TO_SLOT: dict[str, "SlotContext"] = {
    "sql": SlotContext.SQL_VALUE,
    "command": SlotContext.CMD_ARGUMENT,
    "file": SlotContext.FILE_PATH,
    "template": SlotContext.TEMPLATE_EXPR,
    "ssrf": SlotContext.URL,
    "deserialization": SlotContext.DESERIALIZE_OBJ,
}


def _to_hunter_sink(block: "FuncBlock", field: dict) -> SinkCallSite:
    category = _to_category(field.get("category", "sql"))
    expr = field.get("dangerous_arg") or field.get("sink", "")
    line = int(field.get("line") or block.start_line)
    # slot 解析优先级(C1 修复 B): LLM 显式给 slot(且非 generic)> category 派生 > GENERIC。
    # 即便 LLM 漏 slot, fastjson.parseObject(category=deserialization)也能落到
    # DESERIALIZE_OBJ("deserialize")→ 过 _INJECTION_SLOTS 路由进 injection queue。
    raw_slot = field.get("slot")
    if raw_slot and raw_slot != "generic":
        slot = _to_slot(raw_slot)
    else:
        slot = _CATEGORY_TO_SLOT.get(category.value, SlotContext.GENERIC)
    return SinkCallSite(
        id=f"llm:{block.file_path}:{line}",
        caller_id=block.id,
        callee_name=field.get("sink", ""),
        callee_receiver=None,
        category=category,
        sink_subtype=field.get("subtype") or category.value,
        file_path=block.file_path,
        line=line,
        column=0,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=slot,
            expression=expr, is_entry_hint=is_entry_hint(expr, block),
        )],
        rule_id="llm-discovered-sink",
        needs_review=True,
    )


async def discover_sinks_by_entry(
    candidates: list[SinkHunterCandidate],
    llm_client: "LLMClient | None",
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: "ProgressCb" = None,
    token_threshold: int | None = None,
    model: str | None = None,
    max_calls: int | None = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """entry-driven sink 探测器(对称 discover_sources_llm): 对 entry handler 整函数
    送 LLM,自由识别 sink → 软 SinkCallSite + RuleGap。

    与 discover_sinks_llm(候选表筛选→判定器)互补: 覆盖候选表外的框架特有 sink
    (fastjson.parseObject / ClassPathResource.createRelative / 自研 executeCommand)。
    LLM 不可用/超时/不可解析 → 该 chunk 跳过返回空(降级, 守 GitNexus 确定性兜底)。
    """
    if llm_client is None or not candidates:
        return [], []
    effective_threshold = (token_threshold if token_threshold is not None
                           else get_chunk_token_threshold(model))
    effective_max_calls = (max_calls if max_calls is not None
                           else get_chunk_max_calls())
    chunks: list[FileChunk] = chunk_items_by_file(
        candidates,
        block_of=lambda c: c.block,
        token_threshold=effective_threshold,
        max_calls=effective_max_calls,
    )
    emitter = ProgressEmitter("sink-hunter", len(chunks), progress_cb)

    async def _hunt_one(chunk: "FileChunk"):
        prompt = _build_sink_hunter_prompt(chunk)
        raw = await llm_client(prompt, output_format=_SINK_HUNTER_SCHEMA)
        verdicts = _parse_sink_verdicts(raw)
        out: list[SinkCallSite] = []
        for v in verdicts:
            if v.get("is_sink") is not True:
                continue
            try:
                block = _resolve_block_for_line(chunk, v.get("line"))
                out.append(_to_hunter_sink(block, v))
            except Exception:
                logger.debug("discover_sinks_by_entry: skip malformed verdict", exc_info=True)
                continue
        await emitter.tick(detail=out[0].callee_name if out else None, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(), DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))

    async def _on_skip(idx, message):
        chunk = chunks[idx]
        await emitter.note(f"{', '.join(chunk.file_paths)}: {message}")

    per_chunk = await map_llm_with_bounds(
        chunks, _hunt_one,
        concurrency=conc, per_call_timeout=effective_timeout, label="discover_sinks_by_entry",
        on_skip=_on_skip,
    )
    all_sinks = [s for chunk_sinks in per_chunk for s in chunk_sinks]
    gaps = _aggregate_gaps(all_sinks)
    await emitter.finalize(f"{len(all_sinks)} sinks · {len(gaps)} gaps")
    return all_sinks, gaps
