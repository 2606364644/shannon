from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents import RunContextWrapper

from supernova_core.agents.tools_openai import ToolContext
from supernova_core.agents.tools_openai.web import _web_fetch_impl, _web_search_impl


def _ctx(tmp_path):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path)))


@pytest.mark.asyncio
async def test_web_fetch_strips_html(tmp_path):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = "<html><body><p>Hello there</p></body></html>"
    fake_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=fake_resp)
    with patch("supernova_core.agents.tools_openai.web.httpx.AsyncClient", return_value=client):
        out = await _web_fetch_impl(_ctx(tmp_path), "https://example.com")
    assert "Hello there" in out
    assert "<p>" not in out


@pytest.mark.asyncio
async def test_web_fetch_truncates(tmp_path):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = "A" * 60000
    fake_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=fake_resp)
    with patch("supernova_core.agents.tools_openai.web.httpx.AsyncClient", return_value=client):
        out = await _web_fetch_impl(_ctx(tmp_path), "https://example.com", max_length=1000)
    assert len(out) <= 1100


@pytest.mark.asyncio
async def test_web_search_returns_results(tmp_path):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    # 极简 DDG Lite 片段
    fake_resp.text = (
        '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffoo.example%2F">Foo</a>'
        "<td>foo snippet text</td>"
    )
    fake_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=fake_resp)
    with patch("supernova_core.agents.tools_openai.web.httpx.AsyncClient", return_value=client):
        out = await _web_search_impl(_ctx(tmp_path), "foo")
    assert "foo.example" in out or "Foo" in out
