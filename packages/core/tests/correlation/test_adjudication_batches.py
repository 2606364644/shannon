"""裁决批组织（spec 2026-08-27 §7.1）——(service, vc) × 两类输入源，确定性纯函数。

- queue 批：service 该 vc 的 queue 条目 → confirm/downgrade
- dismissed 批：单文件条目按 vuln_class 过滤 → upgrade/maintain；全量进批，
  dismiss_reason 含可达性/暴露面的排批内前部（排序只影响优先级不影响覆盖）
- 批上限（默认 15）分片
"""
from supernova_core.correlation.adjudication import (
    AdjudicationBatch, build_adjudication_batches,
)


def test_queue_batches_per_service_and_vc():
    batches = build_adjudication_batches(
        {"gateway": {"injection": [{"ID": "XSS-1", "title": "t"}]},
         "order-svc": {"xss": [{"ID": "XSS-2", "title": "t"}]}},
        {})
    assert {(b.service, b.vuln_class, b.origin) for b in batches} == {
        ("gateway", "injection", "queue"),
        ("order-svc", "xss", "queue")}


def test_dismissed_batched_by_vuln_class_field():
    dismissed = {"order-svc": [
        {"ID": "D1", "vuln_class": "injection", "dismiss_reason": "judged safe"},
        {"ID": "D2", "vuln_class": "xss", "dismiss_reason": "judged safe"},
    ]}
    batches = build_adjudication_batches({}, dismissed)
    assert {(b.vuln_class, b.origin) for b in batches} == {
        ("injection", "dismissed"), ("xss", "dismissed")}


def test_dismissed_reachability_reason_sorted_first():
    """否决理由含可达性/暴露面的排批内前部——全量保留，只调顺序。"""
    dismissed = {"b": [
        {"ID": "D-safe", "vuln_class": "injection", "dismiss_reason": "sanitizer present"},
        {"ID": "D-reach", "vuln_class": "injection",
         "dismiss_reason": "internal service not reachable from entrypoint"},
        {"ID": "D-expo", "vuln_class": "injection", "dismiss_reason": "exposure internal"},
    ]}
    batches = build_adjudication_batches({}, dismissed)
    assert len(batches) == 1
    assert [f["ID"] for f in batches[0].findings] == ["D-reach", "D-expo", "D-safe"]


def test_batch_limit_splits_shards():
    entries = [{"ID": f"Q{i:02d}", "title": "t"} for i in range(17)]
    batches = build_adjudication_batches({"b": {"injection": entries}}, {},
                                         batch_limit=15)
    assert len(batches) == 2
    assert len(batches[0].findings) == 15
    assert len(batches[1].findings) == 2
    # 分片保序：第 2 片接第 1 片尾部
    assert batches[1].findings[0]["ID"] == "Q15"


def test_empty_inputs_yield_no_batches():
    assert build_adjudication_batches({}, {}) == []


def test_service_without_dismissed_gets_no_dismissed_batch():
    batches = build_adjudication_batches(
        {"gateway": {"injection": [{"ID": "A"}]}}, {"order-svc": [
            {"ID": "D1", "vuln_class": "xss", "dismiss_reason": "r"}]})
    assert all(not (b.service == "gateway" and b.origin == "dismissed")
               for b in batches)
    assert any(b.service == "order-svc" and b.origin == "dismissed" for b in batches)
