from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1].parent.parent / "prompts"

VULN_PROMPTS = {
    "vuln-auth": ("# Authentication Analysis Report", "# 认证分析报告"),
    "vuln-authz": ("# Authorization Analysis Report", "# 授权分析报告"),
    "vuln-injection": ("#Injection Analysis Report (SQLi & Command Injection)",
                       "# 注入分析报告(SQLi 与命令注入)"),
    "vuln-ssrf": ("# SSRF Analysis Report", "# SSRF 分析报告"),
    "vuln-xss": ("# Cross-Site Scripting (XSS) Analysis Report", "# XSS 分析报告(跨站脚本)"),
}

SUBHEADINGS = [
    ("## 1. Executive Summary", "## 一、执行摘要"),
    ("## 2. Dominant Vulnerability Patterns", "## 二、主要漏洞模式"),
    ("## 3. Strategic Intelligence for Exploitation", "## 三、利用情报"),
]


def _read(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def test_each_vuln_prompt_references_shared_language_block():
    for name in VULN_PROMPTS:
        assert "@include(shared/_output-language.txt)" in _read(name), (
            f"{name}.txt 须 @include 共享语言约束块")


def test_main_headings_translated_to_chinese():
    for name, (en, cn) in VULN_PROMPTS.items():
        src = _read(name)
        assert en not in src, f"{name}.txt 仍含英文主标题: {en!r}"
        assert cn in src, f"{name}.txt 缺中文主标题: {cn!r}"


def test_common_subheadings_translated():
    for name in VULN_PROMPTS:
        src = _read(name)
        for en, cn in SUBHEADINGS:
            if en in src:  # 仅断言该 prompt 实际含此英文标题时已被替换
                assert cn in src, f"{name}.txt 子标题未中文化: {en!r} → {cn!r}"
