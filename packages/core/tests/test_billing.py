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

def test_billing_error_pattern():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="billing_error: limit reached")

def test_credit_balance_too_low():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="credit balance is too low")

def test_insufficient_credits():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="insufficient credits")

def test_usage_blocked_insufficient_credits():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="usage is blocked due to insufficient credits")

def test_please_visit_plans_and_billing():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="please visit plans & billing")

def test_please_visit_plans_and_billing_no_ampersand():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="please visit plans and billing")

def test_usage_limit_reached():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="usage limit reached")

def test_quota_exceeded():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="quota exceeded")

def test_daily_rate_limit():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="daily rate limit exceeded")

def test_limit_will_reset():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="limit will reset at midnight")

def test_billing_limit_reached():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="billing limit reached")

def test_cap_reached():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="cap reached for this period")

def test_monthly_limit():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="monthly limit exceeded")
