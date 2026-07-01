from shannon_core.models.retry import GITNEXUS_VERDICT_RETRY, retry_for


def test_gitnexus_verdict_retry_policy():
    p = GITNEXUS_VERDICT_RETRY
    assert p.maximum_attempts == 3          # 多轮 agent 贵,不像 PRODUCTION_RETRY 重试 50 次


def test_retry_for_gitnexus_verdict_category():
    assert retry_for("gitnexus-verdict") is GITNEXUS_VERDICT_RETRY
