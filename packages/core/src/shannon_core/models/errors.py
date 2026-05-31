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
