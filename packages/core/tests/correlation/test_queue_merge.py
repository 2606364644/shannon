import json
from pathlib import Path
from shannon_core.correlation.queue_merge import merge_exploitation_queues
from shannon_core.utils.paths import has_valid_whitebox_results


def _entry(title="t", **kw):
    e = {"title": title, "description": "d", "severity": "high", "location": "f:1"}
    e.update(kw)
    return e


def test_merge_preserves_required_fields_and_adds_service(tmp_path):
    merged = merge_exploitation_queues({
        "gateway": [_entry("g1")],
        "order-svc": [_entry("o1", severity="medium")],
    })
    assert len(merged) == 2
    # 每条都有 service 标注
    services = {m["service"] for m in merged}
    assert services == {"gateway", "order-svc"}
    # B1 硬约束：合并产物仍通过黑盒 has_valid_whitebox_results
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({"vulnerabilities": merged}), encoding="utf-8")
    assert has_valid_whitebox_results(queue_file) is True


def test_merge_drops_entries_missing_required_fields(tmp_path):
    bad = {"title": "x"}  # 缺 description/severity/location
    merged = merge_exploitation_queues({"order-svc": [bad, _entry("ok")]})
    # 缺字段的被丢弃，只留合法的
    assert len(merged) == 1
    assert merged[0]["title"] == "ok"
