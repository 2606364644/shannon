"""provider_settings: provider → 环境变量名映射表。"""
from supernova_core.config.provider_settings import (
    PROVIDER_SETTINGS,
    ProviderFields,
    get_provider_fields,
)


def test_anthropic_reads_anthropic_prefixed_vars():
    f = PROVIDER_SETTINGS["anthropic_api"]
    assert f.base_url == "ANTHROPIC_BASE_URL"
    assert f.api_key == "ANTHROPIC_API_KEY"
    assert f.auth_token == "ANTHROPIC_AUTH_TOKEN"
    assert f.medium_model == "SUPERNOVA_MEDIUM_MODEL"


def test_openai_reads_openai_prefixed_vars():
    f = PROVIDER_SETTINGS["openai_compatible"]
    assert f.base_url == "SUPERNOVA_OPENAI_BASE_URL"
    assert f.api_key == "SUPERNOVA_OPENAI_API_KEY"
    assert f.medium_model == "SUPERNOVA_OPENAI_MEDIUM_MODEL"


def test_anthropic_requires_credential_either_of():
    """anthropic 的 credential 是 api_key/auth_token 二选一。"""
    assert "credential" in PROVIDER_SETTINGS["anthropic_api"].required
    assert "base_url" in PROVIDER_SETTINGS["anthropic_api"].required


def test_openai_requires_api_key():
    req = PROVIDER_SETTINGS["openai_compatible"].required
    assert "api_key" in req
    assert "credential" not in req  # openai 用 api_key, 不是二选一


def test_unused_providers_have_no_required():
    """bedrock/vertex/litellm 用户未使用, 不强校验。"""
    for p in ("bedrock", "vertex", "litellm_router"):
        assert PROVIDER_SETTINGS[p].required == ()


def test_get_provider_fields_unknown_returns_none():
    assert get_provider_fields("nope") is None
    assert get_provider_fields("anthropic_api") is not None


def test_provider_fields_is_frozen():
    import dataclasses
    assert dataclasses.is_dataclass(ProviderFields)
    f = ProviderFields(base_url="X")
    try:
        f.base_url = "Y"  # frozen
        assert False, "应不可变"
    except dataclasses.FrozenInstanceError:
        pass
