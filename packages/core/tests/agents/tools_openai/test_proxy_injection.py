"""Task 2: 工具出口注入——per-scan proxy 注入 bash/web_fetch/web_search。

TDD 测试集（TDD step 1）。验证：
- ToolContext.proxy_url 字段存在，默认 None（向后兼容）。
- _bash_impl：proxy_url 非空时子进程 env 含 HTTPS_PROXY/HTTP_PROXY/NO_PROXY；
  proxy_url=None 时 env=None（继承 worker env，向后兼容铁律）。
- _web_fetch_impl / _web_search_impl：proxy_url 非空时 httpx.AsyncClient 收到
  ``proxy=`` 单数 kwarg（httpx 0.28.x 已移除 ``proxies=`` 复数）；为空时不注入。

注意：FakeClient 定义 ``async def aclose`` 以走真实 finally 代码路径
（真实实现用 ``await client.aclose()`` 而非 ``async with``）。
"""
from __future__ import annotations

import asyncio

import pytest
from agents import RunContextWrapper

from supernova_core.agents.tools_openai import ToolContext
from supernova_core.agents.tools_openai.exec import _bash_impl
from supernova_core.agents.tools_openai.web import _web_fetch_impl, _web_search_impl


def test_tool_context_has_proxy_url_field():
    ctx = ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:8080")
    assert ctx.proxy_url == "http://127.0.0.1:8080"
    # 默认 None，向后兼容
    assert ToolContext(cwd="/tmp").proxy_url is None


@pytest.mark.asyncio
async def test_bash_impl_injects_proxy_env(monkeypatch):
    """有 proxy_url 时，子进程 env 含 HTTPS_PROXY/HTTP_PROXY/NO_PROXY。"""
    captured: dict = {}

    async def fake_shell(cmd, **kw):
        captured["env"] = kw.get("env")
        captured["cmd"] = cmd

        class P:
            stdout = b"ok\n"
            returncode = 0

            async def communicate(self):
                return (b"ok\n", b"")

            async def wait(self):
                return 0

        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    ctx = RunContextWrapper(ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:9090"))
    await _bash_impl(ctx, "curl http://x.test")
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:9090"
    assert captured["env"]["HTTP_PROXY"] == "http://127.0.0.1:9090"
    assert "NO_PROXY" in captured["env"]
    # NO_PROXY 必须保留 loopback，否则代理拦截本机服务
    assert "127.0.0.1" in captured["env"]["NO_PROXY"]
    assert "localhost" in captured["env"]["NO_PROXY"]


@pytest.mark.asyncio
async def test_bash_impl_no_env_when_proxy_none(monkeypatch):
    """无 proxy_url 时 env=None（继承 worker env，向后兼容铁律）。"""
    captured: dict = {}

    async def fake_shell(cmd, **kw):
        captured["env"] = kw.get("env")

        class P:
            stdout = b""
            returncode = 0

            async def communicate(self):
                return (b"", b"")

            async def wait(self):
                return 0

        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    ctx = RunContextWrapper(ToolContext(cwd="/tmp"))  # proxy_url=None
    await _bash_impl(ctx, "echo hi")
    # 不注入：env=None 让 asyncio 继承父进程 env（当前行为）
    assert captured["env"] is None


class _FakeResponse:
    def __init__(self, text: str = "body"):
        self.status_code = 200
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    """伪 httpx.AsyncClient：记录构造 kwargs，模拟真实 finally+aclose 路径。

    真实实现 _web_fetch_impl/_web_search_impl 不用 ``async with``，而是
    ``client = httpx.AsyncClient(...)`` + ``try / finally: await client.aclose()``。
    本 Fake 同步实现 ``aclose`` 以走真实代码路径（避免 mock 透传掩藏 bug）。
    """

    def __init__(self, **kwargs):
        self._captured = kwargs
        self._get_text = kwargs.pop("_text", "body")

    async def aclose(self):
        pass

    async def get(self, url, **kw):
        return _FakeResponse(self._get_text)


@pytest.mark.asyncio
async def test_web_fetch_impl_passes_proxy(monkeypatch):
    """有 proxy_url 时 httpx.AsyncClient 收到 ``proxy=``（httpx 0.28.x 单数形式）。"""
    captured: dict = {}

    class CapturingClient(_FakeClient):
        def __init__(self, **kw):
            captured["kwargs"] = kw
            super().__init__(**kw)

    monkeypatch.setattr(
        "supernova_core.agents.tools_openai.web.httpx.AsyncClient", CapturingClient
    )
    ctx = RunContextWrapper(ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:9090"))
    await _web_fetch_impl(ctx, "http://x.test")
    # httpx 0.28.x 移除了复数 proxies=，必须用单数 proxy=
    assert captured["kwargs"].get("proxy") == "http://127.0.0.1:9090"
    # 旧参数名绝不可出现（会在生产 raise TypeError）
    assert "proxies" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_web_fetch_impl_no_proxy_when_ctx_none(monkeypatch):
    """无 proxy_url 时不注入 proxy kwarg（向后兼容铁律）。"""
    captured: dict = {}

    class CapturingClient(_FakeClient):
        def __init__(self, **kw):
            captured["kwargs"] = kw
            super().__init__(**kw)

    monkeypatch.setattr(
        "supernova_core.agents.tools_openai.web.httpx.AsyncClient", CapturingClient
    )
    ctx = RunContextWrapper(ToolContext(cwd="/tmp"))  # proxy_url=None
    await _web_fetch_impl(ctx, "http://x.test")
    assert "proxy" not in captured["kwargs"]
    assert "proxies" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_web_search_impl_passes_proxy(monkeypatch):
    """web_search 同模式：有 proxy_url 时 httpx.AsyncClient 收到 ``proxy=``。"""
    captured: dict = {}

    class CapturingClient(_FakeClient):
        def __init__(self, **kw):
            captured["kwargs"] = kw
            super().__init__(**kw)

    monkeypatch.setattr(
        "supernova_core.agents.tools_openai.web.httpx.AsyncClient", CapturingClient
    )
    ctx = RunContextWrapper(ToolContext(cwd="/tmp", proxy_url="http://127.0.0.1:9090"))
    await _web_search_impl(ctx, "anything")
    assert captured["kwargs"].get("proxy") == "http://127.0.0.1:9090"
    assert "proxies" not in captured["kwargs"]
