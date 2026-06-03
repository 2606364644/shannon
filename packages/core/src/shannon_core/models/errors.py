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
    re.compile(r"max turns", re.IGNORECASE),
    re.compile(r"budget", re.IGNORECASE),
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


NON_RETRYABLE_TYPES = frozenset({
    "AuthenticationError", "AuthLoginFailedError", "PermissionError",
    "ConfigurationError", "InvalidRequestError", "RequestTooLargeError",
    "ExecutionLimitError", "InvalidTargetError", "GitError",
})
