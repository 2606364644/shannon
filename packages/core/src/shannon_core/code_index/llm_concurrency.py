"""GitNexus 轨 activity 内 LLM 并发执行工具。

把 activity 内的串行 per-function LLM 调用改成 Semaphore 限并发 + 单次
wait_for 超时 + 降级,防大仓 N 个函数累加拖垮 activity 的 start_to_close_timeout。
详见 docs/superpowers/specs/2026-06-30-discover-sinks-llm-concurrency-design.md。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from shannon_core.config.concurrency import get_per_call_timeout

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# 单次 LLM 调用(含 provider 内部 retry)上限秒数的硬回退默认。
# 运行期实际值由 get_per_call_timeout() 读 SHANNON_LLM_PER_CALL_TIMEOUT(env)决定,
# 未设 = 此值(60s);analyze_taint_llm 内部 retry 超此时长会被 cancel(有意,防累加)。
DEFAULT_PER_CALL_TIMEOUT = 60.0


@dataclass
class _Skip:
    """map_llm_with_bounds 单项失败标记。

    区分 timeout vs error, 且延迟到 gather 后再打日志,以便"全部失败"时
    压成 1 条总结而非 per-item 刷屏。
    """
    kind: str  # "timeout" | "error"
    idx: int
    exc: Exception | None


async def map_llm_with_bounds(
    items: list[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
    per_call_timeout: float | None = None,
    label: str = "llm",
) -> list[R]:
    """并发跑 fn(item):Semaphore(concurrency) 限并发 + 每个套 wait_for(per_call_timeout)。

    单次超时/异常 → 该项跳过,gather 不因单个失败而 fail。
    返回成功项结果列表(丢弃失败项)。顺序为并发完成序,不保证与 items 一致。

    per_call_timeout=None 时读 SHANNON_LLM_PER_CALL_TIMEOUT(env),未设 = 60s
    (get_per_call_timeout);显式传值(测试 / 调用方覆盖)优先。

    日志策略(2026-07-01):
    - 部分失败:per-item warning(timeout/error 措辞分开) + 1 条总结。诊断价值高、量小。
    - 全部失败(典型 = LLM 全挂/API down):压成 1 条总结,不再 per-item 刷屏。
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

    if len(skips) == len(items):
        # 全失败: 压成 1 条总结(含首个错误样本),不再 per-item 刷屏。
        first = skips[0]
        reason = (f"timed out (>{per_call_timeout}s)" if first.kind == "timeout"
                  else f"failed: {first.exc}")
        logger.warning(
            "%s: all %d/%d items skipped — likely systemic (LLM unavailable "
            "or disabled). First: %s. Returning empty; deterministic fallback "
            "applies downstream.",
            label, len(skips), len(items), reason,
        )
    else:
        for s in skips:
            if s.kind == "timeout":
                logger.warning(
                    "%s[%d] timed out (>%ss), skipped", label, s.idx, per_call_timeout)
            else:
                logger.warning(
                    "%s[%d] failed, skipped: %s", label, s.idx, s.exc)
        logger.warning("%s: %d/%d items skipped", label, len(skips), len(items))

    return successes
