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


class SessionMutex:
    """Per-sessionId async mutex with FIFO queue semantics."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def lock(self, session_id: str) -> Callable[[], None]:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        await self._locks[session_id].acquire()
        return self._locks[session_id].release
