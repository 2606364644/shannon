"""Web 工具：web_fetch（抓取去标签）、web_search（DuckDuckGo Lite，无 key）。"""
from __future__ import annotations

import re
import urllib.parse

import httpx
from agents import RunContextWrapper, function_tool

from . import ToolContext

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _client_kwargs(proxy_url: str | None) -> dict:
    """构造 httpx.AsyncClient kwargs：proxy_url 非空→加 ``proxy=``（httpx 0.28.x 单数）。

    httpx 0.28.0 移除了复数 ``proxies=`` 参数；用错会在生产 raise TypeError。
    proxy_url 为 None 时不加 proxy kwarg（向后兼容铁律）。
    """
    kw = {"timeout": 30, "follow_redirects": True}
    if proxy_url:
        kw["proxy"] = proxy_url
    return kw


async def _web_fetch_impl(
    ctx: RunContextWrapper[ToolContext],
    url: str,
    max_length: int = 30000,
) -> str:
    """Fetch a URL and return its text content (HTML stripped).

    Args:
        url: The URL to fetch.
        max_length: Max characters to return (default 30000).
    """
    try:
        client = httpx.AsyncClient(**_client_kwargs(ctx.context.proxy_url))
        try:
            resp = await client.get(url, headers={"User-Agent": "shannon-openai-engine/1.0"})
            resp.raise_for_status()
            return _truncate(_strip_html(resp.text), int(max_length))
        finally:
            await client.aclose()
    except Exception as e:
        return f"[web_fetch error] {type(e).__name__}: {e}"


async def _web_search_impl(
    ctx: RunContextWrapper[ToolContext],
    query: str,
    max_results: int = 10,
) -> str:
    """Search the web via DuckDuckGo and return results (title, url, snippet).

    Args:
        query: Search query.
        max_results: Max number of results (default 10).
    """
    try:
        client = httpx.AsyncClient(**_client_kwargs(ctx.context.proxy_url))
        try:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query, "kl": "us-en"},
                headers={"User-Agent": "shannon-openai-engine/1.0"},
            )
            resp.raise_for_status()
            html = resp.text
        finally:
            await client.aclose()
    except Exception as e:
        return f"[web_search error] {type(e).__name__}: {e}"

    rows: list[str] = []
    # 解析结果链接 (uddg=) 与相邻文本片段
    for href, snippet in re.findall(r'uddg=([^"&]+).*?</a>.*?<td[^>]*>(.*?)</td>', html, re.S)[: int(max_results)]:
        link = urllib.parse.unquote(href)
        rows.append(f"- {snippet.strip()[:200]}\n  {link}")
    return _truncate("\n".join(rows), 30000) or "[web_search] no results"


web_fetch = function_tool(_web_fetch_impl, name_override="web_fetch")
web_search = function_tool(_web_search_impl, name_override="web_search")
