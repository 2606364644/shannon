"""Tests for SessionMutex and run_with_concurrency_limit."""

import asyncio

import pytest

from shannon_core.utils.concurrency import SessionMutex, run_with_concurrency_limit


# ---------------------------------------------------------------------------
# run_with_concurrency_limit – smoke test (existing functionality)
# ---------------------------------------------------------------------------


async def _make_coro(value: int, delay: float = 0) -> int:
    """Return a factory that produces an awaitable returning *value*."""
    await asyncio.sleep(delay)
    return value


def test_run_with_concurrency_limit_basic():
    """run_with_concurrency_limit returns results in submission order."""
    coros = [lambda v=i: _make_coro(v) for i in range(5)]
    results = asyncio.run(
        run_with_concurrency_limit(coros, limit=2)
    )
    assert results == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# SessionMutex tests
# ---------------------------------------------------------------------------


def test_basic_lock_unlock_single_session():
    """A single session can acquire and release the lock."""
    mutex = SessionMutex()

    async def _run():
        release = await mutex.lock("sess-1")
        assert "sess-1" in mutex._locks
        release()

    asyncio.run(_run())


def test_fifo_ordering():
    """Multiple lock calls on the same session_id queue up in FIFO order."""
    mutex = SessionMutex()
    order: list[int] = []

    async def _worker(idx: int):
        release = await mutex.lock("sess-1")
        order.append(idx)
        await asyncio.sleep(0.05)  # hold the lock briefly
        release()

    async def _run():
        await asyncio.gather(*[_worker(i) for i in range(5)])

    asyncio.run(_run())
    assert order == [0, 1, 2, 3, 4]


def test_concurrent_different_sessions():
    """Different sessions can hold their locks simultaneously."""
    mutex = SessionMutex()
    held: set[str] = set()
    max_concurrent = 0

    async def _worker(session_id: str):
        nonlocal max_concurrent
        release = await mutex.lock(session_id)
        held.add(session_id)
        max_concurrent = max(max_concurrent, len(held))
        await asyncio.sleep(0.05)
        held.discard(session_id)
        release()

    async def _run():
        await asyncio.gather(*[_worker(f"sess-{i}") for i in range(4)])

    asyncio.run(_run())
    # All 4 different sessions should have been able to run concurrently
    assert max_concurrent == 4


def test_lock_cleanup_allows_next_waiter():
    """After releasing, the next waiter on the same session can proceed."""
    mutex = SessionMutex()
    first_done = asyncio.Event()
    second_started = asyncio.Event()

    async def _first():
        release = await mutex.lock("sess-1")
        first_done.set()
        await asyncio.sleep(0.1)
        release()

    async def _second():
        await first_done.wait()
        release = await mutex.lock("sess-1")
        second_started.set()
        release()

    async def _run():
        await asyncio.gather(_first(), _second())

    asyncio.run(_run())
    assert second_started.is_set()


def test_independent_sessions():
    """Locks for different sessions are independent objects."""
    mutex = SessionMutex()

    async def _run():
        r1 = await mutex.lock("sess-a")
        r2 = await mutex.lock("sess-b")
        # Both locks held simultaneously — different sessions
        assert "sess-a" in mutex._locks
        assert "sess-b" in mutex._locks
        assert mutex._locks["sess-a"] is not mutex._locks["sess-b"]
        r1()
        r2()

    asyncio.run(_run())
