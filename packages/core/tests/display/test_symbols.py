from shannon_core.display.symbols import (
    STEP_PENDING, STEP_DONE, STEP_FAIL,
    AGENT_START, AGENT_DONE, AGENT_FAIL,
    SUMMARY_OK, SUMMARY_FAIL,
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
