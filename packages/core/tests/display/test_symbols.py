from shannon_core.display.symbols import (
    STEP_PENDING, STEP_DONE, STEP_FAIL,
    AGENT_START, AGENT_DONE, AGENT_FAIL,
    SUMMARY_OK, SUMMARY_FAIL,
    AUDIT_COMPLETE_OK,
    AUDIT_COMPLETE_FAIL,
)


def test_step_symbols():
    assert STEP_PENDING == "○"
    assert STEP_DONE == "✓"
    assert STEP_FAIL == "✗"


def test_agent_symbols():
    assert AGENT_START == "▶"
    assert AGENT_DONE == "✓"
    assert AGENT_FAIL == "✗"


def test_summary_symbols():
    assert SUMMARY_OK == "✓"
    assert SUMMARY_FAIL == "✗"


def test_audit_complete_symbol():
    # 终局装饰（非 STEP/AGENT 状态符号族），出现在扫描收官 Panel 行首：成功 🎉 / 失败 💥
    assert AUDIT_COMPLETE_OK == "🎉"
    assert AUDIT_COMPLETE_FAIL == "💥"
