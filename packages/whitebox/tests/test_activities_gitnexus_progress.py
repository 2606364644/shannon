"""Task 7: whitebox activity 注入 progress_cb —— _make_gitnexus_progress_cb 采样 + session 包装。

测 helper 本身（不经真实 AuditSession）：采样规则（final/hit/1/%10）、phase 透传自
sample.phase、best-effort 吞 session 异常。cb=None 路径由 core emitter 兜底，非本层职责。
"""
import pytest

from shannon_whitebox.pipeline.activities import _make_gitnexus_progress_cb
from shannon_core.code_index.progress import ProgressSample


class _FakeSession:
    """记录 log_gitnexus_progress 调用；phase/kind/done/total/hits/detail 全收。"""

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
    for done in range(1, 26):  # all misses (detail=None, not final)
        await cb(_sample(done))
    kinds = [c[1] for c in sess.calls]
    assert kinds == ["progress", "progress", "progress"]  # done == 1, 10, 20
    # phase 透传自 sample
    assert all(c[0] == "sink-discovery" for c in sess.calls)


@pytest.mark.asyncio
async def test_hit_emitted_immediately_regardless_of_done():
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    await cb(_sample(7, detail="'x' @ f.py:1 slot=a", hits=1))  # 7 非采样点
    assert sess.calls[0][1] == "hit"
    assert sess.calls[0][5].startswith("'x'")


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
    for done in (2, 3, 5, 7, 9):  # 非 1、非 %10、无 hit、非 final
        await cb(_sample(done))
    assert sess.calls == []


@pytest.mark.asyncio
async def test_session_exception_swallowed():
    class Boom:
        async def log_gitnexus_progress(self, *a, **k):
            raise RuntimeError("down")

    cb = _make_gitnexus_progress_cb(Boom())
    await cb(_sample(1))  # must not raise


@pytest.mark.asyncio
async def test_phase_threaded_from_sample():
    """phase 不在 helper 硬编码，而是从 sample.phase 透传（chain-verdict 等）。"""
    sess = _FakeSession()
    cb = _make_gitnexus_progress_cb(sess)
    await cb(_sample(1, phase="chain-verdict"))
    assert sess.calls[0][0] == "chain-verdict"
