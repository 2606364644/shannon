"""Error classification — TWO functions with OPPOSITE fallback semantics.

classify_for_temporal:        fallback retryable=True (delegate to Temporal backoff)
is_retryable_for_display:     fallback retryable=False (fail-safe display)

Match order mirrors the original Shannon (TS) classifyErrorForTemporal exactly:
Billing -> Authentication -> Permission -> OutputValidation (before InvalidRequest)
-> InvalidRequest -> RequestTooLarge -> Configuration -> ExecutionLimit -> InvalidTarget
-> TRANSIENT fallback.
"""
from __future__ import annotations

from enum import StrEnum


class ErrorType(StrEnum):
    BILLING = "BillingError"
    RATE_LIMIT = "RateLimitError"
    AUTHENTICATION = "AuthenticationError"
    PERMISSION = "PermissionError"
    OUTPUT_VALIDATION = "OutputValidationError"
    INVALID_REQUEST = "InvalidRequestError"
    CONFIG = "ConfigurationError"
    EXECUTION_LIMIT = "ExecutionLimitError"
    INVALID_TARGET = "InvalidTargetError"
    TRANSIENT = "TransientError"


# (substring-pattern, type, retryable) — ORDER MATTERS. First match wins.
_TEMPORAL_PATTERNS: list[tuple[str, ErrorType, bool]] = [
    ("billing", ErrorType.BILLING, True),
    ("charge failed", ErrorType.BILLING, True),
    ("rate limit", ErrorType.RATE_LIMIT, True),
    ("429", ErrorType.RATE_LIMIT, True),
    ("401", ErrorType.AUTHENTICATION, False),
    ("unauthorized", ErrorType.AUTHENTICATION, False),
    ("403", ErrorType.PERMISSION, False),
    ("forbidden", ErrorType.PERMISSION, False),
    ("output validation", ErrorType.OUTPUT_VALIDATION, True),   # before 400/invalid request
    ("400", ErrorType.INVALID_REQUEST, False),
    ("invalid request", ErrorType.INVALID_REQUEST, False),
    ("413", ErrorType.INVALID_REQUEST, False),
    ("request too large", ErrorType.INVALID_REQUEST, False),
    ("ENOENT", ErrorType.CONFIG, False),
    ("config", ErrorType.CONFIG, False),
    ("max turns", ErrorType.EXECUTION_LIMIT, False),
    ("budget", ErrorType.EXECUTION_LIMIT, False),
    ("limit reached", ErrorType.EXECUTION_LIMIT, False),
]


def classify_for_temporal(error: Exception) -> tuple[ErrorType, bool]:
    """Classify an error for Temporal retry policy.

    Fallback: TRANSIENT + retryable=True (let Temporal backoff handle it).
    """
    msg = str(error).lower()
    for pattern, etype, retryable in _TEMPORAL_PATTERNS:
        if pattern.lower() in msg:
            return etype, retryable
    return ErrorType.TRANSIENT, True


# Display-only patterns. NOTE: the set of "retryable" and "non-retryable"
# keywords is curated independently; unknown errors default to NON-retryable
# (fail-safe), which is the OPPOSITE of classify_for_temporal's fallback.
_NON_RETRYABLE_KEYWORDS = (
    "401", "unauthorized", "403", "forbidden", "400", "invalid request",
    "413", "request too large", "ENOENT", "config", "max turns", "budget",
    "invalid prompt", "out of memory", "permission denied", "invalid api key",
)
_RETRYABLE_KEYWORDS = (
    "rate limit", "429", "timeout", "network", "ECONN", "billing",
    "transient", "502", "503", "504", "validation", "529",
)


def is_retryable_for_display(error: Exception) -> bool:
    """Display-only retry flag. Fallback: False (fail-safe).

    Semantics intentionally DIFFER from classify_for_temporal, which falls
    back to retryable=True. Do not merge these two functions.
    """
    msg = str(error).lower()
    for kw in _NON_RETRYABLE_KEYWORDS:
        if kw.lower() in msg:
            return False
    for kw in _RETRYABLE_KEYWORDS:
        if kw.lower() in msg:
            return True
    return False  # fail-safe default for unknown errors
