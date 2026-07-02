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
