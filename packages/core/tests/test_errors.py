from shannon_core.models.errors import ErrorCode, PentestError

def test_error_code_values():
    assert ErrorCode.CONFIG_NOT_FOUND == "CONFIG_NOT_FOUND"
    assert ErrorCode.AGENT_EXECUTION_FAILED == "AGENT_EXECUTION_FAILED"
    assert ErrorCode.API_RATE_LIMITED == "API_RATE_LIMITED"
    assert ErrorCode.SPENDING_CAP_REACHED == "SPENDING_CAP_REACHED"

def test_pentest_error_basic():
    err = PentestError("test error", "config")
    assert str(err) == "test error"
    assert err.category == "config"
    assert err.retryable is False
    assert err.error_code is None
    assert err.context == {}

def test_pentest_error_full():
    err = PentestError(
        "rate limited",
        "billing",
        retryable=True,
        error_code=ErrorCode.API_RATE_LIMITED,
        context={"agent": "injection-vuln"},
    )
    assert err.retryable is True
    assert err.error_code == ErrorCode.API_RATE_LIMITED
    assert err.context["agent"] == "injection-vuln"

def test_pentest_error_is_exception():
    err = PentestError("fail", "validation")
    assert isinstance(err, Exception)

def test_all_error_codes_exist():
    expected = [
        "CONFIG_NOT_FOUND", "CONFIG_VALIDATION_FAILED", "CONFIG_PARSE_ERROR",
        "AGENT_EXECUTION_FAILED", "OUTPUT_VALIDATION_FAILED",
        "API_RATE_LIMITED", "SPENDING_CAP_REACHED", "INSUFFICIENT_CREDITS",
        "GIT_CHECKPOINT_FAILED", "GIT_ROLLBACK_FAILED",
        "PROMPT_LOAD_FAILED", "DELIVERABLE_NOT_FOUND",
        "REPO_NOT_FOUND", "TARGET_UNREACHABLE",
        "AUTH_FAILED", "AUTH_LOGIN_FAILED", "BILLING_ERROR",
    ]
    for name in expected:
        assert hasattr(ErrorCode, name), f"Missing ErrorCode.{name}"
