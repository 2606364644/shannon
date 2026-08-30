"""map_llm_with_bounds 单测 — Semaphore 并发 + 单次 wait_for 超时 + 降级(治本 2)
+ error 类瞬时重试(2026-08-29 网关 5s 抖动致 discovery 补召回整层丢失)。"""
import asyncio
import logging

from supernova_core.code_index.llm_concurrency import map_llm_with_bounds


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
        ["ok", "boom", "ok2"], fn, concurrency=2, per_call_timeout=5,
        transient_retries=0)
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
    """不传 per_call_timeout 时读 SUPERNOVA_LLM_PER_CALL_TIMEOUT(治本 2 配套)。

    覆盖 analyze_taint_llm 调用点(__init__.py:220 不显式传 timeout)。
    """
    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "0.05")

    async def fn(x):
        await asyncio.sleep(10)  # 远超 0.05s env 值 → 必被砍
        return x

    results = await map_llm_with_bounds([1, 2], fn, concurrency=2)
    assert results == []  # 全部按 env 超时跳过


# --- 诊断走 dispatcher 通道, 不再裸 logger.warning 撞 Rich Live footer (2026-07-01) ---
# 背景: worker 进程 redirect_stderr=False 是硬约束(否则 rich 在 sandbox 线程
# circular import 炸 workflow task)。故裸 logger.warning 经 lastResort 直写 stderr,
# Rich Live 不协调, 与 footer spinner 行碰撞 → 首条 timeout 日志被 \r 重绘截断粘连。
# 新策略: per-skip 诊断经注入的 on_skip 回调上报(外层转 emitter.note → progress_cb
# → GitnexusLlmEvent note 行 → dispatcher → Rich Live 协调正确换行); logger 降到
# DEBUG 作文件级兜底(on_skip=None 或排查时 elevate)。全失败仍压 1 条总结(DEBUG)。

_LOGGER = "supernova_core.code_index.llm_concurrency"


async def test_all_fail_emits_single_summary_at_debug_not_warning(caplog):
    """全失败压成 1 条总结(DEBUG), 不再 WARNING 撞终端。

    全失败=系统性(LLM 全挂/API down), per-item 无诊断价值; 总数由外层 finalize
    summary(progress_cb → dispatcher)报告。裸 warning 撞 footer, 故降到 DEBUG。
    """
    async def fn(x):
        raise RuntimeError("LLM down")

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        results = await map_llm_with_bounds(
            [1, 2, 3, 4, 5], fn, concurrency=2, per_call_timeout=5,
            transient_retries=0)

    assert results == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], (
        f"全失败不应 WARNING 撞终端: {[r.getMessage() for r in warnings]}")
    summaries = [r for r in caplog.records if r.levelno == logging.DEBUG
                 and "all" in r.getMessage() and "5/5" in r.getMessage()]
    assert len(summaries) == 1, (
        f"缺全失败总结: {[r.getMessage() for r in caplog.records]}")


async def test_partial_fail_invokes_on_skip_per_item_no_warning(caplog):
    """部分失败: 每个 skip 调一次 on_skip(idx, message)(走 dispatcher), 不再 WARNING。"""
    async def fn(x):
        if x % 2 == 0:
            raise ValueError("boom")
        return x

    skips: list = []

    async def on_skip(idx, message):
        skips.append((idx, message))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await map_llm_with_bounds(
            [1, 2, 3], fn, concurrency=2, per_call_timeout=5, on_skip=on_skip,
            transient_retries=0)

    # 1 失败(x=2, idx=1) → 1 次 on_skip
    assert len(skips) == 1, f"应 1 次 on_skip, 实得 {skips}"
    assert skips[0][0] == 1, f"idx 应为 1(x=2 在 [1,2,3]): {skips}"
    # 不再 WARNING(撞 footer)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], (
        f"不应 WARNING 撞终端: {[r.getMessage() for r in warnings]}")


async def test_timeout_vs_error_distinct_on_skip_messages(caplog):
    """timeout 与 error 经 on_skip message 区分(不再混说 failed/timed out)。"""
    async def fn(x):
        if x == "slow":
            await asyncio.sleep(10)
        elif x == "boom":
            raise RuntimeError("disabled")
        return x

    messages: list = []

    async def on_skip(idx, message):
        messages.append(message)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await map_llm_with_bounds(
            ["slow", "boom", "ok"], fn, concurrency=3, per_call_timeout=0.1,
            on_skip=on_skip, transient_retries=0)

    assert any("timed out" in m for m in messages), f"缺 timeout 措辞: {messages}"
    assert any("failed" in m and "disabled" in m for m in messages), (
        f"缺 error 措辞: {messages}")
    assert "failed/timed out" not in " ".join(messages)


async def test_on_skip_none_falls_back_to_debug(caplog):
    """on_skip=None 时回退 logger.debug(文件级, 不撞终端), 不崩。

    工具函数自洽: 未注入 on_skip(如非 dispatcher 上下文的调用方)仍有 DEBUG 诊断,
    排查时 elevate 级别即可见。绝不再 WARNING 撞 footer。
    """
    async def fn(x):
        if x == "boom":
            raise ValueError("x")
        return x

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await map_llm_with_bounds(
            ["ok", "boom"], fn, concurrency=2, per_call_timeout=5,
            transient_retries=0)  # 不传 on_skip

    debugs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("failed" in m for m in debugs), f"on_skip=None 应回退 DEBUG: {debugs}"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], f"不应 WARNING: {[r.getMessage() for r in warnings]}"


async def test_on_skip_message_carries_per_call_timeout():
    """on_skip message 含 per_call_timeout 数值, 外层不必自己拼。

    ["ok","slow"]: ok 立即成功, slow 超时 → 部分失败 → on_skip 被调(全失败不调)。
    """
    async def fn(x):
        if x == "slow":
            await asyncio.sleep(10)
        return x

    seen: list = []

    async def on_skip(idx, message):
        seen.append(message)

    await map_llm_with_bounds(
        ["ok", "slow"], fn, concurrency=2, per_call_timeout=0.05, on_skip=on_skip)
    assert seen, "on_skip 未被调用(应部分失败: slow 超时, ok 成功)"
    assert ">0.05s" in seen[0], f"message 应含 per_call_timeout: {seen[0]}"


def test_gn_discovery_agent_timeout_default_and_guard(monkeypatch):
    """SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT：默认 300；畸形/<=0 回退+warning。"""
    from supernova_core.config.concurrency import get_gn_discovery_agent_timeout
    monkeypatch.delenv("SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT", raising=False)
    assert get_gn_discovery_agent_timeout() == 300.0
    monkeypatch.setenv("SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT", "600")
    assert get_gn_discovery_agent_timeout() == 600.0
    monkeypatch.setenv("SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT", "bad")
    assert get_gn_discovery_agent_timeout() == 300.0
    monkeypatch.setenv("SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT", "-5")
    assert get_gn_discovery_agent_timeout() == 300.0


async def test_skip_stats_breaks_down_timeout_and_error():
    """skip_stats 传出 timeout/error 分解（2026-08-28 文案误导修复：消费方
    finalize 原把 skipped 合计一律叫 "N timeouts"，141s<300s 地板的 agent
    失败被误读为超时）。"""
    async def fn(x):
        if x == "slow":
            await asyncio.sleep(10)
        if x == "boom":
            raise ValueError("boom")
        return x

    stats: dict = {}
    results = await map_llm_with_bounds(
        ["ok", "slow", "boom", "ok2"], fn, concurrency=4,
        per_call_timeout=0.1, skip_stats=stats, transient_retries=0)
    assert sorted(results) == ["ok", "ok2"]
    assert stats == {"timeout": 1, "error": 1}


async def test_skip_stats_none_keeps_behavior():
    """不传 skip_stats：行为不变（零破坏，存量调用方无需改）。"""
    async def fn(x):
        return x
    results = await map_llm_with_bounds(
        [1, 2], fn, concurrency=2, per_call_timeout=5)
    assert results == [1, 2]


# --- error 类瞬时重试（2026-08-29：NodeGoat-20260828-162655 网关 5s 抖动，
# Connection error 600ms 即死致 sink/source/storage-r 补召回整层丢失——
# 同一错误 pre-recon 走 Temporal 重试，discovery 却零容忍永久跳过） ---

async def test_transient_error_retries_once_then_succeeds():
    """首次 Connection error、重试成功 → 正常成功项，skip_stats 零 error。"""
    calls = {"flaky": 0}

    async def fn(x):
        if x == "flaky":
            calls["flaky"] += 1
            if calls["flaky"] == 1:
                raise RuntimeError("Connection error.")
        return x

    stats: dict = {}
    results = await map_llm_with_bounds(
        ["ok", "flaky"], fn, concurrency=2, per_call_timeout=5,
        transient_retries=1, transient_retry_delay=0, skip_stats=stats)
    assert sorted(results) == ["flaky", "ok"], (
        f"瞬时错误重试后应恢复成功: {results}")
    assert stats == {"timeout": 0, "error": 0}, f"不应计 skip: {stats}"


async def test_transient_error_retries_exhausted_counts_error():
    """恒败错误：原始 + 重试共 2 次调用，耗尽后仍计 error skip（语义不变）。"""
    calls = {"n": 0}

    async def fn(x):
        calls["n"] += 1
        raise RuntimeError("Connection error.")

    stats: dict = {}
    results = await map_llm_with_bounds(
        [1], fn, concurrency=1, per_call_timeout=5,
        transient_retries=1, transient_retry_delay=0, skip_stats=stats)
    assert results == []
    assert calls["n"] == 2, f"应 1 原始 + 1 重试 = 2 次调用, 实得 {calls['n']}"
    assert stats == {"timeout": 0, "error": 1}


async def test_timeout_not_retried():
    """timeout 不重试（幂等超时重试只是再超时一遍，juice-shop 3×10min 教训）。"""
    calls = {"n": 0}

    async def fn(x):
        calls["n"] += 1
        await asyncio.sleep(10)

    stats: dict = {}
    results = await map_llm_with_bounds(
        [1], fn, concurrency=1, per_call_timeout=0.05,
        transient_retries=1, transient_retry_delay=0, skip_stats=stats)
    assert results == []
    assert calls["n"] == 1, f"timeout 只跑一次, 实得 {calls['n']}"
    assert stats == {"timeout": 1, "error": 0}


async def test_default_enables_one_transient_retry(monkeypatch):
    """不传参数 → 默认重试 1 次（6 个调用方零改动受益）。delay 经 env 置 0 提速。"""
    monkeypatch.setenv("SUPERNOVA_LLM_TRANSIENT_RETRY_DELAY", "0")
    calls = {"n": 0}

    async def fn(x):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("peer closed connection without sending "
                               "complete message body")
        return x

    results = await map_llm_with_bounds([1], fn, concurrency=1, per_call_timeout=5)
    assert results == [1], "默认重试应救回瞬时失败"
    assert calls["n"] == 2


def test_transient_retry_env_getters_default_and_guard(monkeypatch):
    """SUPERNOVA_LLM_TRANSIENT_RETRIES / _DELAY：默认 1 次 / 10s；畸形/负值回退。"""
    from supernova_core.config.concurrency import (
        get_transient_retries, get_transient_retry_delay)
    monkeypatch.delenv("SUPERNOVA_LLM_TRANSIENT_RETRIES", raising=False)
    monkeypatch.delenv("SUPERNOVA_LLM_TRANSIENT_RETRY_DELAY", raising=False)
    assert get_transient_retries() == 1
    assert get_transient_retry_delay() == 10.0
    monkeypatch.setenv("SUPERNOVA_LLM_TRANSIENT_RETRIES", "2")
    monkeypatch.setenv("SUPERNOVA_LLM_TRANSIENT_RETRY_DELAY", "30")
    assert get_transient_retries() == 2
    assert get_transient_retry_delay() == 30.0
    monkeypatch.setenv("SUPERNOVA_LLM_TRANSIENT_RETRIES", "bad")
    monkeypatch.setenv("SUPERNOVA_LLM_TRANSIENT_RETRY_DELAY", "-5")
    assert get_transient_retries() == 1
    assert get_transient_retry_delay() == 10.0
