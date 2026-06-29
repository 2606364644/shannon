# GitNexus 轨 activity 内串行 LLM 并发化（治本 2）

- 日期：2026-06-30
- 状态：设计获批，待出实现计划
- 分支：`feat/fork-py`
- 关联：`2026-06-30` code_index retry 误配修复（治本 1，commit `17251610`）

## 1. 背景

白盒 pre-recon 阶段 `run_code_index`（确定性 GitNexus 轨）对大仓（juice-shop）会跑满 10 分钟 `start_to_close_timeout` 超时。`activity_failures.log` 堆栈定位卡点：

```
run_code_index → build_code_index_with_gitnexus
  → discover_sinks_llm(suspicious, llm_client)        # ③b LLM sink 补召回
    → await llm_client(prompt)                        # 串行,逐函数
```

根因有两层：

- **放大层（治本 1，已修）**：`run_code_index` 原用 `retry_for("standard")` = `PRODUCTION_RETRY(max 50)`，把幂等的 10 分钟超时放大成 50× ≈ 数小时卡死。已改 `CODE_INDEX_RETRY(max 3)`。
- **源头（治本 2，本 spec）**：`discover_sinks_llm` 对 N 个含可疑 call 的函数**串行** `await llm_client(prompt)`，单次调用（GLM medium tier，含 provider 内部 retry）耗时不可控，N × 单次累加超过 activity 的 10 分钟。现有 `try/except` 只兜 `raise`，**不兜"慢/挂死"**。

同样模式在 `build_code_index_with_gitnexus` 的 ⑤ `analyze_taint_llm` 调用点（`__init__.py:175-184`）重复：`for func_id, func_sinks: await analyze_taint_llm(...)` 串行 per-function LLM。且 ⑤ 的规模 = 有 sink 的函数数（含 ③b 产的 `rule_id="llm-discovered"` soft sinks，会被 ③b 放大）——只修 ③b，⑤ 会接力卡。

## 2. 目标 / 非目标

**目标**

- ③b `discover_sinks_llm` 与 ⑤ `analyze_taint_llm` 调用点：串行 → 并发 + 单次超时，让 code_index 在限时内跑完 N 个函数的 LLM 判定（全量召回）。
- 单次 LLM 调用挂死/超时不再拖垮整个 activity：单次超时即该项降级跳过，其余继续。
- 并发上限走 env（复用 `SHANNON_MAX_CONCURRENT`）。

**非目标**

- 不改 `run_claude_prompt` / provider 层的超时（超出本 spec 范围）。
- 不改 LLM 轨（PRE_RECON / vuln agent）的任何 prompt 或行为（守 CLAUDE.md §1 双轨铁律）。
- 不改 `run_code_index` 的 `start_to_close_timeout=10min`（治本 1 已把 retry 收到 max 3；activity 超时是 Temporal 硬上限，本 spec 靠并发在其内跑完）。
- 不新增 env 给单次超时（YAGNI；仅并发走 env）。

## 3. 设计

### 3.1 新增共用 helper

新文件 `packages/core/src/shannon_core/code_index/llm_concurrency.py`：

```python
"""GitNexus 轨 activity 内 LLM 并发执行工具。

把 activity 内的串行 per-function LLM 调用改成 Semaphore 限并发 + 单次
wait_for 超时 + 降级,防大仓 N 个函数累加拖垮 activity 的 start_to_close_timeout。
"""
import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")
R = TypeVar("R")

# 单次 LLM 调用(含 provider 内部 retry)上限。超过即认为该函数判定失败,降级跳过。
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

    单次超时/异常 → 该项跳过(warning log),返回 None;gather 不因单个失败而 fail。
    返回成功项结果列表(丢弃 None)。顺序不保证与 items 一致(并发完成序)。
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
        logger.warning("%s: %d/%d items skipped (timeout/error)",
                       label, skipped, len(items))
    return successes
```

### 3.2 `discover_sinks_llm`（③b）改造

`packages/core/src/shannon_core/code_index/sink_discovery_llm.py`：

- 把 `for func_id, calls in by_func.items(): ... await llm_client(prompt)` 串行循环，重构为：每个函数包成 `_discover_one(item)` coroutine（`build_prompt` → `llm_client` → `parse` → 该函数的 `list[SinkCallSite]`）。
- 交 `map_llm_with_bounds` 并发跑，`concurrency=get_max_concurrent()`，`label="discover_sinks_llm"`。
- `_discover_one` 内部**移除** `try/except`（helper 统一兜超时/异常）；`_parse_verdicts` 内部已有的解析容错保留。
- 末尾 flatten 成 `soft_sinks`，`_aggregate_gaps(soft_sinks)` 不变。

签名变更：新增关键字参数 `concurrency: int | None = None`（默认走 `get_max_concurrent()`），便于测试注入。`per_call_timeout` 同理可注入（测试用短值）。

LLM 不可用契约不变：`llm_client is None` 仍 early-return `[], []`（line 234-235）；raise-client（`SHANNON_GITNEXUS_LLM_ENABLED=0`）经 helper 每项捕获 → 全跳过 → 空结果。

### 3.3 ⑤ taint analysis 改造

`packages/core/src/shannon_core/code_index/__init__.py:175-184`：

```python
# 旧(串行):
intra_results = {}
for func_id, func_sinks in sinks_by_func.items():
    block = blocks_by_id.get(func_id)
    if block is None:
        continue
    intra_results[func_id] = await analyze_taint_llm(block, func_sinks, llm_client)

# 新(并发):
async def _taint_one(item):
    func_id, func_sinks = item
    block = blocks_by_id.get(func_id)
    if block is None:
        return None
    return (func_id, await analyze_taint_llm(block, func_sinks, llm_client))

pairs = await map_llm_with_bounds(
    list(sinks_by_func.items()), _taint_one,
    concurrency=get_max_concurrent(),
    label="analyze_taint_llm",
)
intra_results = {func_id: result for func_id, result in pairs if result is not None}
```

`analyze_taint_llm` 本身不改（它内部的 `retry_count+1` retry + 确定性 fallback 保留）；helper 的 `wait_for` 覆盖整个 `analyze_taint_llm` 调用。

## 4. 错误处理 & 权衡

### 4.1 单次超时/异常 → 降级跳过

helper 的 `_bounded` 捕获一切（含 `asyncio.TimeoutError`），单项失败不传播。这与现有"LLM 不可用 → 降级"哲学一致（spec §3.5 / 立场 B）。

### 4.2 权衡 1：超时跳过的函数丢失 taint fallback（已知，接受）

`analyze_taint_llm` 的确定性 fallback（is_entry_hint 立场 B）只在 LLM"失败返回"时触发。helper 的 `wait_for` cancel 的是**整个 coroutine**，超时时 fallback 不触发 → 该函数 taint 结果缺失 → `propagate_across_chains` 拿不到该函数的 intra result。

接受理由：60s 阈值下正常函数不触发；丢极少数慢函数的 taint 远好过整个 activity 超时卡死。

### 4.3 权衡 2：极大仓仍可能跑满 activity（已知，靠 env 缓解）

本设计选"全量召回"（不设总 budget 主动 break）。对极大仓（N 很大），即使 `concurrency=3` + 60s 超时，仍可能跑满 10min `start_to_close_timeout` → code_index activity 失败（治本 1 的 max 3 retry 对幂等超时无效）。

缓解：在 `.env` 调高 `SHANNON_MAX_CONCURRENT`（如 8）以加速。若未来要"绝不超时"，再叠加总 budget 降级（非本 spec 范围）。

### 4.4 并发安全

- `_discover_one` / `_taint_one` 各自独立（无共享可变状态），并发安全。
- `soft_sinks` / `intra_results` 在 gather 后单线程汇总，无竞争。
- LLM 调用无副作用（纯判定），并发无副作用风险；rate-limit 压力由 `SHANNON_MAX_CONCURRENT`（默认 3）约束。

## 5. 测试策略（TDD）

### 5.1 helper 单测（`tests/code_index/test_llm_concurrency.py`）

- `test_all_items_succeed`：N 项全成功，结果全收，无跳过。
- `test_slow_item_times_out_and_skipped`：一项 `asyncio.sleep` 挂死 + `per_call_timeout` 极短 → 该项跳过，其余成功收齐。
- `test_raising_item_skipped`：一项 raise → 跳过，其余成功。
- `test_semaphore_limits_concurrency`：`concurrency=2` + 并发计数探针 → 同时在跑的 ≤ 2。
- `test_empty_items_returns_empty`。

### 5.2 `discover_sinks_llm` 改造后

- 保现有契约：`llm_client=None` → `[], []`。
- `test_partial_failure_keeps_successful_sinks`：mock `llm_client` 部分慢/挂（`concurrency` + 短 `per_call_timeout` 注入），成功函数仍产 soft sink，失败函数被跳过。
- `test_concurrency_param_respected`：`concurrency=1` 时串行（退化），`concurrency=N` 时并发（靠耗时验证）。

### 5.3 ⑤ taint 改造

`__init__.py` 内联改造，靠 helper 单测 + 现有 `build_code_index_with_gitnexus` 集成测试覆盖，不单独 mock 内联代码。

## 6. 不变量（必须守住）

- **CLAUDE.md §1 双轨铁律**：本 spec 只动 GitNexus 轨（确定性层）的 LLM 调用编排，不改 LLM 轨（`vuln-*.txt` / PRE_RECON）任何 prompt 或行为；不向 LLM 轨喂确定性产物。
- **降级契约不变**：`SHANNON_GITNEXUS_LLM_ENABLED=0` / `llm_client=None` / LLM 不可用 → ③b 返回空、⑤ 走各自 fallback，行为与改造前一致（只是编排从串行变并发）。
- **`discover_sinks_llm` 返回类型不变**：`(list[SinkCallSite], list[RuleGap])`，soft sink 的 `rule_id="llm-discovered"` / `needs_review=True` 不变。
- **`CODE_INDEX_RETRY(max 3)`（治本 1）不动**：本 spec 是治本 1 的补充，不回退 retry 改动。

## 7. 实现顺序（供 writing-plans 参考）

1. helper `map_llm_with_bounds` + 单测（TDD）
2. `discover_sinks_llm` 改造 + 测试
3. ⑤ taint 调用点改造（内联）+ 集成验证
4. 全套相关测试绿（`test_llm_concurrency` + `test_retry_profiles` + `test_retry_policy_coverage` + `test_run_code_index` + 现有 code_index 集成测试）
