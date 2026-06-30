import json
from pathlib import Path

from shannon_core.correlation.queue_merge import merge_exploitation_queues
from shannon_core.utils.paths import WHITEBOX_SUBDIR, has_valid_whitebox_results


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


# ---------------------------------------------------------------------------
# Task 8: multi orchestrator 收集子仓 queue 的 glob-merge 契约
# (whitebox/ 新结构优先, deliverables 根老结构 fallback, 同名去重)
# ---------------------------------------------------------------------------
def _write_queue(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"vulnerabilities": entries}, ensure_ascii=False), encoding="utf-8")


def _collect_queue_files(dlv: Path) -> dict[str, Path]:
    """复刻 orchestrator 的去重 glob: whitebox/ 优先, 根条目仅补白。

    与 shannon_multi.orchestrator.run_cross_repo 中的 collection 逻辑同构,
    用来锚定子仓 queue 读取路径契约(不直接 import orchestrator, 避免 multi 包依赖环)。
    """
    queue_files: dict[str, Path] = {}
    for q in (dlv / WHITEBOX_SUBDIR).glob("*_exploitation_queue.json"):
        queue_files[q.name] = q
    for q in dlv.glob("*_exploitation_queue.json"):
        queue_files.setdefault(q.name, q)
    return queue_files


def test_collect_reads_queue_from_whitebox_subdir(tmp_path):
    """新结构: 子仓 queue 放 whitebox/ 子目录, 收集逻辑能读到。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / WHITEBOX_SUBDIR / "injection_exploitation_queue.json",
                 [_entry("INJ-WB")])
    collected = _collect_queue_files(dlv)
    assert "injection_exploitation_queue.json" in collected
    entries = json.loads(collected["injection_exploitation_queue.json"]
                         .read_text(encoding="utf-8"))["vulnerabilities"]
    assert entries[0]["ID"] == "INJ-WB"


def test_collect_falls_back_to_deliverables_root(tmp_path):
    """老结构 fallback: 子仓 queue 在 deliverables 根(无 whitebox/ 子目录)。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / "ssrf_exploitation_queue.json", [_entry("SSRF-LEGACY")])
    collected = _collect_queue_files(dlv)
    assert collected["ssrf_exploitation_queue.json"] == dlv / "ssrf_exploitation_queue.json"


def test_collect_whitebox_wins_on_name_collision(tmp_path):
    """whitebox/ 优先: 同名 queue 在两处都有时取 whitebox/ 那份。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / WHITEBOX_SUBDIR / "injection_exploitation_queue.json",
                 [_entry("INJ-NEW")])
    _write_queue(dlv / "injection_exploitation_queue.json",
                 [_entry("INJ-OLD")])
    collected = _collect_queue_files(dlv)
    assert collected["injection_exploitation_queue.json"] == \
        dlv / WHITEBOX_SUBDIR / "injection_exploitation_queue.json"
    entries = json.loads(collected["injection_exploitation_queue.json"]
                         .read_text(encoding="utf-8"))["vulnerabilities"]
    assert entries[0]["ID"] == "INJ-NEW"


def test_collect_merges_whitebox_and_root_distinct_names(tmp_path):
    """whitebox/ 与根各有不同 vuln 类时全部收集(去重只对同名生效)。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv / WHITEBOX_SUBDIR / "injection_exploitation_queue.json",
                 [_entry("INJ-01")])
    _write_queue(dlv / "xss_exploitation_queue.json", [_entry("XSS-01")])
    collected = _collect_queue_files(dlv)
    assert set(collected) == {"injection_exploitation_queue.json",
                              "xss_exploitation_queue.json"}
