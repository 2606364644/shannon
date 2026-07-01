"""T7: _make_gitnexus_progress_cb 采样 + 包装 session.log_gitnexus_progress。

采样规则（brief Task 7）：
- final → summary（detail 承载汇总文案）
- detail 非空 → hit（即时上报，不看 done）
- done==1 或 done%10==0 → progress
- 其余静默
phase 透传自 sample.phase（core 的 ProgressEmitter 已带 sink-discovery/
source-discovery/taint-analysis/chain-verdict）。session 异常被吞（best-effort）。
"""
import asyncio
import pytest
from shannon_whitebox.pipeline.activities import _make_gitnexus_progress_cb
from shannon_core.code_index.progress import ProgressSample


class _FakeSession:
    def __init__(self):
        self.calls = []

    async def log_gitnexus_progress(self, phase, kind, done, total, hits, detail=None):
        self.calls.append((phase, kind, done, total, hits, detail))


def _sample(done, *, detail=None, final=False, hits=0, phase="sink-discovery"):
    return ProgressSample(phase, done, 87, hits, detail, final)


@pytest.mark.asyncio
async def test_sampling_progress_emits_only_at_1_and_every_10():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    for done in range(1, 26):
        await cb(_sample(done))   # all misses (detail=None, not final)
    kinds = [c[1] for c in sess.calls]
    assert kinds == ["progress", "progress", "progress"]   # done == 1, 10, 20
    assert all(c[0] == "sink-discovery" for c in sess.calls)  # phase 透传自 sample


@pytest.mark.asyncio
async def test_hit_emitted_immediately_regardless_of_done():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    await cb(_sample(7, detail="'x' @ f.py:1 slot=a", hits=1))   # 7 不是采样点
    assert sess.calls[0][1] == "hit" and sess.calls[0][5].startswith("'x'")


@pytest.mark.asyncio
async def test_summary_emitted_on_final():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    await cb(_sample(87, detail="12 soft sinks", final=True))
    assert sess.calls[0][1] == "summary"


@pytest.mark.asyncio
async def test_mid_range_miss_is_silent():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    for done in (2, 3, 5, 7, 9):   # 非 1、非 %10、无 hit、非 final
        await cb(_sample(done))
    assert sess.calls == []


@pytest.mark.asyncio
async def test_session_exception_swallowed():
    class Boom:
        async def log_gitnexus_progress(self, *a, **k):
            raise RuntimeError("down")
    cb = _make_gitnexus_progress_cb(Boom())
    await cb(_sample(1))   # must not raise
