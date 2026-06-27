"""Agent narration-language directive (Part B of display-ux-polish spec).

Injects a SYSTEM-prompt directive so GLM narrates in Chinese while keeping
code-consumed structure (JSON keys, controlled-vocabulary field values,
structural Markdown headers) in English. Engine-agnostic: each provider applies
it via its native system-prompt seam. Does NOT touch prompts/*.txt (CLAUDE.md
dual-track invariant — this is a language directive, not a deterministic bridge).
"""
from __future__ import annotations

import os

_DIRECTIVE_ZH = """<language>
- 用中文进行所有口述、推理过程与每轮总结（narration）。
- 人读散文用中文：notes / exploitation_hypothesis / missing_defense /
  evidence_chain 的叙述、报告正文、执行摘要正文。
- 以下必须保持英文（代码解析/匹配）：JSON 字段名、代码/文件路径/命令/ID；
  受控词汇字段的"值"——vulnerability_type、confidence、
  suggested_exploit_technique 等保持 prompt 给定的英文枚举；
  结构性 Markdown 标题，尤其 "## Executive Summary"。
</language>"""


def narration_directive() -> str | None:
    """Return the Chinese narration directive, or None when disabled.

    env SHANNON_AGENT_NARRATION_LANG (default "zh"): "zh" → directive on,
    anything else ("en", etc.) → None (unchanged English behavior).
    """
    lang = os.getenv("SHANNON_AGENT_NARRATION_LANG", "zh").strip().lower()
    return _DIRECTIVE_ZH if lang == "zh" else None
