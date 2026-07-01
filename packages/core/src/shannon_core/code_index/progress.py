"""GitNexus 轨 LLM 环节的进度计数与 best-effort 上报。

core 层只定义协议 + 计数器，不感知 whitebox 的 audit session；采样/格式化由
activity 层注入的 progress_cb 负责。cb=None 时全程 no-op（测试/未注入/
SHANNON_GITNEXUS_LLM_ENABLED=0）。cb raise 时吞掉（best-effort，显示通道
失败绝不影响扫描）。计数在 asyncio 单线程下原子（tick 内自增在 await cb 之前）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

Phase = Literal[
    "sink-discovery", "source-discovery", "taint-analysis", "chain-verdict",
]


@dataclass(frozen=True)
class ProgressSample:
    phase: Phase
    done: int
    total: int
    hits: int
    detail: str | None       # 命中细节（hit 行）；None=未命中
    final: bool = False      # True=结束汇总行（detail 此时承载汇总文案）


ProgressCb = Callable[[ProgressSample], Awaitable[None]] | None


class ProgressEmitter:
    """并发安全的 per-item 进度计数器。

    在 map_llm_with_bounds 的 per-item 函数或 builder 的候选循环里，每完成一个
    单位调一次 tick；环节结束调 finalize。total 可为 0（无候选）——此时 tick 不会被
    调，finalize 也只发 done=0 的汇总。
    """

    def __init__(self, phase: Phase, total: int, cb: ProgressCb):
        self._phase = phase
        self._total = total
        self._cb = cb
        self._done = 0
        self._hits = 0

    async def tick(self, detail: str | None = None, hits_delta: int = 0) -> None:
        self._done += 1
        self._hits += hits_delta
        if self._cb is None:
            return
        try:
            await self._cb(ProgressSample(
                self._phase, self._done, self._total, self._hits, detail))
        except Exception:
            pass  # best-effort

    async def finalize(self, summary_detail: str) -> None:
        if self._cb is None:
            return
        try:
            await self._cb(ProgressSample(
                self._phase, self._done, self._total, self._hits,
                summary_detail, final=True))
        except Exception:
            pass  # best-effort
