# openai 引擎成本核算（cost 不再恒 $0）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 openai 引擎（glm-openai profile）的审计报告 `Total Cost` 不再恒为 $0——补全 cache token 提取 + 新增 GLM 价目表换算（¥→$），替换 mapper 里写死的 `cost=0.0`。

**Architecture:** 新增纯函数模块 `agents/pricing.py`（内置 GLM 价目表 + 可配汇率 + 模型归一化 + env 覆盖）；`openai_result_mapper` 的 `_usage_from` 补 `cache_read_input_tokens`、`map_run_result` 的 `cost` 改调 `compute_cost_usd`。claude 引擎、`cost_usd` 数据模型、`MetricsTracker` 聚合、报告展示一律不动。未知模型回落 0.0 + 去重 warning（守「不假估算」）。

**Tech Stack:** Python 3.12、openai-agents SDK（`Usage.input_tokens_details.cached_tokens`）、pytest（caplog / monkeypatch / tmp_path）、标准库 `os`/`json`/`re`/`logging`。

## Global Constraints

- **只改 openai 引擎**；claude 引擎（`providers_anthropic.py`）零改动（spec §3）。
- **货币统一美元**：GLM 人民币 ÷ 汇率 → $，填进现有 `cost_usd`；**不引入 `cost_cny`**（spec §7）。
- **`utils/billing.py` 不动**：spending-cap 文本检测对 cost>0 引擎失效是「接受+文档化」，不为边缘情况改它（spec §4.6）。
- **`providers_openai.py:251` 的 `cost=0.0` 保持不改**——它在 `_handle_error`（错误路径），失败请求成本 0 是正确语义（spec §8 已确认）。
- **不触及双轨 / LLM 轨 prompt / 确定性层**——纯 provider 层成本核算，与双轨铁律无关（CLAUDE.md §1）。
- **价目表数值需按智谱官网核对**：plan 给的是**示例数值**（让测试可跑），执行者实现时按官网真实定价调整（spec §8）。测试**动态引用价目表常量**断言，不绑死数值，故调整数值不会让测试失效。
- **TokenUsage 字段**：`input_tokens` / `output_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens`（`runner.py:12-17`，均默认 0）。
- **测试只跑改动相关文件**（CLAUDE.md §3）：勿广跑全套（Temporal / 网络慢测试会 hang）。用 `uv run pytest <file> -v`。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/agents/pricing.py` | 内置 GLM 价目表 + 汇率 + `normalize_model` + `compute_cost_usd` + `is_model_priced` + env 覆盖（`SHANNON_USD_CNY_RATE` / `SHANNON_PRICING_OVERRIDE`） | 新建 |
| `packages/core/tests/agents/test_pricing.py` | pricing 单测（公式 / cache 折价 / 归一化 / 未知模型 / env 覆盖 / fallback） | 新建 |
| `packages/core/src/shannon_core/agents/openai_result_mapper.py` | `_usage_from` 补 cache_read；`map_run_result` cost 走 pricing + 未知模型去重 warning；文件头注释改写 | 修改 |
| `packages/core/tests/agents/test_openai_result_mapper.py` | mapper 测试：cache 提取 / cost 非零 / 未知模型 warning+去重；**修现有 `test_map_plain_text` 的 `cost==0.0` 断言** | 修改 |
| `packages/core/src/shannon_core/agents/providers_openai.py` | `_ReparsedRunResult` docstring 注释更新（line 307-314，「cost 仍走 GLM 0.0 早退」过时） | 修改（仅注释） |
| `packages/core/tests/test_billing.py` | spending-cap 不变量回归测试（cost>0 早退 False） | 修改 |

---

## Task 1: pricing 模块（价目表 + 换算 + 归一化 + env 覆盖）

**Files:**
- Create: `packages/core/src/shannon_core/agents/pricing.py`
- Test: `packages/core/tests/agents/test_pricing.py`

**Interfaces:**
- Consumes: `TokenUsage`（duck-typed——只读 `.input_tokens` / `.output_tokens` / `.cache_read_input_tokens`，**不 import runner**，避免循环 + 解耦）
- Produces:
  - `GLM_PRICING_CNY: dict[str, dict[str, float]]` —— `{model: {input, output, cache_read}}`，单位 ¥/百万 token
  - `USD_CNY_RATE: float` —— 默认汇率常量
  - `normalize_model(name: str) -> str`
  - `is_model_priced(model: str) -> bool`
  - `compute_cost_usd(model: str, usage) -> float`

- [ ] **Step 1: 写失败测试**（创建 `packages/core/tests/agents/test_pricing.py`）

```python
import json

import pytest

from shannon_core.agents.runner import TokenUsage
from shannon_core.agents.pricing import (
    GLM_PRICING_CNY,
    USD_CNY_RATE,
    compute_cost_usd,
    is_model_priced,
    normalize_model,
)


def test_compute_cost_known_model_no_cache():
    """已知模型、无 cache：按 input/output 价直接算 ¥→$。"""
    p = GLM_PRICING_CNY["glm-4.6"]
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
    expected = (1_000_000 * p["input"] + 500_000 * p["output"]) / 1_000_000 / USD_CNY_RATE
    assert compute_cost_usd("glm-4.6", usage) == pytest.approx(expected)


def test_compute_cost_cache_discount():
    """cache 拆分：input_tokens 含 cached_tokens 子集，命中部分按 cache_read 折价（更便宜）。"""
    p = GLM_PRICING_CNY["glm-4.6"]
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=400_000)
    cost = compute_cost_usd("glm-4.6", usage)
    # billable_input = 1M - 400k = 600k；cache_hit = 400k
    expected = (600_000 * p["input"] + 400_000 * p["cache_read"]) / 1_000_000 / USD_CNY_RATE
    assert cost == pytest.approx(expected)
    # 折价验证：含 cache 的成本 < 全按 input 价的成本
    no_cache = compute_cost_usd("glm-4.6", TokenUsage(input_tokens=1_000_000))
    assert cost < no_cache


def test_normalize_model_variants():
    assert normalize_model("GLM-5.2[1m]") == "glm-5.2"
    assert normalize_model("glm-4.6") == "glm-4.6"
    assert normalize_model("GLM-4.6-20260101") == "glm-4.6"
    assert normalize_model("") == ""


def test_compute_cost_normalizes_model():
    """GLM-5.2[1m] 归一化到 glm-5.2 → 命中价目表，cost 与 glm-5.2 一致。"""
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    assert compute_cost_usd("GLM-5.2[1m]", usage) == compute_cost_usd("glm-5.2", usage)


def test_unknown_model_zero_cost():
    assert compute_cost_usd("some-unknown-model", TokenUsage(input_tokens=1000)) == 0.0
    assert is_model_priced("some-unknown-model") is False
    assert is_model_priced("glm-4.6") is True


def test_env_rate_override(monkeypatch):
    monkeypatch.setenv("SHANNON_USD_CNY_RATE", "10.0")
    p = GLM_PRICING_CNY["glm-4.6"]
    usage = TokenUsage(input_tokens=1_000_000)
    expected = (1_000_000 * p["input"]) / 1_000_000 / 10.0
    assert compute_cost_usd("glm-4.6", usage) == pytest.approx(expected)


def test_invalid_rate_falls_back(monkeypatch):
    """汇率非法（非数）→ 落回默认常量，不崩（spec §4.5）。"""
    monkeypatch.setenv("SHANNON_USD_CNY_RATE", "not-a-number")
    p = GLM_PRICING_CNY["glm-4.6"]
    usage = TokenUsage(input_tokens=1_000_000)
    expected = (1_000_000 * p["input"]) / 1_000_000 / USD_CNY_RATE
    assert compute_cost_usd("glm-4.6", usage) == pytest.approx(expected)


def test_pricing_override_merge(tmp_path, monkeypatch):
    """SHANNON_PRICING_OVERRIDE 同 key 覆盖内置（spec §5）。"""
    override = {"glm-4.6": {"input": 100.0, "output": 100.0, "cache_read": 25.0}}
    f = tmp_path / "pricing.json"
    f.write_text(json.dumps(override))
    monkeypatch.setenv("SHANNON_PRICING_OVERRIDE", str(f))
    usage = TokenUsage(input_tokens=1_000_000)
    expected = (1_000_000 * 100.0) / 1_000_000 / USD_CNY_RATE
    assert compute_cost_usd("glm-4.6", usage) == pytest.approx(expected)


def test_pricing_override_invalid_ignored(tmp_path, monkeypatch):
    """override JSON 解析失败 → 忽略覆盖、用内置、不崩（spec §4.5）。"""
    f = tmp_path / "bad.json"
    f.write_text("{not valid json")
    monkeypatch.setenv("SHANNON_PRICING_OVERRIDE", str(f))
    p = GLM_PRICING_CNY["glm-4.6"]
    usage = TokenUsage(input_tokens=1_000_000)
    expected = (1_000_000 * p["input"]) / 1_000_000 / USD_CNY_RATE
    assert compute_cost_usd("glm-4.6", usage) == pytest.approx(expected)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_pricing.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'shannon_core.agents.pricing'`

- [ ] **Step 3: 写实现**（创建 `packages/core/src/shannon_core/agents/pricing.py`）

```python
"""openai 引擎 GLM 成本换算（¥→$）——纯函数，无副作用。

openai-agents SDK 不像 claude-agent-sdk 给 total_cost_usd，GLM 端点也不返回成本，
故按内置 GLM 价目表 + token 用量自算。未知模型回落 0.0（守「不假估算」，
spec §4.3/§4.5）。spending-cap 文本检测对 cost>0 引擎失效是已接受的不变量
（spec §4.6）——真正限额检测靠结构化错误码（executor.api_error_status），不靠 cost 猜。

usage 参数 duck-typed：只读 .input_tokens / .output_tokens / .cache_read_input_tokens
（通常是 shannon_core.agents.runner.TokenUsage）。
"""
from __future__ import annotations

import json
import logging
import os
import re

_log = logging.getLogger(__name__)

# 单位：¥ / 百万 token。示例数值——执行时按智谱官网核对调整（spec §8）。
# 测试动态引用本常量断言，故数值变化不会让测试失效。
GLM_PRICING_CNY: dict[str, dict[str, float]] = {
    "glm-4.6": {"input": 50.0, "output": 50.0, "cache_read": 12.5},
    "glm-5.2": {"input": 50.0, "output": 50.0, "cache_read": 12.5},
}

# 默认 ¥→$ 汇率；可经 SHANNON_USD_CNY_RATE 覆盖。
USD_CNY_RATE: float = 7.2

# 模型名后缀归一化：去 [xxx] / -YYYYMMDD / --xxx 等。
_MODEL_SUFFIX_RE = re.compile(r"\[.*?\]|-\d{8}.*$|--.*$", re.IGNORECASE)

# 别名 → 归一化 key（实现时按需补）。
_MODEL_ALIASES: dict[str, str] = {}


def normalize_model(name: str) -> str:
    """模型名归一化：小写 + 去后缀 + 别名映射（GLM-5.2[1m] → glm-5.2）。"""
    if not name:
        return ""
    key = name.strip().lower()
    key = _MODEL_SUFFIX_RE.sub("", key).strip()
    return _MODEL_ALIASES.get(key, key)


def _rate() -> float:
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


def _price_table() -> dict:
    merged = dict(GLM_PRICING_CNY)
    merged.update(_load_override())  # override 同 key 覆盖内置（spec §5）
    return merged


def is_model_priced(model: str) -> bool:
    return normalize_model(model) in _price_table()


def compute_cost_usd(model: str, usage) -> float:
    """按 GLM 价目表 + token 用量算 cost（¥→$）。未知模型或无用量 → 0.0。

    计费公式（spec §4.1）：
        billable_input = input_tokens - cached_tokens   # 按 input 价
        cache_hit      = cached_tokens                  # 按 cache_read 折价
        output         = output_tokens                 # 按 output 价
        cost_cny = (billable_input*P_in + cache_hit*P_cache + output*P_out) / 1_000_000
    reasoning_tokens 已包含在 output_tokens 内（OpenAI 语义），不重复计费。
    """
    key = normalize_model(model)
    table = _price_table()
    if key not in table:
        return 0.0
    p = table[key]
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    billable_input = max(inp - cached, 0)
    cost_cny = (
        billable_input * p["input"]
        + cached * p["cache_read"]
        + out * p["output"]
    ) / 1_000_000
    return cost_cny / _rate()
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_pricing.py -v`
Expected: PASS（9 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/pricing.py packages/core/tests/agents/test_pricing.py
git commit -m "feat(openai): GLM 价目表 + pricing 换算（¥→\$）模块"
```

---

## Task 2: mapper 改造（cache 提取 + cost 走 pricing + 未知模型 warning）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/openai_result_mapper.py`（`_usage_from` line 16-23、`map_run_result` line 62、文件头注释 line 11-13）
- Test: `packages/core/tests/agents/test_openai_result_mapper.py`

**Interfaces:**
- Consumes: Task 1 的 `compute_cost_usd` / `is_model_priced` / `normalize_model`
- Produces: `map_run_result` 返回的 `ClaudeRunResult.cost` 不再恒 0.0；`tokens.cache_read_input_tokens` 被填充

- [ ] **Step 1: 写失败测试**（修改 `packages/core/tests/agents/test_openai_result_mapper.py`）

先更新文件顶部 import + `_usage` helper（在现有 `from shannon_core.agents.runner import ...` 下加一行 import，并替换 `_usage` 函数）：

```python
import json
from unittest.mock import MagicMock

from shannon_core.agents.openai_result_mapper import map_run_result
from shannon_core.agents.pricing import compute_cost_usd
from shannon_core.agents.runner import ClaudeRunResult, TokenUsage


def _usage(inp, outp, cached=0):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = outp
    # 显式设 input_tokens_details：cached>0 时给带 cached_tokens 的 mock，否则 None
    # （避免 MagicMock 恒真污染 cache_read）
    if cached:
        u.input_tokens_details = MagicMock(cached_tokens=cached)
    else:
        u.input_tokens_details = None
    return u
```

再改现有 `test_map_plain_text` 的 cost 断言（line 34）——把 `assert res.cost == 0.0` 改为：

```python
    assert res.cost == compute_cost_usd("GLM-5.2[1m]", res.tokens)
    assert res.cost > 0.0  # glm-5.2 在价目表 → 不再恒 0
```

最后在文件末尾追加新测试：

```python
def test_map_extracts_cache_read():
    """_usage_from 提取 input_tokens_details.cached_tokens → cache_read；cache_creation=0。"""
    rr = _run_result("hi", _usage(1000, 500, cached=300))
    res = map_run_result(rr, duration_ms=10, model="glm-4.6", turns=1)
    assert res.tokens.cache_read_input_tokens == 300
    assert res.tokens.cache_creation_input_tokens == 0


def test_map_cost_nonzero_for_priced_model():
    rr = _run_result("hi", _usage(1_000_000, 0))
    res = map_run_result(rr, duration_ms=10, model="glm-4.6", turns=1)
    assert res.cost > 0.0
    assert res.cost == compute_cost_usd("glm-4.6", res.tokens)


def test_map_cost_zero_unknown_model_warning(caplog):
    """未知模型 → cost=0.0 + warning（spec §4.3）。"""
    rr = _run_result("hi", _usage(1000, 500))
    with caplog.at_level("WARNING", logger="shannon_core.agents.openai_result_mapper"):
        res = map_run_result(rr, duration_ms=10, model="mystery-model-xyz", turns=1)
    assert res.cost == 0.0
    assert any("未在价目表" in r.getMessage() for r in caplog.records)


def test_map_unknown_model_warning_dedup(caplog):
    """同模型进程内只 warning 一次（spec §4.3 去重）。"""
    from shannon_core.agents import openai_result_mapper as m
    m._WARNED_UNKNOWN_MODELS.clear()  # 隔离跨测试污染
    rr = _run_result("hi", _usage(1000, 500))
    with caplog.at_level("WARNING", logger="shannon_core.agents.openai_result_mapper"):
        map_run_result(rr, duration_ms=10, model="dedup-model-xyz", turns=1)
        map_run_result(rr, duration_ms=10, model="dedup-model-xyz", turns=1)
    assert sum(1 for r in caplog.records if "未在价目表" in r.getMessage()) == 1
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_openai_result_mapper.py -v`
Expected: FAIL —— `test_map_cost_nonzero_for_priced_model` 等：`res.cost` 仍为 0.0（mapper 还没改）；`test_map_extracts_cache_read`：`cache_read_input_tokens == 0`（没提取）

- [ ] **Step 3: 改实现**（修改 `packages/core/src/shannon_core/agents/openai_result_mapper.py`）

**3a. 文件头注释**（替换 line 11-13 整段注释）：

```python
"""openai-agents RunResult → shannon ClaudeRunResult 映射（纯函数，无副作用）。"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents import RunResult

from .pricing import compute_cost_usd, is_model_priced, normalize_model
from .runner import ClaudeRunResult, TokenUsage

_log = logging.getLogger(__name__)
_WARNED_UNKNOWN_MODELS: set[str] = set()

# cost 由 pricing.py 按内置 GLM 价目表（¥→$，可经 SHANNON_USD_CNY_RATE /
# SHANNON_PRICING_OVERRIDE 覆盖）换算；未知模型回落 0.0 + warning（守「不假估算」）。
# spending-cap 文本检测（utils/billing.is_spending_cap_behavior）的 cost>0→False 早退
# 使该检测对 cost>0 引擎失效——这是已接受的不变量（与 claude 引擎一致），
# 真正限额检测靠结构化错误码（executor.api_error_status）。详见
# docs/superpowers/specs/2026-06-29-openai-engine-cost-accounting-design.md §4.6。
```

> 注：`from __future__ import annotations` / `import json` / `from typing import Any` / `from agents import RunResult` / `from .runner import ...` 这些原文件已有，**只新增** `import logging`、`from .pricing import ...`、`_log`、`_WARNED_UNKNOWN_MODELS` 和替换那段注释。

**3b. `_usage_from` 补 cache**（替换 line 16-23 整个函数）：

```python
def _usage_from(run_result: RunResult) -> TokenUsage:
    usage = getattr(getattr(run_result, "context_wrapper", None), "usage", None)
    if usage is None:
        return TokenUsage()
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=cached or 0,
        cache_creation_input_tokens=0,  # openai 协议无此概念（自动缓存、无创建费）
    )
```

**3c. `map_run_result` cost 走 pricing**（在 line 57-69 的 `return ClaudeRunResult(...)` 块内，把 `cost=0.0,  # 见文件头注释（C1）` 替换为下面的逻辑；需要在 `tokens = _usage_from(run_result)` 之后、`return` 之前插入 cost 计算 + 未知模型 warning）：

在 `tokens = _usage_from(run_result)`（原 line 41）后插入：

```python
    cost = compute_cost_usd(model, tokens)
    if model and cost == 0.0 and not is_model_priced(model):
        norm = normalize_model(model)
        if norm not in _WARNED_UNKNOWN_MODELS:
            _WARNED_UNKNOWN_MODELS.add(norm)
            _log.warning(
                "openai 引擎成本核算：模型 %r 未在价目表中，cost 回落 0.0（不假估算）。"
                "可经 SHANNON_PRICING_OVERRIDE 补充。",
                model,
            )
```

并把 return 块里的 `cost=0.0,` 改为 `cost=cost,`。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_openai_result_mapper.py -v`
Expected: PASS（含改过的 `test_map_plain_text` + 4 个新测试）

- [ ] **Step 5: 跑 pricing 测试确认无回归**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/agents/test_pricing.py packages/core/tests/agents/test_openai_result_mapper.py -v`
Expected: PASS（两文件全过）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/openai_result_mapper.py packages/core/tests/agents/test_openai_result_mapper.py
git commit -m "feat(openai): mapper 提取 cache token + cost 走 pricing（不再恒 \$0）"
```

---

## Task 3: spending-cap 不变量回归 + providers_openai 注释更新

**Files:**
- Modify: `packages/core/tests/test_billing.py`（末尾追加）
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（`_ReparsedRunResult` docstring line 307-314）

**Interfaces:**
- Consumes: 无新接口（锁住现有 `is_spending_cap_behavior` 行为）
- Produces: 无（纯回归保护 + 文档对齐）

- [ ] **Step 1: 写回归测试**（追加到 `packages/core/tests/test_billing.py` 末尾）

```python
def test_spending_cap_ignored_when_cost_positive():
    """不变量回归：cost>0 时 spending-cap 文本检测早退 False（spec §4.6）。

    claude 引擎 cost 一直非 0、本次让 openai cost 也非 0 → 两引擎均不会被判
    spending-cap；真正限额检测靠结构化错误码。锁住此行为不被误改。
    """
    assert not is_spending_cap_behavior(turns=1, cost=0.01, text="spending cap reached")
    assert not is_spending_cap_behavior(turns=1, cost=5.0, text="quota exceeded")
    assert not is_spending_cap_behavior(turns=2, cost=0.5, text="billing limit reached")
```

- [ ] **Step 2: 跑测试验证通过**（回归测试应直接通过——锁的是现有行为）

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_billing.py -v`
Expected: PASS（含新测试）

- [ ] **Step 3: 更新 `_ReparsedRunResult` docstring**（`providers_openai.py` line 307-314，把「cost 仍走 GLM 0.0 早退」那句改成反映新行为）

把 line 310-311 的：
```
    （带 L1 chat completion 的真实 token，避免统计失真；cost 仍走 GLM 0.0 早退）。
```
改为：
```
    （带 L1 chat completion 的真实 token，避免统计失真；cost 经 map_run_result
    的 pricing 换算，非 GLM 0.0 早退——见 pricing.py）。
```

> 注：`_handle_error`（line 251）的 `cost=0.0` **保持不动**——错误路径成本 0 是正确语义（spec §8）。

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/test_billing.py packages/core/src/shannon_core/agents/providers_openai.py
git commit -m "docs(openai): spending-cap 不变量回归测试 + _ReparsedRunResult 注释更新"
```

---

## Task 4: 真机冒烟（人工）

**Files:** 无代码改动——运行现有探针验证端到端。

- [ ] **Step 1: 跑 openai 引擎探针**

Run: `cd /root/shannon-py && uv run python scripts/validate_openai_task_probe.py`
Expected: 探针 success=True；结果输出里 `cost=` 字段 **非 0**（之前恒 0.0）。

- [ ] **Step 2: 若 cost 仍为 0**

排查：① 探针用的模型经 `normalize_model` 后是否在 `GLM_PRICING_CNY` 里（不在 → 走未知模型 warning，按 warning 提示用 `SHANNON_PRICING_OVERRIDE` 补条目）；② 用 `uv run python -c "from shannon_core.agents.pricing import normalize_model; print(normalize_model('<探针实际模型名>'))"` 看归一化结果。

- [ ] **Step 3: 记录 memory**

把「openai 引擎成本核算已实现于 feat/fork-py，真机冒烟 cost 非 0 验证通过（或记待办）」写入 `memory/`（更新 `MEMORY.md` + 新建一条 status memory，参照现有 `openai-structured-output-resilience-status.md` 格式）。

---

## Self-Review

**1. Spec coverage（逐节核对）：**
- §1 现状（两引擎不对称、cost 写死 0.0）→ Task 2 改 mapper。✅
- §2 方案 A（内置价目表 + env 覆盖 + 汇率）→ Task 1。✅
- §3 范围（claude/metrics/billing 不动；providers_openai 注释）→ Task 3 + Global Constraints。✅
- §4.1 计费公式（cache 拆分、reasoning 含在 output、cache_creation=0）→ Task 1 `compute_cost_usd` + `test_compute_cost_cache_discount`。✅
- §4.2 pricing 模块（价目表/汇率/归一化/compute_cost_usd/env）→ Task 1。✅
- §4.3 mapper 改动（cache 提取、cost 走 pricing、未知模型去重 warning、文件头注释）→ Task 2。✅
- §4.4 数据流复用 → 无代码改动（Global Constraints 已声明 metrics 不动）。✅
- §4.5 边缘 fallback（未知模型/汇率非法/override 解析失败）→ Task 1 `test_unknown_model_zero_cost` / `test_invalid_rate_falls_back` / `test_pricing_override_invalid_ignored`。✅
- §4.6 spending-cap 不变量（接受+文档化）→ Task 3 回归测试。✅
- §5 配置 env 清单（`SHANNON_USD_CNY_RATE` / `SHANNON_PRICING_OVERRIDE`）→ Task 1。✅
- §6 测试（pricing/mapper/不变量回归/真机冒烟）→ Task 1/2/3/4。✅
- §7 不做（claude/双币种/pricing API/改 billing/其他 provider）→ Global Constraints。✅
- §8 风险（定价漂移/reasoning 假设/cache 假设/providers_openai 第二处 cost）→ Global Constraints + Task 3 Step 3 注（_handle_error 保持 0.0）。✅

**2. Placeholder scan：** 无 TBD/TODO。价目表数值是具体示例（非占位），标注「执行时按官网核对」+ 测试动态引用常量。✅

**3. Type consistency：** `compute_cost_usd` / `is_model_priced` / `normalize_model` 在 Task 1 定义、Task 2 消费——签名一致。`_WARNED_UNKNOWN_MODELS` Task 2 定义 + dedup 测试引用——同名。`cache_read_input_tokens` / `cache_creation_input_tokens` 字段名与 `runner.py:16-17` 一致。✅

**4. 现有测试破坏点：** `test_map_plain_text:34` 的 `cost==0.0` 断言已在 Task 2 Step 1 显式改为 `compute_cost_usd(...)` 断言。✅
