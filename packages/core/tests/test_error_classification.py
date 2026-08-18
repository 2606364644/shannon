"""Tests for error classification functions: is_retryable_error and classify_error_for_temporal."""

import pytest

from supernova_core.models.errors import (
    NON_RETRYABLE_PATTERNS,
    NON_RETRYABLE_TYPES,
    RETRYABLE_PATTERNS,
    ErrorCode,
    PentestError,
    classify_error_for_temporal,
    classify_for_temporal_with_retry_cap,
    is_retryable_error,
    is_output_validation_retry_exhausted,
)


# ============================================================================
# is_output_validation_retry_exhausted — OUTPUT_VALIDATION_FAILED 独立 cap(对齐 TS=3)
# ============================================================================
class TestIsOutputValidationRetryExhausted:
    """对齐 TS MAX_OUTPUT_VALIDATION_RETRIES(shannon/activities.ts:57):OUTPUT_VALIDATION_FAILED
    用更短的独立重试上限(3),而非通用 VULN_RETRY(8)。大仓下 GLM 反复不吐 queue 时,
    重试=再让模型吐一次 structured output,3 次不行基本就不行,吃满 8 次只白烧 5×~20min。"""

    def test_exhausted_when_attempt_reaches_cap(self):
        err = PentestError("Missing exploitation queue", "validation",
                           error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        assert is_output_validation_retry_exhausted(err, attempt=3) is True

    def test_not_exhausted_before_cap(self):
        err = PentestError("Missing exploitation queue", "validation",
                           error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        assert is_output_validation_retry_exhausted(err, attempt=2) is False

    def test_other_error_code_not_affected(self):
        """非 OUTPUT_VALIDATION_FAILED 不受 cap 影响,走通用 VULN_RETRY。"""
        err = PentestError("agent execution failed", "agent",
                           error_code=ErrorCode.AGENT_EXECUTION_FAILED)
        assert is_output_validation_retry_exhausted(err, attempt=3) is False

    def test_non_pentest_error_not_affected(self):
        assert is_output_validation_retry_exhausted(RuntimeError("boom"), attempt=3) is False


# ============================================================================
# classify_for_temporal_with_retry_cap — classify + OUTPUT_VALIDATION cap(组合)
# ============================================================================
class TestClassifyForTemporalWithRetryCap:
    """classify_error_for_temporal + OUTPUT_VALIDATION_FAILED 独立 cap 的组合。

    activity catch 块的统一入口:catch 块一行调用即可拿到考虑了 cap 的 (type, retryable),
    无需在黑白盒各自内联 if 判断(core 共享,对齐 TS activities.ts:226-232 统一处理)。
    """

    def test_output_validation_exhausted_forces_non_retryable(self):
        err = PentestError("Missing exploitation queue", "validation",
                           error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        # classify 单独 → (OutputValidationError, True);cap 用尽(attempt=3)→ retryable 降 False
        assert classify_for_temporal_with_retry_cap(err, attempt=3) == ("OutputValidationError", False)

    def test_output_validation_before_cap_still_retryable(self):
        err = PentestError("Missing exploitation queue", "validation",
                           error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        assert classify_for_temporal_with_retry_cap(err, attempt=2) == ("OutputValidationError", True)

    def test_unrelated_non_retryable_error_unaffected(self):
        err = PentestError("repo missing", "validation", error_code=ErrorCode.REPO_NOT_FOUND)
        assert classify_for_temporal_with_retry_cap(err, attempt=5) == ("ConfigurationError", False)


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

    # --- Deterministic file-missing ("not found" + location) ---
    # tiering 迁移漏改曾致 fusion 抛 FileNotFoundError("code_index.json not found in
    # .../deliverables/whitebox")——消息路径含 "/deliverables/" → 命中上方 "deliverable"
    # 子串被误判 OutputValidationError retryable=True,白烧 3 次重试 ~15min。
    # 带位置的确定性文件缺失应 fail-fast(重试不改输入);裸 "deliverable not found"
    # (可能是并发产物未落盘)保持 retryable 语义。
    @pytest.mark.parametrize(
        "message",
        [
            "code_index.json not found in /app/workspaces/Brightli/scans/backend-20260818-091852/deliverables/whitebox",
            "git not found in PATH",
            "Prompt file not found: /app/prompts/recon.txt",
        ],
    )
    def test_file_missing_patterns(self, message):
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
            "SchemaMismatchError",
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


# ============================================================================
# classify_error_for_temporal — Pydantic ValidationError handling
# ============================================================================

def test_pydantic_validation_error_is_non_retryable():
    """Deterministic data-format errors must not trigger Temporal retries."""
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        x: int

    try:
        M.model_validate({"x": "not an int"})
    except ValidationError as exc:
        error_type, retryable = classify_error_for_temporal(exc)
        assert retryable is False
        assert error_type == "OutputValidationError"
        return
    raise AssertionError("ValidationError was not raised")


def test_input_should_be_text_is_non_retryable():
    """Raw pydantic error string surfaces non-retryable even without the exception type."""

    class FakeError(Exception):
        pass

    err = FakeError("1 validation error for VulnerabilityQueue\n  Input should be an object")
    error_type, retryable = classify_error_for_temporal(err)
    assert retryable is False
    assert error_type == "OutputValidationError"


# ============================================================================
# classify_error_for_temporal — 确定性编程错误(AttributeError/TypeError/KeyError)
# non-retryable(2026-07-20 sentinel_dashboard recon 崩溃止血)
# ============================================================================
class TestSchemaMismatchNonRetryable:
    """renderer 对 str 调 .get 等(LLM 违规把 object 填成 str)是确定性失败:
    同输入必同崩,重试只被 PRODUCTION_RETRY 放大成数小时卡死 -> non-retryable。"""

    def test_attribute_error_non_retryable(self):
        err = AttributeError("'str' object has no attribute 'get'")
        assert classify_error_for_temporal(err) == ("SchemaMismatchError", False)

    def test_type_error_non_retryable(self):
        err = TypeError("'str' object is not subscriptable")
        assert classify_error_for_temporal(err) == ("SchemaMismatchError", False)

    def test_key_error_non_retryable(self):
        err = KeyError("session_flow")
        assert classify_error_for_temporal(err) == ("SchemaMismatchError", False)

    def test_schema_mismatch_listed_non_retryable(self):
        assert "SchemaMismatchError" in NON_RETRYABLE_TYPES

    def test_with_retry_cap_keeps_non_retryable(self):
        """classify_for_temporal_with_retry_cap 不改变 non-retryable 判定。"""
        err = AttributeError("boom")
        assert classify_for_temporal_with_retry_cap(err, attempt=1) == ("SchemaMismatchError", False)
