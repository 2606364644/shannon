# Auth Parity: Align Python Refactoring with TypeScript Original

**Date**: 2026-06-03
**Status**: Approved
**Scope**: Authentication/verification gap analysis and remediation between `/root/shannon` (TS) and `/root/shannon-py` (Python)

---

## Background

The Python refactoring of Shannon (`shannon-py`) diverged from the TypeScript original (`shannon`) in several authentication/verification areas. This spec documents the 7 actionable gaps (3 items are either already superior or are Python-idiomatic choices) and the remediation plan to achieve full parity.

### Items NOT changed (justified)

| Item | Reason |
|------|--------|
| **Billing detection** (#4) | Python version is already superior (pre-compiled regex + more patterns) |
| **Result pattern** (#7) | Python uses dataclass + exceptions — idiomatic Python, functionally equivalent |
| **DI container** (#8) | Python uses direct construction in activities — simpler and sufficient for Temporal's per-call isolation |

---

## Gap 1: TOTP Generator CLI Tool

**Severity**: HIGH — prompt depends on this tool; runtime will fail without it

**TS reference**: `apps/worker/src/scripts/generate-totp.ts` (163 lines)

### Design

Create `packages/core/scripts/generate_totp.py` — a standalone CLI tool implementing RFC 6238 TOTP generation using only Python standard library.

#### Functions

```python
def base32_decode(encoded: str) -> bytes
    # Manual base32 decoding (no external deps)
    # Validate input matches /^[A-Z2-7]+=*$/i

def generate_hotp(secret: str, counter: int, digits: int = 6) -> str
    # HMAC-SHA1 based HOTP (RFC 4226)
    # Dynamic truncation algorithm

def generate_totp(secret: str, time_step: int = 30, digits: int = 6) -> str
    # TOTP = HOTP with time-based counter (RFC 6238)

def main() -> None
    # CLI entry point: parse --secret argument
    # Output JSON to stdout: {"status":"success","totpCode":"123456","expiresIn":<sec>}
    # Error output: {"status":"error","message":"...","retryable":false}
```

#### Registration

Register as console script in `pyproject.toml`:
```toml
[project.scripts]
generate-totp = "shannon_core.scripts.generate_totp:main"
```

#### Contract

- Same CLI interface: `generate-totp --secret JBSWY3DPEHPK3PXP`
- Same JSON output format
- Same exit codes (0 success, 1 error)
- Same 30-second time step, 6-digit output

---

## Gap 2: Error Classification for Temporal Retries

**Severity**: HIGH — permanent errors get retried unnecessarily

**TS reference**: `apps/worker/src/services/error-handling.ts` (248 lines)

### Design

Extend `packages/core/src/shannon_core/models/errors.py` with two classification functions.

#### `is_retryable_error(error: Exception) -> bool`

String-pattern based quick classification. Two-tier check:
1. Check `NON_RETRYABLE_PATTERNS` first (authentication, invalid prompt, permission denied, invalid api key, ...)
2. If no match, check `RETRYABLE_PATTERNS` (network, connection, timeout, rate limit, 429, server error, max turns, ...)
3. Default: not retryable (fail-safe)

#### `classify_error_for_temporal(error: Exception) -> tuple[str, bool]`

Two-level classification returning `(error_type, retryable)`:

**Level 1 — ErrorCode-based** (for `PentestError` with `error_code` set):
- `AUTH_FAILED` → `("AuthenticationError", False)`
- `AUTH_LOGIN_FAILED` → `("AuthLoginFailedError", False)`
- `BILLING_ERROR` / `SPENDING_CAP_REACHED` / `INSUFFICIENT_CREDITS` → `("BillingError", True)`
- `API_RATE_LIMITED` → `("RateLimitError", True)`
- `CONFIG_NOT_FOUND` / `CONFIG_VALIDATION_FAILED` / `CONFIG_PARSE_ERROR` / `PROMPT_LOAD_FAILED` → `("ConfigurationError", False)`
- `GIT_CHECKPOINT_FAILED` / `GIT_ROLLBACK_FAILED` → `("GitError", False)`
- `OUTPUT_VALIDATION_FAILED` / `DELIVERABLE_NOT_FOUND` → `("OutputValidationError", True)`
- `AGENT_EXECUTION_FAILED` → `("AgentExecutionError", error.retryable)`
- `REPO_NOT_FOUND` → `("ConfigurationError", False)`
- `TARGET_UNREACHABLE` → `("InvalidTargetError", False)`
- Default → `("UnknownError", error.retryable)`

**Level 2 — String pattern fallback** (for external/SDK errors):
- Billing patterns (API + text) → `("BillingError", True)`
- Auth patterns (authentication, api key, 401) → `("AuthenticationError", False)`
- Permission (403, forbidden) → `("PermissionError", False)`
- Output validation → `("OutputValidationError", True)`
- Invalid request (400, malformed) → `("InvalidRequestError", False)`
- Request too large (413) → `("RequestTooLargeError", False)`
- Config (enoent, no such file) → `("ConfigurationError", False)`
- Execution limits (max turns, budget) → `("ExecutionLimitError", False)`
- Invalid URL → `("InvalidTargetError", False)`
- Default → `("TransientError", True)`

#### Error type constants

```python
# Canonical error type names (match TS version for consistency)
NON_RETRYABLE_TYPES = frozenset({
    "AuthenticationError", "AuthLoginFailedError", "PermissionError",
    "ConfigurationError", "InvalidRequestError", "RequestTooLargeError",
    "ExecutionLimitError", "InvalidTargetError", "GitError",
})
```

---

## Gap 3: Session-Level Concurrency Control

**Severity**: LOW — architecture difference, limited practical impact

**TS reference**: `apps/worker/src/utils/concurrency.ts` (61 lines)

### Design

Create `packages/core/src/shannon_core/utils/concurrency.py`.

```python
class SessionMutex:
    """Per-sessionId async mutex with FIFO queue semantics."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def lock(self, session_id: str) -> Callable[[], None]:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        await self._locks[session_id].acquire()
        return self._locks[session_id].release
```

Usage: wrap critical sections that modify shared session state (e.g., metrics writes, auth-state updates).

---

## Gap 5: Temporal Retry Configuration

**Severity**: HIGH — linked with Gap 2

**TS reference**: `apps/worker/src/temporal/workflows.ts`

### Design

#### Retry policy presets

Define in `packages/core/src/shannon_core/models/retry.py` (shared across whitebox/blackbox workflows):

```python
from shannon_core.models.errors import NON_RETRYABLE_TYPES

# Non-retryable error types for all policies
NON_RETRYABLE = sorted(NON_RETRYABLE_TYPES)

PREFLIGHT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

AUTH_VALIDATION_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

PRODUCTION_RETRY = RetryPolicy(
    maximum_attempts=50,
    initial_interval=timedelta(minutes=5),
    maximum_interval=timedelta(minutes=30),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

TESTING_RETRY = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

SUBSCRIPTION_RETRY = RetryPolicy(
    maximum_attempts=100,
    initial_interval=timedelta(minutes=5),
    maximum_interval=timedelta(hours=6),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)
```

#### Workflow changes

**Whitebox** (`workflows.py`):
- preflight: add `retry_policy=PREFLIGHT_RETRY`
- credential_check: add `retry_policy=PREFLIGHT_RETRY`
- auth_validation: `start_to_close_timeout=timedelta(minutes=10)` + `retry_policy=AUTH_VALIDATION_RETRY`
- pre_recon: add `non_retryable_error_types=NON_RETRYABLE` to existing policy
- vuln agents: add `non_retryable_error_types=NON_RETRYABLE` to existing policy

**Blackbox** (`workflows.py`):
- preflight: add `retry_policy=PREFLIGHT_RETRY`
- auth_validation: `start_to_close_timeout=timedelta(minutes=10)` + `retry_policy=AUTH_VALIDATION_RETRY`
- recon/exploit/report: add `non_retryable_error_types=NON_RETRYABLE` to existing policies

#### Activity error wrapping

In activity functions, wrap `PentestError` with `ApplicationFailure` using `classify_error_for_temporal()`:

```python
from temporalio.exceptions import ApplicationFailure
from shannon_core.models.errors import classify_error_for_temporal

try:
    result = await validate_authentication(...)
except PentestError as e:
    error_type, retryable = classify_error_for_temporal(e)
    raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

---

## Gap 6: Configuration Sanitization

**Severity**: MEDIUM — whitespace/case can cause matching failures

**TS reference**: `apps/worker/src/config-parser.ts` — `sanitizeAuthentication()`

### Design

Add `_sanitize_authentication()` to `packages/core/src/shannon_core/config/parser.py`.

```python
def _sanitize_authentication(auth: Authentication) -> Authentication:
    """Normalize auth fields: strip whitespace, lowercase enums."""
    email_login = None
    if auth.credentials.email_login:
        email_login = EmailLogin(
            address=auth.credentials.email_login.address.strip(),
            password=auth.credentials.email_login.password.strip(),
            totp_secret=auth.credentials.email_login.totp_secret.strip() if auth.credentials.email_login.totp_secret else None,
        )
    return Authentication(
        login_type=auth.login_type.strip().lower(),
        login_url=auth.login_url.strip(),
        credentials=Credentials(
            username=auth.credentials.username.strip(),
            password=auth.credentials.password.strip() if auth.credentials.password else None,
            totp_secret=auth.credentials.totp_secret.strip() if auth.credentials.totp_secret else None,
            email_login=email_login,
        ),
        login_flow=[s.strip() for s in auth.login_flow] if auth.login_flow else None,
        success_condition=SuccessCondition(
            type=auth.success_condition.type.strip().lower(),
            value=auth.success_condition.value.strip(),
        ),
    )
```

Call site: in `parse_config()`, after `Config.model_validate(raw)` and before `_validate_config_security(config)`:

```python
    config = Config.model_validate(raw)
    if config.authentication:
        config = _replace_auth(config, _sanitize_authentication(config.authentication))
    _validate_config_security(config)
```

Also add `_sanitize_rule()` for rule fields (trim + lowercase on type).

---

## Gap 9: Auth Validation Timeout

**Severity**: MEDIUM — slow SSO/MFA flows may timeout

**TS reference**: 10 minutes start-to-close + 10 minutes heartbeat, 3 retries

### Design

This is addressed in Gap 5 above. Summary of changes:

- Whitebox `auth_validation`: `start_to_close_timeout` from `5min` → `10min`
- Blackbox `auth_validation`: `start_to_close_timeout` from `5min` → `10min`
- Both add `retry_policy=AUTH_VALIDATION_RETRY` (3 attempts, 10s-1min backoff)
- Worst case: 10min × 3 attempts = 30 minutes (matches TS behavior)

---

## File Change Summary

| File | Action | Lines (est.) |
|------|--------|-------------|
| `packages/core/scripts/generate_totp.py` | **New** | ~120 |
| `packages/core/src/shannon_core/models/errors.py` | **Modify** (add `classify_error_for_temporal`, `is_retryable_error`, pattern lists, `NON_RETRYABLE_TYPES`) | ~130 added |
| `packages/core/src/shannon_core/models/retry.py` | **New** (retry policy presets + NON_RETRYABLE list) | ~60 |
| `packages/core/src/shannon_core/utils/concurrency.py` | **New** | ~25 |
| `packages/core/src/shannon_core/config/parser.py` | **Modify** (add `_sanitize_authentication`) | ~35 added |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | **Modify** (retry policies, timeouts) | ~20 changed |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | **Modify** (retry policies, timeouts) | ~20 changed |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | **Modify** (error wrapping) | ~15 changed |
| `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` | **Modify** (error wrapping) | ~15 changed |
| `packages/core/pyproject.toml` | **Modify** (add console script) | ~2 added |

**Total**: ~440 lines of code changes across 10 files

---

## Testing

Each new function/module should have corresponding tests:

| Test File | Coverage |
|-----------|----------|
| `packages/core/tests/test_generate_totp.py` | Base32 decode, HOTP/TOTP generation, CLI interface, error handling |
| `packages/core/tests/test_error_classification.py` | All ErrorCode mappings, string pattern fallback, edge cases |
| `packages/core/tests/test_concurrency.py` | SessionMutex lock/unlock, FIFO ordering, concurrent access |
| `packages/core/tests/test_config.py` (extend) | Sanitize normalization (whitespace, case, None handling) |
| `packages/core/tests/test_security.py` (extend) | Verify sanitize + security validation interaction |
