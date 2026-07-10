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

from shannon_core.config.concurrency import get_per_call_timeout

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# 单次 LLM 调用(含 provider 内部 retry)上限秒数的硬回退默认。
# 运行期实际值由 get_per_call_timeout() 读 SHANNON_LLM_PER_CALL_TIMEOUT(env)决定,
# 未设 = 此值(60s);analyze_taint_llm 内部 retry 超此时长会被 cancel(有意,防累加)。
DEFAULT_PER_CALL_TIMEOUT = 60.0


# === 文件级聚合 chunking (spec 2026-07-10 sink/source 补召回 per-func → 文件级) =====
# 大仓下 per-function LLM 调用累加耗时远超 activity start_to_close_timeout(真机
# kol_mapping_service 569 函数 ~95min 撞 10min timeout)。改文件级聚合(同文件多函数 →
# 一次调用)大幅减次数; 大文件按 token 贪心拆 chunk 防 prompt 爆 LLM context。

# 单 chunk prompt token 上限(留 response 余量; ~12K)。源码字符数 // 4 粗估 token。
CHUNK_TOKEN_THRESHOLD = 12_000

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
    """LLM 文件级聚合的一个 chunk —— 一组函数(按 token 贪心装箱)。

    小文件 = 1 chunk(全文件聚合, 减调用次数); 大文件 = 多 chunk(按函数拆, 防爆 context)。
    blocks 去重保序(同 block 多 item 只列一次); items 是这些函数对应的 calls/candidates。
    """
    file_path: str
    blocks: tuple[FuncBlock, ...]
    items: tuple[Any, ...]


def chunk_items_by_file(
    items: list[Any],
    *,
    block_of: Callable[[Any], FuncBlock],
    token_threshold: int = CHUNK_TOKEN_THRESHOLD,
) -> list[FileChunk]:
    """按 file_path 分组 + 按 token 贪心装箱 → FileChunk 列表。

    同一文件的 items 先按 block.id 去重保序分组, 再按各 block 源码 token 贪心装箱:
    累加 block token, 超 token_threshold 开新 chunk。单 block 自身超阈值 → 独立成 chunk
    (chunk 单位是函数, 无法再拆)。保证: 同 block 的 items 不被拆散、不同文件不混。
    """
    by_file: dict[str, list[Any]] = defaultdict(list)
    for it in items:
        by_file[block_of(it).file_path].append(it)

    chunks: list[FileChunk] = []
    for file_path, file_items in by_file.items():
        # 按 block.id 去重保序分组
        ordered_blocks: dict[str, FuncBlock] = {}
        items_by_block: dict[str, list[Any]] = defaultdict(list)
        for it in file_items:
            b = block_of(it)
            if b.id not in ordered_blocks:
                ordered_blocks[b.id] = b
            items_by_block[b.id].append(it)
        # 贪心装箱: 累加 block token, 超阈值开新 chunk
        cur_blocks: list[FuncBlock] = []
        cur_items: list[Any] = []
        cur_tokens = 0
        for block in ordered_blocks.values():
            btok = _estimate_tokens(block.source_code)
            if cur_blocks and cur_tokens + btok > token_threshold:
                chunks.append(FileChunk(
                    file_path, tuple(cur_blocks), tuple(cur_items)))
                cur_blocks, cur_items, cur_tokens = [], [], 0
            cur_blocks.append(block)
            cur_items.extend(items_by_block[block.id])
            cur_tokens += btok
        if cur_blocks:
            chunks.append(FileChunk(file_path, tuple(cur_blocks), tuple(cur_items)))
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
) -> list[R]:
    """并发跑 fn(item):Semaphore(concurrency) 限并发 + 每个套 wait_for(per_call_timeout)。

    单次超时/异常 → 该项跳过,gather 不因单个失败而 fail。
    返回成功项结果列表(丢弃失败项)。顺序为并发完成序,不保证与 items 一致。

    per_call_timeout=None 时读 SHANNON_LLM_PER_CALL_TIMEOUT(env),未设 = 60s
    (get_per_call_timeout);显式传值(测试 / 调用方覆盖)优先。

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
      注:SHANNON_GITNEXUS_LLM_ENABLED=0 时 consumer 入口(discover_sinks/sources)
      会直接早退,根本不进入本函数;全失败压缩主要防御真 LLM 故障场景。
    """
    if per_call_timeout is None:
        per_call_timeout = get_per_call_timeout()
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(idx: int, item: T) -> R | _Skip:
        async with sem:
            try:
                return await asyncio.wait_for(fn(item), timeout=per_call_timeout)
            except asyncio.TimeoutError:
                return _Skip("timeout", idx, None)
            except Exception as exc:
                return _Skip("error", idx, exc)

    raw = await asyncio.gather(*[_bounded(i, x) for i, x in enumerate(items)])
    successes: list[R] = [r for r in raw if not isinstance(r, _Skip)]
    skips: list[_Skip] = [r for r in raw if isinstance(r, _Skip)]

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
