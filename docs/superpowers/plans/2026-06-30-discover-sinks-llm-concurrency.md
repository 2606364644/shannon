# GitNexus 轨 activity 内串行 LLM 并发化（治本 2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `run_code_index` 内 `discover_sinks_llm`（③b）与 `analyze_taint_llm` 调用点（⑤）的串行 per-function LLM 调用，改成 Semaphore 并发 + 单次 `wait_for` 超时 + 降级，让大仓 code_index 在 10 分钟 activity 超时内跑完，不再卡死。

**Architecture:** 提取共用 helper `map_llm_with_bounds`（`Semaphore(concurrency)` + 每项 `asyncio.wait_for(per_call_timeout)` + 异常/超时降级跳过）。③b 与 ⑤ 两处串行循环都改用它。并发上限复用 `SHANNON_MAX_CONCURRENT`（`get_max_concurrent()`，默认 3）；单次超时 60s 常量。全量召回方向（不设总 budget）。

**Tech Stack:** Python 3.13 / asyncio / pytest（asyncio mode=AUTO，`async def test_*` 自动收集，无需 `@pytest.mark.asyncio`）/ temporalio（不涉及，改的是 activity 内部纯函数编排）。

## Global Constraints

- **守 CLAUDE.md §1 双轨铁律**：本计划只动 GitNexus 轨（确定性层）的 LLM 调用编排，**不改 LLM 轨**（`vuln-*.txt` / PRE_RECON / recon）任何 prompt 或行为，不向 LLM 轨喂确定性产物。
- **降级契约不变**：`SHANNON_GITNEXUS_LLM_ENABLED=0` / `llm_client=None` / LLM raise → ③b 返回空、⑤ 走各自 fallback，行为与改造前一致（仅编排从串行变并发）。
- **`discover_sinks_llm` 返回类型不变**：`(list[SinkCallSite], list[RuleGap])`，soft sink 的 `rule_id="llm-discovered"` / `needs_review=True` 不变。
- **不回退治本 1**：`run_code_index` 的 `retry_for("code-index")`（commit `17251610`）不动。
- **不新增 env**：并发复用 `SHANNON_MAX_CONCURRENT`；单次超时是 60s 常量。
- **只跑改动相关测试**（CLAUDE.md）：勿广跑全套（有预存 hang）。
- **frequent commits**：每个 Task 结束 commit。

**Spec：** `docs/superpowers/specs/2026-06-30-discover-sinks-llm-concurrency-design.md`

---

## File Structure

- **Create** `packages/core/src/shannon_core/code_index/llm_concurrency.py` — 共用 helper `map_llm_with_bounds`（并发 + 单次超时 + 降级）。单一职责，③b/⑤/未来串行 LLM 都复用。
- **Create** `packages/core/tests/code_index/test_llm_concurrency.py` — helper 单测。
- **Modify** `packages/core/src/shannon_core/code_index/sink_discovery_llm.py:225-257` — `discover_sinks_llm` 串行循环改并发；加 `concurrency` / `per_call_timeout` kwargs（测试注入用）。
- **Modify** `packages/core/tests/code_index/test_sink_discovery_llm.py` — 加并发降级测试；保现有 6 个契约测试。
- **Modify** `packages/core/src/shannon_core/code_index/__init__.py:175-184` — ⑤ taint analysis 串行 for 改 `_taint_one` + helper。

---

### Task 1: 共用 helper `map_llm_with_bounds` + 单测

**Files:**
- Create: `packages/core/src/shannon_core/code_index/llm_concurrency.py`
- Test: `packages/core/tests/code_index/test_llm_concurrency.py`

**Interfaces:**
- Produces: `map_llm_with_bounds(items: list[T], fn: Callable[[T], Awaitable[R]], *, concurrency: int, per_call_timeout: float = 60.0, label: str = "llm") -> list[R]`；常量 `DEFAULT_PER_CALL_TIMEOUT = 60.0`。Task 2/3 消费它。

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/code_index/test_llm_concurrency.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_llm_concurrency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shannon_core.code_index.llm_concurrency'`

- [ ] **Step 3: Write minimal implementation**

Create `packages/core/src/shannon_core/code_index/llm_concurrency.py`:

```python
"""GitNexus 轨 activity 内 LLM 并发执行工具。

把 activity 内的串行 per-function LLM 调用改成 Semaphore 限并发 + 单次
wait_for 超时 + 降级,防大仓 N 个函数累加拖垮 activity 的 start_to_close_timeout。
详见 docs/superpowers/specs/2026-06-30-discover-sinks-llm-concurrency-design.md。
"""
import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# 单次 LLM 调用(含 provider 内部 retry)上限秒数。超过即降级跳过该函数。
# 60s 对 GLM medium tier 单次 prompt→JSON 足够;analyze_taint_llm 内部 retry 在此
# 内会被 cancel(有意,防 retry 累加)。后续如需可 env 化。
DEFAULT_PER_CALL_TIMEOUT = 60.0


async def map_llm_with_bounds(
    items: list[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
    per_call_timeout: float = DEFAULT_PER_CALL_TIMEOUT,
    label: str = "llm",
) -> list[R]:
    """并发跑 fn(item):Semaphore(concurrency) 限并发 + 每个套 wait_for(per_call_timeout)。

    单次超时/异常 → 该项跳过(warning log),gather 不因单个失败而 fail。
    返回成功项结果列表(丢弃 None)。顺序为并发完成序,不保证与 items 一致。
    """
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(idx: int, item: T) -> R | None:
        async with sem:
            try:
                return await asyncio.wait_for(fn(item), timeout=per_call_timeout)
            except Exception as exc:  # 含 asyncio.TimeoutError
                logger.warning(
                    "%s[%d] failed/timed out (>%ss), skipped: %s",
                    label, idx, per_call_timeout, exc,
                )
                return None

    results = await asyncio.gather(*[_bounded(i, x) for i, x in enumerate(items)])
    successes = [r for r in results if r is not None]
    skipped = len(items) - len(successes)
    if skipped:
        logger.warning(
            "%s: %d/%d items skipped (timeout/error)", label, skipped, len(items))
    return successes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_llm_concurrency.py -v`
Expected: PASS, 5 tests green.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/llm_concurrency.py packages/core/tests/code_index/test_llm_concurrency.py
git commit -m "feat(code_index): 新增 map_llm_with_bounds helper(并发+单次超时+降级)"
```

---

### Task 2: `discover_sinks_llm`（③b）改并发

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`（`discover_sinks_llm` 函数，line 225-257）
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py`（加 1 个并发降级测试；保现有 6 个）

**Interfaces:**
- Consumes: Task 1 的 `map_llm_with_bounds` / `DEFAULT_PER_CALL_TIMEOUT`；`shannon_core.config.concurrency.get_max_concurrent`。
- Produces: `discover_sinks_llm` 新增 kwargs `concurrency: int | None = None`、`per_call_timeout: float | None = None`（默认走 env / 60s）。返回类型不变。

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/code_index/test_sink_discovery_llm.py`:

```python
async def test_discover_partial_failure_keeps_successful_sinks():
    """并发改造(治本2):部分函数 LLM 挂死(超时)→ 被跳过,成功函数仍产 soft sink。

    两个不同 block 的 suspicious(raw_query + exec_one),并发跑;raw_query 函数
    正常返回,exec_one 函数挂死 → per_call_timeout 砍掉它。成功的 raw_query
    soft sink 必须保留(不被并发的失败项带垮)。
    """
    import asyncio
    calls = [
        _suspicious(line=1, callee="raw_query"),
        _suspicious(line=2, callee="exec_one"),
    ]

    async def client(prompt, **kw):
        if "raw_query:1" in prompt:
            return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                                "category": "sql", "slot": "sql_value",
                                "arg_index": 0, "rationale": "x"}])
        await asyncio.sleep(10)  # exec_one 挂死

    soft, _ = await discover_sinks_llm(
        calls, client, concurrency=2, per_call_timeout=0.2)
    assert len(soft) == 1
    assert soft[0].callee_name == "raw_query"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/code_index/test_sink_discovery_llm.py::test_discover_partial_failure_keeps_successful_sinks -v`
Expected: FAIL — 当前 `discover_sinks_llm` 串行 + 无 `concurrency`/`per_call_timeout` kwargs。失败信息为 `TypeError: unexpected keyword argument 'concurrency'` 或挂起（exec_one sleep 10s 拖垮，因串行无超时，测试会 hang 到 pytest 超时）。

> 注：若测试 hang（串行 exec_one sleep 10s 无超时砍），Ctrl+C 中断后继续——这正是要修的 bug 表现。

- [ ] **Step 3: Modify `discover_sinks_llm` to use the helper**

In `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`:

顶部 import 块（现有 `from shannon_core.code_index.sink_detector import (...)` 之后）加：

```python
from shannon_core.code_index.llm_concurrency import (
    DEFAULT_PER_CALL_TIMEOUT,
    map_llm_with_bounds,
)
from shannon_core.config.concurrency import get_max_concurrent
```

替换整个 `discover_sinks_llm` 函数（line 225-257）为：

```python
async def discover_sinks_llm(
    suspicious: list[SuspiciousCall],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """对含可疑 call 的函数并发调 LLM, 判定哪些是真 sink → 软 SinkCallSite + RuleGap。

    LLM 不可用(None / raise / 超时 / 不可解析)→ 该函数跳过, 返回空(降级, spec §3.5)。
    调用粒度 = function 级(去重分组, 一函数一次 LLM 调用)。
    并发由 concurrency(Semaphore)限, 默认 get_max_concurrent()(SHANNON_MAX_CONCURRENT);
    单次调用超过 per_call_timeout(默认 DEFAULT_PER_CALL_TIMEOUT=60s)→ 该函数降级跳过。
    大仓 N 个函数并发跑,防串行累加拖垮 activity 的 start_to_close_timeout(治本 2)。
    """
    if llm_client is None or not suspicious:
        return [], []
    by_func: dict[str, list[SuspiciousCall]] = defaultdict(list)
    for sc in suspicious:
        by_func[sc.block.id].append(sc)

    async def _discover_one(item: tuple[str, list[SuspiciousCall]]) -> list[SinkCallSite]:
        _, calls = item
        block = calls[0].block
        prompt = _build_discovery_prompt(block, calls)
        raw = await llm_client(prompt)
        verdicts = _parse_verdicts(raw)
        vmap = {str(v.get("call_ref")): v for v in verdicts}
        out: list[SinkCallSite] = []
        for sc in calls:
            v = vmap.get(f"{sc.callee}:{sc.line}")
            if v is None or not v.get("is_sink"):
                continue
            out.append(_to_soft_sink(sc, v))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    timeout = (per_call_timeout if per_call_timeout is not None
               else DEFAULT_PER_CALL_TIMEOUT)
    per_func = await map_llm_with_bounds(
        list(by_func.items()), _discover_one,
        concurrency=conc, per_call_timeout=timeout, label="discover_sinks_llm",
    )
    soft_sinks: list[SinkCallSite] = [s for func_sinks in per_func for s in func_sinks]
    return soft_sinks, _aggregate_gaps(soft_sinks)
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest packages/core/tests/code_index/test_sink_discovery_llm.py::test_discover_partial_failure_keeps_successful_sinks -v`
Expected: PASS（exec_one 被 0.2s 超时砍掉，raw_query 成功保留）。

- [ ] **Step 5: Run full sink_discovery_llm test file to verify no regression**

Run: `uv run pytest packages/core/tests/code_index/test_sink_discovery_llm.py -v`
Expected: PASS, 全部绿（含现有 6 个契约测试：`test_discover_produces_soft_sink` / `test_discover_skips_non_sink` / `test_discover_degrades_when_llm_unavailable` / `test_gap_aggregation` / `test_soft_sink_flows_into_intra_hits` / `test_soft_sink_does_not_break_injection_whitelist` + 新增 1 个）。

> 现有测试不传 `concurrency` → 走默认 `get_max_concurrent()`（默认 3）；N=1/2 suspicious → 并发槽充足 → 行为与串行等价（仅 soft_sinks 顺序可能变，但现有断言不依赖顺序）。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/tests/code_index/test_sink_discovery_llm.py
git commit -m "feat(code_index): discover_sinks_llm 改并发(map_llm_with_bounds),防大仓串行累加超时"
```

---

### Task 3: ⑤ `analyze_taint_llm` 调用点改并发

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:175-184`（⑤ taint analysis 串行 for 循环）

**Interfaces:**
- Consumes: Task 1 的 `map_llm_with_bounds`；`get_max_concurrent`；现有 `analyze_taint_llm`（不改其签名/内部）。

- [ ] **Step 1: Modify the ⑤ taint loop to use the helper**

In `packages/core/src/shannon_core/code_index/__init__.py`，找到 ⑤ 这段（约 line 175-184）：

```python
    intra_results = {}
    for func_id, func_sinks in sinks_by_func.items():
        block = blocks_by_id.get(func_id)
        if block is None:
            continue
        intra_results[func_id] = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            llm_client=llm_client,
        )
```

替换为：

```python
    # ⑤ LLM taint analysis (only for functions with sinks) — 并发(治本 2):
    # 串行 per-function LLM 会被 ③b 产的 soft sinks 放大,拖垮 activity 超时。
    async def _taint_one(item):
        func_id, func_sinks = item
        block = blocks_by_id.get(func_id)
        if block is None:
            return None
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            llm_client=llm_client,
        )
        return (func_id, result)

    from shannon_core.code_index.llm_concurrency import map_llm_with_bounds
    from shannon_core.config.concurrency import get_max_concurrent
    taint_pairs = await map_llm_with_bounds(
        list(sinks_by_func.items()), _taint_one,
        concurrency=get_max_concurrent(),
        label="analyze_taint_llm",
    )
    intra_results = {func_id: result for func_id, result in taint_pairs}
```

> 说明：`map_llm_with_bounds` 已过滤 `None`（`block is None` 的项 + 超时/异常项），故 `taint_pairs` 全是 `(func_id, result)` 元组，dict 推导无需 `if`。`analyze_taint_llm` 本身不改（内部 retry + 确定性 fallback 保留）；helper 的 `wait_for` 覆盖整个 `analyze_taint_llm` 调用——单函数超 60s 即降级跳过（spec §4.2 权衡 1：该函数 taint 结果缺失，可接受）。

- [ ] **Step 2: Verify import + syntax**

Run: `uv run python -c "from shannon_core.code_index import build_code_index_with_gitnexus; print('import ok')"`
Expected: `import ok`（确认 `__init__.py` 改动无语法/import 错误）。

- [ ] **Step 3: Run existing call-graph integration test to verify no regression**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: PASS（现有集成测试全绿；⑤ 改造逻辑等价于"并发版的 for 循环"，结果集相同）。

> 若该测试有预存 hang/失败（CLAUDE.md 警告全套有预存问题），单独记录预存失败项，确认**本 Task 改动未引入新失败**即可（对比改造前后）。

- [ ] **Step 4: Run helper + discover tests together (full chain)**

Run: `uv run pytest packages/core/tests/code_index/test_llm_concurrency.py packages/core/tests/code_index/test_sink_discovery_llm.py -v`
Expected: PASS, 全绿。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py
git commit -m "feat(code_index): analyze_taint_llm 调用点改并发,⑤ taint 不再串行累加超时"
```

---

## 验证（全计划完成后的回归）

- [ ] `uv run pytest packages/core/tests/code_index/test_llm_concurrency.py packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_llm_taint_analyzer.py -v` — helper + ③b + analyze_taint_llm 单测全绿。
- [ ] `uv run pytest packages/core/tests/test_retry_profiles.py packages/whitebox/tests/test_retry_policy_coverage.py packages/whitebox/tests/test_run_code_index.py -v` — 治本 1 的 17 测试仍绿（确认未回退）。
- [ ] （可选，真机）`SHANNON_MAX_CONCURRENT=8 uv run shannon-whitebox start --repo /Users/mango/project/vuln-range/juice-shop` — 观察 code_index 在 10min 内跑完（discover_sinks_llm + taint 并发），不再反复 10min 超时重试。
