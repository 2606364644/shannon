from shannon_core.utils.billing import is_spending_cap_behavior

def test_spending_cap_detected():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="I've reached my spending cap")

def test_spending_cap_zero_turns_zero_cost():
    assert is_spending_cap_behavior(turns=0, cost=0.0, text="spending limit reached")

def test_normal_execution_not_cap():
    assert not is_spending_cap_behavior(turns=50, cost=2.50, text="Found vulnerability in login")

def test_normal_execution_low_turns():
    assert not is_spending_cap_behavior(turns=1, cost=0.01, text="Analysis complete")

def test_spending_cap_keywords():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="budget exceeded")
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="credit limit")
