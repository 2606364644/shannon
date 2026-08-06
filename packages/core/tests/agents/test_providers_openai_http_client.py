"""_get_client 的 HTTP 层超时 / 重试：openai SDK 默认 600s/2 次，stall 时偏长。

回归 2026-08-06 hk-user-view-20260806-035435：vuln 阶段 GLM /chat/completions 重试
期间 worker 被 cgroup OOM kill（根因是内存，非 stall），但暴露出 openai 引擎 in-process
SDK 缺 HTTP 层超时兜底--AsyncOpenAI(**kwargs) 未传 timeout/max_retries，用 SDK 默认
（600s connect/read、2 次重试）。单次请求 stall 时，SDK 内部按 600s 等待 + 重试放大，
worker 线程长时间 sleeping。本测试锁定：_get_client 构造 AsyncOpenAI 时注入 env 驱动的
timeout/max_retries，给单次 HTTP 请求熔断（区别于 _call_timeout 的整 stream wall-clock 兜底）。

注：本次 OOM 真根因是内存（L1 防 OOM），非 stall；本加固是防御性（L4），防 stall 类静默 hang。
"""
from unittest.mock import MagicMock

from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def test_get_client_passes_http_timeout_and_max_retries_from_env(monkeypatch):
    """env 设了 SUPERNOVA_OPENAI_HTTP_TIMEOUT / _MAX_RETRIES -> AsyncOpenAI 收到这俩 kwargs。"""
    captured: dict = {}

    def _fake_ctor(**kwargs):
        captured.update(kwargs)
        return MagicMock()  # _wrap_client_for_argument_sanitize 访问 chat.completions.create

    monkeypatch.setattr("supernova_core.agents.providers_openai.AsyncOpenAI", _fake_ctor)
    monkeypatch.setenv("SUPERNOVA_OPENAI_HTTP_TIMEOUT", "120")
    monkeypatch.setenv("SUPERNOVA_OPENAI_MAX_RETRIES", "2")

    p = _provider()
    p._get_client()

    assert captured.get("timeout") == 120.0
    assert captured.get("max_retries") == 2


def test_get_client_defaults_when_env_unset(monkeypatch):
    """env 未设 -> 用保守默认（timeout 300s / max_retries 1），不沿用 SDK 600s/2。"""
    captured: dict = {}

    def _fake_ctor(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("supernova_core.agents.providers_openai.AsyncOpenAI", _fake_ctor)
    monkeypatch.delenv("SUPERNOVA_OPENAI_HTTP_TIMEOUT", raising=False)
    monkeypatch.delenv("SUPERNOVA_OPENAI_MAX_RETRIES", raising=False)

    p = _provider()
    p._get_client()

    assert captured.get("timeout") == 300.0
    assert captured.get("max_retries") == 1


def test_get_client_caches_single_instance(monkeypatch):
    """_get_client 幂等：重复调用不重复构造（沿用既有缓存契约，回归保护）。"""
    calls = 0

    def _fake_ctor(**kwargs):
        nonlocal calls
        calls += 1
        return MagicMock()

    monkeypatch.setattr("supernova_core.agents.providers_openai.AsyncOpenAI", _fake_ctor)
    monkeypatch.delenv("SUPERNOVA_OPENAI_HTTP_TIMEOUT", raising=False)
    monkeypatch.delenv("SUPERNOVA_OPENAI_MAX_RETRIES", raising=False)

    p = _provider()
    c1 = p._get_client()
    c2 = p._get_client()
    assert c1 is c2
    assert calls == 1
