from supernova_core.models.retry import GITNEXUS_VERDICT_RETRY, retry_for


def test_gitnexus_verdict_retry_policy():
    p = GITNEXUS_VERDICT_RETRY
    assert p.maximum_attempts == 3          # 多轮 agent 贵,不像 PRODUCTION_RETRY 重试 8 次


def test_retry_for_gitnexus_verdict_category():
    assert retry_for("gitnexus-verdict") is GITNEXUS_VERDICT_RETRY


def test_production_retry_capped_at_8():
    """pre-recon/recon/report 等 standard 单次 LLM agent 的重试上限。
    2026-07-20 从 50 下调:50×~6min 会把 transient/确定性失败放大成 ~5h 卡死
    (sentinel_dashboard 实测)。对齐 VULN_RETRY(8) 哲学。"""
    from supernova_core.models.retry import PRODUCTION_RETRY
    assert PRODUCTION_RETRY.maximum_attempts == 8


def test_standard_category_resolves_to_production_cap():
    """recon/pre-recon -> standard -> PRODUCTION_RETRY(max 8),不再 50。"""
    assert retry_for("standard").maximum_attempts == 8


def test_retry_for_risk_scoring_category():
    """risk-scoring(确定性、幂等、毫秒级 plan())绝不能套 PRODUCTION_RETRY(max 8):
    单次 5min start_to_close 超时会被放大成 ~26min 卡死(2026-08-05 NodeGoat 实测,
    plan() 实际仅 3ms)。短重试 max 3,对齐 code-index/poc 哲学。"""
    from supernova_core.models.retry import RISK_SCORING_RETRY
    assert retry_for("risk-scoring") is RISK_SCORING_RETRY
    assert RISK_SCORING_RETRY.maximum_attempts == 3
    assert RISK_SCORING_RETRY.maximum_attempts < 8  # 防回退到 PRODUCTION_RETRY 量级
