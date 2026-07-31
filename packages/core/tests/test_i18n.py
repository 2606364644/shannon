"""i18n 层测试：语言开关归一化 + 双语 Messages 帮助类。"""
import pytest

from supernova_core.i18n import Messages, current_lang


def test_current_lang_default_zh(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_AGENT_NARRATION_LANG", raising=False)
    assert current_lang() == "zh"


@pytest.mark.parametrize("raw", ["en", "EN", "english", "English"])
def test_current_lang_en(monkeypatch, raw):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", raw)
    assert current_lang() == "en"


@pytest.mark.parametrize("raw", ["zh", "CN", "中文", "chinese", "", "unknown"])
def test_current_lang_normalizes_to_zh(monkeypatch, raw):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", raw)
    assert current_lang() == "zh"


def test_messages_get_by_lang(monkeypatch):
    m = Messages({"title": {"zh": "注入分析报告", "en": "Injection Analysis Report"}})
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    assert m.get("title") == "Injection Analysis Report"
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    assert m.get("title") == "注入分析报告"


def test_messages_get_supports_format(monkeypatch):
    m = Messages({"stored": {"zh": "写入 {store}", "en": "written to {store}"}})
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    assert m.get("stored", store="profiles") == "written to profiles"


def test_messages_missing_lang_raises(monkeypatch):
    m = Messages({"k": {"zh": "中"}})  # 缺 en
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    with pytest.raises(KeyError):
        m.get("k")


def test_messages_missing_key_raises():
    m = Messages({"k": {"zh": "中", "en": "en"}})
    with pytest.raises(KeyError):
        m.get("nope")
