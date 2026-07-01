# packages/core/src/shannon_core/code_index/source_discovery_llm.py
"""LLM source 补召回(平行 sink_discovery_llm)。

规则未命中的 entry handler(非常规框架/解构)→ 送 LLM 判定 → 软 SourcePoint
(rule_id="llm-discovered")。LLM 不可用 → 返回空(降级,守"GitNexus 轨确定性兜底")。
复用 map_llm_with_bounds 并发骨架(对齐 discover_sinks_llm)。
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.source_detector import DEFAULT_SOURCE_RULES
from shannon_core.code_index.llm_concurrency import (
    DEFAULT_PER_CALL_TIMEOUT, map_llm_with_bounds,
)
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
from shannon_core.config.concurrency import get_max_concurrent

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class SourceCandidate:
    """规则未命中的 entry handler,待 LLM 判定其可控字段。"""
    block: "FuncBlock"


def _has_rule_hit(language: str, text: str) -> bool:
    """该 handler 是否已被 detect_sources 规则命中(避免重复)。"""
    return any(
        block_lang in rule.languages and rule.pattern.search(text)
        for rule in DEFAULT_SOURCE_RULES
        for block_lang in (language,)
    )


def collect_source_candidates(
    blocks: "list[FuncBlock]",
    entry_point_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourceCandidate]:
    """收集 entry handler 中规则未命中的(候选送 LLM)。

    启发式:handler 函数体含 input-ish / param-ish 标识符(input.get / params[ /
    @Attribute / data[" ...])但 detect_sources 规则未命中 → 候选。
    """
    _INPUTISH = re.compile(
        r"(input\.get|params\[|body\[|data\[['\"]|@RequestBody|@QueryParam|"
        r"ctx\.Request|c\.Query|c\.Param)",
        re.IGNORECASE,
    )
    out: list[SourceCandidate] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        if _has_rule_hit(block.language, text):
            continue  # 规则已命中
        if _INPUTISH.search(text):
            out.append(SourceCandidate(block=block))
    return out


_PROMPT_TMPL = """You are a user-input source classifier for the GitNexus track.
Given ONE entry handler function, identify ALL user-controllable input fields and
their HTTP source type. Rule-based detection already covered common frameworks
(Express/Django/...); you handle the unconventional ones.

## Function
{func_name} ({file}:{line})
Parameters: {params}

## Source
```
{source}
```

## Task
Return a JSON array. One object per user-controllable field:
{{"field":"<param_name>","source_type":"query|path|body|form|header|cookie|file","expression":"<source-code expr>","line":<int>,"is_source":true|false,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. Omit fields that are NOT user-controllable (is_source=false)."""


def _build_prompt(block) -> str:
    return _PROMPT_TMPL.format(
        func_name=block.function_name, file=block.file_path,
        line=block.start_line, params=list(block.parameters),
        source=block.source_code,
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
        rule_id="llm-discovered",
        needs_review=True,
    )


async def discover_sources_llm(
    candidates: list[SourceCandidate],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
) -> list[SourcePoint]:
    """对候选 handler 并发调 LLM → 软 SourcePoint。LLM 不可用 → 空(降级)。

    progress_cb: T1 的 best-effort 进度回调(per-function tick + 末尾 finalize);
    None 时全程 no-op(测试 / 未注入 / SHANNON_GITNEXUS_LLM_ENABLED=0)。即便早退
    (llm_client=None / 无候选)也发一次 finalize,使通道显示汇总行(T5 不变量)。
    """
    if llm_client is None or not candidates:
        # 早退仍发 finalize(0 候选汇总),保持通道行为一致(T5)。
        await ProgressEmitter("source-discovery", 0, progress_cb).finalize(
            "0 sources · 0 timeouts")
        return []
    by_func: dict[str, list[SourceCandidate]] = defaultdict(list)
    for c in candidates:
        by_func[c.block.id].append(c)

    emitter = ProgressEmitter("source-discovery", len(by_func), progress_cb)

    async def _discover_one(item):
        _, cands = item
        block = cands[0].block
        prompt = _build_prompt(block)
        raw = await llm_client(prompt)
        fields = _parse_fields(raw)
        out = [_to_soft_source(block, f) for f in fields if f.get("is_source") is True]
        detail = None
        if out:
            s0 = out[0]
            detail = (f"'{s0.param_name}' @ {s0.file_path}:{s0.line}"
                      f" source={s0.source_type.value}")
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    timeout = (per_call_timeout if per_call_timeout is not None
               else DEFAULT_PER_CALL_TIMEOUT)
    per_func = await map_llm_with_bounds(
        list(by_func.items()), _discover_one,
        concurrency=conc, per_call_timeout=timeout, label="discover_sources_llm",
    )
    all_sources = [s for func_sources in per_func for s in func_sources]
    skipped = len(by_func) - len(per_func)
    await emitter.finalize(f"{len(all_sources)} sources · {skipped} timeouts")
    return all_sources
