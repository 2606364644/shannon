from shannon_core.models.retry import GITNEXUS_VERDICT_RETRY, retry_for


def test_gitnexus_verdict_retry_policy():
    p = GITNEXUS_VERDICT_RETRY
    assert p.maximum_attempts == 3          # 多轮 agent 贵,不像 PRODUCTION_RETRY 重试 8 次


def test_retry_for_gitnexus_verdict_category():
    assert retry_for("gitnexus-verdict") is GITNEXUS_VERDICT_RETRY


def test_production_retry_capped_at_8():
    """pre-recon/recon/report 等 standard 单次 LLM agent 的重试上限。
    2026-07-20 从 50 下调:50×~6min 会把 transient/确定性失败放大成 ~5h 卡死
    (sentinel_dashboard 实测)。对齐 VULN_RETRY(8) 哲学。"""
    from shannon_core.models.retry import PRODUCTION_RETRY
    assert PRODUCTION_RETRY.maximum_attempts == 8


def test_standard_category_resolves_to_production_cap():
    """recon/pre-recon -> standard -> PRODUCTION_RETRY(max 8),不再 50。"""
    assert retry_for("standard").maximum_attempts == 8
