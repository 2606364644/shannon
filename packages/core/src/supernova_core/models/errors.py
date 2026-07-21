import re
from enum import Enum

class ErrorCode(str, Enum):
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_VALIDATION_FAILED = "CONFIG_VALIDATION_FAILED"
    CONFIG_PARSE_ERROR = "CONFIG_PARSE_ERROR"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    API_RATE_LIMITED = "API_RATE_LIMITED"
    SPENDING_CAP_REACHED = "SPENDING_CAP_REACHED"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    GIT_CHECKPOINT_FAILED = "GIT_CHECKPOINT_FAILED"
    GIT_ROLLBACK_FAILED = "GIT_ROLLBACK_FAILED"
    PROMPT_LOAD_FAILED = "PROMPT_LOAD_FAILED"
    DELIVERABLE_NOT_FOUND = "DELIVERABLE_NOT_FOUND"
    REPO_NOT_FOUND = "REPO_NOT_FOUND"
    TARGET_UNREACHABLE = "TARGET_UNREACHABLE"
    AUTH_FAILED = "AUTH_FAILED"
    AUTH_LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    BILLING_ERROR = "BILLING_ERROR"
    CODE_INDEX_FAILED = "CODE_INDEX_FAILED"
    BROWSER_ENGINE_UNAVAILABLE = "BROWSER_ENGINE_UNAVAILABLE"

PentestErrorType = str

class PentestError(Exception):
    def __init__(
        self,
        message: str,
        category: PentestErrorType,
        retryable: bool = False,
        error_code: ErrorCode | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.retryable = retryable
        self.error_code = error_code
        self.context = context or {}


# ---------------------------------------------------------------------------
# String-pattern based error classification for temporal retries
# ---------------------------------------------------------------------------

NON_RETRYABLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"authentication", re.IGNORECASE),
    re.compile(r"invalid prompt", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"invalid api key", re.IGNORECASE),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
    re.compile(r"not found", re.IGNORECASE),
    re.compile(r"invalid request", re.IGNORECASE),
    re.compile(r"malformed", re.IGNORECASE),
    re.compile(r"enoent", re.IGNORECASE),
    re.compile(r"no such file", re.IGNORECASE),
    re.compile(r"config", re.IGNORECASE),
    re.compile(r"max turns", re.IGNORECASE),
    re.compile(r"budget", re.IGNORECASE),
]

RETRYABLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"network", re.IGNORECASE),
    re.compile(r"connection", re.IGNORECASE),
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"server error", re.IGNORECASE),
    re.compile(r"500", re.IGNORECASE),
    re.compile(r"502", re.IGNORECASE),
    re.compile(r"503", re.IGNORECASE),
]


def is_retryable_error(error: Exception) -> bool:
    """Quick string-pattern based classification.

    Checks NON_RETRYABLE_PATTERNS first, then RETRYABLE_PATTERNS.
    Defaults to *not* retryable (fail-safe).
    """
    text = str(error).lower()
    for pattern in NON_RETRYABLE_PATTERNS:
        if pattern.search(text):
            return False
    for pattern in RETRYABLE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def classify_error_for_temporal(error: Exception) -> tuple[str, bool]:
    """Two-level classification returning ``(error_type, retryable)``.

    Level 1 uses the ``ErrorCode`` on a ``PentestError`` (if present).
    Level 2 falls back to string-pattern matching for external/SDK errors.
    """

    # -- Level 1: ErrorCode-based classification --------------------------------
    if isinstance(error, PentestError) and error.error_code is not None:
        code = error.error_code
        if code == ErrorCode.AUTH_FAILED:
            return ("AuthenticationError", False)
        if code == ErrorCode.AUTH_LOGIN_FAILED:
            return ("AuthLoginFailedError", False)
        if code in (
            ErrorCode.BILLING_ERROR,
            ErrorCode.SPENDING_CAP_REACHED,
            ErrorCode.INSUFFICIENT_CREDITS,
        ):
            return ("BillingError", True)
        if code == ErrorCode.API_RATE_LIMITED:
            return ("RateLimitError", True)
        if code in (
            ErrorCode.CONFIG_NOT_FOUND,
            ErrorCode.CONFIG_VALIDATION_FAILED,
            ErrorCode.CONFIG_PARSE_ERROR,
            ErrorCode.PROMPT_LOAD_FAILED,
            ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
        ):
            return ("ConfigurationError", False)
        if code in (ErrorCode.GIT_CHECKPOINT_FAILED, ErrorCode.GIT_ROLLBACK_FAILED):
            return ("GitError", False)
        if code in (ErrorCode.OUTPUT_VALIDATION_FAILED, ErrorCode.DELIVERABLE_NOT_FOUND):
            return ("OutputValidationError", True)
        if code == ErrorCode.AGENT_EXECUTION_FAILED:
            return ("AgentExecutionError", error.retryable)
        if code == ErrorCode.REPO_NOT_FOUND:
            return ("ConfigurationError", False)
        if code == ErrorCode.TARGET_UNREACHABLE:
            return ("InvalidTargetError", False)
        # Default for known-but-unmapped ErrorCode (includes CODE_INDEX_FAILED)
        return ("UnknownError", error.retryable)

    # -- Level 2: String pattern fallback --------------------------------------
    # 确定性编程错误优先判 non-retryable:renderer 对 str 调 .get / 对 str 迭代
    # (LLM 违规把 object 填成 str)这类,同输入必同崩,重试只被放大成数小时卡死
    # (2026-07-20 sentinel_dashboard recon attempt 1 'str' object has no
    # attribute .get 被 PRODUCTION_RETRY 重试 50×6min)。fail-fast,不耗满重试上限。
    if isinstance(error, (AttributeError, TypeError, KeyError)):
        return ("SchemaMismatchError", False)

    text = str(error).lower()

    # Billing patterns
    if "billing" in text or "spending cap" in text or "insufficient credit" in text:
        return ("BillingError", True)
    if "rate limit" in text:
        return ("BillingError", True)

    # Auth patterns
    if "authentication" in text or "api key" in text or "401" in text:
        return ("AuthenticationError", False)

    # Permission
    if "403" in text or "forbidden" in text:
        return ("PermissionError", False)

    # Output validation
    if "output validation" in text or "deliverable" in text:
        return ("OutputValidationError", True)

    # Pydantic / data-format validation — deterministic, retrying won't change input
    if "validation error" in text or "input should be" in text:
        return ("OutputValidationError", False)

    # Invalid request
    if "400" in text or "malformed" in text or "invalid request" in text:
        return ("InvalidRequestError", False)

    # Request too large
    if "413" in text:
        return ("RequestTooLargeError", False)

    # Config
    if "enoent" in text or "no such file" in text or "config" in text:
        return ("ConfigurationError", False)

    # Execution limits
    if "max turns" in text or "budget" in text:
        return ("ExecutionLimitError", False)

    # Invalid URL
    if "invalid url" in text:
        return ("InvalidTargetError", False)

    # Default
    return ("TransientError", True)


# 对齐 TS MAX_OUTPUT_VALIDATION_RETRIES (shannon/activities.ts:57):OUTPUT_VALIDATION_FAILED
# 用更短的独立重试上限,而非通用 VULN_RETRY(max 8)。大仓下 GLM 反复不吐 exploitation_queue
# 时,重试=再让模型吐一次 structured output,3 次不行基本就不行;吃满 8 次只白烧 ~5×20min。
# PY 此前漏移植这层 cap(只对齐了 createVulnValidator 的存在性校验,未对齐其 cap)。
OUTPUT_VALIDATION_RETRY_CAP = 3


def is_output_validation_retry_exhausted(error: Exception, attempt: int) -> bool:
    """OUTPUT_VALIDATION_FAILED 且 attempt >= cap → 已用尽独立上限,应停止重试(non_retryable)。

    在 activity catch 块中据此把 retryable 强制降为 False,对齐 TS activities.ts:226-232
    (attemptNumber >= MAX_OUTPUT_VALIDATION_RETRIES → ApplicationFailure.nonRetryable)。
    """
    return (
        isinstance(error, PentestError)
        and error.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED
        and attempt >= OUTPUT_VALIDATION_RETRY_CAP
    )


def classify_for_temporal_with_retry_cap(
    error: Exception, attempt: int
) -> tuple[str, bool]:
    """classify_error_for_temporal + OUTPUT_VALIDATION_FAILED 独立 cap 的组合入口。

    activity catch 块一行调用即可拿到考虑了 cap 的 ``(error_type, retryable)``:
    先 classify,若 retryable 且 OUTPUT_VALIDATION 已用尽独立上限(对齐 TS=3),
    则强制 ``retryable=False``(停止重试)。黑白盒 catch 块共用,避免各自内联 if。
    """
    error_type, retryable = classify_error_for_temporal(error)
    if retryable and is_output_validation_retry_exhausted(error, attempt):
        retryable = False
    return error_type, retryable


# Types that are ALWAYS non-retryable.
# For types that may or may not be retryable (AgentExecutionError, UnknownError),
# use the boolean returned by classify_error_for_temporal().
NON_RETRYABLE_TYPES = frozenset({
    "AuthenticationError", "AuthLoginFailedError", "PermissionError",
    "ConfigurationError", "InvalidRequestError", "RequestTooLargeError",
    "ExecutionLimitError", "InvalidTargetError", "GitError",
    "SchemaMismatchError",
})
