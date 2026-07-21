"""Task 2: chain_verdict returns fail-fast info (failed_classes / fail_reasons).

Spec: GitNexus 轨 fail-fast 改造 plan. ``run_gitnexus_chain_verdict`` returns
``failed_classes``/``fail_reasons`` instead of silently degrading; the workflow
(Task 4) reads these to decide fail-fast behavior. Business-level failure does
NOT raise -- only ``PentestError`` does (unchanged).
"""
import json
from contextlib import asynccontextmanager

import pytest

from supernova_whitebox.audit.session_registry import (
    clear_audit_session,
    set_audit_session,
)
from supernova_whitebox.pipeline import activities


# --------------------------------------------------------------------------- #
# Mock helpers (same shape as test_run_gitnexus_chain_verdict.py -- on-disk
# artifacts + real audit session, not mocks).
# --------------------------------------------------------------------------- #

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


def _flow(slot, source="q", source_type="query",
          sink_id="app.py:h:db.execute:5:0", steps=None):
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


_VERDICT_OK = (
    '{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
    '"q->db","mismatch_reason":"concat","confidence":"high"}'
)


def _wire(tmp_path, deliverables, monkeypatch, fake_llm):
    """Wire up _get_paths + verdict LLM client for a test."""
    monkeypatch.setattr(
        activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path)
    )
    monkeypatch.setattr(
        activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False
    )
    session = _RecordingSession()
    set_audit_session(session)
    return session


# --------------------------------------------------------------------------- #
# Test scenarios.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_missing_parameter_graph_returns_failed_classes(tmp_path, monkeypatch):
    """No parameter_graph.json -> all 3 classes in failed_classes."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    async def fake_llm(prompt, **kw):
        raise AssertionError("missing pgraph should not call LLM")

    session = _wire(tmp_path, deliverables, monkeypatch, fake_llm)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert set(result["failed_classes"]) == {"injection", "xss", "ssrf"}
    assert result["per_class"] == {}
    assert set(result["fail_reasons"].keys()) == {"injection", "xss", "ssrf"}
    assert "missing" in result["fail_reasons"]["injection"].lower()
    # No gitnexus queues written.
    assert not (deliverables / "injection_gitnexus_queue.json").exists()


@pytest.mark.asyncio
async def test_invalid_parameter_graph_returns_failed_classes(tmp_path, monkeypatch):
    """Corrupt parameter_graph.json -> all 3 classes in failed_classes."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "parameter_graph.json").write_text("not json")

    async def fake_llm(prompt, **kw):
        raise AssertionError("invalid pgraph should not call LLM")

    session = _wire(tmp_path, deliverables, monkeypatch, fake_llm)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert set(result["failed_classes"]) == {"injection", "xss", "ssrf"}
    assert result["per_class"] == {}
    assert set(result["fail_reasons"].keys()) == {"injection", "xss", "ssrf"}
    assert "invalid" in result["fail_reasons"]["injection"].lower()


@pytest.mark.asyncio
async def test_zero_findings_is_ok_not_failed(tmp_path, monkeypatch):
    """Valid empty pgraph -> builders return [] -> NOT failed (legitimate 0)."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables, [])

    async def fake_llm(prompt, **kw):
        raise AssertionError("empty pgraph should not call LLM")

    session = _wire(tmp_path, deliverables, monkeypatch, fake_llm)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert result["failed_classes"] == []
    assert result["per_class"] == {}
    assert result["fail_reasons"] == {}


@pytest.mark.asyncio
async def test_builder_exception_marks_class_failed(tmp_path, monkeypatch):
    """One builder raising -> only that class in failed_classes; others clean.

    We monkeypatch ``build_injection_findings`` at the source module so the
    ``from ... import build_injection_findings`` inside the function picks up
    the patched callable. The xss/ssrf builders see no xss/ssrf sink_call_sites
    in code_index.json (not written here), so they return [] cleanly.
    """
    import supernova_core.code_index.vuln_chain_builders.injection_builder as inj_mod

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # An injection flow so the injection builder gets called. No xss/ssrf
    # sink_call_sites -> xss/ssrf builders return [] (not failed).
    _write_pgraph(deliverables, [_flow("sql_value")])

    async def _raise_inj(*a, **kw):
        raise RuntimeError("boom injection")

    monkeypatch.setattr(inj_mod, "build_injection_findings", _raise_inj)

    async def fake_llm(prompt, **kw):
        # xss/ssrf builders won't reach the LLM without their sink_call_sites.
        return _VERDICT_OK

    session = _wire(tmp_path, deliverables, monkeypatch, fake_llm)
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    assert "injection" in result["failed_classes"]
    assert "xss" not in result["failed_classes"]
    assert "ssrf" not in result["failed_classes"]
    assert result["fail_reasons"]["injection"].startswith("builder raised:")
