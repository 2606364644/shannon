# packages/core/src/shannon_core/code_index/source_discovery_llm.py
"""LLM source 补召回(平行 sink_discovery_llm)。

规则未命中的 entry handler(非常规框架/解构)→ 送 LLM 判定 → 软 SourcePoint
(rule_id="llm-discovered")。LLM 不可用 → 返回空(降级,守"GitNexus 轨确定性兜底")。
复用 map_llm_with_bounds 并发骨架(对齐 discover_sinks_llm)。
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.source_detector import (
    DEFAULT_SOURCE_RULES, _dedup, _detect_validation, _line_of,
)
from shannon_core.code_index.llm_concurrency import (
    FileChunk,
    chunk_items_by_file,
    map_llm_with_bounds,
)
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
from shannon_core.agents.model_caps import get_chunk_token_threshold
from shannon_core.config.concurrency import (
    get_chunk_max_calls,
    get_max_concurrent,
    get_per_call_timeout,
)

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]

# 文件级 prompt 更重(per-function → 文件级聚合后单次 prompt 含多函数), 单次响应更慢
# → 默认 120s(局部覆盖, 不动 concurrency 全局 60s; spec 2026-07-10 §3.2)。
DEFAULT_DISCOVERY_PER_CALL_TIMEOUT = 120.0


@dataclass(frozen=True)
class SourceCandidate:
    """规则未命中的 entry handler,待 LLM 判定其可控字段。"""
    block: "FuncBlock"


def discover_sources_by_rules(
    blocks: "list[FuncBlock]",
    sink_func_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourcePoint]:
    """规则路径(spec §3.1):对**含 sink 函数**跑 DEFAULT_SOURCE_RULES(扩范围;
    source_detector 只扫 entry_point)→ 命中产 SourcePoint(rule_id=正常规则 id,
    entry_point_id=该函数 id)。

    source_detector 主路径不变(扫 entry_point,守"source 不被 sink 驱动"设计);
    本函数是被 sink 驱动的独立兜底,只对含 sink 函数补 —— handler 不在 entry_point
    也能补回 req.body.preTax 等(NodeGoat 根因 spec §2)。复用 source_detector 的
    _line_of/_detect_validation/_dedup,规则匹配口径与主路径一致。
    """
    out: list[SourcePoint] = []
    for block in blocks:
        if block.id not in sink_func_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        for rule in DEFAULT_SOURCE_RULES:
            if block.language not in rule.languages:
                continue
            for m in rule.pattern.finditer(text):
                param_name = m.group(1)
                rel_line = _line_of(text, m.start())
                abs_line = block.start_line + rel_line - 1
                out.append(SourcePoint(
                    id=f"{block.id}::{param_name}::{abs_line}",
                    entry_point_id=block.id,
                    param_name=param_name,
                    source_type=rule.source_type,
                    expression=m.group(0),
                    file_path=block.file_path,
                    line=abs_line,
                    validation=_detect_validation(text, m.start()),
                    confidence=0.9,
                    rule_id=rule.rule_id,
                    needs_review=False,
                ))
    return _dedup(out)


def _has_rule_hit(language: str, text: str) -> bool:
    """该 handler 是否已被 detect_sources 规则命中(避免重复)。"""
    return any(
        block_lang in rule.languages and rule.pattern.search(text)
        for rule in DEFAULT_SOURCE_RULES
        for block_lang in (language,)
    )


# source 候选启发式:含 sink 函数中,规则未命中的"对象级取用 / 非常规写法"信号。
# 覆盖解构(const {a} = req.body)、对象直传(f(req.body))、括号取用(body[)、注解。
# 点号取用(req.body.x)规则会命中 → 由 _has_rule_hit 拦截,不会误送 LLM。
_SOURCE_CANDIDATE_HINT = re.compile(
    r"(input\.get|params\[|body\[|data\[['\"]|@RequestBody|@QueryParam|@PathVariable|"
    r"ctx\.Request|c\.Query|c\.Param|"
    r"req\.(?:body|query|params|headers|cookies)|"
    r"request\.(?:GET|POST|data|args|form|json)|"
    r"\$_(?:GET|POST|REQUEST))",
    re.IGNORECASE,
)


def collect_source_candidates(
    blocks: "list[FuncBlock]",
    sink_func_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourceCandidate]:
    """收集**含 sink 函数**中规则未命中的(候选送 LLM 判定,spec §3.1)。

    source_detector 主路径只扫 entry_point;本函数对含 sink 函数补召回 —— 函数体含
    对象级 / 非常规取用信号(解构 ``const {a} = req.body``、对象直传、括号取用、注解)
    但 detect_sources 规则未命中 → 候选。点号取用(``req.body.x``)规则会命中 → 不候选。
    """
    out: list[SourceCandidate] = []
    for block in blocks:
        if block.id not in sink_func_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        if _has_rule_hit(block.language, text):
            continue  # 规则已命中 → 规则路径覆盖,不重复送 LLM
        if _SOURCE_CANDIDATE_HINT.search(text):
            out.append(SourceCandidate(block=block))
    return out


_PROMPT_TMPL = """You are a user-input source classifier for the GitNexus track.
Given a FILE with one or more entry handler functions, identify ALL user-controllable
input fields and their HTTP source type. Rule-based detection already covered common
frameworks (Express/Django/...); you handle the unconventional ones.

## File(s)
{file_paths}

## Functions
{functions_repr}

## Task
Return a JSON array. One object per user-controllable field:
{{"field":"<param_name>","source_type":"query|path|body|form|header|cookie|file","expression":"<source-code expr>","line":<int>,"is_source":true|false,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. Omit fields that are NOT user-controllable (is_source=false).
`line` is the FILE-absolute line number of the field."""


def _build_prompt(chunk: FileChunk) -> str:
    """文件级 prompt: 该 chunk 所有候选函数源码(去重)。chunk.blocks 已去重保序。

    跨文件合并后(spec 2026-07-10)一个 chunk 可含多文件: {file_paths} join 全部文件;
    各函数仍 per-block 标注 (file_path:line), 判定语义不变。
    """
    func_parts: list[str] = []
    for b in chunk.blocks:
        func_parts.append(
            f"### {b.function_name} ({b.file_path}:{b.start_line})\n"
            f"Parameters: {list(b.parameters)}\n"
            f"```\n{b.source_code}\n```"
        )
    return _PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        functions_repr="\n\n".join(func_parts),
    )


def _parse_fields(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("discover_sources_llm: failed to parse LLM JSON: %s", raw[:120])
        return []


def _to_source_type(v: str) -> ParameterSource:
    mapping = {
        "query": ParameterSource.QUERY_PARAM, "path": ParameterSource.PATH_PARAM,
        "body": ParameterSource.BODY_FIELD, "form": ParameterSource.FORM_FIELD,
        "header": ParameterSource.HEADER, "cookie": ParameterSource.COOKIE,
        "file": ParameterSource.FILE_UPLOAD,
    }
    return mapping.get((v or "").lower(), ParameterSource.UNKNOWN)


def _to_soft_source(block, field: dict) -> SourcePoint:
    name = str(field.get("field", ""))
    line = int(field.get("line", block.start_line))
    return SourcePoint(
        id=f"{block.id}::{name}::{line}",
        entry_point_id=block.id,
        param_name=name,
        source_type=_to_source_type(field.get("source_type", "unknown")),
        expression=str(field.get("expression", "")),
        file_path=block.file_path,
        line=line,
        validation="NONE",
        confidence=0.6,
        rule_id="llm-discovered-source",
        needs_review=True,
    )


def _resolve_block_for_line(chunk: FileChunk, field: dict) -> "FuncBlock":
    """文件级归位(spec §3.1): verdict.line(文件绝对行)反查所属 block(∈ [start_line, end_line])。

    单函数(per-func)时 SourcePoint 直接用唯一 block; 文件级后一个 chunk 含多函数, verdict
    的 line 是文件绝对行, 须定位到具体函数才能正确填 entry_point_id/file_path。line 缺失/
    越界 → 回退 chunk 首个 block(保守, needs_review 仍会过下游复核)。
    """
    try:
        line = int(field.get("line", 0))
    except (TypeError, ValueError):
        return chunk.blocks[0]
    for b in chunk.blocks:
        if b.start_line <= line <= b.end_line:
            return b
    return chunk.blocks[0]


@dataclass(frozen=True)
class SourceGap:
    """一条 source 规则缺口 —— 聚合 LLM 软 source 的非常规取用写法(解构等),
    驱动 source_rules.yml 迭代(类比 sink 的 RuleGap)。

    language 不在 SourcePoint 上,留空(同 RuleGap;由调用方按需补)。
    """
    pattern: str            # 写法,如软 source 的 expression("req.body")
    language: str
    source_type: str
    count: int
    sample_evidence: list[str] = field(default_factory=list)


def _aggregate_source_gaps(soft_sources: list[SourcePoint]) -> list[SourceGap]:
    """聚合软 source 的非常规取用写法 → SourceGap(按 expression + source_type 分桶)。"""
    buckets: dict[tuple, SourceGap] = {}
    for s in soft_sources:
        key = (s.expression, s.source_type.value)
        evidence = f"{s.file_path}:{s.line}  {s.param_name}"
        if key in buckets:
            b = buckets[key]
            buckets[key] = SourceGap(
                pattern=b.pattern, language=b.language, source_type=b.source_type,
                count=b.count + 1, sample_evidence=(b.sample_evidence + [evidence])[:5],
            )
        else:
            buckets[key] = SourceGap(
                pattern=s.expression, language="", source_type=s.source_type.value,
                count=1, sample_evidence=[evidence],
            )
    return list(buckets.values())


async def discover_sources_llm(
    candidates: list[SourceCandidate],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
    token_threshold: int | None = None,
    model: str | None = None,
    max_calls: int | None = None,
) -> tuple[list[SourcePoint], list[SourceGap]]:
    """对候选(含 sink 函数中规则未命中的)并发调 LLM → 软 SourcePoint + SourceGap。

    调用粒度 = **文件级**(spec 2026-07-10 §3.1): 同文件所有候选函数 → 一个 chunk →
    一次 LLM 调用。大文件按 token 贪心拆 chunk(token_threshold, 默认由
    get_chunk_token_threshold(model) 派生: 按当前模型 context 自适应)防 prompt 爆 LLM context。verdict.line 反查所属 block 归位(见 _resolve_block_for_line)。

    LLM 不可用(None / 超时 / 不可解析)→ 返回 ([], [])(降级,守"GitNexus 轨确定性兜底")。
    软 source rule_id="llm-discovered-source" needs_review=True(下游 intra-first/verdict 复核)。

    progress_cb: best-effort 进度上报(每 chunk 一 tick + 一次 finalize 汇总);
    cb=None 全程 no-op。
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

    emitter = ProgressEmitter("source-discovery", len(chunks), progress_cb)

    async def _discover_one(chunk: FileChunk):
        prompt = _build_prompt(chunk)
        raw = await llm_client(prompt)
        fields = _parse_fields(raw)
        out: list[SourcePoint] = []
        for f in fields:
            if f.get("is_source") is not True:
                continue
            # per-item 防护: 单条 field malformed(line=null 等)只跳过该 source,
            # 不让 int()/解析异常带垮整文件 chunk(同 sink_discovery_llm, spec §3.1)。
            try:
                block = _resolve_block_for_line(chunk, f)
                out.append(_to_soft_source(block, f))
            except Exception:
                logger.debug("discover_sources_llm: skip malformed field", exc_info=True)
                continue
        detail = None
        if out:
            s0 = out[0]
            detail = (f"'{s0.param_name}' @ {s0.file_path}:{s0.line}"
                      f" source={s0.source_type.value}")
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    # spec §3.2: SHANNON_LLM_PER_CALL_TIMEOUT env 可覆盖; 文件级 prompt 更重故取
    # max(env, 120) —— env 未设(60)→120、env=200→200, 显式传值仍优先。旧版恒 120
    # 绕过 env(违反 spec「均可经 env 覆盖」+ concurrency.py docstring), 已修。
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(), DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))

    async def _on_skip(idx, message):
        # idx → 文件路径(同 sink_discovery_llm): 跨文件合并后诊断单位是 chunk(可多文件),
        # file_paths join 标注。
        chunk = chunks[idx]
        await emitter.note(f"{', '.join(chunk.file_paths)}: {message}")

    per_chunk = await map_llm_with_bounds(
        chunks, _discover_one,
        concurrency=conc, per_call_timeout=effective_timeout, label="discover_sources_llm",
        on_skip=_on_skip,
    )
    all_sources = [s for chunk_sources in per_chunk for s in chunk_sources]
    skipped = len(chunks) - len(per_chunk)
    gaps = _aggregate_source_gaps(all_sources)
    await emitter.finalize(
        f"{len(all_sources)} sources · {len(gaps)} source gaps · {skipped} timeouts")
    return all_sources, gaps
