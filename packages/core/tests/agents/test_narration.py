import pytest

from shannon_core.agents.narration import narration_directive, DIRECTIVE_ZH


@pytest.mark.parametrize("val", ["zh", "ZH", " zh "])
def test_zh_returns_directive(monkeypatch, val):
    monkeypatch.setenv("SHANNON_AGENT_NARRATION_LANG", val)
    assert narration_directive() == DIRECTIVE_ZH


@pytest.mark.parametrize("val", ["en", "EN", "", "off"])
def test_non_zh_returns_none(monkeypatch, val):
    monkeypatch.setenv("SHANNON_AGENT_NARRATION_LANG", val)
    assert narration_directive() is None


def test_default_is_zh(monkeypatch):
    monkeypatch.delenv("SHANNON_AGENT_NARRATION_LANG", raising=False)
    assert narration_directive() == DIRECTIVE_ZH


def test_directive_enforces_english_for_structure():
    """安全锚点：受控词/标题/JSON key 必须留英文（spec §4.2）。"""
    d = DIRECTIVE_ZH
    assert "vulnerability_type" in d
    assert "## Executive Summary" in d
    assert "JSON" in d
    assert "中文" in d            # 确实要求中文口述
