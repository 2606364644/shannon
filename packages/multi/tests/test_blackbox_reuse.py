import json

from shannon_blackbox.pipeline.workflows import _load_correlation_context


def test_load_correlation_context_when_files_exist(tmp_path):
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    (dlv / "cross-service-topology.json").write_text(
        json.dumps({"services": [], "edges": []}), encoding="utf-8"
    )
    (dlv / "trust-boundaries.json").write_text("[]", encoding="utf-8")
    ctx = _load_correlation_context(tmp_path)
    assert ctx is not None
    assert ctx["topology"]["edges"] == []
    assert ctx["boundaries"] == []


def test_load_correlation_context_none_when_absent(tmp_path):
    assert _load_correlation_context(tmp_path) is None
