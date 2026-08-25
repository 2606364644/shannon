"""报告风格指南 shared include + LLM 轨 collector 新字段接线锁定（spec 2026-08-25 Task 7）。

三层锁定：
1. 5 个 vuln-*.txt：字段表教 severity/impact/remediation/cwe_id + 尾部 @include 风格指南。
2. 5 个 *-exploit.txt + attack-chain.txt：头部含 output-language 与 report-style 两个 include。
3. _report-style 双语言变体存在（走 _process_includes 的 lang-aware fallback，
   与 _output-language.<lang>.txt 同机制，按 SUPERNOVA_AGENT_NARRATION_LANG 选择）。

守 CLAUDE.md §1 铁律：vuln prompt 不得引入确定性层产物关键词。

注：brief 原文写 parents[3]，会解析到 packages/prompts（不存在）——对齐既有测试
（test_output_language_lang_aware.py 等）用 resolve().parents[4] = repo root。
"""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[4] / "prompts"
VULN_PROMPTS = ["vuln-injection.txt", "vuln-xss.txt", "vuln-auth.txt",
                "vuln-authz.txt", "vuln-ssrf.txt"]
EXPLOIT_PROMPTS = ["injection-exploit.txt", "xss-exploit.txt", "auth-exploit.txt",
                   "authz-exploit.txt", "ssrf-exploit.txt"]


def test_vuln_prompts_have_style_and_new_fields():
    for name in VULN_PROMPTS:
        text = (PROMPTS / name).read_text()
        assert "@include(shared/_report-style.txt)" in text, name
        for field in ("severity", "impact", "remediation", "cwe_id"):
            assert field in text, f"{name}: {field}"


def test_exploit_prompts_have_language_and_style():
    for name in EXPLOIT_PROMPTS + ["attack-chain.txt"]:
        text = (PROMPTS / name).read_text()
        assert "@include(shared/_output-language.txt)" in text, name
        assert "@include(shared/_report-style.txt)" in text, name


def test_style_include_exists_both_langs():
    zh = (PROMPTS / "shared" / "_report-style.zh.txt").read_text()
    assert "结论先行" in zh and "不要使用全大写" in zh
    en = (PROMPTS / "shared" / "_report-style.en.txt").read_text()
    assert len(en) > 100


def test_no_deterministic_artifacts_include():  # 守双轨铁律
    for name in VULN_PROMPTS:
        text = (PROMPTS / name).read_text()
        for banned in ("parameter_graph", "SinkCallSite", "gitnexus_queue"):
            assert banned not in text, f"{name}: {banned}"
