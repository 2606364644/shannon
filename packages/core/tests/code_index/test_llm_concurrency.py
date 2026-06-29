"""map_llm_with_bounds 单测 — Semaphore 并发 + 单次 wait_for 超时 + 降级(治本 2)."""
import asyncio

from shannon_core.code_index.llm_concurrency import map_llm_with_bounds


async def test_all_items_succeed():
    async def fn(x):
        return x * 2
    results = await map_llm_with_bounds([1, 2, 3], fn, concurrency=2, per_call_timeout=5)
    assert sorted(results) == [2, 4, 6]


async def test_slow_item_times_out_and_skipped():
    async def fn(x):
        if x == "slow":
            await asyncio.sleep(10)
        return x
    results = await map_llm_with_bounds(
        ["fast", "slow", "fast2"], fn, concurrency=3, per_call_timeout=0.1)
    assert "slow" not in results
    assert sorted(results) == ["fast", "fast2"]


async def test_raising_item_skipped():
    async def fn(x):
        if x == "boom":
            raise ValueError("boom")
        return x
    results = await map_llm_with_bounds(
        ["ok", "boom", "ok2"], fn, concurrency=2, per_call_timeout=5)
    assert sorted(results) == ["ok", "ok2"]


async def test_semaphore_limits_concurrency():
    in_flight = 0
    peak = 0

    async def fn(x):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return x

    await map_llm_with_bounds(list(range(6)), fn, concurrency=2, per_call_timeout=5)
    assert peak <= 2


async def test_empty_items_returns_empty():
    async def fn(x):
        return x
    assert await map_llm_with_bounds([], fn, concurrency=2) == []
