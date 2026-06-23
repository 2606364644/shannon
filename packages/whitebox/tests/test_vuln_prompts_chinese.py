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


def test_vuln_authz_has_framework_endpoint_guidance():
    """vuln-authz 必须含 finale-rest/epilogue 框架端点 IDOR 方法论(原始退化修复)。"""
    src = _read("vuln-authz")
    # 必须引导先读 recon 的 Endpoint Security Context(Section 4.2)
    assert "Endpoint Security Context" in src, \
        "vuln-authz 须引导读 recon 的 Endpoint Security Context"
    assert "recon_deliverable.md" in src, "须引用 recon deliverable 路径"
    # 必须含框架端点方法论关键词
    assert "finale-rest" in src, "须含 finale-rest 框架端点指引"
    assert "epilogue" in src, "须含 epilogue 框架端点指引"
    # 必须要求在 finding 里记录 framework_origin
    assert "framework_origin" in src, "须要求 finding 记录 framework_origin 字段"
    # 必须提示自动生成端点默认缺 ownership validation → assume vulnerable
    assert "ownership validation" in src.lower() or "ownership check" in src.lower(), \
        "须提示框架端点默认缺 ownership validation"
