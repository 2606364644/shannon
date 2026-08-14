"""报告语言 i18n 层：单一语言开关 + 双语 message 帮助类。

语言开关复用 ``SUPERNOVA_AGENT_NARRATION_LANG``（默认 ``zh``）。所有确定性
渲染器、攻击链模板、渲染标签经 ``Messages`` 取双语文案；LLM 轨 prompt 语言
约束经 lang-aware ``@include``（见 ``prompts/manager.py``）。本模块只管语言，
不触碰 CLAUDE.md §1 的确定性→LLM dataflow hints 铁律（语言指令 ≠ 确定性 hints）。
"""
from __future__ import annotations

import os

from supernova_core.config.scan_env import ws_getenv

# 归一化为英文的取值；其余一切（zh/cn/中文/chinese/空/未知）回落中文（默认）。
_EN_VALUES = {"en", "english"}


def current_lang() -> str:
    """返回 ``"zh"`` 或 ``"en"``。默认 ``"zh"``（与现状一致）。

    ``SUPERNOVA_AGENT_NARRATION_LANG`` 为 ``en``/``english`` → ``"en"``；
    其它（含 ``zh``/``cn``/``中文``/未设/未知）→ ``"zh"``。
    """
    raw = ws_getenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh").strip().lower()
    return "en" if raw in _EN_VALUES else "zh"


class Messages:
    """双语 message 字典。每个 key 必须同时含 zh+en，缺失 fail-fast（防漏翻）。"""

    def __init__(self, table: dict[str, dict[str, str]]):
        self._table = table

    def get(self, key: str, **fmt) -> str:
        """按当前语言取文案。缺 key → KeyError；缺该 lang → KeyError（防漏翻）。"""
        entry = self._table[key]  # KeyError if unknown key
        text = entry[current_lang()]  # KeyError if lang missing
        return text.format(**fmt) if fmt else text
