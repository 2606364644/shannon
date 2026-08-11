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
    # 不得残留硬编码 "(Section 4.2)" —— LIVE/OFFLINE 编号不一致(离线为 2.1),改按节名引用
    assert "(Section 4.2)" not in src and "Section 4.2" not in src, \
        "vuln-authz 不得硬编码 'Section 4.2'(离线对应节号为 2.1),改按 'Endpoint Security Context' 节名引用"


def test_title_schema_uses_chinese():
    """title schema 示例须中文化。

    漏洞标题(title)字段的语言曾是无人管辖的灰色地带——schema 示例全英文 +
    _output-language 只覆盖叙述文字/章节标题，导致 LLM 按 schema 英文示例产出
    英文标题(如 'Stored XSS via signup firstName into layout.html navbar')。
    修复:title schema 显式要求简体中文撰写。
    """
    for name in VULN_PROMPTS:
        src = _read(name)
        title_lines = [ln for ln in src.splitlines() if '"title":' in ln]
        assert title_lines, f"{name}.txt 缺 title schema 行"
        assert any("用简体中文撰写" in ln for ln in title_lines), (
            f"{name}.txt 的 title schema 须明确要求简体中文撰写")
        # 旧的纯英文 schema 描述须已移除
        assert not any("one-line descriptive name encoding" in ln for ln in title_lines), (
            f"{name}.txt 的 title schema 仍含旧英文描述 'one-line descriptive name encoding'")


def test_output_language_covers_title_field():
    """共享语言约束须显式覆盖 exploitation_queue 的 title 字段(标题语言 SSOT 兜底)。"""
    zh = (PROMPTS / "shared" / "_output-language.zh.txt").read_text(encoding="utf-8")
    assert "exploitation_queue" in zh, \
        "_output-language.zh.txt 须点名 exploitation_queue 的 title 字段"
    assert "title" in zh.lower(), "_output-language.zh.txt 须显式约束 title 字段语言"


def test_attack_chain_name_example_is_chinese():
    """attack-chain 的 name(攻击链标题)示例须中文化,避免英文 anchor 致产出英文链名。"""
    src = _read("attack-chain")
    name_lines = [ln for ln in src.splitlines() if '"name":' in ln]
    assert name_lines, "attack-chain.txt 缺 name schema 行"
    assert any("攻击链" in ln for ln in name_lines), \
        "attack-chain.txt 的 name 示例须中文化"
