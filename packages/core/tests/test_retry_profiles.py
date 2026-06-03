"""Tests for retry profile selection logic."""
from datetime import timedelta

from temporalio.common import RetryPolicy

from shannon_core.models.retry import (
    PRODUCTION_RETRY,
    TESTING_RETRY,
    SUBSCRIPTION_RETRY,
    get_retry_policy,
)


class TestGetRetryPolicy:
    def test_production_profile(self):
        policy = get_retry_policy("production")
        assert policy.maximum_attempts == 50
        assert policy.initial_interval == timedelta(minutes=5)
        assert policy.maximum_interval == timedelta(minutes=30)
        assert policy.backoff_coefficient == 2.0

    def test_testing_profile(self):
        policy = get_retry_policy("testing")
        assert policy.maximum_attempts == 5
        assert policy.initial_interval == timedelta(seconds=10)
        assert policy.maximum_interval == timedelta(seconds=30)

    def test_subscription_profile(self):
        policy = get_retry_policy("subscription")
        assert policy.maximum_attempts == 100
        assert policy.initial_interval == timedelta(minutes=5)
        assert policy.maximum_interval == timedelta(hours=6)

    def test_unknown_defaults_to_production(self):
        policy = get_retry_policy("unknown_mode")
        assert policy.maximum_attempts == 50

    def test_none_defaults_to_production(self):
        policy = get_retry_policy(None)
        assert policy.maximum_attempts == 50
