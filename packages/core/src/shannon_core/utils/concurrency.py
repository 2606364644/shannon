import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

async def run_with_concurrency_limit(
    coroutines: list[Callable[[], Awaitable[T]]],
    limit: int,
) -> list[T]:
    semaphore = asyncio.Semaphore(limit)
    results: list[T] = []

    async def run_one(fn: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await fn()

    tasks = [asyncio.create_task(run_one(fn)) for fn in coroutines]
    completed = await asyncio.gather(*tasks, return_exceptions=True)
    for item in completed:
        if isinstance(item, Exception):
            raise item
        results.append(item)
    return results
