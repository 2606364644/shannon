"""profile_validator: 按 PROVIDER_SETTINGS 校验当前 profile 必填变量。"""
import pytest

from supernova_core.config.profile_validator import validate_active_profile
from supernova_core.models.errors import ErrorCode, PentestError

# anthropic_api 完整 profile 的基准变量
ANTHROPIC_OK = {
    "SUPERNOVA_AI_PROVIDER": "anthropic_api",
    "ANTHROPIC_BASE_URL": "https://x/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "tok",
    "SUPERNOVA_SMALL_MODEL": "GLM-4.5-Air",
    "SUPERNOVA_MEDIUM_MODEL": "GLM-5.2[1m]",
    "SUPERNOVA_LARGE_MODEL": "GLM-5.2[1m]",
}
OPENAI_OK = {
    "SUPERNOVA_AI_PROVIDER": "openai_compatible",
    "SUPERNOVA_OPENAI_BASE_URL": "https://x/v4",
    "SUPERNOVA_OPENAI_API_KEY": "key",
    "SUPERNOVA_OPENAI_SMALL_MODEL": "glm-4.5-air",
    "SUPERNOVA_OPENAI_MEDIUM_MODEL": "glm-5.2",
    "SUPERNOVA_OPENAI_LARGE_MODEL": "glm-5.2",
}

# 校验可能触及的全部环境变量命名空间; 每个 test 开头清空, 统一隔离。
_PROFILE_ENV_NAMESPACE = (
    "SUPERNOVA_AI_PROVIDER",
    "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "SUPERNOVA_API_KEY", "SUPERNOVA_BASE_URL", "SUPERNOVA_AUTH_TOKEN",
    "SUPERNOVA_PROJECT_ID", "SUPERNOVA_REGION", "SUPERNOVA_MODEL",
    "SUPERNOVA_SMALL_MODEL", "SUPERNOVA_MEDIUM_MODEL", "SUPERNOVA_LARGE_MODEL",
    "SUPERNOVA_OPENAI_BASE_URL", "SUPERNOVA_OPENAI_API_KEY",
    "SUPERNOVA_OPENAI_SMALL_MODEL", "SUPERNOVA_OPENAI_MEDIUM_MODEL",
    "SUPERNOVA_OPENAI_LARGE_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_profile_env(monkeypatch):
    """每个 test 开头清空 profile 相关环境变量, 杜绝跨用例污染。"""
    for var in _PROFILE_ENV_NAMESPACE:
        monkeypatch.delenv(var, raising=False)


def test_anthropic_full_passes(monkeypatch):
    for k, v in ANTHROPIC_OK.items():
        monkeypatch.setenv(k, v)
    # 不抛即通过
    validate_active_profile()


def test_anthropic_api_key_accepted_instead_of_token(monkeypatch):
    """credential 二选一: 有 ANTHROPIC_API_KEY 也行。"""
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_AUTH_TOKEN"]
    env["ANTHROPIC_API_KEY"] = "sk"
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()


def test_anthropic_missing_credential_raises(monkeypatch):
    # Set environment without credentials
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_AUTH_TOKEN"]  # 既无 token 也无 api_key
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "credential" in exc.value.message or "api_key" in exc.value.message


def test_anthropic_missing_base_url_raises(monkeypatch):
    # Set environment without base_url
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_BASE_URL"]
    env["ANTHROPIC_AUTH_TOKEN"] = "tok"  # Add back the token
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "ANTHROPIC_BASE_URL" in exc.value.message


def test_anthropic_missing_medium_model_raises(monkeypatch):
    env = {**ANTHROPIC_OK}
    del env["SUPERNOVA_MEDIUM_MODEL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SUPERNOVA_MEDIUM_MODEL" in exc.value.message


def test_anthropic_empty_optional_model_yields_none_in_config(monkeypatch):
    """Important #2: set 但空的 SUPERNOVA_MODEL(optional)在校验时不阻塞,
    build_provider_config 产出的 config.model 为 None(而非空串)。"""
    from supernova_core.agents.providers import build_provider_config

    env = {**ANTHROPIC_OK}
    env["SUPERNOVA_MODEL"] = ""  # 空串
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()  # model 非 required, 不抛

    cfg = build_provider_config()
    assert cfg.model is None  # 空串被视为未设置, 不是 ""


def test_openai_full_passes(monkeypatch):
    for k, v in OPENAI_OK.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()


def test_openai_missing_api_key_raises(monkeypatch):
    env = {**OPENAI_OK}
    del env["SUPERNOVA_OPENAI_API_KEY"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SUPERNOVA_OPENAI_API_KEY" in exc.value.message


def test_openai_missing_base_url_raises(monkeypatch):
    env = {**OPENAI_OK}
    del env["SUPERNOVA_OPENAI_BASE_URL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SUPERNOVA_OPENAI_BASE_URL" in exc.value.message


def test_openai_empty_required_api_key_treated_as_missing(monkeypatch):
    """Important #2: set 但空的必填变量(空串)按缺失处理, 校验抛错。"""
    env = {**OPENAI_OK}
    env["SUPERNOVA_OPENAI_API_KEY"] = ""  # 空串
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SUPERNOVA_OPENAI_API_KEY" in exc.value.message


def test_bedrock_skips_strict_validation(monkeypatch):
    """bedrock required 为空, 不做强校验, 不抛。"""
    monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "bedrock")
    validate_active_profile()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "bogus")
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
