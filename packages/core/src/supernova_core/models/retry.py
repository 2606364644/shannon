from datetime import timedelta
from typing import Literal

from temporalio.common import RetryPolicy

from supernova_core.models.errors import NON_RETRYABLE_TYPES

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
    # max 8(2026-07-20 从 50 下调):pre-recon/recon/report 等单次 LLM agent 跑满
    # ~6min,max 50 会把任何 transient/确定性失败放大成 ~5h 卡死 + 巨量 token
    # (sentinel_dashboard recon 确定性 schema 违规被重试 50×6min 实测)。对齐
    # VULN_RETRY(8) 哲学;确定性错误另经 SchemaMismatchError non-retryable fail-fast。
    maximum_attempts=8,
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
# (max 8)——那会把单次超时放大成 8x ≈ 80min 卡死(2026-06-30 juice-shop 实测:
# attempt 1/2/3 各 10m10s 超时,PRE_RECON 早已完成但 gather 等代码索引重试耗尽)。
# max 3:给 transient(MCP 连接抖动/IO)几次机会,但不放大幂等超时。
CODE_INDEX_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

# GitNexus 多轮 verdict agent 专用:短重试 + 多轮。
# 多轮 agent(带 grep/read 追链)比单次贵,max 3 避免幂等失败被放大;
# 区别于 PRODUCTION_RETRY(max 8,给单次 LLM agent)。详见
# docs/superpowers/specs/2026-07-02-gitnexus-deep-agent-infra-design.md §3.3。
GITNEXUS_VERDICT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=30),
    maximum_interval=timedelta(minutes=2),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

# PoC 报告增强(generate_poc_report)专用:短重试。
# PoC 对 N 个 externally_exploitable 漏洞串行 llm_fill_gap(各 max_turns 上限),
# 单 activity 耗时易超 start_to_close_timeout;超时幂等(同输入再跑照样超时),
# 绝不能套 PRODUCTION_RETRY(max 8)——那会把单次超时放大成数小时卡死
# (2026-07-10 NodeGoat 实测:5 个串行各 max_turns=50,5min timeout 反复重入
# "白盒 PoC: 5 个" 1h43m+,与 code_index 同构坑)。PoC 是非关键路径(activity
# 内 try/except 吞异常),max 3 给 transient 几次机会但不放大幂等超时。
POC_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=1),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)

# risk-scoring activity(确定性 plan())专用:短重试。
# run_risk_scoring 读 code_index/parameter_graph 跑 plan()——确定性、幂等、毫秒级
# (NodeGoat 112 次循环 events.ndjson 实测 duration_ms=3)。track_step 进入时曾同步
# await dispatcher.dispatch(日志写盘),多 scan 并发 + 磁盘 iowait 时 dispatch 排队
# 等 aiofiles 共享线程池 → 5min start_to_close 超时。套 PRODUCTION_RETRY(max 8 +
# 5min backoff)会把单次超时放大成 ~26min 静默卡死(2026-08-05 NodeGoat 实测,plan()
# 实际仅 3ms)。日志解耦(dispatcher 队列化,2026-08-05-multi-scan-concurrency-fix)
# 已治本移除阻塞源;此处短重试 max 3 为止血——即使极端情况超时也不再被放大,
# 与 code-index/poc 同构。
RISK_SCORING_RETRY = RetryPolicy(
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


Category = Literal["standard", "vuln", "log", "preflight", "auth-validation", "code-index", "gitnexus-verdict", "poc", "risk-scoring"]


def retry_for(category: Category, mode: str | None = None) -> RetryPolicy:
    """按 activity 类别选 retry policy(单一映射源)。

    - standard: LLM agent + 确定性处理。委托 get_retry_policy(mode) 保留 mode 感知
      (testing/subscription);不传 mode 默认 production。
    - vuln:     per-vt vuln agent,有界 VULN_RETRY。
    - code-index: 确定性 code_index 轨,短 CODE_INDEX_RETRY(防幂等超时被放大)。
    - log:      phase log marker(10s 写),短 policy。
    - preflight / auth-validation: 现有短 tier。
    - gitnexus-verdict: 多轮 verdict agent,有界 GITNEXUS_VERDICT_RETRY。
    - poc:      PoC 报告增强,短 POC_RETRY(防幂等超时被放大,同 code-index 理)。
    - risk-scoring: 确定性 plan() 轨,短 RISK_SCORING_RETRY(防幂等超时被放大,同 code-index/poc 理)。
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
    if category == "gitnexus-verdict":
        return GITNEXUS_VERDICT_RETRY
    if category == "poc":
        return POC_RETRY
    if category == "risk-scoring":
        return RISK_SCORING_RETRY
    raise ValueError(f"unknown activity category: {category!r}")


def agent_retry_category(agent_name: str) -> Category:
    """Map an agent name to its retry-policy category (single source of truth).

    Mirrors workflows.py retry_for() calls: vuln agents (per-vt fan-out) → 'vuln'
    (VULN_RETRY, max 8); pre-recon/recon/report and others → 'standard'
    (PRODUCTION_RETRY, max 8). Used by the live display to resolve max_attempts.
    """
    if agent_name.endswith("-vuln"):
        return "vuln"
    return "standard"
