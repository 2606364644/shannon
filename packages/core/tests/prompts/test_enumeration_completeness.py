# packages/core/tests/prompts/test_enumeration_completeness.py
from pathlib import Path

# prompts is at repo root ( /root/supernova/prompts ), tests are at packages/core/tests/
# File is at: /root/supernova/packages/core/tests/prompts/test_*.py
# parents[0] = /root/supernova/packages/core/tests/prompts/
# parents[1] = /root/supernova/packages/core/tests/
# parents[2] = /root/supernova/packages/core/
# parents[3] = /root/supernova/packages/
# parents[4] = /root/supernova/
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def test_enumeration_completeness_has_not_applicable_delta():
    """EC-B delta 分类必须含 not-applicable（白盒纯静态适配）。"""
    content = (PROMPTS_DIR / "shared" / "_enumeration-completeness.txt").read_text("utf-8")
    assert "`not-applicable`" in content, (
        "EC-B delta 分类缺 not-applicable——白盒纯静态下 Angle 4/5 无从分类"
    )
    # 必须要求 grep 零结果证据（防 LLM 偷懒标 N/A）
    assert "grep" in content.lower() or "zero" in content.lower() or "no match" in content.lower(), (
        "not-applicable 必须附证据（grep 零结果），防偷懒"
    )


def test_enumeration_completeness_keeps_original_three_deltas():
    """原 3 类 delta 必须保留（向后兼容）。"""
    content = (PROMPTS_DIR / "shared" / "_enumeration-completeness.txt").read_text("utf-8")
    for cls in ("`dedup`", "`out-of-scope`", "`true-miss`"):
        assert cls in content, f"原 delta 分类 {cls} 丢失"
