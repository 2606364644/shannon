# chunk threshold 按模型 context 自适应 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 sink/source LLM 补召回的 chunk token threshold 按当前模型 context window 自适应（默认 128K，glm-5.2 走 1M），并把 token 估算从 `len//4` 改为 CJK 加权启发式，根治大仓 chunk 过细撞 activity timeout。

**Architecture:** 新增 `agents/model_caps.py`（context 表 + override 加载 + `get_chunk_token_threshold`），改进 `llm_concurrency._estimate_tokens` 为 CJK 加权，activity 层 resolve medium-tier model 名后传参给 `discover_sinks/sources_llm`（不裸读 env，防 tier 错配）。threshold 优先级链：`SHANNON_CHUNK_TOKEN_THRESHOLD`（hard override）> `context × 0.75`（派生）> 128K 默认。

**Tech Stack:** Python 3.12，stdlib only（`re`/`math`/`json`/`os`），零新依赖；pytest TDD；temporalio activity。

## Global Constraints

- 守 CLAUDE.md §1 双轨铁律：只动 GitNexus 轨 chunking 参数派生，**不改 LLM 轨 prompt、不引确定性->LLM hints 桥梁**。`test_static_dataflow_hints_decoupling.py` 必须仍绿。
- 守「畸形 env 不崩 scan」契约（对齐 `concurrency.get_max_concurrent` / `get_per_call_timeout`）：override 解析失败/畸形 -> 回落默认 + warning，绝不 raise。
- **不裸读 `SHANNON_MODEL` env 做 context**：gitnexus 轨 LLM 用 `model_tier="medium"`（`activities.py:761`），而 `SHANNON_MODEL`=large tier，错配会估大 context 导致爆 context。model 名必须由 activity 经 `resolve_tier_model("medium")` 传参。
- token 估算偏保守（宁可高估切小 chunk，不可低估爆 context）。
- 删的是**代码常量** `CHUNK_TOKEN_THRESHOLD = 12_000`（`llm_concurrency.py:37`），**保留** hard override env `SHANNON_CHUNK_TOKEN_THRESHOLD`（优先级最高档）。两者同名易混，注意区分。
- 测试只跑改动相关文件，勿广跑全套（预存挂起/失败，见 CLAUDE.md §3 + memory `feat-fork-py-test-gotchas`）。

---

## File Structure

| 文件 | 责任 | 状态 |
|---|---|---|
| `packages/core/src/shannon_core/agents/model_caps.py`（新） | context 表 + override 加载 + `get_model_context_window` / `get_chunk_token_threshold` | 新建 |
| `packages/core/tests/agents/test_model_caps.py`（新） | model_caps TDD 测试 | 新建 |
| `packages/core/src/shannon_core/agents/providers.py` | 提取 `resolve_tier_model(tier)` 模块级公共函数 | 修改 |
| `packages/core/tests/agents/test_providers_tier_model.py`（新） | resolve_tier_model TDD 测试 | 新建 |
| `packages/core/src/shannon_core/code_index/llm_concurrency.py` | `_estimate_tokens` CJK 加权；删 `CHUNK_TOKEN_THRESHOLD` 常量；`chunk_items_by_file` 默认参数改 `None` | 修改 |
| `packages/core/tests/code_index/test_llm_chunking.py` | 适配常量删除 + 加 token 估算测试 | 修改 |
| `packages/core/src/shannon_core/code_index/sink_discovery_llm.py` | 增 `model` 参数；threshold 从 `get_chunk_token_threshold(model)` 派生 | 修改 |
| `packages/core/src/shannon_core/code_index/source_discovery_llm.py` | 同上 | 修改 |
| `packages/core/src/shannon_core/code_index/__init__.py` | `build_code_index_with_gitnexus` 增 `model` 参数，透传两个 discovery | 修改 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `_make_gitnexus_llm_client` resolve medium model 名，传入 build_code_index | 修改 |
| `.env.profiles.example/glm-anthropic.env.example` | 示例：`SHANNON_MODEL_CONTEXT_OVERRIDE` + `SHANNON_CHUNK_TOKEN_THRESHOLD` 注释 | 修改 |

---

### Task 1: model_caps 配置层（context 表 + threshold 派生）

**Files:**
- Create: `packages/core/src/shannon_core/agents/model_caps.py`
- Test: `packages/core/tests/agents/test_model_caps.py`

**Interfaces:**
- Consumes: `pricing.normalize_model(name) -> str`（复用，`agents/pricing.py:60`）
- Produces:
  - `MODEL_CONTEXT_WINDOWS: dict[str, int]`
  - `DEFAULT_CONTEXT_WINDOW = 128_000`
  - `CHUNK_RESERVE_RATIO = 0.75`
  - `get_model_context_window(model: str | None) -> int`
  - `get_chunk_token_threshold(model: str | None) -> int`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/agents/test_model_caps.py`:

```python
"""model_caps: 模型 context window 配置层 + chunk threshold 派生(spec 2026-07-10)。

复用 pricing.normalize_model 归一化模型名; context 来源优先级:
override JSON(SHANNON_MODEL_CONTEXT_OVERRIDE) > 内置表 > DEFAULT_CONTEXT_WINDOW。
threshold = context × CHUNK_RESERVE_RATIO(留 25% 给 output+system prompt+估算误差)。
"""
import pytest

from shannon_core.agents.model_caps import (
    CHUNK_RESERVE_RATIO,
    DEFAULT_CONTEXT_WINDOW,
    MODEL_CONTEXT_WINDOWS,
    get_chunk_token_threshold,
    get_model_context_window,
)


def test_builtin_context_table_has_glm():
    assert MODEL_CONTEXT_WINDOWS.get("glm-5.2") == 1_000_000
    assert MODEL_CONTEXT_WINDOWS.get("glm-4.5-air") == 128_000


def test_get_context_window_builtin_model():
    assert get_model_context_window("glm-5.2") == 1_000_000


def test_get_context_window_normalizes_model():
    """带后缀 [1m] / 大小写都归一化到 glm-5.2。"""
    assert get_model_context_window("GLM-5.2[1m]") == 1_000_000


def test_get_context_window_unknown_model_falls_back_default():
    assert get_model_context_window("some-unknown-model") == DEFAULT_CONTEXT_WINDOW
    assert DEFAULT_CONTEXT_WINDOW == 128_000


def test_get_context_window_none_falls_back_default():
    assert get_model_context_window(None) == DEFAULT_CONTEXT_WINDOW


def test_get_chunk_token_threshold_derives_from_context():
    """threshold = context × 0.75。"""
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * CHUNK_RESERVE_RATIO)
    assert get_chunk_token_threshold("glm-5.2") == 750_000


def test_get_chunk_token_threshold_default_model():
    assert get_chunk_token_threshold("unknown") == int(DEFAULT_CONTEXT_WINDOW * CHUNK_RESERVE_RATIO)
    assert get_chunk_token_threshold(None) == int(DEFAULT_CONTEXT_WINDOW * CHUNK_RESERVE_RATIO)


def test_override_json_overrides_builtin(tmp_path, monkeypatch):
    """SHANNON_MODEL_CONTEXT_OVERRIDE JSON: {"models": {model: ctx}} 覆盖内置表。"""
    f = tmp_path / "caps.json"
    f.write_text('{"models": {"glm-5.2": 500_000}}', encoding="utf-8")
    monkeypatch.setenv("SHANNON_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("glm-5.2") == 500_000
    assert get_chunk_token_threshold("glm-5.2") == int(500_000 * CHUNK_RESERVE_RATIO)


def test_override_json_adds_new_model(tmp_path, monkeypatch):
    """override 可补充内置表没有的模型。"""
    f = tmp_path / "caps.json"
    f.write_text('{"models": {"custom-model": 200_000}}', encoding="utf-8")
    monkeypatch.setenv("SHANNON_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("custom-model") == 200_000


def test_override_invalid_ignored(tmp_path, monkeypatch):
    """override JSON 解析失败 -> 忽略覆盖、用内置表、不崩(spec 容错契约)。"""
    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("SHANNON_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("glm-5.2") == 1_000_000  # 回落内置


def test_override_non_object_top_ignored(tmp_path, monkeypatch):
    """override 顶层非 object / models 非 dict -> 忽略、不崩。"""
    f = tmp_path / "bad2.json"
    f.write_text('["not", "an", "object"]', encoding="utf-8")
    monkeypatch.setenv("SHANNON_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("glm-5.2") == 1_000_000


def test_hard_override_env_takes_precedence(tmp_path, monkeypatch):
    """SHANNON_CHUNK_TOKEN_THRESHOLD(hard override)> 派生值, 跳过 context 计算。"""
    monkeypatch.setenv("SHANNON_CHUNK_TOKEN_THRESHOLD", "50000")
    assert get_chunk_token_threshold("glm-5.2") == 50_000


def test_hard_override_env_invalid_falls_back(monkeypatch):
    """SHANNON_CHUNK_TOKEN_THRESHOLD 畸形(非 int)<=0 -> 回落派生值, 不崩。"""
    monkeypatch.setenv("SHANNON_CHUNK_TOKEN_THRESHOLD", "not-a-number")
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * CHUNK_RESERVE_RATIO)


def test_hard_override_env_zero_or_negative_falls_back(monkeypatch):
    monkeypatch.setenv("SHANNON_CHUNK_TOKEN_THRESHOLD", "0")
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * CHUNK_RESERVE_RATIO)
    monkeypatch.setenv("SHANNON_CHUNK_TOKEN_THRESHOLD", "-5")
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * CHUNK_RESERVE_RATIO)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/test_model_caps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shannon_core.agents.model_caps'`

- [ ] **Step 3: Write minimal implementation**

Create `packages/core/src/shannon_core/agents/model_caps.py`:

```python
"""模型 context window 配置层 + chunk token threshold 派生(spec 2026-07-10)。

GitNexus 轨 sink/source LLM 补召回的 chunk 切分 threshold 按当前模型 context 自适应:
默认/未知 128K, glm-5.2 走 1M(经 normalize_model 归一化)。threshold = context × 0.75
(留 25% 给 output + system prompt + token 估算误差), 结构上防 prompt 爆 context。

context 来源优先级(spec §4):
  1. SHANNON_MODEL_CONTEXT_OVERRIDE JSON({"models": {model: ctx}}), 经 env_loader 天然
     per-profile(.profiles 覆盖 .env)
  2. 内置 MODEL_CONTEXT_WINDOWS 表
  3. DEFAULT_CONTEXT_WINDOW = 128_000(未知模型)

threshold 最终值优先级:
  1. SHANNON_CHUNK_TOKEN_THRESHOLD env(hard override, 调试/止血用)
  2. get_model_context_window(model) × CHUNK_RESERVE_RATIO(派生)

容错契约(对齐 concurrency.get_max_concurrent / pricing._load_override):
override 解析失败 / 畸形 env -> 回落默认 + warning, 绝不 raise, 绝不崩 scan。
"""
from __future__ import annotations

import json
import logging
import os

from .pricing import normalize_model

_log = logging.getLogger(__name__)

# 内置已知模型真实 context window(2026-07-10; 待官网核对, 见 spec §10)。
# 数值错 -> threshold 估错; 可经 SHANNON_MODEL_CONTEXT_OVERRIDE 纠正。
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "glm-5.2": 1_000_000,
    "glm-4.5-air": 128_000,
}

# 未知模型保守回落(= 用户「默认 128K」)。
DEFAULT_CONTEXT_WINDOW = 128_000

# 留 25% 给 output + system prompt + token 估算误差。sink discovery output 小
# (结构化 verdict), 本可更激进(如 0.85); 但取 0.75 兼顾 taint/未来场景 output 较大时更稳。
CHUNK_RESERVE_RATIO = 0.75


def _load_context_override() -> dict[str, int]:
    """加载 SHANNON_MODEL_CONTEXT_OVERRIDE JSON, 返回 {归一化 model: ctx}。

    schema: {"models": {model: ctx_int}}。顶层非 object / models 非 dict / 值非 int
    -> 返回 {} + warning(容错, 不崩)。未设 env -> {}。
    """
    path = os.environ.get("SHANNON_MODEL_CONTEXT_OVERRIDE")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("SHANNON_MODEL_CONTEXT_OVERRIDE 解析失败（%s），忽略覆盖", e)
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        _log.warning("SHANNON_MODEL_CONTEXT_OVERRIDE 顶层非 object 或 models 非 dict，忽略覆盖")
        return {}
    out: dict[str, int] = {}
    for name, ctx in models.items():
        if not isinstance(ctx, int) or ctx <= 0:
            _log.warning("SHANNON_MODEL_CONTEXT_OVERRIDE model %r 的 ctx=%r 非正 int，跳过", name, ctx)
            continue
        out[normalize_model(name)] = ctx
    return out


def get_model_context_window(model: str | None) -> int:
    """模型 -> context window token 数。未知/None -> DEFAULT_CONTEXT_WINDOW。"""
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    key = normalize_model(model)
    override = _load_context_override()
    if key in override:
        return override[key]
    return MODEL_CONTEXT_WINDOWS.get(key, DEFAULT_CONTEXT_WINDOW)


def _hard_override_threshold() -> int | None:
    """读 SHANNON_CHUNK_TOKEN_THRESHOLD env(hard override, 调试用)。非法 -> None。"""
    raw = os.environ.get("SHANNON_CHUNK_TOKEN_THRESHOLD")
    if raw is None:
        return None
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SHANNON_CHUNK_TOKEN_THRESHOLD=%r 非 int，回落派生值", raw)
        return None
    if val <= 0:
        _log.warning("SHANNON_CHUNK_TOKEN_THRESHOLD=%d 须 >0，回落派生值", val)
        return None
    return val


def get_chunk_token_threshold(model: str | None) -> int:
    """chunk token threshold: hard override env > context × CHUNK_RESERVE_RATIO。"""
    hard = _hard_override_threshold()
    if hard is not None:
        return hard
    return int(get_model_context_window(model) * CHUNK_RESERVE_RATIO)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/test_model_caps.py -v`
Expected: PASS (all 14 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/agents/model_caps.py packages/core/tests/agents/test_model_caps.py
git commit -m "feat(agents): model_caps 配置层 - context 表 + threshold 派生"
```

---

### Task 2: resolve_tier_model 公共函数（provider tier 解析提取）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/providers.py`（在 `DEFAULT_MODELS` import 区附近 / 模块级）
- Test: `packages/core/tests/agents/test_providers_tier_model.py`

**Interfaces:**
- Consumes: `runner.DEFAULT_MODELS: dict[str, dict[str, str]]`（`runner.py:47`）；provider `config.small_model/medium_model/large_model/model`（`ProviderConfig`）
- Produces: `resolve_tier_model(config, model_tier) -> str`（模块级函数，复用两引擎 `_get_model` 优先级）

**背景**：`providers_anthropic._get_model`（:52）和 `providers_openai._get_model`（:52）逻辑几乎相同（tier env > global model > DEFAULT_MODELS），但各自内联、provider_key 不同。提取一个公共模块级函数供两引擎复用 + activity 层用于 resolve medium model 名（不实例化整个 provider）。

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/agents/test_providers_tier_model.py`:

```python
"""resolve_tier_model: provider tier 解析提取为模块级公共函数(spec 2026-07-10)。

优先级: tier-specific config(medium_model 等) > global config.model > DEFAULT_MODELS。
供 activity 层 resolve medium-tier model 名(传给 chunk threshold 派生, 不裸读 env)。
"""
from shannon_core.agents.providers import resolve_tier_model
from shannon_core.agents.runner import DEFAULT_MODELS, ProviderConfig


def _cfg(**kw):
    """ProviderConfig 最小构造(其余字段默认 None)。"""
    return ProviderConfig(type="anthropic_api", **kw)


def test_tier_specific_model_wins():
    cfg = _cfg(medium_model="glm-5.2", model="gpt-4o")
    assert resolve_tier_model(cfg, "medium") == "glm-5.2"


def test_global_model_fallback():
    """tier 未配 -> 用 global model。"""
    cfg = _cfg(model="glm-4.5-air")
    assert resolve_tier_model(cfg, "medium") == "glm-4.5-air"


def test_default_models_fallback_anthropic():
    """tier 和 global 都未配 -> DEFAULT_MODELS。"""
    cfg = _cfg()
    assert resolve_tier_model(cfg, "medium") == DEFAULT_MODELS["anthropic_api"]["medium"]


def test_default_models_fallback_openai():
    cfg = ProviderConfig(type="openai_compatible")
    assert resolve_tier_model(cfg, "medium") == DEFAULT_MODELS["openai_compatible"]["medium"]


def test_unknown_tier_falls_back_to_medium():
    cfg = _cfg()
    # 未知 tier -> 回落 medium(DEFAULT_MODELS 兜底)
    result = resolve_tier_model(cfg, "nonexistent_tier")
    assert result == DEFAULT_MODELS["anthropic_api"]["medium"]


def test_small_and_large_tiers():
    cfg = _cfg(small_model="haiku", large_model="opus")
    assert resolve_tier_model(cfg, "small") == "haiku"
    assert resolve_tier_model(cfg, "large") == "opus"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/test_providers_tier_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_tier_model'`

- [ ] **Step 3: Write minimal implementation**

在 `packages/core/src/shannon_core/agents/providers.py` 顶部 import 区后（`DEFAULT_MODELS` 已从 runner import 的前提下）添加模块级函数。先确认 providers.py 是否已 import DEFAULT_MODELS：

```bash
grep -n "DEFAULT_MODELS\|from .runner import\|ProviderConfig" packages/core/src/shannon_core/agents/providers.py | head
```

如未 import，补 import。然后在文件中（模块级，类外）添加：

```python
def resolve_tier_model(config: ProviderConfig, model_tier: str) -> str:
    """根据 tier 解析模型名(模块级公共函数, 供两引擎 _get_model 复用 + activity 层用)。

    优先级(对齐两引擎 _get_model): tier-specific config > global config.model > DEFAULT_MODELS。
    provider_key 由 config.type 决定(anthropic_api/bedrock/vertex/openai_compatible/litellm_router)。
    未知 tier -> 回落 medium(DEFAULT_MODELS 兜底)。
    """
    # 1. Tier-specific override
    tier_models = {
        "small": config.small_model,
        "medium": config.medium_model,
        "large": config.large_model,
    }
    tier_model = tier_models.get(model_tier)
    if tier_model:
        return tier_model

    # 2. Global model fallback
    if config.model:
        return config.model

    # 3. DEFAULT_MODELS
    ptype = config.type or "anthropic_api"
    if ptype == "bedrock":
        provider_key = "bedrock"
    elif ptype == "vertex":
        provider_key = "vertex"
    elif ptype == "litellm_router":
        provider_key = "litellm_router"
    elif ptype == "openai_compatible":
        provider_key = "openai_compatible"
    else:
        provider_key = "anthropic_api"
    models = DEFAULT_MODELS.get(provider_key, DEFAULT_MODELS["anthropic_api"])
    return models.get(model_tier, models.get("medium", "claude-sonnet-4-6"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/test_providers_tier_model.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: (可选重构) 让两引擎 _get_model 复用 resolve_tier_model**

此步为去重收益（非必须，但符合 DRY）。将 `providers_anthropic._get_model`（:52-79）和 `providers_openai._get_model`（:52-64）体改为 `return resolve_tier_model(self.config, model_tier)`。改后跑两个引擎现有测试确认无回归：

```bash
cd /root/shannon-py
.venv/bin/python -m pytest packages/core/tests/agents/ -k "provider" -v 2>&1 | tail -20
```

如出现失败，回退此步（保留旧 `_get_model`，resolve_tier_model 独立存在即可，不影响后续 task）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/agents/providers.py packages/core/tests/agents/test_providers_tier_model.py
# 若 Step 5 重构了双引擎, 一并 add
git add packages/core/src/shannon_core/agents/providers_anthropic.py packages/core/src/shannon_core/agents/providers_openai.py 2>/dev/null
git commit -m "feat(agents): 提取 resolve_tier_model 公共函数(tier 解析复用)"
```

---

### Task 3: token 估算改 CJK 加权（零依赖）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/llm_concurrency.py:36-42`
- Test: `packages/core/tests/code_index/test_llm_chunking.py`（加 token 估算测试）

**Interfaces:**
- Produces: `_estimate_tokens(text: str) -> int`（CJK × 1.5 + 其余 / 4，向上取整）

**背景**：现状 `_estimate_tokens = len//4`（`llm_concurrency.py:42`）对中文注释严重低估 4-8x，违背「宁可高估防 context 爆」目标。改 CJK 加权。注意：`_estimate_tokens` 是模块私有函数（下划线前缀），测试经 `from ... import _estimate_tokens` 直引。

- [ ] **Step 1: Write the failing test**

在 `packages/core/tests/code_index/test_llm_chunking.py` 末尾追加（顶部 import 区加 `_estimate_tokens`）。

先改 import（:9-13 现状）：

```python
from shannon_core.code_index.llm_concurrency import (
    FileChunk,
    _estimate_tokens,
    chunk_items_by_file,
)
```
（删 `CHUNK_TOKEN_THRESHOLD` import——本 task 暂留常量不动，Task 4 才删；但 `test_chunk_default_threshold_is_set`（:96-99）引用了它，Task 4 处理。本 task 先不动 import 那行以外的引用。）

实际操作：本 task 只在文件末尾追加 token 估算测试 + import 加 `_estimate_tokens`。**保留** `CHUNK_TOKEN_THRESHOLD` import（Task 4 才删）。

```python
# === token 估算 CJK 加权(spec 2026-07-10) ===

def test_estimate_tokens_ascii_approx_len_div_4():
    """纯 ASCII: ~4 char/token(与旧 len//4 行为一致)。"""
    text = "def f():\n    return 1\n"  # 20 chars
    assert _estimate_tokens(text) == 5  # ceil(0*1.5 + 20/4) = 5


def test_estimate_tokens_chinese_not_underestimated():
    """纯中文: ~1.5 token/char(不再被 len//4 低估成 0.25 token/char)。"""
    text = "中" * 100  # 100 chars 全 CJK
    assert _estimate_tokens(text) == 150  # ceil(100*1.5 + 0/4)


def test_estimate_tokens_mixed_cjk_and_ascii():
    """混合: CJK 按 1.5, 其余按 /4。"""
    cjk = "你好世界"  # 4 CJK chars
    ascii_part = "x" * 40  # 40 ascii
    text = cjk + ascii_part  # len=44, cjk=4
    expected = 4 * 1.5 + 40 / 4  # 6 + 10 = 16
    assert _estimate_tokens(text) == 16  # ceil(16.0)=16


def test_estimate_tokens_japanese_korean_counted_as_cjk():
    """日文/韩文也按 CJK 高权重(防低估)。"""
    jp = "こんにちは" * 10  # 50 CJK chars
    kr = "안녕하세요" * 10  # 50 CJK chars
    text = jp + kr  # 100 CJK
    assert _estimate_tokens(text) == 150


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_never_underestimates_pure_cjk_vs_len_div_4():
    """核心不变量: 对纯中文, CJK 加权 > len//4(防 context 爆)。"""
    text = "代码注释中文" * 50  # 300 CJK chars
    assert _estimate_tokens(text) > len(text) // 4  # 450 > 75
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_llm_chunking.py -k "estimate_tokens" -v`
Expected: FAIL（`_estimate_tokens` 返回 `len//4`，中文测试不符）

- [ ] **Step 3: Write minimal implementation**

改 `packages/core/src/shannon_core/code_index/llm_concurrency.py:36-42`。先看现状：

```python
# 单 chunk prompt token 上限(留 response 余量; ~12K)。源码字符数 // 4 粗估 token。
CHUNK_TOKEN_THRESHOLD = 12_000


def _estimate_tokens(text: str) -> int:
    """源码字符数粗估 token(英文 ~4 chars/token; 粗估用于 chunk 装箱, 非精确计费)。"""
    return len(text) // 4
```

改为（**保留** `CHUNK_TOKEN_THRESHOLD` 常量到 Task 4 删，本步只改 `_estimate_tokens`，加 `import math, re` 到文件顶部）：

```python
import math
import re
# ... (文件已有的 import 区)


# CJK 字符范围: 中文/日文/韩文。BPE 下常 1~2 token/char, 取 1.5 中位偏保守防低估。
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _estimate_tokens(text: str) -> int:
    """源码 token 估算: CJK × 1.5 + 其余 / 4, 向上取整(spec 2026-07-10)。

    比旧 len//4 准 2-3x(中文注释不再被严重低估 4-8x), 偏保守防 prompt 爆 context。
    仅用于 chunk 装箱, 不进 pricing 计费(计费走真实 usage)。
    """
    cjk = len(_CJK_RE.findall(text))
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 4)
```

注：`CHUNK_TOKEN_THRESHOLD = 12_000` 常量 + 其上方注释**本步保留不动**（Task 4 删），避免本 task 动太多。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_llm_chunking.py -v`
Expected: PASS（含新增 6 个 estimate_tokens 测试 + 原有 chunking 测试仍绿，`test_chunk_default_threshold_is_set` 仍绿因常量还在）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/llm_concurrency.py packages/core/tests/code_index/test_llm_chunking.py
git commit -m "feat(code_index): token 估算改 CJK 加权(防中文注释低估爆 context)"
```

---

### Task 4: chunking 删硬编码常量，threshold 改显式传入

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/llm_concurrency.py`（删 `CHUNK_TOKEN_THRESHOLD` 常量 + `chunk_items_by_file` 默认参数改 `None`）
- Modify: `packages/core/tests/code_index/test_llm_chunking.py`（适配常量删除）

**Interfaces:**
- Produces: `chunk_items_by_file(items, *, block_of, token_threshold: int)` — `token_threshold` 现为**必填**（无默认），防误用旧 12K

**背景**：删代码常量 `CHUNK_TOKEN_THRESHOLD = 12_000`（保留 hard override env `SHANNON_CHUNK_TOKEN_THRESHOLD`，两者同名易混，见 Global Constraints）。`chunk_items_by_file` 的 `token_threshold` 默认从 `CHUNK_TOKEN_THRESHOLD` 改为必填，强制调用方显式传派生值。

- [ ] **Step 1: Write the failing test**

改 `packages/core/tests/code_index/test_llm_chunking.py`：

(a) 删 import 中的 `CHUNK_TOKEN_THRESHOLD`（:10）：

```python
from shannon_core.code_index.llm_concurrency import (
    FileChunk,
    _estimate_tokens,
    chunk_items_by_file,
)
```

(b) 删 `test_chunk_default_threshold_is_set`（:96-99）整段（常量已删，断言无意义），替换为：

```python
def test_chunk_token_threshold_is_required():
    """token_threshold 现为必填(无默认), 防误用旧 12K 硬编码(spec §3 模块3)。"""
    import pytest
    b = _blk("app.py", "f", 1)
    with pytest.raises(TypeError):
        chunk_items_by_file([_Item(b)], block_of=lambda it: it.block)  # 缺 token_threshold
```

(c) 确认其他 chunking 测试都已显式传 `token_threshold=...` 或接受必填。检查：`test_chunk_small_file_single_chunk`（:40）、`test_chunk_separates_different_files`（:50）、`test_chunk_keeps_same_block_items_together`（:67-68）这三处**未传** token_threshold。需补上默认测试值。在这三处的 `chunk_items_by_file(...)` 调用加 `token_threshold=12_000`（测试用任意正值）：

```python
# test_chunk_small_file_single_chunk
chunks = chunk_items_by_file(items, block_of=lambda it: it.block, token_threshold=12_000)
# test_chunk_separates_different_files
chunks = chunk_items_by_file(items, block_of=lambda it: it.block, token_threshold=12_000)
# test_chunk_keeps_same_block_items_together
chunks = chunk_items_by_file(
    [_Item(b1), _Item(b1), _Item(b1)], block_of=lambda it: it.block, token_threshold=12_000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_llm_chunking.py -v`
Expected: FAIL（`CHUNK_TOKEN_THRESHOLD` import 失败 / `chunk_items_by_file` 缺参不报错因默认还在）

- [ ] **Step 3: Write minimal implementation**

改 `packages/core/src/shannon_core/code_index/llm_concurrency.py`：

(a) 删常量及其注释（:36-37）：
```python
# 单 chunk prompt token 上限(留 response 余量; ~12K)。源码字符数 // 4 粗估 token。
CHUNK_TOKEN_THRESHOLD = 12_000
```
整段删除。

(b) 改 `chunk_items_by_file` 签名（:57-61 现状 `token_threshold: int = CHUNK_TOKEN_THRESHOLD`）为必填：

```python
def chunk_items_by_file(
    items: list[Any],
    *,
    block_of: Callable[[Any], FuncBlock],
    token_threshold: int,
) -> list[FileChunk]:
    """按 file_path 分组 + 按 token 贪心装箱 -> FileChunk 列表。

    token_threshold 必填(spec 2026-07-10): 由调用方经 get_chunk_token_threshold(model)
    派生后传入(默认/未知 96K, glm-5.2 走 750K), 不再内联硬编码 12K。

    同一文件的 items 先按 block.id 去重保序分组, 再按各 block 源码 token 贪心装箱:
    累加 block token, 超 token_threshold 开新 chunk。单 block 自身超阈值 -> 独立成 chunk
    (chunk 单位是函数, 无法再拆)。保证: 同 block 的 items 不被拆散、不同文件不混。
    """
```
（函数体不变，`token_threshold` 已是参数。）

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_llm_chunking.py -v`
Expected: PASS（所有 chunking 测试 + 新 `test_chunk_token_threshold_is_required`）

- [ ] **Step 5: 跑 sink/source discovery 现有测试看是否因必填炸**

```bash
cd /root/shannon-py
.venv/bin/python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py -v 2>&1 | tail -25
```
Expected: 可能 FAIL（discovery 内部 `else CHUNK_TOKEN_THRESHOLD` 还在，Task 5 修）。**记录失败点**，Task 5 修复。若意外 PASS（discovery 已显式传参），也继续 Task 5 接 model。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/llm_concurrency.py packages/core/tests/code_index/test_llm_chunking.py
git commit -m "refactor(code_index): 删 CHUNK_TOKEN_THRESHOLD 硬编码, token_threshold 改必填"
```

---

### Task 5: discovery 接 model 参数 + threshold 派生

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py:294-325`
- Modify: `packages/core/src/shannon_core/code_index/source_discovery_llm.py:258-289`
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py` + `test_source_discovery_llm.py`

**Interfaces:**
- Consumes: `model_caps.get_chunk_token_threshold(model) -> int`（Task 1）
- Produces: `discover_sinks_llm(..., model: str | None = None)` / `discover_sources_llm(..., model: str | None = None)`

**背景**：两 discovery 函数现状 `token_threshold: int | None = None`，None 时用 `CHUNK_TOKEN_THRESHOLD`（已删，Task 4 后会炸）。改为：增 `model` 参数，`token_threshold` 未显式传时从 `get_chunk_token_threshold(model)` 派生。

- [ ] **Step 1: Write the failing test**

在 `packages/core/tests/code_index/test_sink_discovery_llm.py` 末尾追加（import 区加 `get_chunk_token_threshold` 与 `monkeypatch` 支持；若文件已用 pytest fixture 直接加）：

```python
def test_discover_sinks_threshold_derives_from_model(monkeypatch):
    """model='glm-5.2' -> token_threshold 派生为 750K(spec §3 模块3)。

    验证: 传 model 时, chunk 切分用 model context 派生的 threshold(而非硬编码)。
    用一个文件级聚合但函数源码很大的 suspicious, 确认 chunk 数随 model 变化。
    """
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parameter_models import SuspiciousCall
    from shannon_core.agents.model_caps import get_chunk_token_threshold

    big_src = "x = 1\n" * 100_000  # ~400K chars -> ~100K tokens(ascii)
    block = FuncBlock(
        id="app.py:f:1", file_path="app.py", function_name="f",
        start_line=1, end_line=5, source_code=big_src,
        parameters=[], language="python",
    )
    sc = SuspiciousCall(callee="exec", line=1, receiver=None, block=block)
    calls = []

    async def fake_client(prompt):
        calls.append(prompt)
        return "[]"  # 空 verdict

    # model=glm-5.2 -> threshold 750K, big_src ~100K tokens < 750K -> 1 chunk
    import asyncio
    sinks, gaps = asyncio.run(discover_sinks_llm(
        [sc], fake_client, model="glm-5.2"))
    assert len(calls) == 1  # 整个大函数进 1 chunk(750K 容得下 100K)


def test_discover_sinks_threshold_default_model(monkeypatch):
    """model=None -> 走默认 128K context -> threshold 96K。"""
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parameter_models import SuspiciousCall
    import asyncio

    big_src = "x = 1\n" * 100_000  # ~100K tokens
    block = FuncBlock(
        id="app.py:f:1", file_path="app.py", function_name="f",
        start_line=1, end_line=5, source_code=big_src,
        parameters=[], language="python",
    )
    sc = SuspiciousCall(callee="exec", line=1, receiver=None, block=block)
    calls = []

    async def fake_client(prompt):
        calls.append(prompt)
        return "[]"

    # model=None -> 96K threshold; 100K tokens > 96K -> 但单 block 超 threshold 独立成 1 chunk
    sinks, gaps = asyncio.run(discover_sinks_llm([sc], fake_client, model=None))
    assert len(calls) == 1  # 单 block 独立成 chunk(无法再拆)
```

对 `packages/core/tests/code_index/test_source_discovery_llm.py` 加等价测试（用 `SourceCandidate` 替代 `SuspiciousCall`，调 `discover_sources_llm(..., model="glm-5.2")`）。参考其现有 fixture 的构造方式：

```bash
grep -n "SourceCandidate\|def test_\|block=" packages/core/tests/code_index/test_source_discovery_llm.py | head
```
按现有 fixture 模式构造 `SourceCandidate(block=block, ...)`，断言同上。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py -k "threshold_derives_from_model or threshold_default_model" -v`
Expected: FAIL（`discover_sinks_llm` 无 `model` 参数 -> TypeError，或仍用旧 `CHUNK_TOKEN_THRESHOLD`）

- [ ] **Step 3: Write minimal implementation**

改 `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`：

(a) import 区（:25-32 附近）加：
```python
from shannon_core.agents.model_caps import get_chunk_token_threshold
```
删 `from .llm_concurrency import ... CHUNK_TOKEN_THRESHOLD ...` 中的 `CHUNK_TOKEN_THRESHOLD`（若 import 行有它）。

(b) 改签名（:294-302）加 `model` 参数：
```python
async def discover_sinks_llm(
    suspicious: list[SuspiciousCall],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: ProgressCb = None,
    token_threshold: int | None = None,
    model: str | None = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
```

(c) 改 chunk 调用（:320-325）派生 threshold：
```python
    effective_threshold = (token_threshold if token_threshold is not None
                           else get_chunk_token_threshold(model))
    chunks: list[FileChunk] = chunk_items_by_file(
        suspicious,
        block_of=lambda sc: sc.block,
        token_threshold=effective_threshold,
    )
```

(d) 更新 docstring：`token_threshold(默认 CHUNK_TOKEN_THRESHOLD)` 改为 `token_threshold(默认由 get_chunk_token_threshold(model) 派生)`。

对 `packages/core/src/shannon_core/code_index/source_discovery_llm.py` 做完全对应的四处改动（import / 签名加 `model` / chunk 调用派生 / docstring）。其现状结构（:258-289）与 sink 对称。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py -v`
Expected: PASS（含新增 model 派生测试 + 原有测试）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/src/shannon_core/code_index/source_discovery_llm.py packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py
git commit -m "feat(code_index): discovery 接 model 参数, threshold 按 context 派生"
```

---

### Task 6: build_code_index_with_gitnexus + activity 接线传 model

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:110-117, 198-199, 313-314`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:751-799`
- Test: `packages/core/tests/code_index/test_build_code_index.py`

**Interfaces:**
- Consumes: `resolve_tier_model(config, tier) -> str`（Task 2）；`discover_sinks/sources_llm(..., model=)`（Task 5）
- Produces: `build_code_index_with_gitnexus(..., model: str | None = None)`

**背景**：activity 层 `_make_gitnexus_llm_client`（activities.py:751）用 `model_tier="medium"`（:761），需 resolve 出 medium tier 实际 model 名，经 `build_code_index_with_gitnexus` 透传到两个 discovery。**不裸读 `SHANNON_MODEL`**（那是 large tier，错配爆 context）。

- [ ] **Step 1: Write the failing test**

在 `packages/core/tests/code_index/test_build_code_index.py` 加测试，验证 `model` 参数透传到 discovery（用 monkeypatch 拦截 `discover_sinks_llm`）：

```python
def test_build_code_index_passes_model_to_discovery(monkeypatch):
    """build_code_index_with_gitnexus 的 model 参数透传到 discover_sinks/sources_llm(spec §3)。"""
    import asyncio
    from shannon_core.code_index import __init__ as ci
    from shannon_core.code_index.parameter_models import SuspiciousCall

    captured = {}

    async def fake_discover_sinks(suspicious, llm_client, *, model=None, **kw):
        captured["sinks_model"] = model
        return [], []

    async def fake_discover_sources(candidates, llm_client, *, model=None, **kw):
        captured["sources_model"] = model
        return [], []

    monkeypatch.setattr(ci, "discover_sinks_llm", fake_discover_sinks)
    monkeypatch.setattr(ci, "discover_sources_llm", fake_discover_sources)
    # 其余依赖(file_discovery/sink_detector/gitnexus)按现有 fixture 桩掉
    # 参考 test_build_code_index.py 已有的最小 build fixture
    ...
    # asyncio.run(build_code_index_with_gitnexus(repo, mcp_client=stub, llm_client=stub, model="glm-5.2"))
    assert captured["sinks_model"] == "glm-5.2"
    assert captured["sources_model"] == "glm-5.2"
```

注：`test_build_code_index.py` 现有 `threads_progress` 是预存失败（memory `feat-fork-py-test-gotchas`），**勿跑全套**，只跑新增的 `test_build_code_index_passes_model_to_discovery`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_build_code_index.py::test_build_code_index_passes_model_to_discovery -v`
Expected: FAIL（`build_code_index_with_gitnexus` 无 `model` 参数 / 不透传）

- [ ] **Step 3: Write minimal implementation**

(a) 改 `packages/core/src/shannon_core/code_index/__init__.py:110-117` 签名加 `model`：

```python
async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
    auto_index: bool = False,
    progress_cb=None,
    model: str | None = None,
) -> tuple[CodeIndex, list[RuleGap], list[SourceGap]]:
```

(b) 改 :198-199 调用传 model：
```python
    soft_sinks, rule_gaps = await discover_sinks_llm(
        suspicious, llm_client, progress_cb=progress_cb, model=model)
```

(c) 改 :313-314 调用传 model：
```python
    soft_sources, source_gaps = await discover_sources_llm(
        source_candidates, llm_client, progress_cb=progress_cb, model=model)
```

(d) 改 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`。activity 层已有现成用法（:718-720 `config = build_provider_config(api_key=input.api_key or None)`）。在调用 `build_code_index_with_gitnexus`（:793）前，用同一入口 resolve medium model：

```python
            # resolve medium-tier model 名(spec 2026-07-10): 传给 discovery 做 chunk
            # threshold 派生。不裸读 SHANNON_MODEL(=large tier, 错配爆 context)。
            from shannon_core.agents.providers import build_provider_config, resolve_tier_model
            try:
                _pcfg = build_provider_config(api_key=input.api_key or None)
                _medium_model = resolve_tier_model(_pcfg, "medium")
            except Exception:
                _medium_model = None  # resolve 失败 -> discovery 走默认 context, 不阻断
```

注：`input` 是 activity 的 `ActivityInput`（`run_code_index` 的入参），:718 已用 `input.api_key`，此处复用。然后传给 build：

```python
                    index, rule_gaps, source_gaps = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=mcp,
                        llm_client=_llm_taint_client,
                        auto_index=False,
                        progress_cb=_make_gitnexus_progress_cb(get_audit_session()),
                        model=_medium_model,
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_build_code_index.py::test_build_code_index_passes_model_to_discovery -v`
Expected: PASS

- [ ] **Step 5: 跑白盒 run_code_index 相关测试 + 防回退**

```bash
cd /root/shannon-py
.venv/bin/python -m pytest packages/whitebox/tests/test_run_code_index.py -v 2>&1 | tail -20
.venv/bin/python -m pytest packages/core/tests/code_index/test_llm_chunking.py packages/core/tests/agents/test_model_caps.py -v 2>&1 | tail -15
```
Expected: PASS（白盒 run_code_index + chunking + model_caps 全绿）

- [ ] **Step 6: 守双轨铁律防回退测试**

```bash
cd /root/shannon-py
.venv/bin/python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v 2>&1 | tail -10
```
Expected: PASS（铁律未破，本改动只动 GitNexus 轨 chunking）

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/__init__.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/code_index/test_build_code_index.py
git commit -m "feat(code_index): activity resolve medium model 名透传至 discovery chunking"
```

---

### Task 7: profile 示例 + 文档收尾

**Files:**
- Modify: `.env.profiles.example/glm-anthropic.env.example`
- Modify: `.env.profiles.example/glm-openai.env.example`（若有对应行）

**背景**：补 `SHANNON_MODEL_CONTEXT_OVERRIDE` + `SHANNON_CHUNK_TOKEN_THRESHOLD` 的示例与注释，让用户知道可 per-profile 配 context。

- [ ] **Step 1: 查现状**

```bash
grep -n "SHANNON_PRICING_OVERRIDE\|SHANNON_MEDIUM_MODEL\|SHANNON_MODEL\b" .env.profiles.example/glm-anthropic.env.example
```

- [ ] **Step 2: 在 SHANNON_PRICING_OVERRIDE 行附近追加（glm-anthropic.env.example :15 后）**

```bash
# per-model context window 覆盖(spec 2026-07-10): JSON {"models": {model: ctx_tokens}}。
# chunk threshold = context × 0.75。glm-5.2 内置 1M(派生 750K), 此文件可补充/纠正其他模型。
# 切 profile 即切 context(env_loader override=True 天然 per-profile)。
# SHANNON_MODEL_CONTEXT_OVERRIDE=.env.profiles/glm.context.json

# hard override(调试/止血用): 直接固定 chunk threshold, 跳过 context 派生。优先级最高。
# 不设时按 model context 自动派生(推荐)。
# SHANNON_CHUNK_TOKEN_THRESHOLD=
```

对 `glm-openai.env.example` 做同样追加（若它也有 PRICING_OVERRIDE 行；查 `grep -n PRICING .env.profiles.example/glm-openai.env.example`）。

- [ ] **Step 3: Commit**

```bash
cd /root/shannon-py
git add .env.profiles.example/glm-anthropic.env.example .env.profiles.example/glm-openai.env.example
git commit -m "docs(env): 补 SHANNON_MODEL_CONTEXT_OVERRIDE / SHANNON_CHUNK_TOKEN_THRESHOLD 示例"
```

- [ ] **Step 4: 全量回归（改动相关文件）**

```bash
cd /root/shannon-py
.venv/bin/python -m pytest \
  packages/core/tests/agents/test_model_caps.py \
  packages/core/tests/agents/test_providers_tier_model.py \
  packages/core/tests/code_index/test_llm_chunking.py \
  packages/core/tests/code_index/test_sink_discovery_llm.py \
  packages/core/tests/code_index/test_source_discovery_llm.py \
  packages/core/tests/code_index/test_build_code_index.py::test_build_code_index_passes_model_to_discovery \
  packages/whitebox/tests/test_run_code_index.py \
  packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py \
  -v 2>&1 | tail -30
```
Expected: 全 PASS（预存 `threads_progress` 失败不在选集内）

---

## 真机冒烟（plan 外，人工执行）

plan 实现完后，真机重跑 kol_mapping_service 核实：
- chunk 数 259 -> ?（预期个位数~十几个）
- `run_code_index` activity 是否进 20min（不再超时重试）
- sink 召回数回归（不因 chunk 变大漏召回）
- `SHANNON_GITNEXUS_LLM_ENABLED=0` 关闭补召回仍正常降级（回归）

记录到 memory `[sink-source-file-level-aggregation-status]` follow-up。
