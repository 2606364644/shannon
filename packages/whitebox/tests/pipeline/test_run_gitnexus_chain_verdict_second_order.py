"""Task 8 (子项⑤): activity-level integration test for second-order findings.

Verifies that ``run_gitnexus_chain_verdict`` now also invokes
``build_second_order_findings`` and routes any emitted ``2ND-GN-*`` findings
into the matching ``{vc}_gitnexus_queue.json`` (here, xss).

Fixture design:
- ``parameter_graph.json``: a single TaintFlow with
  ``source_type=STORAGE`` → XSS ``sink_call_site_id``.
- ``code_index.json``: carries
  (a) a ``sink_call_sites`` entry with ``category=xss`` matching the flow's
      sink id (so the read-side single-hop extractor routes it as XSS);
  (b) a ``source_points`` entry with ``source_type=storage`` keyed by
      ``param_name="users"`` and ``expression='"users"'`` so that
      ``extract_second_order_candidates`` resolves its literal token to
      ``"users"``;
  (c) a ``storage_write_points`` entry with ``storage_token="users"`` and
      ``written_expr="user.bio"`` (non-literal → user-tainted) so the join
      produces a candidate and the builder emits a finding.
- LLM stub: returns a ``vulnerable`` verdict JSON so ``judge_chain_verdict``
  parses a vulnerable read-side verdict.

Expected: ``xss_gitnexus_queue.json`` contains a finding whose ``ID`` starts
with ``"2ND-GN-"``.
"""
import json
from contextlib import asynccontextmanager

import pytest

from supernova_whitebox.audit.session_registry import (
    clear_audit_session,
    set_audit_session,
)
from supernova_whitebox.pipeline import activities


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


_SINK_ID = "C.java:ProfileServlet:innerHTML:5:0"


def _write_pgraph(deliverables):
    """STORAGE-sourced TaintFlow → XSS sink (innerHTML)."""
    pgraph = {
        "taint_flows": [
            {
                "flow_id": "ep#" + _SINK_ID,
                "entry_point_id": "C.java:ProfileServlet:1",
                "source_param": "users",
                "source_type": "storage",
                "sink_call_site_id": _SINK_ID,
                "sink_slot": "generic",     # xss routes by SinkCallSite.category, not slot
                "propagation_steps": [],
                "confidence": 1.0,
                "has_sanitizer_hint": False,
            },
        ],
        "language_coverage": ["java"],
        "skipped_languages": [],
    }
    (deliverables / "parameter_graph.json").write_text(json.dumps(pgraph))


def _write_code_index(deliverables):
    """code_index.json with storage_write_points + STORAGE source_point + XSS sink."""
    ci = {
        "repository": "r",
        "language": "java",
        "total_blocks": 0,
        "total_entry_points": 0,
        "total_chains": 0,
        "blocks": [],
        "edges": [],
        "entry_points": [],
        "chains": [],
        "sink_call_sites": [
            {
                "id": _SINK_ID,
                "caller_id": "C.java:ProfileServlet",
                "callee_name": "innerHTML",
                "callee_receiver": "el",
                "category": "xss",
                "sink_subtype": "xss_innerhtml",
                "file_path": "C.java",
                "line": 5,
                "column": 10,
                "dangerous_slots": [],
                "rule_id": "xss-innerhtml",
            },
        ],
        "source_points": [
            {
                "id": "C.java:ProfileServlet:1::users::3",
                "entry_point_id": "C.java:ProfileServlet:1",
                "param_name": "users",
                "source_type": "storage",
                "expression": '"users"',
                "file_path": "C.java",
                "line": 3,
                "column": 0,
                "validation": "NONE",
                "confidence": 0.9,
                "rule_id": "storage-read",
                "needs_review": False,
            },
        ],
        "storage_write_points": [
            {
                "id": "w1",
                "caller_id": "C.java:SaveProfile:1",
                "callee_name": "save",
                "callee_receiver": "repo",
                "medium": "db",
                "storage_token": "users",
                "written_expr": "user.bio",   # not a pure literal → user-tainted
                "file_path": "C.java",
                "line": 3,
                "column": 0,
                "rule_id": "java-orm-save",
                "needs_review": False,
            },
        ],
    }
    (deliverables / "code_index.json").write_text(json.dumps(ci))


@pytest.mark.asyncio
async def test_xss_queue_contains_second_order_finding(tmp_path, monkeypatch):
    """2ND-GN-* finding routed into xss_gitnexus_queue.json."""
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    _write_pgraph(deliverables)
    _write_code_index(deliverables)

    async def fake_llm(prompt, **kw):
        return (
            '{"verdict":"vulnerable","witness_payload":"<svg>alert(1)</svg>",'
            '"evidence_chain":"users(Storage) -> innerHTML unescaped",'
            '"mismatch_reason":"stored value rendered without encoding",'
            '"confidence":"high"}'
        )

    monkeypatch.setattr(
        activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path)
    )
    # Patch the call-site factory so the stub is used regardless of the
    # is_gitnexus_llm_enabled branch (default=True would otherwise build a
    # real-LLM client and bypass the stub below). See Task 8 fix.
    monkeypatch.setattr(
        activities, "_make_verdict_llm_client", lambda repo: fake_llm
    )
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_gitnexus_chain_verdict(_input(tmp_path))
    finally:
        clear_audit_session()

    q = deliverables / "xss_gitnexus_queue.json"
    assert q.exists(), "xss_gitnexus_queue.json must be written"
    data = json.loads(q.read_text())
    ids = [v["ID"] for v in data["vulnerabilities"]]
    assert any(i.startswith("2ND-GN-") for i in ids), (
        f"xss_gitnexus_queue must contain a 2ND-GN-* finding, got ids={ids}"
    )
    # the matching finding's source_track must be gitnexus (not LLM track)
    second_order = [v for v in data["vulnerabilities"] if v["ID"].startswith("2ND-GN-")]
    assert second_order[0]["source_track"] == "gitnexus"
    assert second_order[0]["vulnerability_type"] == "second_order_xss"
    # per_class counts include the second-order finding
    assert result["per_class"].get("xss", 0) >= 1
