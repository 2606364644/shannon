"""验证 _output-language 的 lang-aware @include：按当前语言选 zh/en 变体。

守 CLAUDE.md §1 不变量：拆分后两变体都只是"输出语言指令"，
不含确定性 dataflow 产物（SinkCallSite/parameter_graph 等）。
"""
from pathlib import Path

from supernova_core.prompts.manager import PromptManager

# 仓库根的 prompts/：packages/core/tests/prompts/<this> → parents[4] = repo root
PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


def test_include_picks_zh_variant(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    mgr = PromptManager(PROMPTS)
    out = mgr._process_includes("@include(shared/_output-language.txt)", PROMPTS)
    assert "简体中文" in out  # zh 变体特征


def test_include_picks_en_variant(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    mgr = PromptManager(PROMPTS)
    out = mgr._process_includes("@include(shared/_output-language.txt)", PROMPTS)
    assert "简体中文" not in out  # 不含中文版特征
    assert "English" in out  # en 变体特征


def test_output_language_variants_no_deterministic_hints():
    """§1 铁律：语言 partial 不得夹带确定性 dataflow 产物。"""
    for variant in ("zh", "en"):
        p = PROMPTS / "shared" / f"_output-language.{variant}.txt"
        text = p.read_text(encoding="utf-8")
        for forbidden in ("SinkCallSite", "parameter_graph", "dataflow", "source_sink"):
            assert forbidden.lower() not in text.lower(), (
                f"_output-language.{variant}.txt 含确定性产物关键词 {forbidden}"
            )
