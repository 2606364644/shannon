"""P3c 阶段 0：OpenAIProvider 读 self.config 运行时调参，None 回落 env。"""
import pytest

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


# —— Task 4: per-scan proxy_url 穿线 _make_subagent_runner → 子代理 ToolContext ——

@pytest.mark.asyncio
async def test_make_subagent_runner_propagates_proxy_url(monkeypatch):
    """_make_subagent_runner(model, cwd, proxy_url) 返回的 runner 把 proxy_url 注入子代理 ToolContext。

    子代理继承同一 per-scan 代理（与主 agent 同）。验证 Runner.run 收到的
    context.proxy_url 与传入一致。
    """
    captured: dict = {}

    async def fake_run(agent, input, context, max_turns):
        captured["proxy_url"] = context.proxy_url
        # 返回最小 stub（runner 只用 res.final_output）
        result = type("R", (), {"final_output": "ok"})()
        return result

    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run", fake_run)

    p = _make({"api_key": "test", "base_url": "https://x.example.com"})
    runner = p._make_subagent_runner("m", "/tmp", proxy_url="http://127.0.0.1:9090")
    out = await runner("look at foo.py")
    assert out == "ok"
    assert captured["proxy_url"] == "http://127.0.0.1:9090"


@pytest.mark.asyncio
async def test_make_subagent_runner_proxy_url_none_default(monkeypatch):
    """_make_subagent_runner 不传 proxy_url → ToolContext.proxy_url None（向后兼容）。"""
    captured: dict = {}

    async def fake_run(agent, input, context, max_turns):
        captured["proxy_url"] = context.proxy_url
        result = type("R", (), {"final_output": "ok"})()
        return result

    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run", fake_run)

    p = _make({"api_key": "test", "base_url": "https://x.example.com"})
    runner = p._make_subagent_runner("m", "/tmp")
    await runner("look at foo.py")
    assert captured["proxy_url"] is None
