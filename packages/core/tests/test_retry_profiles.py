"""Tests for retry profile selection logic."""
from datetime import timedelta

from temporalio.common import RetryPolicy

from shannon_core.models.retry import (
    PRODUCTION_RETRY,
    TESTING_RETRY,
    SUBSCRIPTION_RETRY,
    PREFLIGHT_RETRY,
    AUTH_VALIDATION_RETRY,
    VULN_RETRY,
    get_retry_policy,
    retry_for,
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


class TestVulnRetry:
    def test_vuln_retry_params(self):
        assert VULN_RETRY.maximum_attempts == 5
        assert VULN_RETRY.initial_interval == timedelta(minutes=1)
        assert VULN_RETRY.maximum_interval == timedelta(minutes=5)
        assert VULN_RETRY.backoff_coefficient == 2.0
        assert VULN_RETRY.non_retryable_error_types  # 共享 NON_RETRYABLE


class TestRetryFor:
    def test_standard_delegates_to_get_retry_policy(self):
        assert retry_for("standard") == get_retry_policy(None)
        assert retry_for("standard", "production") == PRODUCTION_RETRY
        assert retry_for("standard", "testing") == TESTING_RETRY
        assert retry_for("standard", "subscription") == SUBSCRIPTION_RETRY

    def test_standard_default_is_production(self):
        assert retry_for("standard") == PRODUCTION_RETRY

    def test_vuln_category(self):
        assert retry_for("vuln") == VULN_RETRY

    def test_log_category(self):
        assert retry_for("log") == PREFLIGHT_RETRY

    def test_preflight_category(self):
        assert retry_for("preflight") == PREFLIGHT_RETRY

    def test_auth_validation_category(self):
        assert retry_for("auth-validation") == AUTH_VALIDATION_RETRY

    def test_unknown_category_raises(self):
        import pytest
        with pytest.raises(ValueError):
            retry_for("bogus")
