"""P3c 阶段 0：OpenAIProvider 读 self.config 运行时调参，None 回落 env。"""
from supernova_core.agents.runner import ProviderConfig
from supernova_core.agents.providers_openai import OpenAIProvider


def _make(cfg_overrides: dict | None = None) -> OpenAIProvider:
    return OpenAIProvider(ProviderConfig(type="openai_compatible", **(cfg_overrides or {})))


# —— _max_turns（SUPERNOVA_OPENAI_MAX_TURNS）——

def test_max_turns_from_config():
    assert _make({"max_turns": 333})._max_turns() == 333


def test_max_turns_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_OPENAI_MAX_TURNS", "250")
    assert _make()._max_turns() == 250


def test_max_turns_default(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_OPENAI_MAX_TURNS", raising=False)
    assert _make()._max_turns() == 10000


# —— _subagent_max_turns（SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS）——

def test_subagent_max_turns_from_config():
    assert _make({"subagent_max_turns": 77})._subagent_max_turns() == 77


def test_subagent_max_turns_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", "60")
    assert _make()._subagent_max_turns() == 60


def test_subagent_max_turns_default(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", raising=False)
    assert _make()._subagent_max_turns() == 100


# —— _call_timeout（SUPERNOVA_OPENAI_CALL_TIMEOUT）——

def test_call_timeout_from_config():
    assert _make({"call_timeout": 120.0})._call_timeout() == 120.0


def test_call_timeout_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", "900")
    assert _make()._call_timeout() == 900.0


def test_call_timeout_default(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", raising=False)
    assert _make()._call_timeout() == 2400.0
