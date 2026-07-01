"""map_llm_with_bounds 单测 — Semaphore 并发 + 单次 wait_for 超时 + 降级(治本 2)."""
import asyncio
import logging

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


async def test_default_timeout_resolved_from_env(monkeypatch):
    """不传 per_call_timeout 时读 SHANNON_LLM_PER_CALL_TIMEOUT(治本 2 配套)。

    覆盖 analyze_taint_llm 调用点(__init__.py:220 不显式传 timeout)。
    """
    monkeypatch.setenv("SHANNON_LLM_PER_CALL_TIMEOUT", "0.05")

    async def fn(x):
        await asyncio.sleep(10)  # 远超 0.05s env 值 → 必被砍
        return x

    results = await map_llm_with_bounds([1, 2], fn, concurrency=2)
    assert results == []  # 全部按 env 超时跳过


# --- 日志质量: 全失败压缩 + timeout/error 措辞区分 (2026-07-01) ---
# 背景: 关闭 GitNexus LLM(SHANNON_GITNEXUS_LLM_ENABLED=0)或 LLM 全挂时,
# 旧实现对每个 item 打 "failed/timed out (>60s)" warning,N 个 item 刷屏 N 条,
# 且措辞把"瞬间 raise"误说成"超时 >60s"。新策略: 全失败压 1 条总结,
# 部分失败保留 per-item 诊断, 且 timeout 与 error 措辞分开。

_LOGGER = "shannon_core.code_index.llm_concurrency"


async def test_all_fail_emits_single_summary(caplog):
    """全部 item 失败(典型 = LLM 全挂/API down)压成 1 条总结, 不再 per-item 刷屏。"""
    async def fn(x):
        raise RuntimeError("LLM down")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        results = await map_llm_with_bounds(
            [1, 2, 3, 4, 5], fn, concurrency=2, per_call_timeout=5)

    assert results == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, (
        f"全失败应只 1 条总结, 实得 {len(warnings)}: {[r.getMessage() for r in warnings]}")
    msg = warnings[0].getMessage()
    assert "all" in msg and "5/5" in msg and "skipped" in msg


async def test_partial_fail_keeps_per_item_diagnostics(caplog):
    """部分失败保留 per-item 诊断(量小) + 1 条总结。"""
    async def fn(x):
        if x % 2 == 0:
            raise ValueError("boom")
        return x

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await map_llm_with_bounds([1, 2, 3], fn, concurrency=2, per_call_timeout=5)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # 1 失败 → 1 per-item + 1 总结 = 2
    assert len(warnings) == 2, (
        f"部分失败应 per-item + 总结共 2 条, 实得 {len(warnings)}: "
        f"{[r.getMessage() for r in warnings]}")


async def test_timeout_vs_error_distinct_messages(caplog):
    """timeout 与 error 措辞区分, 不再混说 'failed/timed out (>60s)'。"""
    async def fn(x):
        if x == "slow":
            await asyncio.sleep(10)
        elif x == "boom":
            raise RuntimeError("disabled")
        return x

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await map_llm_with_bounds(
            ["slow", "boom", "ok"], fn, concurrency=3, per_call_timeout=0.1)

    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("timed out" in m for m in msgs), f"缺 timeout 措辞: {msgs}"
    assert any("failed" in m and "disabled" in m for m in msgs), f"缺 error 措辞: {msgs}"
    # 不再出现失实的混合措辞
    assert "failed/timed out" not in " ".join(msgs)
