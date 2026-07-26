"""P3c 阶段 2：WsConfigStore 读写 + 凭据加密 + resolve 覆盖。"""
import pytest
from pathlib import Path

from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.ws_config_store import (
    WsConfig, WsProviderFields, WsConfigStore, validate_ws_config,
)


@pytest.fixture
def store(tmp_path):
    vault = CredentialVault(tmp_path / ".master_key")
    return WsConfigStore(tmp_path, vault)


def test_read_missing_ws_returns_empty(store, tmp_path):
    """无 config.yaml → 空 WsConfig（全 None）。"""
    (tmp_path / "ws-a").mkdir()
    cfg = store.read("ws-a")
    assert cfg.provider.ai_provider is None
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
    """未填字段 → 回落全局默认（build_provider_config）。"""
    (tmp_path / "ws-a").mkdir()
    pc = store.resolve_provider_config("ws-a")
    assert pc["type"] in ("anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router")
    assert "api_key" in pc  # 全局默认有此键


def test_resolve_provider_config_ws_overrides(store, tmp_path):
    """ws 显式字段覆盖全局默认（ai_provider → type 映射）。"""
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible", api_key="sk-ws", max_turns=999,
    )))
    pc = store.resolve_provider_config("ws-a")
    assert pc["type"] == "openai_compatible"   # ai_provider → type
    assert pc["api_key"] == "sk-ws"
    assert pc["max_turns"] == 999


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
