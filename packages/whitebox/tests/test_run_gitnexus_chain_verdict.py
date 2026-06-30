import json
from contextlib import asynccontextmanager

import pytest

from shannon_whitebox.audit.session_registry import clear_audit_session, set_audit_session
from shannon_whitebox.pipeline import activities


class _RecordingSession:
    def __init__(self):
        self.info_calls: list[tuple[str, str]] = []

    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        yield

    async def log_info(self, message: str, level: str = "info"):
        self.info_calls.append((message, level))


def _input(repo):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
        deliverables_subdir = None
        workspace_name = None
        workspace_path = None

    return FakeInput()


def _write_pgraph(deliverables, flows):
    """Write a minimal parameter_graph.json with given TaintFlow-like dicts."""
    pgraph = {
        "taint_flows": flows,
        "language_coverage": ["python"],
        "skipped_languages": [],
    }
    (deliverables / "parameter_graph.json").write_text(json.dumps(pgraph))


def _flow(slot, source="q", source_type="query", sink_id="app.py:h:db.execute:5:0",
          steps=None):
    return {
        "flow_id": "ep#" + sink_id,
        "entry_point_id": "app.py:h:1",
        "source_param": source,
        "source_type": source_type,
        "sink_call_site_id": sink_id,
        "sink_slot": slot,
        "propagation_steps": steps or [],
        "confidence": 1.0,
        "has_sanitizer_hint": False,
    }


def _write_sink(deliverables, sink_id, category, sink_subtype):
    """Append a sink_call_site to code_index.json (created if absent)."""
    from pathlib import Path
    ci_path: Path = deliverables / "code_index.json"
    if ci_path.exists():
        ci = json.loads(ci_path.read_text())
    else:
        ci = {
            "repository": "r", "language": "python",
            "total_blocks": 0, "total_entry_points": 0, "total_chains": 0,
            "blocks": [], "edges": [], "entry_points": [], "chains": [],
        }
    ci.setdefault("sink_call_sites", []).append({
        "id": sink_id,
        "caller_id": "app.py:h",
        "callee_name": sink_subtype.split("_")[-1],
        "callee_receiver": "el",
        "category": category,
        "sink_subtype": sink_subtype,
        "file_path": "app.py",
        "line": 5,
        "column": 0,
        "dangerous_slots": [],
        "rule_id": "rule",
    })
    ci_path.write_text(json.dumps(ci))


@pytest.mark.asyncio
async def test_writes_injection_gitnexus_queue(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [_flow("sql_value")])

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    q = deliverables / "injection_gitnexus_queue.json"
    assert q.exists()
    data = json.loads(q.read_text())
    assert len(data["vulnerabilities"]) == 1
    assert data["vulnerabilities"][0]["source_track"] == "gitnexus"
    assert "injection" in result["per_class"]
    # 可观测性（spec §3.4）：有 findings → 汇总 info（per-class 明细）。
    levels = [lvl for (_msg, lvl) in session.info_calls]
    msgs = [msg for (msg, _lvl) in session.info_calls]
    assert "info" in levels
    assert any("inj=1" in m for m in msgs)


@pytest.mark.asyncio
async def test_no_parameter_graph_skips_gracefully(tmp_path, monkeypatch):
    """Plan 1 not landed -> no parameter_graph.json -> all gitnexus queues absent."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["per_class"] == {}
    assert not (deliverables / "injection_gitnexus_queue.json").exists()
    # 可观测性（spec §3.2）：pgraph 缺失 early return 发 warning。
    levels = [lvl for (_msg, lvl) in session.info_calls]
    assert "warning" in levels
    assert any("缺失" in msg for (msg, _lvl) in session.info_calls)


@pytest.mark.asyncio
async def test_writes_xss_and_ssrf_queues(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    xss_sid = "app.py:h:innerHTML:5:0"
    # xss routes via SinkCallSite.category == XSS (SlotContext has no render slot),
    # so a sink_call_site must exist in code_index.json with a matching id.
    _write_pgraph(deliverables, [
        _flow("generic", source="u", sink_id=xss_sid),
        _flow("url", source="u", sink_id="app.py:h:fetch:6:0"),
    ])
    _write_sink(deliverables, xss_sid, "xss", "xss_innerhtml")

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"x","evidence_chain":"s->k",'
                '"mismatch_reason":"m","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "xss" in result["per_class"]
    assert "ssrf" in result["per_class"]
    assert (deliverables / "xss_gitnexus_queue.json").exists()
    assert (deliverables / "ssrf_gitnexus_queue.json").exists()


@pytest.mark.asyncio
async def test_invalid_parameter_graph_skips_gracefully(tmp_path, monkeypatch):
    """Corrupt parameter_graph.json -> skip, don't crash."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "parameter_graph.json").write_text("not json")

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["per_class"] == {}
    # 可观测性（spec §3.3）：pgraph 无效 early return 发 warning。
    levels = [lvl for (_msg, lvl) in session.info_calls]
    assert "warning" in levels
    assert any("无效" in msg for (msg, _lvl) in session.info_calls)


@pytest.mark.asyncio
async def test_summary_warns_when_all_classes_zero(tmp_path, monkeypatch):
    """空壳 parameter_graph（taint_flows=[]）→ 3 类 0 findings → 汇总 warning（spec §3.4）。"""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [])

    async def fake_llm(prompt, **kw):
        raise AssertionError("empty pgraph should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)
    session = _RecordingSession()
    set_audit_session(session)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["per_class"] == {}
    levels = [lvl for (_msg, lvl) in session.info_calls]
    msgs = [msg for (msg, _lvl) in session.info_calls]
    assert "warning" in levels
    assert any("3 类 0 findings" in m and "taint_flows=0" in m for m in msgs)
