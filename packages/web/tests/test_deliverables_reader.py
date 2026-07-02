import json

import pytest

from shannon_web.components.deliverables_reader import DeliverablesReader


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
