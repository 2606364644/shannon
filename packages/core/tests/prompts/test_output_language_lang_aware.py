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
    """§1 铁律：语言 partial 不得夹带确定性 dataflow 产物。

    forbidden 收窄（2026-08-26）：移除裸词 "dataflow"——partial 现在点名
    dataflow_steps 字段做语言覆盖（叙述字段扩展），字段名 ≠ 确定性产物；
    SinkCallSite / parameter_graph / source_sink 仍是确定性产物特征，守住。"""
    for variant in ("zh", "en"):
        p = PROMPTS / "shared" / f"_output-language.{variant}.txt"
        text = p.read_text(encoding="utf-8")
        for forbidden in ("SinkCallSite", "parameter_graph", "source_sink"):
            assert forbidden.lower() not in text.lower(), (
                f"_output-language.{variant}.txt 含确定性产物关键词 {forbidden}"
            )


def test_zh_variant_covers_all_narrative_fields():
    """黑盒实证（NodeGoat-20260820）：partial 只点名 title → agent 仅标题中文，
    add_exploit 参数（影响/利用步骤/影响证据）整段英文直达 evidence/报告。
    zh 变体须把覆盖面扩到全部叙述产物：queue 叙述字段、dataflow_steps label、
    add_exploit 全部叙述参数、deliverable 正文。"""
    text = (PROMPTS / "shared" / "_output-language.zh.txt").read_text(encoding="utf-8")
    # add_exploit 叙述参数点名（黑盒 evidence 正文来源）
    for arg in ("impact", "exploitation_steps", "proof_of_impact",
                "current_blocker", "what_we_tried"):
        assert arg in text, f"zh 变体未点名 add_exploit 参数 {arg}"
    # queue 叙述字段点名（title 之外的叙述字段）
    for field in ("notes", "remediation", "mismatch_reason",
                  "sanitization_observed"):
        assert field in text, f"zh 变体未点名 queue 叙述字段 {field}"
    # 数据流步骤 label 的描述性文字
    assert "dataflow_steps" in text


def test_en_variant_covers_all_narrative_fields_symmetrically():
    """en 变体对称扩展（同一覆盖面，英文指令）。"""
    text = (PROMPTS / "shared" / "_output-language.en.txt").read_text(encoding="utf-8")
    for arg in ("impact", "exploitation_steps", "proof_of_impact"):
        assert arg in text, f"en 变体未点名 add_exploit 参数 {arg}"
    assert "dataflow_steps" in text
