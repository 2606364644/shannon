"""P3c 阶段 2：WsConfigStore 读写 + 凭据加密 + resolve 覆盖。"""
import pytest
from pathlib import Path

from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.ws_config_store import (
    WsConfig, WsProviderFields, WsConfigStore, ProviderConfigIncomplete,
    validate_ws_config,
)


@pytest.fixture
def store(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    return WsConfigStore(tmp_path, vault)


def test_read_missing_ws_returns_default_template(store, tmp_path):
    """无 config.yaml → OpenAI 工作区默认模板（API key 仍为空）。"""
    (tmp_path / "ws-a").mkdir()
    cfg = store.read("ws-a")
    assert cfg.provider.ai_provider == "openai_compatible"
    assert cfg.provider.base_url == "https://llm-proxy.futuoa.com/v1"
    assert cfg.provider.large_model == "glm-5.2-coder"
    assert cfg.provider.medium_model == "glm-5.2-coder"
    assert cfg.provider.small_model == "glm-5.2-coder"
    assert cfg.provider.api_key is None


def test_write_then_read_roundtrip(store, tmp_path):
    """写 → 读往返；凭据密文落盘但读回明文。"""
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-secret", base_url="http://x", model="m",
    )))
    # 落盘密文（cat 不见明文）
    raw = (tmp_path / "ws-a" / "config.yaml").read_text()
    assert "sk-secret" not in raw
    # 读回明文
    cfg = store.read("ws-a")
    assert cfg.provider.api_key == "sk-secret"
    assert cfg.provider.ai_provider == "openai_compatible"


def test_resolve_provider_config_global_default_when_unset(store, tmp_path):
    """未填 API key → 即使全局有配置也不能回落；抛 ProviderConfigIncomplete 带 missing 列表。"""
    (tmp_path / "ws-a").mkdir()
    with pytest.raises(ProviderConfigIncomplete) as ei:
        store.resolve_provider_config("ws-a")
    assert "SUPERNOVA_OPENAI_API_KEY" in ei.value.missing


def test_resolve_provider_config_ws_overrides(store, tmp_path):
    """完整 ws 配置直接构成 ProviderConfig（ai_provider → type 映射）。"""
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-ws",
        base_url="https://llm-proxy.futuoa.com/v1",
        small_model="glm-5.2-coder", medium_model="glm-5.2-coder",
        large_model="glm-5.2-coder", max_turns=999,
    )))
    pc = store.resolve_provider_config("ws-a")
    assert pc["type"] == "openai_compatible"   # ai_provider → type
    assert pc["api_key"] == "sk-ws"
    assert pc["base_url"] == "https://llm-proxy.futuoa.com/v1"
    assert pc["small_model"] == "glm-5.2-coder"
    assert pc["medium_model"] == "glm-5.2-coder"
    assert pc["large_model"] == "glm-5.2-coder"
    assert pc["max_turns"] == 999


def test_resolve_provider_config_requires_all_openai_fields(store, tmp_path):
    """工作区缺 tier 模型时失败，不用全局或 Provider 默认模型补齐。"""
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible",
        base_url="https://llm-proxy.futuoa.com/v1",
        api_key="sk-ws",
        medium_model="glm-5.2-coder",
    )))
    with pytest.raises(ProviderConfigIncomplete) as ei:
        store.resolve_provider_config("ws-a")
    assert "SUPERNOVA_OPENAI_SMALL_MODEL" in ei.value.missing


def test_resolve_provider_config_returns_only_workspace_values(store, tmp_path, monkeypatch):
    """完整工作区配置不包含全局 model 或其他全局 Provider 值。"""
    (tmp_path / "ws-a").mkdir()
    monkeypatch.setenv("SUPERNOVA_MODEL", "global-model")
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible",
        base_url="https://llm-proxy.futuoa.com/v1",
        api_key="sk-ws",
        small_model="glm-5.2-coder",
        medium_model="glm-5.2-coder",
        large_model="glm-5.2-coder",
    )))
    resolved = store.resolve_provider_config("ws-a")
    assert resolved["type"] == "openai_compatible"
    assert resolved["api_key"] == "sk-ws"
    assert resolved["base_url"] == "https://llm-proxy.futuoa.com/v1"
    assert resolved["small_model"] == "glm-5.2-coder"
    assert resolved["medium_model"] == "glm-5.2-coder"
    assert resolved["large_model"] == "glm-5.2-coder"
    assert resolved["model"] is None
    assert "global-model" not in resolved.values()


def test_path_traversal_rejected(store):
    with pytest.raises(ValueError):
        store.read("../etc/passwd")
    with pytest.raises(ValueError):
        store.write("..", WsConfig())


def test_validate_ws_config_unknown_provider():
    with pytest.raises(ValueError):
        validate_ws_config(WsConfig(provider=WsProviderFields(ai_provider="bogus")))


def test_validate_ws_config_none_provider_ok():
    """未覆盖 ai_provider → 不校验（回落全局）。"""
    validate_ws_config(WsConfig(provider=WsProviderFields()))  # 不抛


def test_validate_ws_config_known_provider_ok():
    validate_ws_config(WsConfig(provider=WsProviderFields(ai_provider="openai_compatible")))


# ---- P3c 阶段 4：git 段 + 凭据加解密 ----

def test_write_then_read_git_credentials(store, tmp_path):
    """git.gitlab_token 密文落盘，读回明文。"""
    (tmp_path / "ws-a").mkdir()
    from supernova_web.components.ws_config_store import WsConfig, WsGitFields
    store.write("ws-a", WsConfig(
        provider=WsProviderFields(),
        git=WsGitFields(gitlab_user="bot-a", gitlab_token="glpat-a"),
    ))
    raw = (tmp_path / "ws-a" / "config.yaml").read_text()
    assert "glpat-a" not in raw                # 密文
    assert "bot-a" in raw                      # user 明文
    cfg = store.read("ws-a")
    assert cfg.git.gitlab_user == "bot-a"
    assert cfg.git.gitlab_token == "glpat-a"   # 读回明文


def test_read_missing_git_returns_empty(store, tmp_path):
    """无 config.yaml → git 段全 None。"""
    (tmp_path / "ws-a").mkdir()
    cfg = store.read("ws-a")
    assert cfg.git.gitlab_user is None
    assert cfg.git.gitlab_token is None


def test_credential_fields_includes_gitlab_token():
    """gitlab_token 在凭据白名单（WsConfigStore 据此加密）。"""
    from supernova_web.components.credential_vault import CredentialVault
    assert "gitlab_token" in CredentialVault.CREDENTIAL_FIELDS
