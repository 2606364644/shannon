"""Tests for retry profile selection logic."""
from datetime import timedelta

from temporalio.common import RetryPolicy

from supernova_core.models.retry import (
    PRODUCTION_RETRY,
    TESTING_RETRY,
    SUBSCRIPTION_RETRY,
    PREFLIGHT_RETRY,
    AUTH_VALIDATION_RETRY,
    VULN_RETRY,
    NON_RETRYABLE,
    get_retry_policy,
    retry_for,
)


class TestGetRetryPolicy:
    def test_production_profile(self):
        policy = get_retry_policy("production")
        assert policy.maximum_attempts == 3
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
        assert policy.maximum_attempts == 3

    def test_none_defaults_to_production(self):
        policy = get_retry_policy(None)
        assert policy.maximum_attempts == 3


class TestVulnRetry:
    def test_vuln_retry_params(self):
        assert VULN_RETRY.maximum_attempts == 3
        assert VULN_RETRY.initial_interval == timedelta(minutes=1)
        assert VULN_RETRY.maximum_interval == timedelta(minutes=5)
        assert VULN_RETRY.backoff_coefficient == 2.0
        assert VULN_RETRY.non_retryable_error_types == NON_RETRYABLE  # 共享 NON_RETRYABLE


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

    def test_code_index_category_uses_bounded_retry(self):
        """确定性 code_index 轨不能套 PRODUCTION_RETRY(max 50)。

        run_code_index 内部的 LLM sink discovery 对大仓会跑满 10 分钟
        start_to_close_timeout;超时是幂等的(同输入再跑照样超时),PRODUCTION_RETRY
        会把它放大成 50x ≈ 数小时的"卡死"(2026-06-30 juice-shop 实测)。
        确定性轨用短重试,给 transient 错误几次机会即可。
        """
        policy = retry_for("code-index")
        assert policy is not PRODUCTION_RETRY
        assert policy.maximum_attempts <= 3

    def test_poc_category_uses_bounded_retry(self):
        """PoC 报告增强是非关键路径(activity 内 try/except 吞异常),失败来源
        只有 start_to_close_timeout;超时幂等(同输入再跑照样超时),PRODUCTION_RETRY
        (max 50) 会放大成数小时卡死(2026-07-10 NodeGoat 实测:5 个
        externally_exploitable 串行 llm_fill_gap 各 max_turns=50,5min timeout
        反复重入"白盒 PoC: 5 个" 1h43m+,与 code_index 同构坑)。短重试给
        transient 几次机会即可。
        """
        policy = retry_for("poc")
        assert policy is not PRODUCTION_RETRY
        assert policy.maximum_attempts <= 3

    def test_unknown_category_raises(self):
        import pytest
        with pytest.raises(ValueError):
            retry_for("bogus")
