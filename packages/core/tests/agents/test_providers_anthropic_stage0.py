"""P3c 阶段 0：AnthropicProvider 读 self.config 运行时调参，None 回落 env。"""
import pytest

from supernova_core.agents.runner import ProviderConfig
from supernova_core.agents.providers_anthropic import AnthropicProvider


def _make(cfg_overrides: dict | None = None) -> AnthropicProvider:
    return AnthropicProvider(ProviderConfig(type="anthropic_api", **(cfg_overrides or {})))


# —— max_output_tokens（_build_sdk_env → CLAUDE_CODE_MAX_OUTPUT_TOKENS）——

def test_max_output_tokens_from_config(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    p = _make({"max_output_tokens": 12345})
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "12345"


def test_max_output_tokens_falls_back_to_env(monkeypatch):
    """字段 None → 回落 env（阶段 0 行为不变）。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "99999")
    p = _make()  # max_output_tokens=None
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "99999"


def test_max_output_tokens_default_when_unset(monkeypatch):
    """字段 None + env 未设 → 默认 64000（与改造前一致）。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    p = _make()
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"


def test_max_output_tokens_not_set_when_zero_and_no_default(monkeypatch):
    """env 未设且无默认时，原逻辑不写入该 key（保改造前空串语义）。"""
    # 改造前 :198-200 max_tokens 默认 "64000" 总是非空，故总会写入。
    # 这里验证字段 0 时仍写入 "0"（显式覆盖），不混淆"未覆盖"。
    p = _make({"max_output_tokens": 0})
    assert p._build_sdk_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "0"


# —— adaptive_thinking（_is_adaptive_thinking_enabled → CLAUDE_ADAPTIVE_THINKING）——

def test_adaptive_thinking_from_config_true():
    p = _make({"adaptive_thinking": True})
    assert p._is_adaptive_thinking_enabled() is True


def test_adaptive_thinking_from_config_false():
    p = _make({"adaptive_thinking": False})
    assert p._is_adaptive_thinking_enabled() is False


def test_adaptive_thinking_falls_back_to_env_false(monkeypatch):
    monkeypatch.setenv("CLAUDE_ADAPTIVE_THINKING", "false")
    p = _make()
    assert p._is_adaptive_thinking_enabled() is False


def test_adaptive_thinking_falls_back_to_env_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
    p = _make()
    assert p._is_adaptive_thinking_enabled() is True  # 默认 true


# —— max_turns（_resolve_max_turns → CLAUDE_MAX_TURNS，提取纯函数便于测试）——

def test_resolve_max_turns_override_wins():
    p = _make({"max_turns": 100})
    # max_turns_override（vuln 外部）优先级最高
    assert p._resolve_max_turns(200) == 200


def test_resolve_max_turns_from_config(monkeypatch):
    monkeypatch.delenv("CLAUDE_MAX_TURNS", raising=False)
    p = _make({"max_turns": 333})
    assert p._resolve_max_turns(None) == 333


def test_resolve_max_turns_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_MAX_TURNS", "250")
    p = _make()  # max_turns=None
    assert p._resolve_max_turns(None) == 250


def test_resolve_max_turns_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_MAX_TURNS", raising=False)
    p = _make()
    assert p._resolve_max_turns(None) == 200  # 默认 200


# —— anthropic_api 认证注入：auth_token / base_url 须显式注入 sdk_env，
#    不能只靠 PASSTHROUGH 从进程 env 兜（否则 worker env 无 ANTHROPIC_* 时
#    CLI 子进程拿不到凭据 → "Not logged in"，2026-07-30 web 引擎错配根因之一）——

def test_anthropic_api_injects_auth_token_and_base_url_from_config(monkeypatch):
    """config.auth_token/base_url 应显式注入 sdk_env（glm-anthropic 走 token、无 api_key）。

    回归锚点：进程 env 无 ANTHROPIC_AUTH_TOKEN 时，若只靠 PASSTHROUGH 透传，
    CLI 子进程 0 凭据 → "Not logged in · Please run /login"。
    """
    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    p = _make({"auth_token": "glm-token-xxx", "base_url": "https://open.bigmodel.cn/api/anthropic"})
    env = p._build_sdk_env()
    assert env.get("ANTHROPIC_AUTH_TOKEN") == "glm-token-xxx"
    assert env.get("ANTHROPIC_BASE_URL") == "https://open.bigmodel.cn/api/anthropic"
