from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1].parent.parent / "prompts" / "report-executive.txt"


def test_report_prompt_uses_chinese_executive_summary_headings():
    src = PROMPT.read_text(encoding="utf-8")
    assert "# 安全评估报告" in src
    assert "## 执行摘要" in src
    assert "## 按漏洞类型汇总" in src
    # 废弃英文标题不应再作为"要生成"的标题出现
    assert "# Security Assessment Report" not in src
    assert "## Summary by Vulnerability Type" not in src


def test_report_prompt_chinese_field_labels():
    src = PROMPT.read_text(encoding="utf-8")
    # 范围 (scope) is driven by {{VULN_CLASSES_TESTED}}, a user-config placeholder
    # (manager.py reads config.vuln_classes), not deterministic-track coupling.
    # Task 4 误判删除；本 fix 已恢复。
    for label in ["目标:", "评估日期:", "范围:", "利用情况:"]:
        assert label in src, f"缺中文字段标签 {label}"
