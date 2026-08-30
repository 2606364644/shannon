"""GitNexus 轨 activity 内 LLM 并发执行工具。

把 activity 内的串行 per-function LLM 调用改成 Semaphore 限并发 + 单次
wait_for 超时 + 降级,防大仓 N 个函数累加拖垮 activity 的 start_to_close_timeout。
详见 docs/superpowers/specs/2026-06-30-discover-sinks-llm-concurrency-design.md。
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from supernova_core.config.concurrency import (
    get_per_call_timeout,
    get_transient_retries,
    get_transient_retry_delay,
)

if TYPE_CHECKING:
    from supernova_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# 单次 LLM 调用(含 provider 内部 retry)上限秒数的硬回退默认。
# 运行期实际值由 get_per_call_timeout() 读 SUPERNOVA_LLM_PER_CALL_TIMEOUT(env)决定,
# 未设 = 此值(60s);analyze_taint_llm 内部 retry 超此时长会被 cancel(有意,防累加)。
DEFAULT_PER_CALL_TIMEOUT = 60.0


# === 文件级聚合 chunking (spec 2026-07-10 sink/source 补召回 per-func → 文件级) =====
# 大仓下 per-function LLM 调用累加耗时远超 activity start_to_close_timeout(真机
# kol_mapping_service 569 函数 ~95min 撞 10min timeout)。改文件级聚合(同文件多函数 →
# 一次调用)大幅减次数; 大文件按 token 贪心拆 chunk 防 prompt 爆 LLM context。

# CJK 字符范围: 中文/日文/韩文。BPE 下常 1~2 token/char, 取 1.5 中位偏保守防低估。
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _estimate_tokens(text: str) -> int:
    """源码 token 估算: CJK × 1.5 + 其余 / 4, 向上取整(spec 2026-07-10)。

    比旧 len//4 准 2-3x(中文注释不再被严重低估 4-8x), 偏保守防 prompt 爆 context。
    仅用于 chunk 装箱, 不进 pricing 计费(计费走真实 usage)。
    """
    cjk = len(_CJK_RE.findall(text))
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 4)


@dataclass(frozen=True)
class FileChunk:
    """LLM 跨文件聚合的一个 chunk —— 文件作装箱单位 + 跨文件贪心合并(spec 2026-07-10)。

    多个小文件包合并进一个 chunk(减调用次数, kol 259 文件 → ~7 chunk); 单文件超双上限
    (token / call 数)时退化按 block 拆。blocks 去重保序; items 是这些函数对应的 calls/candidates。
    file_paths: 该 chunk 涉及的全部文件(跨文件合并后可多文件, 字典序)。
    """
    file_paths: tuple[str, ...]
    blocks: tuple[FuncBlock, ...]
    items: tuple[Any, ...]


def chunk_items_by_file(
    items: list[Any],
    *,
    block_of: Callable[[Any], FuncBlock],
    token_threshold: int,
    max_calls: int,
) -> list[FileChunk]:
    """文件作装箱单位 + 跨文件贪心合并 + 双上限(spec 2026-07-10)。

    1. 按 file_path 分组 → 每文件一个「文件包」(ordered blocks + items + total_tokens +
       call_count)。同文件 block 不拆(保留 intra-file 优先语义)。
    2. 按 file_path 字典序遍历文件包, 跨文件贪心装箱: 整包累加到当前 chunk; 加入后超
       token_threshold 或 call_count > max_calls → 开新 chunk(双上限)。
    3. 单文件包自身超任一上限 → 文件内按 block 退化拆分(同文件 block 连续, 保序)。
    保证: 同文件 block 不被拆到不同 chunk(除非单文件超限退化); 不同文件可合并。
    """
    by_file: dict[str, list[Any]] = defaultdict(list)
    for it in items:
        by_file[block_of(it).file_path].append(it)

    chunks: list[FileChunk] = []
    # 跨文件贪心装箱的当前 chunk 累加状态
    cur_files: list[str] = []
    cur_blocks: list[FuncBlock] = []
    cur_items: list[Any] = []
    cur_tokens = 0
    cur_calls = 0

    def _flush() -> None:
        nonlocal cur_files, cur_blocks, cur_items, cur_tokens, cur_calls
        if cur_blocks:
            chunks.append(FileChunk(
                tuple(cur_files), tuple(cur_blocks), tuple(cur_items)))
        cur_files, cur_blocks, cur_items = [], [], []
        cur_tokens, cur_calls = 0, 0

    # 字典序遍历文件包(稳定可重现; 同文件 block 因分组天然连续)
    for file_path in sorted(by_file):
        file_items = by_file[file_path]
        # 按 block.id 去重保序分组
        ordered_blocks: dict[str, FuncBlock] = {}
        items_by_block: dict[str, list[Any]] = defaultdict(list)
        for it in file_items:
            b = block_of(it)
            if b.id not in ordered_blocks:
                ordered_blocks[b.id] = b
            items_by_block[b.id].append(it)
        blocks = list(ordered_blocks.values())
        pkg_tokens = sum(_estimate_tokens(b.source_code) for b in blocks)
        pkg_calls = len(file_items)

        # 退化: 单文件包自身超任一上限 → 文件内按 block 拆(不与跨文件 chunk 混)
        if pkg_tokens > token_threshold or pkg_calls > max_calls:
            _flush()
            sub_blocks: list[FuncBlock] = []
            sub_items: list[Any] = []
            sub_tokens = 0
            sub_calls = 0
            for block in blocks:
                btok = _estimate_tokens(block.source_code)
                bcalls = len(items_by_block[block.id])
                if sub_blocks and (sub_tokens + btok > token_threshold
                                   or sub_calls + bcalls > max_calls):
                    chunks.append(FileChunk(
                        (file_path,), tuple(sub_blocks), tuple(sub_items)))
                    sub_blocks, sub_items, sub_tokens, sub_calls = [], [], 0, 0
                sub_blocks.append(block)
                sub_items.extend(items_by_block[block.id])
                sub_tokens += btok
                sub_calls += bcalls
            if sub_blocks:
                chunks.append(FileChunk(
                    (file_path,), tuple(sub_blocks), tuple(sub_items)))
            continue

        # 正常: 整包加入当前跨文件 chunk; 加入后超双上限则先 flush 开新 chunk
        if cur_blocks and (cur_tokens + pkg_tokens > token_threshold
                           or cur_calls + pkg_calls > max_calls):
            _flush()
        cur_files.append(file_path)
        cur_blocks.extend(blocks)
        cur_items.extend(file_items)
        cur_tokens += pkg_tokens
        cur_calls += pkg_calls

    _flush()
    return chunks


@dataclass
class _Skip:
    """map_llm_with_bounds 单项失败标记。

    区分 timeout vs error, 且延迟到 gather 后再打日志,以便"全部失败"时
    压成 1 条总结而非 per-item 刷屏。
    """
    kind: str  # "timeout" | "error"
    idx: int
    exc: Exception | None


# per-skip 诊断上报回调: (idx, message)。idx 为 items 索引(调用方可映射回业务身份,
# 如函数名); message 由本模块拼好(含 timeout 秒数/error exc)。
# best-effort: 抛异常由 _notify_skip 吞掉, 绝不影响扫描结果。
OnSkip = Callable[[int, str], Awaitable[None]]


async def _notify_skip(on_skip: OnSkip | None, idx: int, message: str) -> None:
    """best-effort 把 per-skip 诊断上报到注入的 on_skip。

    外层(discover_sinks_llm 等)把 on_skip 转成 emitter.note → progress_cb →
    GitnexusLlmEvent note 行 → dispatcher → Rich Live 协调正确换行。on_skip=None
    (未注入)或 on_skip 抛异常时 no-op, 绝不影响扫描结果。
    """
    if on_skip is None:
        return
    try:
        await on_skip(idx, message)
    except Exception:
        pass  # best-effort: 显示通道失败不影响扫描


async def map_llm_with_bounds(
    items: list[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
    per_call_timeout: float | None = None,
    label: str = "llm",
    on_skip: OnSkip | None = None,
    skip_stats: dict | None = None,
    transient_retries: int | None = None,
    transient_retry_delay: float | None = None,
) -> list[R]:
    """并发跑 fn(item):Semaphore(concurrency) 限并发 + 每个套 wait_for(per_call_timeout)。

    单次超时/异常 → 该项跳过,gather 不因单个失败而 fail。
    返回成功项结果列表(丢弃失败项)。顺序为并发完成序,不保证与 items 一致。

    per_call_timeout=None 时读 SUPERNOVA_LLM_PER_CALL_TIMEOUT(env),未设 = 60s
    (get_per_call_timeout);显式传值(测试 / 调用方覆盖)优先。

    error 类瞬时重试(2026-08-29, NodeGoat-20260828-162655 网关 5s 抖动致
    discovery 补召回整层丢失——Connection error 600ms 即死永久跳过,而同一错误
    pre-recon 走 Temporal 重试):error 异常先重试 transient_retries 次(每次前
    sleep transient_retry_delay)再放弃;timeout 类**不重试**(幂等超时重试只是
    再超时一遍,juice-shop 3×10min 教训,见 CODE_INDEX_RETRY 注释)。重试期间
    占住 semaphore 槽(可接受:并发槽本就是为限制同时 LLM 调用,backoff 不发请求)。
    重试成功 → 正常成功项,skip_stats 不计;耗尽 → 维持 _Skip("error") 语义不变。
    transient_retries/delay=None 时读 SUPERNOVA_LLM_TRANSIENT_RETRIES(默认 1)/
    SUPERNOVA_LLM_TRANSIENT_RETRY_DELAY(默认 10s,盖过典型网关抖动窗口);
    显式传值(测试提速 retries=0/delay=0)优先。0 = 显式关闭。

    诊断策略(2026-07-01, 走 dispatcher 通道, 不裸 logger.warning):
    - per-skip 诊断(timeout/error)经 on_skip 回调上报 → 外层 emitter.note →
      progress_cb → GitnexusLlmEvent note 行 → dispatcher → Rich Live 协调正确换行。
      绝不裸 logger.warning: worker 进程 redirect_stderr=False 是硬约束(否则 rich 在
      sandbox 线程 circular import 炸 workflow task), 裸 warning 经 lastResort 直写
      stderr, Rich Live 不协调, 与 footer spinner 行碰撞 → 首条被 \\r 重绘截断粘连。
    - logger 降到 DEBUG 作文件级兜底(on_skip=None 或排查时 elevate); DEBUG 不进终端
      lastResort(WARNING+ 才进), 故不撞 footer。
    - 全失败(典型 = LLM 全挂/API down):压成 1 条 DEBUG 总结, 不 per-skip 调 on_skip
      (避免 N 条刷屏); 总数由外层 finalize summary 报告。
      注:SUPERNOVA_GITNEXUS_LLM_ENABLED=0 时 consumer 入口(discover_sinks/sources)
      会直接早退,根本不进入本函数;全失败压缩主要防御真 LLM 故障场景。
    """
    if per_call_timeout is None:
        per_call_timeout = get_per_call_timeout()
    if transient_retries is None:
        transient_retries = get_transient_retries()
    if transient_retry_delay is None:
        transient_retry_delay = get_transient_retry_delay()
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(idx: int, item: T) -> R | _Skip:
        async with sem:
            attempts = 1 + transient_retries
            for attempt in range(1, attempts + 1):
                try:
                    return await asyncio.wait_for(fn(item), timeout=per_call_timeout)
                except asyncio.TimeoutError:
                    # timeout 不重试:幂等超时重试只是再超时一遍(juice-shop
                    # 3×10min 教训,同 CODE_INDEX_RETRY 不放大哲学)。
                    return _Skip("timeout", idx, None)
                except Exception as exc:
                    if attempt < attempts:
                        logger.debug(
                            "%s: item %d error on attempt %d/%d, "
                            "retrying in %.1fs: %s",
                            label, idx, attempt, attempts,
                            transient_retry_delay, exc)
                        await asyncio.sleep(transient_retry_delay)
                        continue
                    return _Skip("error", idx, exc)

    raw = await asyncio.gather(*[_bounded(i, x) for i, x in enumerate(items)])
    successes: list[R] = [r for r in raw if not isinstance(r, _Skip)]
    skips: list[_Skip] = [r for r in raw if isinstance(r, _Skip)]

    # skip 构成分解（2026-08-28 文案误导修复）：消费方 finalize 原把 skipped
    # 合计一律叫 "N timeouts"，agent 执行失败（141s < 300s 地板）被误读为超时、
    # 排查方向被带偏。传 skip_stats（dict）即得 {"timeout": n, "error": n}；
    # 不传零破坏。
    if skip_stats is not None:
        skip_stats["timeout"] = sum(1 for s in skips if s.kind == "timeout")
        skip_stats["error"] = sum(1 for s in skips if s.kind == "error")

    if not skips:
        return successes

    def _skip_msg(s: _Skip) -> str:
        if s.kind == "timeout":
            return f"timed out (>{per_call_timeout}s), skipped"
        return f"failed, skipped: {s.exc}"

    if len(skips) == len(items):
        # 全失败: 压成 1 条 DEBUG 总结(不 per-skip 调 on_skip, 避免刷屏)。
        first = skips[0]
        reason = (f"timed out (>{per_call_timeout}s)" if first.kind == "timeout"
                  else f"failed: {first.exc}")
        logger.debug(
            "%s: all %d/%d items skipped — likely systemic (LLM unavailable "
            "or disabled). First: %s. Returning empty; deterministic fallback "
            "applies downstream.",
            label, len(skips), len(items), reason,
        )
    else:
        for s in skips:
            msg = _skip_msg(s)
            logger.debug("%s[%d] %s", label, s.idx, msg)
            await _notify_skip(on_skip, s.idx, msg)
        logger.debug("%s: %d/%d items skipped", label, len(skips), len(items))

    return successes
