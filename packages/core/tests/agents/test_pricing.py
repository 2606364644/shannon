import pytest

from supernova_core.agents.runner import TokenUsage
from supernova_core.agents.pricing import (
    GLM_PRICING_CNY,
    CostAmount,
    compute_cost,
    compute_cost_usd,
    currency_symbol,
    is_model_priced,
    normalize_model,
)


# ---- compute_cost 返回 CostAmount（本币直达，不除汇率）----


def test_compute_cost_returns_costamount_with_currency():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)
    r = compute_cost("glm-5.2", usage)
    assert isinstance(r, CostAmount)
    assert r.currency == "CNY"  # 内置表默认 CNY
    assert r.cost == 8.0  # 1M input × 8 / 1M


def test_compute_cost_known_model_no_cache():
    """已知模型、无 cache：input/output 价直接算本币（不除汇率）。"""
    p = GLM_PRICING_CNY["glm-4.5-air"]
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
    expected = (1_000_000 * p["input"] + 500_000 * p["output"]) / 1_000_000
    r = compute_cost("glm-4.5-air", usage)
    assert r.cost == pytest.approx(expected)
    assert r.currency == "CNY"


def test_compute_cost_four_tiers_cache_creation():
    """四档计费：input + cache_creation + cache_read + output（claude 场景）。

    input_tokens 已归一为不含 cache 命中（归一由 mapper 负责）；cache_read/cache_creation
    独立计费、不与 input 互相扣减。
    """
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=500_000,
        cache_read_input_tokens=500_000,
    )
    r = compute_cost("glm-5.2", usage)
    # 内置 GLM 表 cache_creation=0：input 1M×8 + cc 0.5M×0 + cr 0.5M×2 + out 1M×28, /1M
    expected = 8.0 + 0.0 + 1.0 + 28.0
    assert r.cost == pytest.approx(expected)
    assert r.currency == "CNY"


def test_compute_cost_input_and_cache_independent():
    """compute_cost 不做 input-cached 减法（归一移至 mapper）。

    input_tokens 即 billable，cache_read 独立折价——模拟 mapper 归一后传入。
    """
    p = GLM_PRICING_CNY["glm-4.5-air"]
    usage = TokenUsage(input_tokens=600_000, output_tokens=0, cache_read_input_tokens=400_000)
    r = compute_cost("glm-4.5-air", usage)
    expected = (600_000 * p["input"] + 400_000 * p["cache_read"]) / 1_000_000
    assert r.cost == pytest.approx(expected)


def test_compute_cost_unknown_model_zero_with_table_currency():
    """未知模型 → cost 0.0，但仍带表币种（便于上层显示）。"""
    r = compute_cost("unknown-model", TokenUsage(input_tokens=100))
    assert r.cost == 0.0
    assert r.currency == "CNY"


def test_unknown_model_zero_cost():
    assert compute_cost_usd("some-unknown-model", TokenUsage(input_tokens=1000)) == 0.0
    assert is_model_priced("some-unknown-model") is False
    assert is_model_priced("glm-4.5-air") is True


# ---- normalize_model ----


def test_normalize_model_variants():
    assert normalize_model("GLM-5.2[1m]") == "glm-5.2"
    assert normalize_model("glm-4.5-air") == "glm-4.5-air"
    assert normalize_model("GLM-4.6-20260101") == "glm-4.6"  # 测日期后缀剥离（不查价表）
    assert normalize_model("") == ""


def test_normalize_model_claude_and_deepseek():
    assert normalize_model("claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert normalize_model("Claude-Sonnet-4-5-20251022") == "claude-sonnet-4-5"
    assert normalize_model("deepseek-chat") == "deepseek-chat"
    assert normalize_model("GLM-5.2[1m]") == "glm-5.2"


def test_compute_cost_normalizes_model():
    """GLM-5.2[1m] 归一化到 glm-5.2 → 命中价目表，cost 与 glm-5.2 一致。"""
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    assert compute_cost("GLM-5.2[1m]", usage) == compute_cost("glm-5.2", usage)


# ---- currency_symbol ----


def test_currency_symbol():
    assert currency_symbol("CNY") == "¥"
    assert currency_symbol("USD") == "$"
    assert currency_symbol("EUR") == "$"  # 未知回落 $


# ---- compute_cost_usd wrapper（过渡兼容，返回本币 .cost）----


def test_compute_cost_usd_wrapper_returns_cost():
    """过渡 wrapper：返回 compute_cost().cost（本币，不再 /汇率）。"""
    assert compute_cost_usd("glm-5.2", TokenUsage(input_tokens=1_000_000)) == 8.0


# ---- 汇率不再影响单 session cost ----


def test_compute_cost_ignores_usd_cny_rate(monkeypatch):
    """单 session cost 本币直达，SUPERNOVA_USD_CNY_RATE 不再参与计算（spec §4.4）。"""
    monkeypatch.setenv("SUPERNOVA_USD_CNY_RATE", "10.0")
    p = GLM_PRICING_CNY["glm-4.5-air"]
    usage = TokenUsage(input_tokens=1_000_000)
    expected = (1_000_000 * p["input"]) / 1_000_000  # = 0.8，不除汇率
    assert compute_cost_usd("glm-4.5-air", usage) == pytest.approx(expected)
    assert compute_cost("glm-4.5-air", usage).currency == "CNY"


# ---- override ----


def test_override_new_schema_with_currency(tmp_path, monkeypatch):
    """新 schema: {"currency","models"} → 按其币种直达计费。"""
    f = tmp_path / "p.json"
    f.write_text(
        '{"currency":"USD","models":{"glm-5.2":{"input":10,"output":30,"cache_read":2,"cache_creation":4}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", str(f))
    r = compute_cost("glm-5.2", TokenUsage(input_tokens=1_000_000, output_tokens=0))
    assert r.currency == "USD"
    assert r.cost == 10.0  # USD 直达，不除汇率


def test_override_old_flat_schema_defaults_cny(tmp_path, monkeypatch):
    """旧 flat schema: {model:{...}}（无 currency/models 包裹）→ 币种回落 CNY。"""
    f = tmp_path / "p.json"
    f.write_text(
        '{"glm-4.5-air":{"input":100.0,"output":100.0,"cache_read":25.0}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", str(f))
    r = compute_cost("glm-4.5-air", TokenUsage(input_tokens=1_000_000))
    assert r.currency == "CNY"
    assert r.cost == pytest.approx(100.0)  # override 同 key 覆盖内置


def test_pricing_override_invalid_ignored(tmp_path, monkeypatch):
    """override JSON 解析失败 → 忽略覆盖、用内置、不崩（spec §4.5）。"""
    f = tmp_path / "bad.json"
    f.write_text("{not valid json")
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", str(f))
    p = GLM_PRICING_CNY["glm-4.5-air"]
    usage = TokenUsage(input_tokens=1_000_000)
    expected = (1_000_000 * p["input"]) / 1_000_000
    assert compute_cost_usd("glm-4.5-air", usage) == pytest.approx(expected)


def test_compute_cost_unknown_model_warns_not_silent(caplog):
    """未知模型 → cost 0 + warning（CLAUDE.md §4 契约），非静默。

    回归：deepseek-v4-flash-coder 全程 ¥0.00 无任何提示——docstring 说会打
    warning，实际静默返回，用户无从得知 cost 为何是 0。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.pricing"):
        r = compute_cost("deepseek-v4-flash-coder", TokenUsage(input_tokens=123))
    assert r == CostAmount(0.0, "CNY")
    assert any("deepseek-v4-flash-coder" in rec.message for rec in caplog.records)
