"""GitNexus 轨 activity 内 LLM 并发执行工具。

把 activity 内的串行 per-function LLM 调用改成 Semaphore 限并发 + 单次
wait_for 超时 + 降级,防大仓 N 个函数累加拖垮 activity 的 start_to_close_timeout。
详见 docs/superpowers/specs/2026-06-30-discover-sinks-llm-concurrency-design.md。
"""
import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# 单次 LLM 调用(含 provider 内部 retry)上限秒数。超过即降级跳过该函数。
# 60s 对 GLM medium tier 单次 prompt→JSON 足够;analyze_taint_llm 内部 retry 在此
# 内会被 cancel(有意,防 retry 累加)。后续如需可 env 化。
DEFAULT_PER_CALL_TIMEOUT = 60.0


async def map_llm_with_bounds(
    items: list[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
    per_call_timeout: float = DEFAULT_PER_CALL_TIMEOUT,
    label: str = "llm",
) -> list[R]:
    """并发跑 fn(item):Semaphore(concurrency) 限并发 + 每个套 wait_for(per_call_timeout)。

    单次超时/异常 → 该项跳过(warning log),gather 不因单个失败而 fail。
    返回成功项结果列表(丢弃 None)。顺序为并发完成序,不保证与 items 一致。
    """
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(idx: int, item: T) -> R | None:
        async with sem:
            try:
                return await asyncio.wait_for(fn(item), timeout=per_call_timeout)
            except Exception as exc:  # 含 asyncio.TimeoutError
                logger.warning(
                    "%s[%d] failed/timed out (>%ss), skipped: %s",
                    label, idx, per_call_timeout, exc,
                )
                return None

    results = await asyncio.gather(*[_bounded(i, x) for i, x in enumerate(items)])
    successes = [r for r in results if r is not None]
    skipped = len(items) - len(successes)
    if skipped:
        logger.warning(
            "%s: %d/%d items skipped (timeout/error)", label, skipped, len(items))
    return successes
