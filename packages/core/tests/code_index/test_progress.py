import asyncio
import pytest
from shannon_core.code_index.progress import ProgressEmitter, ProgressSample


def test_sample_is_frozen():
    s = ProgressSample("sink-discovery", 1, 10, 0, None)
    assert s.phase == "sink-discovery" and s.final is False
    with pytest.raises(Exception):
        s.done = 2  # frozen


async def _drain(emitter, ticks):
    """Simulate concurrent per-item ticks via gather (like map_llm_with_bounds)."""
    async def one(detail, delta):
        await emitter.tick(detail=detail, hits_delta=delta)
    await asyncio.gather(*[one(d, delta) for d, delta in ticks])


@pytest.mark.asyncio
async def test_tick_counts_done_and_hits():
    seen = []
    emitter = ProgressEmitter("sink-discovery", 3, lambda s: seen.append(s) or asyncio.sleep(0))
    await emitter.tick(detail=None, hits_delta=0)       # miss
    await emitter.tick(detail="hit-A", hits_delta=1)    # hit
    await emitter.tick(detail="hit-B", hits_delta=2)    # hit
    assert seen[-1].done == 3 and seen[-1].hits == 3
    assert seen[1].detail == "hit-A" and seen[2].detail == "hit-B"


@pytest.mark.asyncio
async def test_finalize_emits_final_sample():
    seen = []
    emitter = ProgressEmitter("chain-verdict", 5, lambda s: seen.append(s) or asyncio.sleep(0))
    await emitter.tick(hits_delta=1)
    await emitter.finalize("5 vulnerable · 4.2s/chain avg")
    assert seen[-1].final is True
    assert seen[-1].detail == "5 vulnerable · 4.2s/chain avg"


@pytest.mark.asyncio
async def test_cb_none_is_noop():
    emitter = ProgressEmitter("taint-analysis", 2, None)   # cb=None
    await emitter.tick(detail="x", hits_delta=1)           # must not raise
    await emitter.finalize("done")                         # must not raise


@pytest.mark.asyncio
async def test_cb_exception_is_swallowed():
    async def boom(s):
        raise RuntimeError("display channel down")
    emitter = ProgressEmitter("sink-discovery", 2, boom)
    await emitter.tick(detail="x", hits_delta=1)           # must not raise
    await emitter.finalize("done")


@pytest.mark.asyncio
async def test_concurrent_ticks_do_not_lose_count():
    seen = []
    emitter = ProgressEmitter("sink-discovery", 50, lambda s: seen.append(s) or asyncio.sleep(0))
    await _drain(emitter, [(None, 0)] * 40 + [("hit", 1)] * 10)
    assert emitter._done == 50 and emitter._hits == 10


@pytest.mark.asyncio
async def test_note_emits_sample_without_changing_counts():
    """emitter.note 发带 note 的 sample, 不改 done/hits 计数(detail=None, final=False)。

    用于 map_llm_with_bounds 的 per-skip 诊断(timeout/error)经 progress_cb 上报 —— 走
    dispatcher 通道(GitnexusLlmEvent note 行, Rich Live 协调正确换行), 而非裸
    logger.warning(撞 Rich Live footer, 因 redirect_stderr=False 是硬约束)。
    """
    seen = []
    emitter = ProgressEmitter("sink-discovery", 5, lambda s: seen.append(s) or asyncio.sleep(0))
    await emitter.tick(hits_delta=1)                       # done=1, hits=1
    await emitter.note("[f1] timed out (>60s), skipped")
    assert seen[-1].note == "[f1] timed out (>60s), skipped"
    assert seen[-1].detail is None
    assert seen[-1].final is False
    # note 不改计数(区别于 tick)
    assert emitter._done == 1 and emitter._hits == 1


@pytest.mark.asyncio
async def test_note_cb_none_and_exception_swallowed():
    """note 与 tick/finalize 同为 best-effort: cb=None no-op, cb raise 吞掉。"""
    emitter = ProgressEmitter("sink-discovery", 2, None)
    await emitter.note("x")  # must not raise

    async def boom(s):
        raise RuntimeError("display channel down")
    emitter2 = ProgressEmitter("sink-discovery", 2, boom)
    await emitter2.note("x")  # must not raise
