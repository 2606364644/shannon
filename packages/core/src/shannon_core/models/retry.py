from datetime import timedelta

from temporalio.common import RetryPolicy

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
