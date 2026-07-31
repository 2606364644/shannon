import pytest

from supernova_core.agents.narration import (
    narration_directive,
    DIRECTIVE_ZH,
    DIRECTIVE_EN,
)


@pytest.mark.parametrize("val", ["zh", "ZH", " zh "])
def test_zh_returns_directive(monkeypatch, val):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", val)
    assert narration_directive() == DIRECTIVE_ZH


@pytest.mark.parametrize("val", ["en", "EN", "english", "English"])
def test_en_returns_english_directive(monkeypatch, val):
    """en 模式返回英文叙述指令（非 None），确保 LLM 轨明确输出英文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", val)
    assert narration_directive() == DIRECTIVE_EN


@pytest.mark.parametrize("val", ["off", "none", "disable"])
def test_explicit_disable_returns_none(monkeypatch, val):
    """显式关闭 escape hatch：off/none/disable → 不注入指令。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", val)
    assert narration_directive() is None


@pytest.mark.parametrize("val", ["CN", "中文", "chinese"])
def test_non_en_non_disable_normalizes_to_zh(monkeypatch, val):
    """归一化：cn/中文 等非 en、非 disable 值回落中文指令（默认 zh）。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", val)
    assert narration_directive() == DIRECTIVE_ZH


def test_default_is_zh(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_AGENT_NARRATION_LANG", raising=False)
    assert narration_directive() == DIRECTIVE_ZH


def test_directive_zh_enforces_english_for_structure():
    """安全锚点：受控词/标题/JSON key 必须留英文（spec §4.2）。"""
    d = DIRECTIVE_ZH
    assert "vulnerability_type" in d
    assert "## Executive Summary" in d
    assert "JSON" in d
    assert "中文" in d            # 确实要求中文口述


def test_directive_en_enforces_english_for_structure():
    """en 指令同样要求结构（受控词/标题/JSON key）留英文。"""
    d = DIRECTIVE_EN
    assert "vulnerability_type" in d
    assert "## Executive Summary" in d
    assert "JSON" in d
    assert "English" in d         # 确实要求英文口述
