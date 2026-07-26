"""P3c 阶段 4：GitFetcher 按 ws 解析凭据（ws git 段优先，回落全局）。"""
from unittest.mock import MagicMock

from supernova_web.components.git_fetcher import GitFetcher
from supernova_web.components.ws_config_store import WsConfig, WsGitFields


def test_creds_global_when_no_ws_store():
    """无 ws_config_store（CLI / 旧测试兼容）→ 全局凭据，行为不变。"""
    f = GitFetcher("/tmp/r", "global-user", "global-token")
    assert f.available() is True
    assert f._inject_auth("https://host/x") == "https://global-user:global-token@host/x"


def test_creds_from_ws_when_configured():
    """ws 配了 git 凭据 → 用 ws 凭据。"""
    store = MagicMock()
    store.read.return_value = WsConfig(git=WsGitFields(gitlab_user="ws-user", gitlab_token="ws-token"))
    f = GitFetcher("/tmp/r", "global-user", "global-token", ws_config_store=store)
    assert f.available("ws-a") is True
    assert f._inject_auth("https://host/x", "ws-a") == "https://ws-user:ws-token@host/x"


def test_creds_fall_back_to_global_when_ws_git_unset():
    """ws 空 git 段 → 回落全局。"""
    store = MagicMock()
    store.read.return_value = WsConfig(git=WsGitFields())   # 空 git 段
    f = GitFetcher("/tmp/r", "global-user", "global-token", ws_config_store=store)
    assert f.available("ws-a") is True
    assert f._inject_auth("https://host/x", "ws-a") == "https://global-user:global-token@host/x"


def test_partial_override_user_only():
    """只覆盖 user，token 回落全局（字段级 or 合并）。"""
    store = MagicMock()
    store.read.return_value = WsConfig(git=WsGitFields(gitlab_user="ws-user"))  # token=None
    f = GitFetcher("/tmp/r", "global-user", "global-token", ws_config_store=store)
    assert f._inject_auth("https://host/x", "ws-a") == "https://ws-user:global-token@host/x"


def test_available_false_when_no_creds():
    """全局无凭据且无 ws store → available 始终 False（含 ws=None 与 ws 指定）。"""
    f = GitFetcher("/tmp/r", None, None)
    assert f.available() is False
    assert f.available("ws-a") is False
