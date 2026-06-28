import json
from shannon_core.correlation.queue_merge import merge_exploitation_queues
from shannon_core.utils.paths import has_valid_whitebox_results


def _entry(ID="INJ-001", **kw):
    """真实 vuln queue 字段(ID/vulnerability_type/externally_exploitable/confidence/
    source/...);**无** title/description/severity/location(那是 exploit 阶段字段)。"""
    e = {
        "ID": ID,
        "vulnerability_type": "SQLi",
        "externally_exploitable": True,
        "confidence": "high",
        "source": "req.body.q at routes/search.js:12",
    }
    e.update(kw)
    return e


def test_merge_preserves_entries_and_adds_service(tmp_path):
    merged = merge_exploitation_queues({
        "gateway": [_entry("INJ-01")],
        "order-svc": [_entry("INJ-02", confidence="medium")],
    })
    assert len(merged) == 2
    # 每条都有 service 标注
    services = {m["service"] for m in merged}
    assert services == {"gateway", "order-svc"}
    # 合并产物仍通过黑盒 has_valid_whitebox_results(对齐 TS:只查 vulnerabilities 非空)
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({"vulnerabilities": merged}), encoding="utf-8")
    assert has_valid_whitebox_results(queue_file) is True


def test_merge_drops_non_dict_entries():
    """对齐 TS:仅丢弃非 dict 条目;不再因缺 title/description/severity/location 丢弃。"""
    merged = merge_exploitation_queues({"order-svc": ["not-a-dict", 42, _entry("INJ-01")]})
    assert len(merged) == 1
    assert merged[0]["ID"] == "INJ-01"


def test_merge_keeps_real_vuln_fields_without_exploit_fields():
    """回归锚点:真实 vuln queue 字段(无 title/description/severity/location)不被丢弃。"""
    merged = merge_exploitation_queues({"svc": [_entry("INJ-01")]})
    assert len(merged) == 1
    assert "service" in merged[0]
