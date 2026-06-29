from datetime import timedelta
from typing import Literal

from temporalio.common import RetryPolicy

from shannon_core.models.errors import NON_RETRYABLE_TYPES

# Non-retryable error types for all policies
NON_RETRYABLE = sorted(NON_RETRYABLE_TYPES)

# PREFLIGHT_RETRY and AUTH_VALIDATION_RETRY are intentionally decoupled
# for independent tuning despite currently sharing the same parameters.
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

# Presets for future use by testing and subscription pipelines.
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

# vuln agent 专用:per-vt fan-out 下封顶 ~20min,有意分歧于 TS PRODUCTION_RETRY。
# 详见 docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md §2.3
# 及 2026-06-28-llm-track-vuln-parity-restoration-design.md §4.3。
VULN_RETRY = RetryPolicy(
    maximum_attempts=8,
    initial_interval=timedelta(minutes=1),
    maximum_interval=timedelta(minutes=5),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

# code_index activity(确定性 GitNexus 轨)专用:短重试。
# run_code_index 内部的 LLM sink discovery 对大仓会跑满 start_to_close_timeout
# (10 分钟)超时;超时是幂等的(同输入再跑照样超时),绝不能套 PRODUCTION_RETRY
# (max 50)——那会把单次超时放大成 50x ≈ 数小时卡死(2026-06-30 juice-shop 实测:
# attempt 1/2/3 各 10m10s 超时,PRE_RECON 早已完成但 gather 等代码索引重试耗尽)。
# max 3:给 transient(MCP 连接抖动/IO)几次机会,但不放大幂等超时。
CODE_INDEX_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)


def get_retry_policy(mode: str | None = None) -> RetryPolicy:
    """Select a retry policy by mode name.

    Returns PRODUCTION_RETRY when *mode* is ``None`` or unrecognised.
    """
    profiles = {
        "production": PRODUCTION_RETRY,
        "testing": TESTING_RETRY,
        "subscription": SUBSCRIPTION_RETRY,
    }
    return profiles.get(mode or "production", PRODUCTION_RETRY)


Category = Literal["standard", "vuln", "log", "preflight", "auth-validation", "code-index"]


def retry_for(category: Category, mode: str | None = None) -> RetryPolicy:
    """按 activity 类别选 retry policy(单一映射源)。

    - standard: LLM agent + 确定性处理。委托 get_retry_policy(mode) 保留 mode 感知
      (testing/subscription);不传 mode 默认 production。
    - vuln:     per-vt vuln agent,有界 VULN_RETRY。
    - code-index: 确定性 code_index 轨,短 CODE_INDEX_RETRY(防幂等超时被放大)。
    - log:      phase log marker(10s 写),短 policy。
    - preflight / auth-validation: 现有短 tier。
    """
    if category == "standard":
        return get_retry_policy(mode)
    if category == "vuln":
        return VULN_RETRY
    if category == "code-index":
        return CODE_INDEX_RETRY
    if category == "log":
        return PREFLIGHT_RETRY
    if category == "preflight":
        return PREFLIGHT_RETRY
    if category == "auth-validation":
        return AUTH_VALIDATION_RETRY
    raise ValueError(f"unknown activity category: {category!r}")


def agent_retry_category(agent_name: str) -> Category:
    """Map an agent name to its retry-policy category (single source of truth).

    Mirrors workflows.py retry_for() calls: vuln agents (per-vt fan-out) → 'vuln'
    (VULN_RETRY, max 8); pre-recon/recon/report and others → 'standard'
    (PRODUCTION_RETRY, max 50). Used by the live display to resolve max_attempts.
    """
    if agent_name.endswith("-vuln"):
        return "vuln"
    return "standard"
