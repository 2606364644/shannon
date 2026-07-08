# per-profile cost 定价 + 双引擎统一自算 + 币种/token 明细 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个 profile 配置自己的 LLM 计费价表（4 档 token 单价 + 币种），双引擎（claude/openai）统一走"项目自算"，cost 展示跟 profile 币种走，并顺带把 token 明细落盘+展示。

**Architecture:** claude 引擎的 `_extract_cost` 从"读 SDK total_cost_usd"改成"`tokens × 价表`自算"（与 openai 引擎复用同一 `compute_cost`），消除双引擎不对称。价表 per-profile 化（JSON 文件 + profile 用 `SHANNON_PRICING_OVERRIDE` 指向，底层 env override 机制已支持）。`cost_usd` 字段名全链路保留（值=cost_currency 币种金额），新增 `cost_currency` 字段；token 4 档字段补进 `AgentEndResult`（修复当前截断）一路落到 session.json / events / 展示层。

**Tech Stack:** Python 3.13 / pydantic / pytest（core+whitebox+blackbox）；React+TypeScript+vitest（web 前端）；dotenv profile。

**Spec:** `docs/superpowers/specs/2026-07-09-per-profile-cost-pricing-design.md`

## Global Constraints

- **字段名不变约束**：全链路保留 `cost_usd` / `total_cost_usd` 字段名（值语义变为"cost_currency 币种的金额"），仅新增 `cost_currency: str` 字段（默认 `"USD"`）。**不要**把 `cost_usd` 改名为 `cost`（会触发 pydantic 序列化 + 前端 8 处 + session.json schema 的批量回归）。
- **token 字段命名**：落盘/传输用 `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens`（与 `AgentMetrics` 现有命名一致，注意是 `cache_read_tokens` 不是 `cache_read_input_tokens`）。
- **币种**：仅支持 `"CNY"` / `"USD"`（符号 ¥ / $）；未知币种回落 `$`。
- **双轨铁律**：不得把确定性层产物喂进 LLM 轨 prompt（本次纯 cost 核算层，与双轨无关，但任何 prompt 文件零改动）。
- **测试隔离**：只跑改动相关测试文件，勿跑全套 pytest（会 hang，见 memory `pytest-whitebox-hang`）；前端命令必须 `cd packages/web/frontend`（见 memory `frontend-test-must-cd-frontend`）。
- **分支**：`feat/fork-py`（当前分支），每个 task 末尾 commit。
- **向后兼容**：旧 session.json（无 `cost_currency`、`cost_usd` 是真美元）→ 读取时 `cost_currency` 默认 `"USD"`、token 字段缺失按 0/None。

## File Structure

**改动文件分组（按责任）：**

| 责任 | 文件 | 改动 |
|---|---|---|
| 计价核心 | `packages/core/src/shannon_core/agents/pricing.py` | `CostAmount` + `compute_cost` + 币种 + 4 档 + 新 override schema + normalize 扩展 |
| token 语义归一 + openai 接入 | `packages/core/src/shannon_core/agents/openai_result_mapper.py` | `_usage_from` input 归一；`map_run_result` 用 `compute_cost` |
| claude 引擎自算 | `packages/core/src/shannon_core/agents/providers_anthropic.py` | `_extract_cost` 自算；`_extract_result` 填 currency |
| 结果/指标 dataclass | `packages/core/src/shannon_core/agents/runner.py`、`packages/core/src/shannon_core/models/metrics.py`、`packages/core/src/shannon_core/models/audit.py` | 加 `cost_currency` + token 字段 |
| executor 透传 | `packages/core/src/shannon_core/agents/executor.py` | `AgentMetrics` 填 `cost_currency` |
| pipeline 透传 | `packages/whitebox/src/shannon_whitebox/.../activities.py`、`packages/blackbox/src/shannon_blackbox/.../activities.py` | 构造 `AgentEndResult` 处补字段 |
| 落盘聚合 | `packages/core/src/shannon_core/audit/metrics_tracker.py` | session.json metrics 写 `cost_currency` + token 汇总 |
| 事件流 | `packages/core/src/shannon_core/display/events.py`、`packages/core/src/shannon_core/audit/workflow_logger.py` | `AgentEvent`/`SummaryEvent`/`AgentMetric` 加字段 |
| CLI 展示 | `packages/core/src/shannon_core/display/rich_renderer.py`、`file_renderer.py` | 按币种显示 + token 列 |
| web 归一 | `packages/web/src/shannon_web/components/metrics_normalizer.py` | 归一 `cost_currency` + token |
| web 前端 | `packages/web/frontend/src/{api/types.ts, routes/WorkspaceDetail/OverviewTab.tsx, components/DashboardPanel.tsx, pages/DashboardPage.tsx, pages/WorkspaceListPage.tsx, state/dashboardReducer.ts}` | 按币种渲染 + token |
| profile 示例 | `.env.profiles.example/*.env.example` + 新增 `*.pricing.json` | `SHANNON_PRICING_OVERRIDE` 行 + 价表示例 |

---

## Task 1: pricing.py 核心改造（CostAmount + 4 档 + 币种 + 新 override schema）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/pricing.py`
- Test: `packages/core/tests/agents/test_pricing.py`

**Interfaces:**
- Consumes: 无（底层纯函数）
- Produces:
  - `CostAmount` dataclass：`cost: float`、`currency: str`
  - `compute_cost(model: str, usage) -> CostAmount`（替换 `compute_cost_usd` 的职责）
  - `currency_symbol(currency: str) -> str`（CNY→¥，USD→$，未知→$）
  - `compute_cost_usd(model, usage) -> float` 保留为 thin wrapper（`return compute_cost(model, usage).cost`），供 mapper 过渡期使用
  - `is_model_priced(model) -> bool` 语义不变
  - `normalize_model(name)` 扩展支持 `claude-*` / `deepseek-*`

- [ ] **Step 1: 写失败测试（CostAmount + 4 档 + 币种）**

在 `packages/core/tests/agents/test_pricing.py` 顶部 import 区追加 `CostAmount`，并新增以下测试（保留既有测试，但需更新断言——见 Step 1b）：

```python
from shannon_core.agents.pricing import (
    GLM_PRICING_CNY, USD_CNY_RATE, CostAmount, compute_cost, compute_cost_usd,
    currency_symbol, is_model_priced, normalize_model,
)


def test_compute_cost_returns_costamount_with_currency():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)
    r = compute_cost("glm-5.2", usage)
    assert isinstance(r, CostAmount)
    assert r.currency == "CNY"  # 内置表默认 CNY
    assert r.cost == 50.0  # 1M input × 50 / 1M


def test_compute_cost_four_tiers_cache_creation():
    # claude 场景：input + cache_creation + cache_read + output
    usage = TokenUsage(
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_creation_input_tokens=500_000, cache_read_input_tokens=500_000,
    )
    # 内置 GLM 表 cache_creation=0
    r = compute_cost("glm-5.2", usage)
    # input 1M×50 + cache_creation 0.5M×0 + cache_read 0.5M×12.5 + output 1M×50, /1M
    expected = (50.0 + 0.0 + 6.25 + 50.0)
    assert r.cost == expected
    assert r.currency == "CNY"


def test_compute_cost_unknown_model_zero_with_table_currency():
    r = compute_cost("unknown-model", TokenUsage(input_tokens=100))
    assert r.cost == 0.0
    assert r.currency == "CNY"  # 仍带表币种，便于上层显示


def test_currency_symbol():
    assert currency_symbol("CNY") == "¥"
    assert currency_symbol("USD") == "$"
    assert currency_symbol("EUR") == "$"  # 未知回落


def test_override_new_schema_with_currency(tmp_path, monkeypatch):
    # 新 schema: {"currency": ..., "models": {...}}
    f = tmp_path / "p.json"
    f.write_text('{"currency":"USD","models":{"glm-5.2":{"input":10,"output":30,"cache_read":2,"cache_creation":4}}}', encoding="utf-8")
    monkeypatch.setenv("SHANNON_PRICING_OVERRIDE", str(f))
    r = compute_cost("glm-5.2", TokenUsage(input_tokens=1_000_000, output_tokens=0))
    assert r.currency == "USD"
    assert r.cost == 10.0  # USD 直达，不除汇率


def test_override_old_flat_schema_defaults_cny(tmp_path, monkeypatch):
    # 旧 flat schema: {"glm-5.2": {...}}（无 currency/models 包裹）→ 回落 CNY
    f = tmp_path / "p.json"
    f.write_text('{"glm-5.2":{"input":50,"output":50,"cache_read":12.5}}', encoding="utf-8")
    monkeypatch.setenv("SHANNON_PRICING_OVERRIDE", str(f))
    r = compute_cost("glm-5.2", TokenUsage(input_tokens=1_000_000))
    assert r.currency == "CNY"
    assert r.cost == 50.0


def test_normalize_model_claude_and_deepseek():
    assert normalize_model("claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert normalize_model("Claude-Sonnet-4-5-20251022") == "claude-sonnet-4-5"
    assert normalize_model("deepseek-chat") == "deepseek-chat"
    assert normalize_model("GLM-5.2[1m]") == "glm-5.2"


def test_compute_cost_usd_wrapper_returns_cost():
    # 过渡期 wrapper：返回 compute_cost().cost（本币，不再 /汇率）
    assert compute_cost_usd("glm-5.2", TokenUsage(input_tokens=1_000_000)) == 50.0
```

- [ ] **Step 1b: 更新既有测试断言（移除 /汇率 语义）**

既有 `test_compute_cost_known_model_no_cache` / `test_compute_cost_cache_discount` / `test_compute_cost_normalizes_model` / `test_env_rate_override` / `test_invalid_rate_falls_back` / `test_compute_cost_clamps_negative_billable` 断言的是 `cost_cny / 7.2`（美元）。现在 `compute_cost_usd` wrapper 返回本币（不除汇率）。把这些测试的期望值从 `/ USD_CNY_RATE` 改为不除（直接 CNY 值），并加注释「cost_usd 现为本币值（CNY），不除汇率」。具体：把每个 `assert ... == <cny>/7.2` 改成 `== <cny>`。`test_env_rate_override` / `test_invalid_rate_falls_back` 改为断言「汇率不再影响单 session cost」（`_rate()` 不再被 `compute_cost` 调用），可改为验证 `compute_cost` 结果不受 `SHANNON_USD_CNY_RATE` 影响。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_pricing.py -v`
Expected: FAIL（`CostAmount` / `compute_cost` / `currency_symbol` 未定义；既有测试因 /汇率 断言失败）

- [ ] **Step 3: 实现 pricing.py 改造**

把 `packages/core/src/shannon_core/agents/pricing.py` 改为（完整替换文件体，保留 docstring 首段并更新）：

```python
"""LLM 成本换算——纯函数，无副作用。

双引擎统一自算（spec §4.5）：claude/openai 引擎都用本模块按 token 用量 × 价目表算 cost。
价目表来源：内置 ``GLM_PRICING_CNY``（默认 CNY）∪ ``SHANNON_PRICING_OVERRIDE`` 指向的 JSON
文件（per-profile，经 env_loader override=True 天然 per-profile）。override 支持新 schema
(``{"currency","models"}``) 与旧 flat schema（回落 CNY）。

返回 ``CostAmount{cost, currency}``：cost 是 ``currency`` 币种的金额（单 session 本币直达，
不再 /汇率）；未知模型回落 ``CostAmount(0.0, currency)``（守「不假估算」）。

usage 参数 duck-typed：只读 .input_tokens / .output_tokens / .cache_read_input_tokens /
.cache_creation_input_tokens（通常是 shannon_core.agents.runner.TokenUsage）。
input_tokens 须已归一为「不含 cache 命中」（openai mapper 负责，spec §4.3）。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# 单位：本币（CNY）/ 百万 token。示例数值——执行时按智谱官网核对调整（spec §8）。
# cache_creation 对 GLM/openai 协议恒 0（无此概念）；claude 引擎走 override 配置。
GLM_PRICING_CNY: dict[str, dict[str, float]] = {
    "glm-4.6": {"input": 50.0, "output": 50.0, "cache_read": 12.5, "cache_creation": 0.0},
    "glm-5.2": {"input": 50.0, "output": 50.0, "cache_read": 12.5, "cache_creation": 0.0},
}

# 默认 ¥→$ 汇率；单 session 不再使用（本币直达），仅保留供未来跨 session/跨币种聚合。
USD_CNY_RATE: float = 7.2

_CURRENCY_SYMBOLS = {"CNY": "¥", "USD": "$"}

# 去后缀：[1m] / -YYYYMMDD / --xxx；并折叠 claude 日期快照后缀
_MODEL_SUFFIX_RE = re.compile(r"\[.*?\]|-\d{8}.*$|--.*$", re.IGNORECASE)
_MODEL_ALIASES: dict[str, str] = {}


@dataclass(frozen=True)
class CostAmount:
    """成本 + 币种（cost 是 currency 币种的金额）。"""
    cost: float
    currency: str


def currency_symbol(currency: str) -> str:
    """币种 → 显示符号（CNY→¥，USD→$，未知→$）。"""
    return _CURRENCY_SYMBOLS.get(currency, "$")


def normalize_model(name: str) -> str:
    """模型名归一化：小写 + 去后缀 + 别名映射（GLM-5.2[1m]→glm-5.2，claude-sonnet-4-5-20251022→claude-sonnet-4-5）。"""
    if not name:
        return ""
    key = name.strip().lower()
    key = _MODEL_SUFFIX_RE.sub("", key).strip()
    return _MODEL_ALIASES.get(key, key)


def _rate() -> float:
    """汇率（单 session 不再使用；保留供未来跨币种聚合）。"""
    raw = os.environ.get("SHANNON_USD_CNY_RATE")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            _log.warning("SHANNON_USD_CNY_RATE=%r 非法，落回默认 %s", raw, USD_CNY_RATE)
    return USD_CNY_RATE


def _load_override() -> dict:
    path = os.environ.get("SHANNON_PRICING_OVERRIDE")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        _log.warning("SHANNON_PRICING_OVERRIDE 顶层非 object，忽略覆盖")
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("SHANNON_PRICING_OVERRIDE 解析失败（%s），忽略覆盖", e)
    return {}


def _pricing() -> tuple[dict, str]:
    """合并内置表 + override，返回 (价目表, 币种)。

    override 新 schema: {"currency": "CNY"|"USD", "models": {model: {4 档}}}
    override 旧 flat schema: {model: {input,output,cache_read}}  → 币种回落 CNY
    """
    override = _load_override()
    table = dict(GLM_PRICING_CNY)
    if isinstance(override.get("models"), dict):
        currency = override.get("currency", "CNY")
        table.update(override["models"])
    elif override:
        currency = "CNY"
        table.update(override)
    else:
        currency = "CNY"
    return table, currency


def _price_table() -> dict:
    """向后兼容：仅返回价目表（is_model_priced 用）。"""
    return _pricing()[0]


def is_model_priced(model: str) -> bool:
    return normalize_model(model) in _price_table()


def compute_cost(model: str, usage) -> CostAmount:
    """按价目表 + token 用量算成本。未知模型 → CostAmount(0.0, currency)。

    计费公式（spec §4.4，input_tokens 已归一为不含 cache 命中）：
        cost = ( input*P_in + cache_creation*P_cc + cache_read*P_cr + output*P_out ) / 1e6
    """
    key = normalize_model(model)
    table, currency = _pricing()
    if key not in table:
        return CostAmount(0.0, currency)
    p = table[key]
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cost = (
        inp * p["input"]
        + cache_creation * p.get("cache_creation", 0.0)
        + cache_read * p["cache_read"]
        + out * p["output"]
    ) / 1_000_000
    return CostAmount(cost, currency)


def compute_cost_usd(model: str, usage) -> float:
    """过渡兼容 wrapper：返回 compute_cost().cost（本币值，不再 /汇率）。

    新代码请用 compute_cost 拿 (cost, currency)。mapper 切换后本函数可移除。
    """
    return compute_cost(model, usage).cost
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_pricing.py -v`
Expected: PASS（全部，含新测试 + 更新后的旧测试）

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/agents/pricing.py packages/core/tests/agents/test_pricing.py
git commit -m "feat(cost): pricing 4档+币种+CostAmount+新override schema"
```

---

## Task 2: openai mapper — 统一 input 语义 + 接入 compute_cost

**Files:**
- Modify: `packages/core/src/shannon_core/agents/openai_result_mapper.py`
- Test: `packages/core/tests/agents/test_openai_result_mapper.py`

**Interfaces:**
- Consumes: Task 1 的 `compute_cost` / `CostAmount`
- Produces: `map_run_result` 返回的 `ClaudeRunResult` 带 `cost_currency`（下个 task 给 `ClaudeRunResult` 加字段后生效；本 task 先在 mapper 里取 `cost_amount.currency` 透传）

- [ ] **Step 1: 写失败测试（input 归一 + currency）**

在 `packages/core/tests/agents/test_openai_result_mapper.py` 新增：

```python
def test_usage_from_input_excludes_cached():
    # OpenAI input_tokens 含 cached；归一后 input = raw - cached
    u = _usage(inp=1000, outp=500, cached=300)
    rr = _run_result("x", u)
    mapped = map_run_result(rr, duration_ms=10, model="glm-5.2", turns=1)
    assert mapped.tokens.input_tokens == 700      # 1000 - 300
    assert mapped.tokens.cache_read_input_tokens == 300


def test_map_cost_carries_currency():
    mapped = map_run_result(_run_result("x", _usage(inp=1_000_000, outp=0)),
                            duration_ms=10, model="glm-5.2", turns=1)
    assert mapped.cost > 0
    assert mapped.cost_currency == "CNY"  # ClaudeRunResult.cost_currency（Task 3 加字段后）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_openai_result_mapper.py::test_usage_from_input_excludes_cached packages/core/tests/agents/test_openai_result_mapper.py::test_map_cost_carries_currency -v`
Expected: FAIL（input 仍含 cached；`cost_currency` 属性不存在）

- [ ] **Step 3: 改 `_usage_from`（input 归一）+ `map_run_result`（透传 currency）**

`openai_result_mapper.py` 的 import 行改为：
```python
from .pricing import compute_cost, is_model_priced, normalize_model
```
（移除 `compute_cost_usd` import）

`_usage_from`（L24-35）改为：
```python
def _usage_from(run_result: RunResult) -> TokenUsage:
    usage = getattr(getattr(run_result, "context_wrapper", None), "usage", None)
    if usage is None:
        return TokenUsage()
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    cached = cached or 0
    raw_input = getattr(usage, "input_tokens", 0) or 0
    billable_input = max(raw_input - cached, 0)  # 归一为「不含 cache 命中」（spec §4.3）
    return TokenUsage(
        input_tokens=billable_input,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,  # openai 协议无此概念
    )
```

`map_run_result` 里 `cost = compute_cost_usd(model, tokens)`（L54）改为：
```python
    cost_amount = compute_cost(model, tokens)
    cost = cost_amount.cost
    if model and cost == 0.0 and not is_model_priced(model):
        norm = normalize_model(model)
        if norm not in _WARNED_UNKNOWN_MODELS:
            _WARNED_UNKNOWN_MODELS.add(norm)
            _log.warning(
                "成本核算：模型 %r 未在价目表中，cost 回落 0.0（不假估算）。"
                " 可经 SHANNON_PRICING_OVERRIDE 补充。",
                model,
            )
```
并在最后 `return ClaudeRunResult(...)` 里加 `cost_currency=cost_amount.currency`：
```python
    return ClaudeRunResult(
        text=text,
        success=not is_max_turns,
        duration=duration_ms,
        turns=turns,
        cost=cost,
        cost_currency=cost_amount.currency,
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
        error_code="ExecutionLimitError" if is_max_turns else None,
        retryable=False if is_max_turns else True,
    )
```

- [ ] **Step 4: 先做 Task 3 的 ClaudeRunResult 字段（mapper 测试依赖它）**

`ClaudeRunResult` 还没有 `cost_currency` 字段，mapper 测试会 FAIL。先去 `runner.py`（Task 3 Step 3）给 `ClaudeRunResult` 加 `cost_currency: str = "USD"` 字段，再回来跑测试。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_openai_result_mapper.py -v`
Expected: PASS（注意：既有 `test_map_extracts_cache_read` 若断言 `input_tokens==1000` 需更新为 `==1000-cached`；检查并更新）

- [ ] **Step 6: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/agents/openai_result_mapper.py packages/core/src/shannon_core/agents/runner.py packages/core/tests/agents/test_openai_result_mapper.py
git commit -m "feat(cost): openai mapper 归一 input 语义 + 透传 currency"
```

---

## Task 3: ClaudeRunResult + claude 引擎自算 _extract_cost

**Files:**
- Modify: `packages/core/src/shannon_core/agents/runner.py`（加字段——Task 2 Step 4 可能已做）
- Modify: `packages/core/src/shannon_core/agents/providers_anthropic.py`
- Test: `packages/core/tests/agents/test_providers.py`（扩 cost 提取测试）

**Interfaces:**
- Consumes: Task 1 `compute_cost`
- Produces: claude 引擎 `ClaudeRunResult` 也带 `cost_currency`；`_extract_cost` 不再读 SDK `total_cost_usd`

- [ ] **Step 1: 确认/补 ClaudeRunResult.cost_currency 字段**

若 Task 2 未加，在 `runner.py` 的 `ClaudeRunResult` dataclass（L75-104）加字段（紧挨 `cost: float = 0.0` 后）：
```python
    cost: float = 0.0
    cost_currency: str = "USD"
    model: str | None = None
```

- [ ] **Step 2: 写失败测试（claude 自算，不再读 total_cost_usd）**

在 `packages/core/tests/agents/test_providers.py` 新增（需 import `compute_cost` 或构造已知 token 期望值；用内置 GLM 表，CNY）：

```python
def test_extract_cost_ignores_sdk_total_cost_usd():
    """claude 引擎自算：_extract_cost 用 tokens×价表，不读 SDK total_cost_usd。"""
    provider = _make_anthropic_provider(model="glm-5.2")  # 用现有 helper 构造 provider
    rm = _mock_result_message(
        text="ok",
        usage=_usage_obj(input_tokens=1_000_000, output_tokens=0,
                         cache_read=0, cache_creation=0),
        total_cost_usd=999.0,  # SDK 给个假高值，必须被忽略
    )
    result = provider._extract_result(rm, duration=10, model="glm-5.2")
    assert result.cost == 50.0          # 1M input × 50 / 1M（CNY 本币），不是 999
    assert result.cost_currency == "CNY"
```

注：`_make_anthropic_provider` / `_mock_result_message` / `_usage_obj` 用文件内现有 helper；若不存在，参考 `TestCallWithTurnCount`（L968）和 `TestExtractResult`（L1638）的构造方式。实现时按现有 helper 风格写。（cache_creation 4 档计费已在 `test_pricing.py::test_compute_cost_four_tiers_cache_creation` 覆盖，provider 层不必重复测。）

- [ ] **Step 3: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py -v -k "extract_cost"`（或新测试名）
Expected: FAIL（`_extract_cost` 还读 `total_cost_usd` → cost==999，断言 50 失败）

- [ ] **Step 4: 改 `_extract_cost` 自算 + `_extract_result` 透传 currency**

`providers_anthropic.py` 顶部 import 加：
```python
from .pricing import compute_cost
```

`_extract_cost`（L415-419）改为（接收 model）：
```python
def _extract_cost(self, result_message: ResultMessage, model: str):
    """自算成本（spec §4.5）：tokens × 价目表，不再读 SDK total_cost_usd。"""
    tokens = self._extract_tokens(result_message)
    return compute_cost(model, tokens)
```

`_extract_result`（L334-397）里 `cost = self._extract_cost(result_message)` 改为：
```python
    cost_amount = self._extract_cost(result_message, model)
```
并在末尾 `return ClaudeRunResult(...)` 加 `cost_currency`：
```python
    return ClaudeRunResult(
        text=text, success=success, duration=duration, turns=turn_count,
        cost=cost_amount.cost, cost_currency=cost_amount.currency, model=model,
        structured_output=structured_output, stop_reason=stop_reason, tokens=tokens,
    )
```

- [ ] **Step 5: 核实无其他消费方依赖 openai 侧「input 含 cached」原始语义**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && rg "input_tokens" packages/ --type py -l` 然后人工扫读非测试的消费点（agents/、orchestrator/、audit/）。确认没有地方假设 openai `TokenUsage.input_tokens` 含 cached。预期：只有 pricing/mapper/executor/metrics_tracker 用，且都已被覆盖或即将覆盖。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/agents/test_providers.py packages/core/tests/agents/test_openai_result_mapper.py packages/core/tests/agents/test_pricing.py -v`
Expected: PASS（注意：`test_providers.py` 里 `TestSpendingCapDetection` 的 mock 带 `total_cost_usd=0.05`（L1176）——那些测试走 `call()` 全链路，改自算后 cost 来自 tokens×价表；若 mock 的 usage token 是 0，cost 会变 0，需更新这些 mock 给 usage 配 token 或调整断言。逐个核实 L1015/L1039/L1051/L992 的 mock）

- [ ] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/agents/runner.py packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/tests/agents/test_providers.py
git commit -m "feat(cost): claude 引擎改自算(_extract_cost 用 tokens×价表),不再读 SDK total_cost_usd"
```

---

## Task 4: AgentMetrics + executor 透传 cost_currency

**Files:**
- Modify: `packages/core/src/shannon_core/models/metrics.py`
- Modify: `packages/core/src/shannon_core/agents/executor.py`
- Test: `packages/core/tests/test_metrics.py`

**Interfaces:**
- Consumes: Task 3 `ClaudeRunResult.cost_currency`
- Produces: `AgentMetrics.cost_currency`（下游 activities/metrics_tracker 用）

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/test_metrics.py` 新增：
```python
def test_agent_metrics_has_cost_currency():
    m = AgentMetrics(duration_ms=10, cost_usd=1.5, cost_currency="CNY")
    assert m.cost_currency == "CNY"

def test_agent_metrics_cost_currency_defaults_usd():
    m = AgentMetrics(duration_ms=10)
    assert m.cost_currency == "USD"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_metrics.py -v -k cost_currency`
Expected: FAIL（`cost_currency` 字段不存在）

- [ ] **Step 3: 加字段 + executor 填充**

`models/metrics.py` 的 `AgentMetrics`（L3-13）在 `cost_usd` 后加：
```python
    cost_usd: float | None = None
    cost_currency: str = "USD"
```

`executor.py` 的 `execute()`（L161-172）构造 `AgentMetrics` 处加：
```python
    return AgentMetrics(
        duration_ms=duration_ms,
        cost_usd=result.cost,
        cost_currency=result.cost_currency,
        num_turns=result.turns,
        ...
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/models/metrics.py packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_metrics.py
git commit -m "feat(cost): AgentMetrics + executor 透传 cost_currency"
```

---

## Task 5: AgentEndResult + AgentLogDetails + WorkflowSummary 加 cost_currency + token 字段

**Files:**
- Modify: `packages/core/src/shannon_core/models/audit.py`
- Test: `packages/core/tests/test_audit_types.py`

**Interfaces:**
- Consumes: Task 4 `AgentMetrics`
- Produces: `AgentEndResult` / `AgentLogDetails` / `WorkflowSummary` 带 `cost_currency` + 4 档 token（修复 AgentEndResult 当前截断丢弃 token）

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/test_audit_types.py` 新增：
```python
def test_agent_end_result_has_cost_and_token_fields():
    r = AgentEndResult(
        success=True, duration_ms=10, cost_usd=1.5, cost_currency="CNY",
        input_tokens=100, output_tokens=50, cache_read_tokens=10, cache_creation_tokens=0,
    )
    assert r.cost_currency == "CNY"
    assert r.input_tokens == 100
    assert r.cache_creation_tokens == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_audit_types.py -v -k "cost_and_token"`
Expected: FAIL（字段不存在）

- [ ] **Step 3: 加字段**

`models/audit.py`：
- `AgentEndResult`（L6-15）加（`cost_usd` 后接 `cost_currency`，末尾接 token 4 档，全部带默认值不破坏现有构造）：
```python
class AgentEndResult(BaseModel):
    success: bool
    duration_ms: int
    cost_usd: float
    cost_currency: str = "USD"
    attempt_number: int = 1
    model: str | None = None
    error: str | None = None
    is_final_attempt: bool = True
    checkpoint: str | None = None
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
```
- `AgentLogDetails`（L18-23）：加 `cost_currency: str = "USD"` + 同 4 档 token（默认 None）。
- `WorkflowSummary`（L31-37）：加 `cost_currency: str = "USD"` + 顶层 4 档 token 汇总（`total_input_tokens: int = 0` 等，默认 0）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/test_audit_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/models/audit.py packages/core/tests/test_audit_types.py
git commit -m "feat(cost): AgentEndResult/AgentLogDetails/WorkflowSummary 加 cost_currency+token 字段"
```

---

## Task 6: whitebox/blackbox activities 透传 cost_currency + token 到 AgentEndResult

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/.../activities.py`（构造 `AgentEndResult` 处，约 L222；grep `AgentEndResult(` 定位全部）
- Modify: `packages/blackbox/src/shannon_blackbox/.../activities.py`（约 L178/243/370）
- Test: 相关 package 的 activities 测试（grep 现有 test_activities）

**Interfaces:**
- Consumes: Task 4 `AgentMetrics.cost_currency` + token；Task 5 `AgentEndResult` 新字段
- Produces: `AgentEndResult` 实例带 `cost_currency` + token，供 metrics_tracker 落盘

- [ ] **Step 1: 定位所有 AgentEndResult 构造点**

Run:
```bash
cd /Users/mango/project/shannon-refactor/shannon-py
rg "AgentEndResult\(" packages/whitebox packages/blackbox --type py -n
```
记录每一处（whitebox ~3 处，blackbox ~13 处）。

- [ ] **Step 2: 写失败测试（whitebox，取一处代表）**

在 whitebox activities 测试里新增（构造一个带 token 的 AgentMetrics，跑 activity，断言 AgentEndResult.input_tokens 透传）：
```python
def test_activity_passes_tokens_and_currency_to_end_result():
    metrics = AgentMetrics(duration_ms=10, cost_usd=1.0, cost_currency="CNY",
                           input_tokens=100, output_tokens=50,
                           cache_read_tokens=10, cache_creation_tokens=0)
    end = _build_end_result(metrics, success=True)  # 用 activity 内构造逻辑的提取/helper
    assert end.cost_currency == "CNY"
    assert end.input_tokens == 100
```
（若 activity 内是内联构造无 helper，直接调 activity 函数并用真实/ mock executor 返回该 metrics，断言返回的 AgentEndResult 字段。按现有测试风格。）

- [ ] **Step 3: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox -k "tokens_and_currency" -v`（路径按实际）
Expected: FAIL（透传未做）

- [ ] **Step 4: 透传字段**

在每个 `AgentEndResult(...)` 构造处，从 `metrics: AgentMetrics` 补：
```python
AgentEndResult(
    success=...,
    duration_ms=metrics.duration_ms,
    cost_usd=metrics.cost_usd or 0.0,
    cost_currency=metrics.cost_currency,
    model=metrics.model,
    num_turns=metrics.num_turns,
    input_tokens=metrics.input_tokens,
    output_tokens=metrics.output_tokens,
    cache_read_tokens=metrics.cache_read_tokens,
    cache_creation_tokens=metrics.cache_creation_tokens,
    ...
)
```
blackbox 的多处同理（DRY：若重复多，可抽一个 `_metrics_to_end(metrics, success, **kw)` helper，但优先跟现有代码风格——若现有就是内联，保持内联逐处改）。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_metrics_tracker.py packages/blackbox -k "metrics or activity" -v`（按实际测试路径；只跑改动相关）
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/whitebox packages/blackbox
git commit -m "feat(cost): whitebox/blackbox activities 透传 cost_currency+token 到 AgentEndResult"
```

---

## Task 7: metrics_tracker 落盘 cost_currency + token 汇总到 session.json

**Files:**
- Modify: `packages/core/src/shannon_core/audit/metrics_tracker.py`
- Test: `packages/whitebox/tests/test_metrics_tracker.py`（core 的 metrics_tracker 测试在 whitebox 包测，见调研）

**Interfaces:**
- Consumes: Task 5/6 `AgentEndResult.cost_currency` + token
- Produces: session.json `metrics` 含 `cost_currency`（顶层）+ 顶层 token 汇总 + `agents[name]`/`phases[phase]` 的 token

- [ ] **Step 1: 写失败测试**

在 `packages/whitebox/tests/test_metrics_tracker.py` 新增：
```python
def test_end_agent_persists_cost_currency_and_tokens(tmp_path):
    tracker = _make_tracker(tmp_path)  # 现有 helper，注意 SHANNON_WORKER_ROOT 隔离（见 memory）
    tracker.start_agent("recon", 1)
    asyncio.run(tracker.end_agent("recon", AgentEndResult(
        success=True, duration_ms=100, cost_usd=0.5, cost_currency="CNY",
        input_tokens=1000, output_tokens=500, cache_read_tokens=100, cache_creation_tokens=0,
    )))
    data = json.loads((tmp_path / "session.json").read_text())
    m = data["metrics"]
    assert m["cost_currency"] == "CNY"
    assert m["total_input_tokens"] == 1000
    assert m["agents"]["recon"]["input_tokens"] == 1000
    assert m["agents"]["recon"]["cost_currency"] == "CNY"
```
（`_make_tracker` 用现有 helper；必须设 `SHANNON_WORKER_ROOT=tmp_path` 避免污染真实 workspaces——见 memory `whitebox-worker-test-isolate-workspaces`）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_metrics_tracker.py -v -k "cost_currency_and_tokens"`
Expected: FAIL（session.json 无 cost_currency / token 字段）

- [ ] **Step 3: 改 initialize / start_agent / end_agent / _aggregate_phase**

`initialize`（L21-62）的 `new_payload["metrics"]` 加字段：
```python
        "metrics": {
            "total_duration_ms": 0,
            "total_cost_usd": 0,
            "cost_currency": "USD",
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_creation_tokens": 0,
            "phases": {},
            "agents": {},
        },
```

`start_agent`（L64-71）的 agents[name] 初始结构加 token 字段：
```python
        self._data["metrics"]["agents"][agent_name] = {
            "duration_ms": 0,
            "cost_usd": 0,
            "cost_currency": "USD",
            "attempts": attempt_number,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
```

`end_agent`（L73-95）的 `agents[name].update({...})` 加字段，并把顶层汇总加 token + 设 cost_currency（取首个 agent 的 currency，session 内一致）：
```python
    agents[agent_name].update({
        "duration_ms": result.duration_ms,
        "cost_usd": result.cost_usd,
        "cost_currency": result.cost_currency,
        "success": result.success,
        "attempt_number": result.attempt_number,
        "model": result.model,
        "input_tokens": result.input_tokens or 0,
        "output_tokens": result.output_tokens or 0,
        "cache_read_tokens": result.cache_read_tokens or 0,
        "cache_creation_tokens": result.cache_creation_tokens or 0,
    })
    ...
    self._data["metrics"]["total_duration_ms"] += result.duration_ms
    self._data["metrics"]["total_cost_usd"] += result.cost_usd
    self._data["metrics"]["cost_currency"] = result.cost_currency  # session 内一致
    self._data["metrics"]["total_input_tokens"] += result.input_tokens or 0
    self._data["metrics"]["total_output_tokens"] += result.output_tokens or 0
    self._data["metrics"]["total_cache_read_tokens"] += result.cache_read_tokens or 0
    self._data["metrics"]["total_cache_creation_tokens"] += result.cache_creation_tokens or 0
```

`_aggregate_phase`（L97-118）的 phase 结构加 4 档 token 累加（`input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens`，+= result 对应字段）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/whitebox/tests/test_metrics_tracker.py -v`
Expected: PASS（含既有 `test_end_agent_updates_metrics` / `test_end_agent_accumulates_totals` / `test_end_agent_populates_phases`，它们用默认 currency/token，应仍绿）

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/audit/metrics_tracker.py packages/whitebox/tests/test_metrics_tracker.py
git commit -m "feat(cost): metrics_tracker 落盘 cost_currency+token 汇总到 session.json"
```

---

## Task 8: events + workflow_logger 把 cost_currency + token 进事件流

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py`
- Modify: `packages/core/src/shannon_core/audit/workflow_logger.py`
- Test: `packages/core/tests/display/`（grep 现有 workflow_logger / events 测试）

**Interfaces:**
- Consumes: Task 5 `AgentLogDetails`/`WorkflowSummary` 新字段
- Produces: `AgentEvent`/`AgentMetric`/`SummaryEvent` 带 `cost_currency` + token；StructuredEventRenderer 经 `asdict` 自动序列化到 events.ndjson

- [ ] **Step 1: 写失败测试**

在 display 测试里新增（构造 SummaryEvent，断言 cost_currency 序列化）：
```python
def test_summary_event_carries_cost_currency():
    e = SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                     total_duration_ms=100, total_cost_usd=1.5, cost_currency="CNY")
    assert e.cost_currency == "CNY"
    d = asdict(e)
    assert d["cost_currency"] == "CNY"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/display -v -k "cost_currency"`
Expected: FAIL

- [ ] **Step 3: 加事件字段 + logger 透传**

`events.py`：
- `AgentEvent`（L50-57）：加 `cost_currency: str = "USD"` + `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_creation_tokens: int | None = None`。
- `AgentMetric`（L115-120）：加 `cost_currency: str = "USD"` + 同 4 档 token（`int | None = None`）。
- `SummaryEvent`（L123-129）：加 `cost_currency: str = "USD"` + `total_input_tokens`/`total_output_tokens`/`total_cache_read_tokens`/`total_cache_creation_tokens: int = 0`。

`workflow_logger.py`：
- `log_agent`（L157-165）的 `AgentEvent(...)` 加 `cost_currency=d.cost_currency, input_tokens=d.input_tokens, ...`（从 `AgentLogDetails`）。
- `log_workflow_complete`（L247-260）的 `AgentMetric(name=..., ...)` 加 `cost_currency=m.cost_currency`（需 `WorkflowSummary.agent_metrics` 的 metric 带 currency——确认 `WorkflowSummary` 的 agent_metrics 值类型，若不含 currency 则从 summary.cost_currency 取），`SummaryEvent(...)` 加 `cost_currency=summary.cost_currency, total_input_tokens=summary.total_input_tokens, ...`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/display -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/display/events.py packages/core/src/shannon_core/audit/workflow_logger.py packages/core/tests/display
git commit -m "feat(cost): events+workflow_logger 携带 cost_currency+token 进事件流"
```

---

## Task 9: CLI renderer（rich/file）按币种显示 + token 列

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`（L180-203）
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`（L124-147）
- Test: `packages/core/tests/display/test_rich_renderer.py`、`test_file_renderer.py`（若无则新建，参考现有 renderer 测试风格）

**Interfaces:**
- Consumes: Task 1 `currency_symbol`；Task 8 事件 `cost_currency` + token 字段
- Produces: CLI 终端 / workflow.log 按 `¥`/`$` 显示 cost + agent 表格 token 列

- [ ] **Step 1: 写失败测试**

renderer 测试（rich 用 `Console(file=io.StringIO())` 捕获；file 直接断言字符串）。新增：
```python
def test_rich_summary_uses_currency_symbol_cny():
    e = SummaryEvent(timestamp="2026-07-09 12:00:00", category="SUMMARY",
                     status="completed", total_duration_ms=100,
                     total_cost_usd=0.0886, cost_currency="CNY")
    out = _render_capturing(e)  # 现有捕获 helper
    assert "¥0.0886" in out
    assert "$" not in out.split("Total Cost")[1]  # 不再用 $

def test_file_summary_usd_symbol():
    e = SummaryEvent(timestamp="2026-07-09 12:00:00", category="SUMMARY",
                     status="completed", total_duration_ms=100,
                     total_cost_usd=0.0123, cost_currency="USD")
    assert "Total Cost:  $0.0123" in renderer._summary(e)
```
（注意 Rich markup 别用单字母时间戳 tag——见 memory `rich-markup-single-char-tag-swallow`，用真实 `YYYY-MM-DD HH:MM:SS`）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/display -v -k "currency_symbol or summary_usd"`
Expected: FAIL

- [ ] **Step 3: 改 rich_renderer**

顶部 import：`from shannon_core.agents.pricing import currency_symbol`。

`_render_summary`（L180-203）：把 L188 `f"Total Cost: ${e.total_cost_usd:.4f}"` 改为：
```python
        f"Total Cost: {currency_symbol(e.cost_currency)}{e.total_cost_usd:.4f}",
```
L199 `cost = f"${m.cost_usd:.4f}"` 改为：
```python
            cost = f"{currency_symbol(m.cost_currency)}{m.cost_usd:.4f}" if m.cost_usd is not None else "—"
```
agent 表格补 token 列：在 `table.add_column("Cost")` 后加 `table.add_column("Tokens")`，`table.add_row(...)` 末尾加 token 摘要（如 `f"{m.input_tokens or 0}/{m.output_tokens or 0}"` 或更详细）。注意 `AgentMetric` 现带 token 字段（Task 8）。

- [ ] **Step 4: 改 file_renderer**

`_summary`（L124-147）：L133 `f"Total Cost:  ${e.total_cost_usd:.4f}"` → `f"Total Cost:  {currency_symbol(e.cost_currency)}{e.total_cost_usd:.4f}"`；L140 `cost = f", ${m.cost_usd:.4f}"` → `cost = f", {currency_symbol(m.cost_currency)}{m.cost_usd:.4f}"`。可选：agent 行补 token。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/display -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display
git commit -m "feat(cost): CLI renderer 按币种显示+token 列"
```

---

## Task 10: web metrics_normalizer 归一 cost_currency + token

**Files:**
- Modify: `packages/web/src/shannon_web/components/metrics_normalizer.py`
- Modify: `packages/web/src/shannon_web/api/workspaces.py`（顶层 cost_currency 透传，L46 区）
- Test: `packages/web/tests/`（grep metrics_normalizer 测试）

**Interfaces:**
- Consumes: Task 7 session.json 新 schema（`cost_currency` + token）；旧 schema 兼容
- Produces: API 响应带 `cost_currency`（顶层 + agents）+ token，供前端

- [ ] **Step 1: 写失败测试**

新增/扩展 normalizer 测试：
```python
def test_normalize_old_schema_defaults_usd():
    # 旧 session.json：无 cost_currency，cost_usd 是真美元
    metrics = {"total_cost_usd": 0.5, "agents": {"recon": {"cost_usd": 0.5, "duration_ms": 10}}}
    out = normalize_metrics(metrics)
    assert out["cost_currency"] == "USD"
    assert out["agents"]["recon"]["cost_currency"] == "USD"

def test_normalize_new_schema_passes_cny_and_tokens():
    metrics = {"total_cost_usd": 0.0886, "cost_currency": "CNY",
               "total_input_tokens": 1000,
               "agents": {"recon": {"cost_usd": 0.0886, "cost_currency": "CNY",
                                    "input_tokens": 1000, "duration_ms": 10}}}
    out = normalize_metrics(metrics)
    assert out["cost_currency"] == "CNY"
    assert out["total_input_tokens"] == 1000
    assert out["agents"]["recon"]["input_tokens"] == 1000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests -v -k "normalize"`（路径按实际）
Expected: FAIL

- [ ] **Step 3: 改 normalizer + workspaces.py**

`metrics_normalizer.py`：
- `_normalize_agent` 的 `out` dict 加 `cost_currency`（`_pick(a, last, "cost_currency", None, "cost_currency", "USD")`）+ token 4 档（`_pick(..., "input_tokens", None, "input_tokens", 0)` 等，缺失默认 0）。
- `normalize_metrics`：顶层加 `out["cost_currency"] = metrics.get("cost_currency", "USD")` + 顶层 token 透传（`total_input_tokens` 等用 `metrics.get(key, 0)`）。

`workspaces.py`（L46 区）：顶层 `cost_currency` 已随 `normalize_metrics(metrics)` 返回，确认 API response 含之。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/web/tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/web/src/shannon_web/components/metrics_normalizer.py packages/web/src/shannon_web/api/workspaces.py packages/web/tests
git commit -m "feat(web): metrics_normalizer 归一 cost_currency+token(兼容旧 schema)"
```

---

## Task 11: web 前端按币种渲染 + token

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`（L28/47/85/93/96/99 区，加 cost_currency + token）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/OverviewTab.tsx`（L72/107/142）
- Modify: `packages/web/frontend/src/components/DashboardPanel.tsx`（L25）
- Modify: `packages/web/frontend/src/pages/DashboardPage.tsx`（L93/111/139）
- Modify: `packages/web/frontend/src/pages/WorkspaceListPage.tsx`（L86-90）
- Modify: `packages/web/frontend/src/state/dashboardReducer.ts`（L17/29/62/135）
- Test: 对应 `.test.tsx`（vitest）

**Interfaces:**
- Consumes: Task 10 API `cost_currency` + token；ndjson events `cost_currency`（Task 8）
- Produces: 前端按 `¥`/`$` 渲染 cost + 显示 token

- [ ] **Step 1: 加 currency helper + 写失败测试**

在 `packages/web/frontend/src/` 加一个 `utils/currency.ts`（或复用现有 utils）：
```ts
export const CURRENCY_SYMBOL: Record<string, string> = { CNY: "¥", USD: "$" };
export function currencySymbol(c?: string | null): string {
  return (c && CURRENCY_SYMBOL[c]) || "$";
}
export function fmtCost(v: number | null | undefined, currency?: string | null): string {
  return v == null ? "—" : `${currencySymbol(currency)}${v.toFixed(2)}`;
}
```
写 `utils/currency.test.ts`：
```ts
import { fmtCost, currencySymbol } from "./currency";
test("fmtCost CNY", () => { expect(fmtCost(0.0886, "CNY")).toBe("¥0.09"); });
test("fmtCost null", () => { expect(fmtCost(null)).toBe("—"); });
test("currencySymbol unknown", () => { expect(currencySymbol("EUR")).toBe("$"); });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py/packages/web/frontend && npx vitest run src/utils/currency.test.ts`
Expected: FAIL（文件不存在）

- [ ] **Step 3: 加 helper + 跑通**

创建 `utils/currency.ts` + 跑 Step 2 命令 → PASS。

- [ ] **Step 4: 更新 types.ts 契约**

`api/types.ts`：给 ndjson `AgentEvent`（L28）加 `cost_currency?: string` + token；`SummaryEvent`（L47）加 `cost_currency?: string` + total token；`SessionMetrics`（L93）加 `cost_currency: string` + token；`Workspace`（L85）`total_cost_usd` 旁加 `cost_currency?: string`；agent/phase（L96/99）加 `cost_currency` + token。

- [ ] **Step 5: 替换 8 处硬编码 `$`**

把 4 个组件里的 ``$${x.toFixed(2)}`` / `${...}` 全换成 `fmtCost(x, currency)`，currency 从最近的 metrics/workspace 上下文取（OverviewTab 用 session metrics 的 `cost_currency`；DashboardPage 列表项用 `w.cost_currency`；WorkspaceListPage 同；DashboardPanel 用 state 的 currency——在 reducer 里维护）。

具体：
- `OverviewTab.tsx:72` → `fmtCost(m.total_cost_usd, m.cost_currency)`
- `OverviewTab.tsx:107` → 阶段行：`fmtCost(p.cost_usd, m.cost_currency)`
- `OverviewTab.tsx:142` → `fmtCost(a.cost_usd, a.cost_currency)`
- `DashboardPanel.tsx:25` → `fmtCost(state.total_cost, state.cost_currency)`
- `DashboardPage.tsx:93/111/139` → `fmtCost(..., w.cost_currency)`
- `WorkspaceListPage.tsx:89` → cell: `fmtCost(v, row.original.cost_currency)`（需 accessor 能拿到 cost_currency，可能加一列或用 row original）

可选：OverviewTab agent 表加 token 列（input/output）。

- [ ] **Step 6: 更新 dashboardReducer.ts 维护 currency**

`dashboardReducer.ts`：state 加 `cost_currency: string`（默认 "USD"）；处理 SummaryEvent 时设 `cost_currency: event.cost_currency ?? "USD"`（L135 区）；agent state（L17）加 `cost_currency`。

- [ ] **Step 7: 跑前端测试 + tsc + build**

Run:
```bash
cd /Users/mango/project/shannon-refactor/shannon-py/packages/web/frontend
npx vitest run
npx tsc --noEmit
npm run build
```
Expected: 全绿（注意若有 Radix Tabs 测试用 fireEvent.click 切不动——见 memory `radix-ui-testing-activation-gotcha`，本次不动 Tabs 应无影响）

- [ ] **Step 8: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/web/frontend
git commit -m "feat(web): 前端按币种渲染 cost+token(fmtCost helper 替换 8 处硬编码 \$)"
```

---

## Task 12: profile 示例 + .pricing.json + 文档

**Files:**
- Create: `.env.profiles.example/glm-anthropic.pricing.json`
- Create: `.env.profiles.example/glm-openai.pricing.json`
- Modify: `.env.profiles.example/glm-anthropic.env.example`（加 `SHANNON_PRICING_OVERRIDE` 行）
- Modify: `.env.profiles.example/glm-openai.env.example`（同上）
- Modify: `.env.profiles.example/deepseek.env.example`（同上 + pricing.json）
- Modify: `CLAUDE.md` §1 或新增 cost 小节（文档化 per-profile 定价）
- Test: 无（配置/文档；可加一个 normalizer 验证 JSON 加载的 smoke 测试可选）

**Interfaces:**
- Consumes: Task 1 override schema
- Produces: 开箱即用的 per-profile 价表示例 + 文档

- [ ] **Step 1: 创建 pricing.json 示例**

`.env.profiles.example/glm-anthropic.pricing.json`：
```json
{
  "currency": "CNY",
  "models": {
    "glm-5.2": {"input": 50.0, "output": 50.0, "cache_read": 12.5, "cache_creation": 0.0},
    "glm-4.6": {"input": 50.0, "output": 50.0, "cache_read": 12.5, "cache_creation": 0.0},
    "glm-4.5-air": {"input": 0.5, "output": 0.5, "cache_read": 0.125, "cache_creation": 0.0}
  }
}
```
（数值为占位——按智谱官网核对，spec §8）

`.env.profiles.example/glm-openai.pricing.json`：同上（glm-openai 用相同 GLM 模型，可 symlink 或复制）。

`.env.profiles.example/deepseek.pricing.json`：
```json
{
  "currency": "CNY",
  "models": {
    "deepseek-chat": {"input": 2.0, "output": 8.0, "cache_read": 0.5, "cache_creation": 0.0}
  }
}
```
（占位，按 deepseek 官网核对）

- [ ] **Step 2: profile .env.example 加 SHANNON_PRICING_OVERRIDE 行**

`glm-anthropic.env.example` 末尾加：
```
# per-profile cost 定价（spec 2026-07-09）：指向本目录同名 .pricing.json。
# 切 profile 即切定价；未设则回落内置 GLM 价表（CNY）。
SHANNON_PRICING_OVERRIDE=.env.profiles/glm-anthropic.pricing.json
```
`glm-openai.env.example`、`deepseek.env.example` 同理（路径换对应名）。

- [ ] **Step 3: 文档化**

在 `CLAUDE.md` 适当位置（§1 双轨概念附近或新增「cost 计费」小节）加一段：per-profile 定价机制（JSON 价表 4 档 + 币种 + profile 指向）、双引擎统一自算、`cost_usd` 字段语义（=cost_currency 币种金额）。更新 spec §4.6 的 `total_cost` → 实现保留 `total_cost_usd` + `cost_currency` 的偏离说明。

- [ ] **Step 4: smoke（可选）**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run python -c "from shannon_core.agents.pricing import _pricing; import os; os.environ['SHANNON_PRICING_OVERRIDE']='.env.profiles.example/glm-anthropic.pricing.json'; print(_pricing())"`
Expected: 打印合并后的价目表 + `"CNY"`

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add .env.profiles.example CLAUDE.md
git commit -m "docs(cost): profile 示例加 per-profile pricing.json + 文档化"
```

---

## Task 13: 双引擎对齐测试 + 真机冒烟 + memory

**Files:**
- Modify: `packages/core/tests/agents/test_dual_engine_alignment.py`
- Run: `scripts/validate_glm_task_probe.py` / `scripts/validate_openai_task_probe.py`（人工冒烟）
- Memory: 更新/新增 cost 相关 memory

**Interfaces:**
- Consumes: Task 1-12 全部
- Produces: 锁定双引擎 cost 路径对齐 + 真机验证 + 知识沉淀

- [ ] **Step 1: 写双引擎 cost 对齐测试**

在 `test_dual_engine_alignment.py` 新增：
```python
def test_both_engines_compute_cost_via_pricing(monkeypatch):
    """两引擎 cost 都经 pricing.compute_cost（claude 不再读 SDK total_cost_usd）。"""
    # 构造相同 tokens + 相同价表，断言两引擎算出相同 CostAmount
    from shannon_core.agents.pricing import compute_cost
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
    r = compute_cost("glm-5.2", usage)
    # claude provider._extract_cost 与 openai mapper 都调 compute_cost → 同输入同输出
    assert r.cost > 0 and r.currency == "CNY"
```
（更端到端：mock 两引擎的 ResultMessage/RunResult 带相同 usage，跑 provider.call/map_run_result，断言 ClaudeRunResult.cost 相等 + cost_currency 相等。）

- [ ] **Step 2: 跑全改动相关测试回归**

Run:
```bash
cd /Users/mango/project/shannon-refactor/shannon-py
uv run pytest packages/core/tests/agents/test_pricing.py packages/core/tests/agents/test_openai_result_mapper.py packages/core/tests/agents/test_providers.py packages/core/tests/agents/test_dual_engine_alignment.py packages/core/tests/test_metrics.py packages/core/tests/test_audit_types.py packages/core/tests/display packages/whitebox/tests/test_metrics_tracker.py -v
```
Expected: 全 PASS

- [ ] **Step 3: 真机冒烟（人工，两引擎各一次）**

glm-openai：`cd /Users/mango/project/shannon-refactor/shannon-py && SHANNON_PROFILE=glm-openai uv run python scripts/validate_openai_task_probe.py` → 确认结果 `cost=` 非零、`cost_currency` 为 CNY、session.json 有 token 明细。
glm-anthropic：`SHANNON_PROFILE=glm-anthropic uv run python scripts/validate_glm_task_probe.py` → 确认 claude 引擎 cost 来自自算（非 SDK），币种 CNY，token 明细落盘。
（若 probe 脚本不打印 cost，临时加 print 或查对应 session.json/workflow.log）

- [ ] **Step 4: 更新 memory**

新增/更新一条 memory（如 `per-profile-cost-pricing-implemented.md`）：记录 per-profile 定价 + 双引擎统一自算 + 币种/token 明细已实现，关键不变量（`cost_usd` 字段保留语义=cost_currency 金额）、配置方式（profile `SHANNON_PRICING_OVERRIDE` 指向 JSON）、待真机冒烟 merge。在 `MEMORY.md` 加索引行。

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/tests/agents/test_dual_engine_alignment.py
git commit -m "test(cost): 双引擎 cost 对齐(claude 自算,不再读 SDK)+ 回归绿"
```

---

## 验收（Definition of Done）

- [ ] 13 个 task 全部 commit，每 task 测试绿
- [ ] `glm-anthropic` / `glm-openai` 两 profile 真机冒烟：cost 非零、币种正确（CNY）、token 明细落盘 session.json + 展示
- [ ] claude 引擎 cost 来自项目自算（grep 确认 `total_cost_usd` 在 `providers_anthropic` 不再被读）
- [ ] CLI 终端 / workflow.log / Web dashboard 按币种显示 ¥/$ + token
- [ ] 旧 session.json（无 cost_currency）在 Web 仍可读（默认 USD）
- [ ] memory 更新，MEMORY.md 索引
