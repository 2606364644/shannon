import pytest

from shannon_core.errors.classification import ErrorType, classify_for_temporal


@pytest.mark.parametrize("message,expected_type,expected_retryable", [
    ("billing API charge failed", ErrorType.BILLING, True),
    ("rate limit exceeded, retry later", ErrorType.RATE_LIMIT, True),
    ("401 unauthorized", ErrorType.AUTHENTICATION, False),
    ("403 forbidden", ErrorType.PERMISSION, False),
    ("output validation failed", ErrorType.OUTPUT_VALIDATION, True),
    ("400 invalid request body", ErrorType.INVALID_REQUEST, False),
    ("ENOENT config not found", ErrorType.CONFIG, False),
    ("max turns limit reached", ErrorType.EXECUTION_LIMIT, False),
    ("budget exceeded", ErrorType.EXECUTION_LIMIT, False),
    ("some random transient glitch", ErrorType.TRANSIENT, True),
])
def test_classify_for_temporal_match_order(message, expected_type, expected_retryable):
    err = RuntimeError(message)
    etype, retryable = classify_for_temporal(err)
    assert etype == expected_type
    assert retryable == expected_retryable


def test_classify_for_temporal_fallback_is_retryable():
    # Unknown errors fall through to TRANSIENT (retryable) for Temporal backoff
    etype, retryable = classify_for_temporal(RuntimeError("totally unknown xyz"))
    assert etype == ErrorType.TRANSIENT
    assert retryable is True


def test_authz_validation_order_validation_before_invalid_request():
    # OUTPUT_VALIDATION must match before INVALID_REQUEST even if both keywords present
    etype, _ = classify_for_temporal(RuntimeError("output validation failed with 400"))
    assert etype == ErrorType.OUTPUT_VALIDATION


from shannon_core.errors.classification import is_retryable_for_display


def test_display_retryable_true_for_known_retryable():
    assert is_retryable_for_display(RuntimeError("rate limit exceeded")) is True
    assert is_retryable_for_display(RuntimeError("billing charge failed")) is True


def test_display_retryable_false_for_known_non_retryable():
    assert is_retryable_for_display(RuntimeError("401 unauthorized")) is False
    assert is_retryable_for_display(RuntimeError("ENOENT config not found")) is False


def test_display_retryable_fallback_is_false():
    # OPPOSITE of classify_for_temporal: unknown errors are NOT retryable (fail-safe)
    assert is_retryable_for_display(RuntimeError("totally unknown xyz")) is False


def test_two_functions_agree_on_known_types_but_differ_on_unknown():
    from shannon_core.errors.classification import classify_for_temporal
    known = RuntimeError("rate limit exceeded")
    # Agree on known retryable
    assert classify_for_temporal(known)[1] is True
    assert is_retryable_for_display(known) is True
    # Differ on unknown
    unknown = RuntimeError("totally unknown glitch")
    assert classify_for_temporal(unknown)[1] is True   # Temporal: retry
    assert is_retryable_for_display(unknown) is False  # Display: fail-safe


def test_output_validation_consistent_between_functions():
    """Known retryable types must agree between both functions (only UNKNOWN errors differ)."""
    from shannon_core.errors.classification import classify_for_temporal
    for msg in ["output validation failed", "billing charge failed", "rate limit exceeded"]:
        err = RuntimeError(msg)
        _, classify_retryable = classify_for_temporal(err)
        display_retryable = is_retryable_for_display(err)
        assert classify_retryable == display_retryable, (
            f"{msg!r}: classify={classify_retryable} but display={display_retryable}"
        )


def test_display_retryable_true_for_529_overload():
    """529（GLM 模型服务端过载）应显示 retryable —— 修复 is_retryable_for_display 漏 529 的 bug。

    真实错误 message 形如 'SDK result failure: subtype=success, api_error_status=529'。
    """
    assert is_retryable_for_display(
        RuntimeError("SDK result failure: subtype=success, api_error_status=529")
    ) is True


def test_classify_for_temporal_529_still_transient():
    """529 不在 _TEMPORAL_PATTERNS，fallback 到 TRANSIENT(retryable=True)。

    回归保护：确保补 is_retryable_for_display 关键词不误伤 classify_for_temporal 的 error_type。
    """
    etype, retryable = classify_for_temporal(RuntimeError("api_error_status=529"))
    assert etype == ErrorType.TRANSIENT
    assert retryable is True
