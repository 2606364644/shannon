"""resolve_tier_model: provider tier 解析提取为模块级公共函数(spec 2026-07-10)。

优先级: tier-specific config(medium_model 等) > global config.model > DEFAULT_MODELS。
供 activity 层 resolve medium-tier model 名(传给 chunk threshold 派生, 不裸读 env)。
"""
from supernova_core.agents.providers import resolve_tier_model
from supernova_core.agents.runner import DEFAULT_MODELS, ProviderConfig


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
