"""OpenAI 引擎的工具集（对齐 claude code 内置工具的核心子集）。

cwd 经 RunContextWrapper[ToolContext] 注入，所有工具共享同一工作目录，
等价于 anthropic 侧 permission_mode=bypassPermissions + cwd。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


@dataclass
class ToolContext:
    """Runner context：注入工具的工作目录 + 子代理 runner（改动 4a）。"""

    cwd: str
    # spec 改动 4a：子代理委派 runner。provider 注入（关 chat_model+cwd）；测试可 mock。
    subagent_run: Callable[[str], Awaitable[str]] | None = None
    # Task 2（黑盒 HOST 档案）：per-scan 出口代理 URL。None=不注入（向后兼容）。
    # 由 executor（Task 4）从 host_profile 灌入；bash/web_fetch/web_search 读此字段
    # 注入 HTTPS_PROXY/HTTP_PROXY/NO_PROXY env 或 httpx ``proxy=`` kwarg。
    proxy_url: str | None = None
    # readonly-code topology policy: empty tuple preserves legacy unrestricted behavior.
    allowed_roots: tuple[Path, ...] = ()


def build_readonly_code_tools():
    """Read/search-only tool surface used by topology pre-analysis."""
    from .exec import grep
    from .fs import glob, read_file

    return [read_file, glob, grep]


def build_tools():
    """返回 OpenAI 引擎的全部 @function_tool 列表。

    分文件定义，这里汇总，供 OpenAIProvider 注入 Agent(tools=...)。
    """
    from .exec import bash, grep
    from .fs import edit_file, glob, read_file, write_file
    from .task import task
    from .web import web_fetch, web_search

    return [bash, read_file, write_file, edit_file, grep, glob, web_fetch, web_search, task]


__all__ = ["ToolContext", "build_readonly_code_tools", "build_tools"]
