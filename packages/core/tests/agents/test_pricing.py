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


def test_compute_cost_clamps_negative_billable():
    """cached > input 时 billable_input clamp 到 0（防御性，spec §4.1 max 守卫）。"""
    p = GLM_PRICING_CNY["glm-4.6"]
    usage = TokenUsage(input_tokens=100, output_tokens=0, cache_read_input_tokens=500)
    cost = compute_cost_usd("glm-4.6", usage)
    # billable_input = max(100 - 500, 0) = 0；cache_hit = 500
    expected = (0 * p["input"] + 500 * p["cache_read"]) / 1_000_000 / USD_CNY_RATE
    assert cost == pytest.approx(expected)
