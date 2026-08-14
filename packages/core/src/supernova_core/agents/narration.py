"""Agent narration-language directive (Part B of display-ux-polish spec).

Injects a SYSTEM-prompt directive so GLM narrates in the configured language (zh default) while keeping
code-consumed structure (JSON keys, controlled-vocabulary field values,
structural Markdown headers) in English. Engine-agnostic: each provider applies
it via its native system-prompt seam. Does NOT touch prompts/*.txt (CLAUDE.md
dual-track invariant — this is a language directive, not a deterministic bridge).
"""
from __future__ import annotations

import os

from supernova_core.i18n import current_lang
from supernova_core.config.scan_env import ws_getenv

DIRECTIVE_ZH = """<language>
- 用中文进行所有口述、推理过程与每轮总结（narration）。
- 人读散文用中文：notes / exploitation_hypothesis / missing_defense /
  evidence_chain 的叙述、报告正文、执行摘要正文。
- 以下必须保持英文（代码解析/匹配）：JSON 字段名、代码/文件路径/命令/ID；
  受控词汇字段的"值"——vulnerability_type、confidence、
  suggested_exploit_technique 等保持 prompt 给定的英文枚举；
  结构性 Markdown 标题，尤其 "## Executive Summary"。
</language>"""

DIRECTIVE_EN = """<language>
- Narrate all reasoning, walk-through, and per-turn summaries in English.
- Human-prose in English: notes / exploitation_hypothesis / missing_defense /
  evidence_chain narration, report body, executive summary body.
- Keep these in English (code-parsed/matched): JSON keys, code/file paths/
  commands/IDs; controlled-vocabulary field VALUES — vulnerability_type,
  confidence, suggested_exploit_technique stay the English enums from the
  prompt; structural Markdown headers, especially "## Executive Summary".
</language>"""

# 显式关闭 escape hatch（off/none/disable → 不注入）；语言由 i18n.current_lang() 定
_DISABLE_VALUES = {"off", "none", "disable"}


def narration_directive() -> str | None:
    """Return the narration directive for the current language, or None if disabled.

    env SUPERNOVA_AGENT_NARRATION_LANG (default "zh"):
      - "zh"（默认）→ DIRECTIVE_ZH（中文叙述指令）
      - "en"/"english" → DIRECTIVE_EN（英文叙述指令）
      - "off"/"none"/"disable" → None（显式关闭，不注入）
      - 其它 → 回落 zh（默认中文）
    语言 zh/en 由 i18n.current_lang() 单一决定；本函数仅额外判定显式关闭。
    """
    raw = ws_getenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh").strip().lower()
    if raw in _DISABLE_VALUES:
        return None
    return DIRECTIVE_ZH if current_lang() == "zh" else DIRECTIVE_EN
