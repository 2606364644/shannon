from supernova_core.models.deliverables import classify_tier


def test_dataflow_view_classified_intermediate():
    assert classify_tier("whitebox/intermediate/dataflow_view.json") == "intermediate"


def test_chain_verdicts_classified_intermediate():
    assert classify_tier("whitebox/intermediate/injection_chain_verdicts.json") == "intermediate"


def test_dataflow_view_flat_fallback_intermediate():
    # 平铺兜底（无 intermediate/ 段）时靠 pattern 命中
    assert classify_tier("whitebox/dataflow_view.json") == "intermediate"


def test_safe_vectors_classified_intermediate():
    # Task 6 产物 *_safe_vectors.json（新结构落 intermediate/）
    assert classify_tier("whitebox/intermediate/injection_safe_vectors.json") == "intermediate"


def test_safe_vectors_flat_fallback_intermediate():
    # 平铺兜底（无 intermediate/ 段）时靠 pattern 命中（旧结构平铺不误归 deliverable tier）
    assert classify_tier("whitebox/injection_safe_vectors.json") == "intermediate"
