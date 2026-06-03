"""Tests for error classification functions: is_retryable_error and classify_error_for_temporal."""

import pytest

from shannon_core.models.errors import (
    NON_RETRYABLE_PATTERNS,
    NON_RETRYABLE_TYPES,
    RETRYABLE_PATTERNS,
    ErrorCode,
    PentestError,
    classify_error_for_temporal,
    is_retryable_error,
)


# ============================================================================
# is_retryable_error — non-retryable patterns
# ============================================================================

class TestIsRetryableErrorNonRetryable:
    """Every pattern in NON_RETRYABLE_PATTERNS should classify as not retryable."""

    @pytest.mark.parametrize(
        "message",
        [
            "Authentication failed",
            "invalid prompt template",
            "Permission denied for resource",
            "invalid api key provided",
            "Unauthorized access",
            "Forbidden operation",
            "File not found in path",
            "invalid request body",
            "Malformed JSON payload",
            "ENOENT: no such file or directory",
            "no such file or directory",
            "config value is missing",
            "max turns reached",
            "budget exceeded",
        ],
    )
    def test_non_retryable_patterns(self, message):
        err = Exception(message)
        assert is_retryable_error(err) is False

    def test_non_retryable_precedence_over_retryable(self):
        """If a message matches both non-retryable and retryable, non-retryable wins."""
        # "authentication" is non-retryable, "timeout" is retryable
        err = Exception("authentication timeout occurred")
        assert is_retryable_error(err) is False

    def test_non_retryable_case_insensitive(self):
        err = Exception("AUTHENTICATION FAILURE")
        assert is_retryable_error(err) is False


class TestIsRetryableErrorRetryable:
    """Every pattern in RETRYABLE_PATTERNS should classify as retryable."""

    @pytest.mark.parametrize(
        "message",
        [
            "network error",
            "connection refused",
            "timeout after 30s",
            "rate limit exceeded",
            "HTTP 429 Too Many Requests",
            "server error occurred",
            "HTTP 500 Internal Server Error",
            "HTTP 502 Bad Gateway",
            "HTTP 503 Service Unavailable",
        ],
    )
    def test_retryable_patterns(self, message):
        err = Exception(message)
        assert is_retryable_error(err) is True


class TestIsRetryableErrorDefault:
    """Default (no pattern match) should be not retryable (fail-safe)."""

    def test_unknown_error_is_not_retryable(self):
        err = Exception("something completely unexpected")
        assert is_retryable_error(err) is False

    def test_empty_message_is_not_retryable(self):
        err = Exception("")
        assert is_retryable_error(err) is False


# ============================================================================
# classify_error_for_temporal — Level 1: ErrorCode-based
# ============================================================================

class TestClassifyErrorCodeBased:
    """Test every ErrorCode value maps to the correct (type, retryable)."""

    def test_auth_failed(self):
        err = PentestError("auth fail", "auth", error_code=ErrorCode.AUTH_FAILED)
        assert classify_error_for_temporal(err) == ("AuthenticationError", False)

    def test_auth_login_failed(self):
        err = PentestError("login fail", "auth", error_code=ErrorCode.AUTH_LOGIN_FAILED)
        assert classify_error_for_temporal(err) == ("AuthLoginFailedError", False)

    @pytest.mark.parametrize(
        "code",
        [ErrorCode.BILLING_ERROR, ErrorCode.SPENDING_CAP_REACHED, ErrorCode.INSUFFICIENT_CREDITS],
    )
    def test_billing_codes(self, code):
        err = PentestError("billing issue", "billing", error_code=code)
        assert classify_error_for_temporal(err) == ("BillingError", True)

    def test_api_rate_limited(self):
        err = PentestError("rate limited", "api", error_code=ErrorCode.API_RATE_LIMITED)
        assert classify_error_for_temporal(err) == ("RateLimitError", True)

    @pytest.mark.parametrize(
        "code",
        [
            ErrorCode.CONFIG_NOT_FOUND,
            ErrorCode.CONFIG_VALIDATION_FAILED,
            ErrorCode.CONFIG_PARSE_ERROR,
            ErrorCode.PROMPT_LOAD_FAILED,
        ],
    )
    def test_config_codes(self, code):
        err = PentestError("config error", "config", error_code=code)
        assert classify_error_for_temporal(err) == ("ConfigurationError", False)

    @pytest.mark.parametrize(
        "code",
        [ErrorCode.GIT_CHECKPOINT_FAILED, ErrorCode.GIT_ROLLBACK_FAILED],
    )
    def test_git_codes(self, code):
        err = PentestError("git failure", "git", error_code=code)
        assert classify_error_for_temporal(err) == ("GitError", False)

    @pytest.mark.parametrize(
        "code",
        [ErrorCode.OUTPUT_VALIDATION_FAILED, ErrorCode.DELIVERABLE_NOT_FOUND],
    )
    def test_output_validation_codes(self, code):
        err = PentestError("validation fail", "output", error_code=code)
        assert classify_error_for_temporal(err) == ("OutputValidationError", True)

    def test_agent_execution_failed_retryable(self):
        err = PentestError("agent fail", "agent", retryable=True, error_code=ErrorCode.AGENT_EXECUTION_FAILED)
        assert classify_error_for_temporal(err) == ("AgentExecutionError", True)

    def test_agent_execution_failed_not_retryable(self):
        err = PentestError("agent fail", "agent", retryable=False, error_code=ErrorCode.AGENT_EXECUTION_FAILED)
        assert classify_error_for_temporal(err) == ("AgentExecutionError", False)

    def test_repo_not_found(self):
        err = PentestError("repo missing", "config", error_code=ErrorCode.REPO_NOT_FOUND)
        assert classify_error_for_temporal(err) == ("ConfigurationError", False)

    def test_target_unreachable(self):
        err = PentestError("target unreachable", "target", error_code=ErrorCode.TARGET_UNREACHABLE)
        assert classify_error_for_temporal(err) == ("InvalidTargetError", False)

    def test_code_index_failed_defaults_to_unknown(self):
        """CODE_INDEX_FAILED is a valid ErrorCode but has no explicit mapping."""
        err = PentestError("index failed", "index", retryable=True, error_code=ErrorCode.CODE_INDEX_FAILED)
        assert classify_error_for_temporal(err) == ("UnknownError", True)

    def test_code_index_failed_not_retryable(self):
        err = PentestError("index failed", "index", retryable=False, error_code=ErrorCode.CODE_INDEX_FAILED)
        assert classify_error_for_temporal(err) == ("UnknownError", False)


# ============================================================================
# classify_error_for_temporal — Level 2: String pattern fallback
# ============================================================================

class TestClassifyStringFallback:
    """Test string-pattern fallback for plain Exception and PentestError without error_code."""

    # --- Billing ---

    @pytest.mark.parametrize(
        "message",
        ["billing error", "Spending cap exceeded", "Insufficient credits"],
    )
    def test_billing_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("BillingError", True)

    def test_rate_limit_string(self):
        err = Exception("rate limit exceeded")
        assert classify_error_for_temporal(err) == ("BillingError", True)

    # --- Auth ---

    @pytest.mark.parametrize(
        "message",
        ["authentication failed", "invalid api key", "HTTP 401 Unauthorized"],
    )
    def test_auth_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("AuthenticationError", False)

    # --- Permission ---

    @pytest.mark.parametrize(
        "message",
        ["HTTP 403 Forbidden", "forbidden access"],
    )
    def test_permission_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("PermissionError", False)

    # --- Output validation ---

    @pytest.mark.parametrize(
        "message",
        ["output validation failed", "deliverable not found"],
    )
    def test_output_validation_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("OutputValidationError", True)

    # --- Invalid request ---

    @pytest.mark.parametrize(
        "message",
        ["HTTP 400 Bad Request", "malformed request body", "invalid request parameter"],
    )
    def test_invalid_request_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("InvalidRequestError", False)

    # --- Request too large ---

    def test_request_too_large(self):
        err = Exception("HTTP 413 Payload Too Large")
        assert classify_error_for_temporal(err) == ("RequestTooLargeError", False)

    # --- Config ---

    @pytest.mark.parametrize(
        "message",
        ["ENOENT: no such file", "no such file or directory", "config error"],
    )
    def test_config_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("ConfigurationError", False)

    # --- Execution limits ---

    @pytest.mark.parametrize(
        "message",
        ["max turns reached", "budget exceeded"],
    )
    def test_execution_limit_patterns(self, message):
        err = Exception(message)
        assert classify_error_for_temporal(err) == ("ExecutionLimitError", False)

    # --- Invalid URL ---

    def test_invalid_url(self):
        err = Exception("invalid URL format")
        assert classify_error_for_temporal(err) == ("InvalidTargetError", False)

    # --- Default ---

    def test_default_transient(self):
        err = Exception("something unexpected happened")
        assert classify_error_for_temporal(err) == ("TransientError", True)


# ============================================================================
# Edge cases
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_plain_exception_goes_to_string_fallback(self):
        """A plain Exception should not hit Level 1 (ErrorCode path)."""
        err = Exception("timeout waiting for response")
        assert classify_error_for_temporal(err) == ("TransientError", True)

    def test_pentest_error_without_error_code_uses_fallback(self):
        """PentestError without error_code should fall through to string matching."""
        err = PentestError("authentication failed", "auth")
        # Falls to Level 2 string matching
        assert classify_error_for_temporal(err) == ("AuthenticationError", False)

    def test_pentest_error_without_code_unknown_message(self):
        err = PentestError("some unknown message", "misc")
        assert classify_error_for_temporal(err) == ("TransientError", True)

    def test_pentest_error_with_none_error_code_uses_fallback(self):
        """PentestError with error_code=None should fall through to string matching."""
        err = PentestError("rate limit exceeded", "api", error_code=None)
        assert classify_error_for_temporal(err) == ("BillingError", True)

    def test_non_retryable_types_is_frozenset(self):
        assert isinstance(NON_RETRYABLE_TYPES, frozenset)

    def test_non_retryable_types_contents(self):
        expected = {
            "AuthenticationError", "AuthLoginFailedError", "PermissionError",
            "ConfigurationError", "InvalidRequestError", "RequestTooLargeError",
            "ExecutionLimitError", "InvalidTargetError", "GitError",
        }
        assert NON_RETRYABLE_TYPES == expected

    def test_patterns_are_compiled(self):
        """Verify pattern lists contain compiled regex patterns."""
        import re
        for pattern in NON_RETRYABLE_PATTERNS:
            assert isinstance(pattern, re.Pattern)
        for pattern in RETRYABLE_PATTERNS:
            assert isinstance(pattern, re.Pattern)

    def test_every_error_code_has_coverage(self):
        """Ensure every ErrorCode value is tested via Level 1 or handled."""
        # Verify all codes are either explicitly mapped or hit the default
        for code in ErrorCode:
            err = PentestError(f"test {code.value}", "test", retryable=False, error_code=code)
            error_type, retryable = classify_error_for_temporal(err)
            assert isinstance(error_type, str)
            assert isinstance(retryable, bool)

    def test_classify_returns_tuple(self):
        err = Exception("test")
        result = classify_error_for_temporal(err)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_is_retryable_with_pentest_error(self):
        """is_retryable_error works with PentestError too (uses str())."""
        err = PentestError("network timeout", "network")
        assert is_retryable_error(err) is True

    def test_is_retryable_with_pentest_error_non_retryable(self):
        err = PentestError("authentication failed", "auth")
        assert is_retryable_error(err) is False
