"""AZ-4 防回退（spec §3.3）：recon 不得用 ALL 符号掩盖 DELETE。

历史背景：docs/gap/authz-effect-gap-analysis.md AZ-4 曾记"recon 用 ALL
符号掩盖 DELETE"。当前 _endpoint-security-context.txt:11-14 已落地"禁止
ALL / 逐方法列 5 动词"，recon.txt 经 @include 注入；recon-static.txt 用
自己的 Section 2.1 + Method 列内联等价机制。本测试锁定两者不退化。
"""
from pathlib import Path

# parents[4] = repo root（同 test_static_dataflow_hints_decoupling.py 范式）
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"
ESC = PROMPTS_DIR / "shared" / "_endpoint-security-context.txt"


def test_endpoint_security_context_forbids_all_shorthand():
    """partial 必须显式禁止 ALL（带引号形式）且逐方法列全 5 动词。"""
    text = ESC.read_text()
    assert "Do NOT use" in text
    assert '"ALL"' in text          # prompt 实际：Do NOT use "ALL" shorthand
    assert "shorthand" in text
    assert "List each method explicitly" in text
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert verb in text, f"_endpoint-security-context.txt 缺少 HTTP 动词 {verb}"


def test_recon_online_includes_endpoint_security_context():
    """recon.txt（在线模式）必须 @include endpoint-security-context。"""
    content = (PROMPTS_DIR / "recon.txt").read_text()
    assert "@include(shared/_endpoint-security-context.txt)" in content


def test_recon_static_lists_methods_per_row():
    """recon-static.txt（离线模式）不 @include partial，但用自己的
    Section 2.1 + Method 列逐行列端点方法。锁这个内联机制不退化为 ALL 符号。"""
    text = (PROMPTS_DIR / "recon-static.txt").read_text()
    assert "Endpoint Security Context" in text   # Section 2.1 标题
    assert "| Method |" in text                   # 逐方法列表头（非 ALL）
