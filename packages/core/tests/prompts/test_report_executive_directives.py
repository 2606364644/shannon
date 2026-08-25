# packages/core/tests/prompts/test_report_executive_directives.py
# 注: plan Step 1 原文为 parents[3](= packages/,其下无 prompts/),按同目录既有测试
# (test_report_executive_i18n.py 等)的约定修正为 parents[4](= repo root),其余逐字。
from pathlib import Path

TEXT = (Path(__file__).parents[4] / "prompts" / "report-executive.txt").read_text()

def test_summary_structure_directives():
    assert "漏洞速查表" in TEXT
    assert "禁止删除" in TEXT or "不得删除" in TEXT
    assert "修复路线" in TEXT and "P0" in TEXT and "P1" in TEXT
    for banned in ("CTOs, CISOs", "单点卡片"):
        assert banned not in TEXT, banned
