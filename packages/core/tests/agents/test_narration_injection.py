"""Part B / Task 7: verify the narration directive is wired into both engines'
system-prompt seams, and that the directive never leaks into prompts/*.txt.

The directive is engine-agnostic: claude applies it as a preset/append system
prompt (SDK → `--append-system-prompt`, true system-prompt position, does NOT
replace the base), openai applies it as the Agent's `instructions`. Both turn
off cleanly (None) when narration is disabled. The task subagent on the openai
side intentionally does NOT get the directive (terse code output for the parent).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shannon_core.agents.narration import _DIRECTIVE_ZH

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def test_claude_options_get_append_system_prompt_when_zh():
    from shannon_core.agents.providers_anthropic import AnthropicProvider

    with patch(
        "shannon_core.agents.providers_anthropic.narration_directive",
        return_value=_DIRECTIVE_ZH,
    ):
        prov = AnthropicProvider.__new__(AnthropicProvider)
        with patch.object(prov, "_is_adaptive_thinking_enabled", return_value=False), \
             patch.object(prov, "_build_sdk_env", return_value={}):
            opts = prov._build_options(cwd="/tmp", model="m", output_format=None)
    assert opts.system_prompt == {"type": "preset", "append": _DIRECTIVE_ZH}


def test_claude_options_unchanged_when_disabled():
    from shannon_core.agents.providers_anthropic import AnthropicProvider

    with patch(
        "shannon_core.agents.providers_anthropic.narration_directive",
        return_value=None,
    ):
        prov = AnthropicProvider.__new__(AnthropicProvider)
        with patch.object(prov, "_is_adaptive_thinking_enabled", return_value=False), \
             patch.object(prov, "_build_sdk_env", return_value={}):
            opts = prov._build_options(cwd="/tmp", model="m", output_format=None)
    assert opts.system_prompt is None


def test_openai_instructions_carry_directive_when_zh():
    from shannon_core.agents import providers_openai as po

    with patch(
        "shannon_core.agents.providers_openai.narration_directive",
        return_value=_DIRECTIVE_ZH,
    ):
        prov = po.OpenAIProvider.__new__(po.OpenAIProvider)
        assert prov._instructions() == _DIRECTIVE_ZH


def test_openai_instructions_none_when_disabled():
    from shannon_core.agents import providers_openai as po

    with patch(
        "shannon_core.agents.providers_openai.narration_directive",
        return_value=None,
    ):
        prov = po.OpenAIProvider.__new__(po.OpenAIProvider)
        assert prov._instructions() is None


def test_prompts_do_not_contain_narration_directive():
    """CLAUDE.md dual-track invariant: the language directive lives only in the
    system-prompt layer, never in prompts/*.txt (no deterministic bridge; prompts
    stay English). `_DIRECTIVE_ZH`'s distinctive snippet must not appear there."""
    directive_snippet = "narration-language"  # distinctive phrase per spec
    offenders = []
    for p in PROMPTS_DIR.rglob("*.txt"):
        if directive_snippet in p.read_text(encoding="utf-8"):
            offenders.append(str(p.relative_to(PROMPTS_DIR)))
    assert not offenders, f"directive leaked into prompts: {offenders}"
