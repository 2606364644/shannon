"""profile_validator: 按 PROVIDER_SETTINGS 校验当前 profile 必填变量。"""
import pytest

from shannon_core.config.profile_validator import validate_active_profile
from shannon_core.models.errors import ErrorCode, PentestError

# anthropic_api 完整 profile 的基准变量
ANTHROPIC_OK = {
    "SHANNON_AI_PROVIDER": "anthropic_api",
    "ANTHROPIC_BASE_URL": "https://x/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "tok",
    "SHANNON_SMALL_MODEL": "GLM-4.5-Air",
    "SHANNON_MEDIUM_MODEL": "GLM-5.2[1m]",
    "SHANNON_LARGE_MODEL": "GLM-5.2[1m]",
}
OPENAI_OK = {
    "SHANNON_AI_PROVIDER": "openai_compatible",
    "SHANNON_OPENAI_BASE_URL": "https://x/v4",
    "SHANNON_OPENAI_API_KEY": "key",
    "SHANNON_OPENAI_SMALL_MODEL": "glm-4.5-air",
    "SHANNON_OPENAI_MEDIUM_MODEL": "glm-5.2",
    "SHANNON_OPENAI_LARGE_MODEL": "glm-5.2",
}


def test_anthropic_full_passes(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    for k, v in ANTHROPIC_OK.items():
        monkeypatch.setenv(k, v)
    # 不抛即通过
    validate_active_profile()


def test_anthropic_api_key_accepted_instead_of_token(monkeypatch):
    """credential 二选一: 有 ANTHROPIC_API_KEY 也行。"""
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**ANTHROPIC_OK}
    del env["ANTHROPIC_AUTH_TOKEN"]
    env["ANTHROPIC_API_KEY"] = "sk"
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()


def test_anthropic_missing_credential_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("SHANNON_SMALL_MODEL", raising=False)
    monkeypatch.delenv("SHANNON_MEDIUM_MODEL", raising=False)
    monkeypatch.delenv("SHANNON_LARGE_MODEL", raising=False)

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
    # Clean up environment first
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("SHANNON_SMALL_MODEL", raising=False)
    monkeypatch.delenv("SHANNON_MEDIUM_MODEL", raising=False)
    monkeypatch.delenv("SHANNON_LARGE_MODEL", raising=False)

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
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**ANTHROPIC_OK}
    del env["SHANNON_MEDIUM_MODEL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SHANNON_MEDIUM_MODEL" in exc.value.message


def test_openai_full_passes(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    for k, v in OPENAI_OK.items():
        monkeypatch.setenv(k, v)
    validate_active_profile()


def test_openai_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**OPENAI_OK}
    del env["SHANNON_OPENAI_API_KEY"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SHANNON_OPENAI_API_KEY" in exc.value.message


def test_openai_missing_base_url_raises(monkeypatch):
    monkeypatch.delenv("SHANNON_AI_PROVIDER", raising=False)
    env = {**OPENAI_OK}
    del env["SHANNON_OPENAI_BASE_URL"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert "SHANNON_OPENAI_BASE_URL" in exc.value.message


def test_bedrock_skips_strict_validation(monkeypatch):
    """bedrock required 为空, 不做强校验, 不抛。"""
    monkeypatch.setenv("SHANNON_AI_PROVIDER", "bedrock")
    validate_active_profile()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("SHANNON_AI_PROVIDER", "bogus")
    with pytest.raises(PentestError) as exc:
        validate_active_profile()
    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED