import json

import pytest

from supernova_web.components.deliverables_reader import DeliverablesReader


def test_read_json_new_layout(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [1]}))
    assert DeliverablesReader(ws).read("xss_exploitation_queue.json") == {"vulnerabilities": [1]}


def test_read_md_legacy_flat_layout(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables"
    dl.mkdir(parents=True)
    (dl / "report.md").write_text("# hi")
    assert DeliverablesReader(ws).read("report.md") == "# hi"


def test_empty_json_returns_empty_list(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "attack_chains.json").write_text("[]")
    assert DeliverablesReader(ws).read("attack_chains.json") == []


def test_summary(tmp_path):
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{}]}))
    (dl / "report.md").write_text("# r")
    s = DeliverablesReader(ws).summary()
    assert "xss" in s["vuln_queues"]
    assert "report.md" in s["reports"]


def test_missing_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(FileNotFoundError):
        DeliverablesReader(ws).read("nope.md")


def test_read_poc_track_scoped(tmp_path):
    """PoC md 在 deliverables/{track}/ 下,read_poc 返回文本(PoC 自带「# 可利用漏洞 PoC 集合」标题)。"""
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    poc = "# 可利用漏洞 PoC 集合（白盒）\n\n```bash\ncurl -i 'https://t/x'\n```"
    (dl / "exploitable_poc_collection.md").write_text(poc)
    assert DeliverablesReader(ws).read_poc() == poc


def test_read_poc_blackbox_track(tmp_path):
    """黑盒扫描 PoC 在 deliverables/blackbox/,_infer_track 正确指向。"""
    ws = tmp_path / "ws"
    dl = ws / "deliverables" / "blackbox"
    dl.mkdir(parents=True)
    (dl / "exploitable_poc_collection.md").write_text("# 可利用漏洞 PoC 集合（黑盒）")
    assert DeliverablesReader(ws).read_poc() == "# 可利用漏洞 PoC 集合（黑盒）"


def test_read_poc_legacy_flat_layout(tmp_path):
    """老 workspace 平铺 deliverables/exploitable_poc_collection.md 也能读(resolve_track_deliverable fallback)。"""
    ws = tmp_path / "ws"
    dl = ws / "deliverables"
    dl.mkdir(parents=True)
    (dl / "exploitable_poc_collection.md").write_text("# PoC")
    assert DeliverablesReader(ws).read_poc() == "# PoC"


def test_read_poc_missing_returns_none(tmp_path):
    """无 PoC md(扫描中断/PoC activity 未跑)-> None,不抛,调用方按无 PoC 处理。"""
    ws = tmp_path / "ws"
    (ws / "deliverables" / "whitebox").mkdir(parents=True)
    assert DeliverablesReader(ws).read_poc() is None


def test_summary_mixed_layout_no_duplicate(tmp_path):
    """A queue present in BOTH the new track-scoped dir (deliverables/whitebox/)
    AND the legacy top-level dir (deliverables/) must be counted exactly once.

    Core's summarize_deliverables_dir is now recursive (rglob) and dedups by
    vuln_class, so a mixed layout does not double-count. This guards against
    drift between the two layouts during the migration window.
    """
    ws = tmp_path / "ws"
    top = ws / "deliverables"
    track_dir = top / "whitebox"
    track_dir.mkdir(parents=True)

    queue_payload = json.dumps({"vulnerabilities": [{}]})
    # Same vuln class in both locations
    (top / "xss_exploitation_queue.json").write_text(queue_payload)
    (track_dir / "xss_exploitation_queue.json").write_text(queue_payload)
    # report in both locations too
    (top / "report.md").write_text("# top")
    (track_dir / "report.md").write_text("# track")

    s = DeliverablesReader(ws).summary()
    assert s["vuln_queues"].count("xss") == 1
    assert s["reports"].count("report.md") == 1
