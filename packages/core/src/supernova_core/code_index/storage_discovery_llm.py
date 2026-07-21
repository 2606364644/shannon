# packages/core/src/supernova_core/code_index/storage_discovery_llm.py
"""LLM storage read/write hunter (spec 子项⑤ §3.3), symmetric to discover_sources_llm
/ discover_sinks_by_entry.

The deterministic ``storage_detector`` (Task 3) covers standard ORM patterns in
``storage_rules.yml``. This module catches the rest — non-standard ORM
(``repo.findByName(name)``), dynamic-but-literal-token writes, framework config
``@Value`` / ``getProperty`` — by sending each candidate function to the LLM and
producing **soft anchors**:

- Reads → ``SourcePoint(source_type=STORAGE)`` (flavor decision A) — feeds the
  existing source-point channel; ``chain_verdict`` re-checks on the read side.
- Writes → ``StorageWritePoint`` (independent type, never enters
  ``sink_call_sites`` — writing to DB is not itself a vuln).

Soft markers: ``rule_id="llm-discovered-storage"``, ``needs_review=True``.

LLM unavailable (None / timeout / unparseable) → return ``([], [])`` (deterministic
-fallback posture: hard rules stand). Mirrors ``discover_sources_llm`` /
``discover_sinks_llm``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from supernova_core.code_index.models import ParameterSource
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.storage_models import StorageMedium, StorageWritePoint
from supernova_core.code_index.llm_concurrency import (
    FileChunk,
    chunk_items_by_file,
    map_llm_with_bounds,
)
from supernova_core.code_index.progress import ProgressCb, ProgressEmitter
from supernova_core.agents.model_caps import get_chunk_token_threshold
from supernova_core.config.concurrency import (
    get_chunk_max_calls,
    get_max_concurrent,
    get_per_call_timeout,
)

if TYPE_CHECKING:
    from supernova_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]

# 文件级 prompt 更重 → 默认 120s(同 source/sink discovery;spec §3.2)。
DEFAULT_DISCOVERY_PER_CALL_TIMEOUT = 120.0


@dataclass(frozen=True)
class StorageReadCandidate:
    """A function whose body may contain storage reads the hard rules missed.

    Mirrors ``SourceCandidate`` / ``SinkHunterCandidate`` — a thin wrapper so
    ``chunk_items_by_file`` can pick the block out via ``block_of=lambda c: c.block``.
    """
    block: "FuncBlock"


@dataclass(frozen=True)
class StorageWriteCandidate:
    """A function whose body may contain storage writes the hard rules missed."""
    block: "FuncBlock"


@dataclass(frozen=True)
class StorageGap:
    """一条 storage 规则缺口 — 聚合 LLM 软 anchor 的非常规 storage 写法,
    驱动 ``storage_rules.yml`` 迭代(对称 ``SourceGap`` / ``RuleGap``)。

    language 不在 SourcePoint / StorageWritePoint 上,留空(由调用方按需补)。
    """
    pattern: str            # 写法, 如软 read 的 expression("repo.findByName(...)")
    language: str
    medium: str             # db | config | cache | file
    kind: str               # "read" | "write"
    count: int
    sample_evidence: list[str] = field(default_factory=list)


# === Prompt templates =======================================================
# 文件级 prompt(同 source/sink discovery): {file_paths} join 该 chunk 涉及的全部
# 文件; {functions_repr} 是逐函数源码拼接。LLM 返回 JSON 数组, line = 文件绝对行。

_READ_PROMPT_TMPL = """You are a storage READ detector for the GitNexus track.
Given a FILE with one or more entry handler functions, identify ALL storage READ
points the rule-based detector may have missed:
- DB find / select via non-standard ORM (repo.findByName, dao.fetch, etc.)
- Config getProperty / @Value injection
- Cache get (caffeine.get, redisClient.get)
- File read (Files.readAllBytes, new FileReader)

Only LITERAL tokens (table name / cache key / file path); skip dynamic or
concatenated tokens (mark them by leaving "token" null).

## File(s)
{file_paths}

## Functions
{functions_repr}

## Task
Return a JSON array. One object per storage READ:
{{"read":"<call expression>","medium":"db|config|cache|file","token":"<literal token or null>","read_var":"<variable receiving the value>","line":<int>,"is_storage_read":true,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. Omit non-storage reads (is_storage_read=false).
`line` is the FILE-absolute line number of the read call."""


_WRITE_PROMPT_TMPL = """You are a storage WRITE detector for the GitNexus track.
Given a FILE with one or more entry handler functions, identify ALL storage WRITE
points the rule-based detector may have missed:
- DB save / insert / update via non-standard ORM (repo.save, dao.persist)
- Config setProperty
- Cache put / set (caffeine.put, redisClient.set)
- File write (Files.write, new FileWriter)

Only LITERAL tokens (table / key / path); dynamic or concatenated → "token": null.

## File(s)
{file_paths}

## Functions
{functions_repr}

## Task
Return a JSON array. One object per storage WRITE:
{{"write":"<call expression>","medium":"db|config|cache|file","token":"<literal token or null>","written_arg":"<expression being written>","line":<int>,"is_storage_write":true,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. Omit non-storage writes (is_storage_write=false).
`line` is the FILE-absolute line number of the write call."""


def _build_read_prompt(chunk: FileChunk) -> str:
    return _READ_PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        functions_repr=_functions_repr(chunk),
    )


def _build_write_prompt(chunk: FileChunk) -> str:
    return _WRITE_PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        functions_repr=_functions_repr(chunk),
    )


def _functions_repr(chunk: FileChunk) -> str:
    """逐函数源码拼接(对称 source/sink discovery 的 func_parts)。"""
    parts: list[str] = []
    for b in chunk.blocks:
        parts.append(
            f"### {b.function_name} ({b.file_path}:{b.start_line})\n"
            f"Parameters: {list(b.parameters)}\n"
            f"```\n{b.source_code}\n```"
        )
    return "\n\n".join(parts)


def _parse_verdicts(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("storage_discovery_llm: failed to parse LLM JSON: %s",
                     raw[:120])
        return []


def _resolve_block_for_line(chunk: FileChunk, line: int | None) -> "FuncBlock":
    """verdict.line(文件绝对行)反查所属 block; 缺失/越界 → 首个 block(保守,
    needs_review 仍过下游复核)。对称 source_discovery_llm._resolve_block_for_line。
    """
    if line is None:
        return chunk.blocks[0]
    try:
        ln = int(line)
    except (TypeError, ValueError):
        return chunk.blocks[0]
    for b in chunk.blocks:
        if b.start_line <= ln <= b.end_line:
            return b
    return chunk.blocks[0]


def _to_medium(v: str | None) -> StorageMedium:
    """LLM verdict.medium → StorageMedium; 兜底 DB(同 storage_detector 默认)。"""
    try:
        return StorageMedium((v or "db").lower())
    except ValueError:
        return StorageMedium.DB


def _to_soft_read(block: "FuncBlock", d: dict) -> SourcePoint:
    """LLM verdict → soft SourcePoint(STORAGE). 对称 _to_soft_source(source_discovery)。"""
    name = str(d.get("read_var") or "storage_value")
    try:
        line = int(d.get("line") or block.start_line)
    except (TypeError, ValueError):
        line = block.start_line
    return SourcePoint(
        id=f"{block.id}::{name}::{line}",
        entry_point_id=block.id,
        param_name=name,
        source_type=ParameterSource.STORAGE,
        expression=str(d.get("read", "")),
        file_path=block.file_path,
        line=line,
        validation="NONE",
        confidence=0.6,
        rule_id="llm-discovered-storage",
        needs_review=True,
    )


def _to_soft_write(block: "FuncBlock", d: dict) -> StorageWritePoint:
    """LLM verdict → soft StorageWritePoint. 对称 _to_hunter_sink(sink_discovery)。"""
    try:
        line = int(d.get("line") or block.start_line)
    except (TypeError, ValueError):
        line = block.start_line
    tok = d.get("token")
    # callee_name: "repo.save(name, u)" → "save"; fallback "storage_write"
    write_expr = str(d.get("write", ""))
    callee = (write_expr.split("(", 1)[0].split(".")[-1] or "storage_write")
    return StorageWritePoint(
        id=f"{block.id}::llm-storage::{line}",
        caller_id=block.id,
        callee_name=callee,
        callee_receiver=None,
        medium=_to_medium(d.get("medium")),
        storage_token=(tok if tok else "unresolvable"),
        written_expr=str(d.get("written_arg", "")),
        file_path=block.file_path,
        line=line,
        rule_id="llm-discovered-storage",
        needs_review=True,
    )


def _aggregate_read_gaps(soft_reads: list[SourcePoint]) -> list[StorageGap]:
    buckets: dict[tuple, StorageGap] = {}
    for s in soft_reads:
        key = (s.expression, "read")
        evidence = f"{s.file_path}:{s.line}  {s.param_name}"
        if key in buckets:
            b = buckets[key]
            buckets[key] = StorageGap(
                pattern=b.pattern, language=b.language, medium=b.medium,
                kind=b.kind, count=b.count + 1,
                sample_evidence=(b.sample_evidence + [evidence])[:5],
            )
        else:
            buckets[key] = StorageGap(
                pattern=s.expression, language="", medium="db",
                kind="read", count=1, sample_evidence=[evidence],
            )
    return list(buckets.values())


def _aggregate_write_gaps(soft_writes: list[StorageWritePoint]) -> list[StorageGap]:
    buckets: dict[tuple, StorageGap] = {}
    for w in soft_writes:
        key = (w.callee_name, w.medium.value, "write")
        evidence = f"{w.file_path}:{w.line}  {w.callee_name}"
        if key in buckets:
            b = buckets[key]
            buckets[key] = StorageGap(
                pattern=b.pattern, language=b.language, medium=b.medium,
                kind=b.kind, count=b.count + 1,
                sample_evidence=(b.sample_evidence + [evidence])[:5],
            )
        else:
            buckets[key] = StorageGap(
                pattern=w.callee_name, language="", medium=w.medium.value,
                kind="write", count=1, sample_evidence=[evidence],
            )
    return list(buckets.values())


# === Public API =============================================================


async def discover_storage_reads_llm(
    candidates: list[StorageReadCandidate],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
    token_threshold: int | None = None,
    model: str | None = None,
    max_calls: int | None = None,
) -> tuple[list[SourcePoint], list[StorageGap]]:
    """LLM 补召回 storage READ — 软 SourcePoint(STORAGE) + StorageGap。

    调用粒度 = 文件级(同 source/sink discovery): 同文件所有候选函数 → 一个 chunk →
    一次 LLM 调用。大文件按 token 贪心拆 chunk(``token_threshold``, 默认
    ``get_chunk_token_threshold(model)`` 按模型 context 自适应)。

    LLM 不可用 / 超时 / 不可解析 → ``([], [])`` 降级(守 GitNexus 确定性兜底)。
    软 read rule_id=``"llm-discovered-storage"`` needs_review=True
    (下游 chain_verdict 复核)。
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

    emitter = ProgressEmitter("storage-read-discovery", len(chunks), progress_cb)

    async def _discover_one(chunk: FileChunk) -> list[SourcePoint]:
        prompt = _build_read_prompt(chunk)
        raw = await llm_client(prompt)
        verdicts = _parse_verdicts(raw)
        out: list[SourcePoint] = []
        for v in verdicts:
            if v.get("is_storage_read") is not True:
                continue
            # per-item 防护: 单条 verdict malformed(line=null 等)只跳过该条,
            # 不让 int()/解析异常带垮整文件 chunk(同 source/sink discovery)。
            try:
                block = _resolve_block_for_line(chunk, v.get("line"))
                out.append(_to_soft_read(block, v))
            except Exception:
                logger.debug(
                    "discover_storage_reads_llm: skip malformed verdict",
                    exc_info=True)
                continue
        detail = None
        if out:
            s0 = out[0]
            detail = (f"'{s0.param_name}' @ {s0.file_path}:{s0.line}"
                      f" medium=storage")
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(),
                                  DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))

    async def _on_skip(idx, message):
        chunk = chunks[idx]
        await emitter.note(f"{', '.join(chunk.file_paths)}: {message}")

    per_chunk = await map_llm_with_bounds(
        chunks, _discover_one,
        concurrency=conc, per_call_timeout=effective_timeout,
        label="discover_storage_reads_llm", on_skip=_on_skip,
    )
    all_reads = [s for chunk_reads in per_chunk for s in chunk_reads]
    skipped = len(chunks) - len(per_chunk)
    gaps = _aggregate_read_gaps(all_reads)
    await emitter.finalize(
        f"{len(all_reads)} storage reads · {len(gaps)} gaps · {skipped} timeouts")
    return all_reads, gaps


async def discover_storage_writes_llm(
    candidates: list[StorageWriteCandidate],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
    token_threshold: int | None = None,
    model: str | None = None,
    max_calls: int | None = None,
) -> tuple[list[StorageWritePoint], list[StorageGap]]:
    """LLM 补召回 storage WRITE — 软 StorageWritePoint + StorageGap。

    对称 ``discover_storage_reads_llm``: 同样的文件级聚合 / 双上限拆 chunk /
    per-item 容错 / LLM 不可用降级。软 write rule_id=``"llm-discovered-storage"``
    needs_review=True(下游二阶 LLM join 复核)。
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

    emitter = ProgressEmitter("storage-write-discovery", len(chunks), progress_cb)

    async def _discover_one(chunk: FileChunk) -> list[StorageWritePoint]:
        prompt = _build_write_prompt(chunk)
        raw = await llm_client(prompt)
        verdicts = _parse_verdicts(raw)
        out: list[StorageWritePoint] = []
        for v in verdicts:
            if v.get("is_storage_write") is not True:
                continue
            try:
                block = _resolve_block_for_line(chunk, v.get("line"))
                out.append(_to_soft_write(block, v))
            except Exception:
                logger.debug(
                    "discover_storage_writes_llm: skip malformed verdict",
                    exc_info=True)
                continue
        detail = None
        if out:
            w0 = out[0]
            detail = (f"'{w0.callee_name}' @ {w0.file_path}:{w0.line}"
                      f" medium={w0.medium.value}")
        await emitter.tick(detail=detail, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(),
                                  DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))

    async def _on_skip(idx, message):
        chunk = chunks[idx]
        await emitter.note(f"{', '.join(chunk.file_paths)}: {message}")

    per_chunk = await map_llm_with_bounds(
        chunks, _discover_one,
        concurrency=conc, per_call_timeout=effective_timeout,
        label="discover_storage_writes_llm", on_skip=_on_skip,
    )
    all_writes = [w for chunk_writes in per_chunk for w in chunk_writes]
    skipped = len(chunks) - len(per_chunk)
    gaps = _aggregate_write_gaps(all_writes)
    await emitter.finalize(
        f"{len(all_writes)} storage writes · {len(gaps)} gaps · {skipped} timeouts")
    return all_writes, gaps
