"""Tests for second_order_builder (spec §3.3, Task 7).

Verifies that build_second_order_findings:
(a) emits a second-order XSS finding when the write side is user-tainted AND
    the read-side single-hop chain_verdict returns "vulnerable";
(b) emits nothing when the read side judges "safe".

Fixture strategy: build a minimal ParameterPropagationGraph with one TaintFlow
(STORAGE source -> XSS sink). The matching SinkCallSite(category=XSS) lives in
sink_call_sites, and reads_by_id carries the STORAGE SourcePoint keyed by its
param_name. The write is a StorageWritePoint whose storage_token matches the
read's resolved literal token, so extract_second_order_candidates pairs them.
"""
import pytest

from supernova_core.code_index.chain_verdict import CandidateChain
from supernova_core.code_index.models import ParameterSource
from supernova_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    SinkCallSite,
    SinkCategory,
    SourcePoint,
    TaintFlow,
)
from supernova_core.code_index.storage_models import StorageMedium, StorageWritePoint
from supernova_core.code_index.vuln_chain_builders.second_order_builder import (
    build_second_order_findings,
    _looks_user_tainted,
)


_SINK_ID = "C.java:ProfileServlet:innerHTML:5:0"


def _build_xss_second_order_pgraph():
    """One STORAGE-sourced TaintFlow -> XSS sink (innerHTML).

    Returns (pgraph, reads_by_id, sink_call_sites) matching the contract that
    build_second_order_findings expects. The read-side SourcePoint has
    expression='"users"' so second_order_join resolves its literal token to
    "users", matching the write's storage_token.
    """
    flow = TaintFlow(
        flow_id="ep#sink1",
        entry_point_id="C.java:ProfileServlet:1",
        source_param="users",
        source_type=ParameterSource.STORAGE,
        sink_call_site_id=_SINK_ID,
        # slot value is irrelevant for xss routing (routed by SinkCallSite.category)
        propagation_steps=[],
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[flow],
        language_coverage=["java"],
    )
    sink = SinkCallSite(
        id=_SINK_ID,
        caller_id="C.java:ProfileServlet",
        callee_name="innerHTML",
        callee_receiver="el",
        category=SinkCategory.XSS,
        sink_subtype="xss_innerhtml",
        file_path="C.java",
        line=5,
        column=10,
        dangerous_slots=[],
        rule_id="xss-innerhtml",
    )
    sink_call_sites = {_SINK_ID: sink}
    read_src = SourcePoint(
        id="C.java:ProfileServlet:1::users::3",
        entry_point_id="C.java:ProfileServlet:1",
        param_name="users",
        source_type=ParameterSource.STORAGE,
        expression='"users"',
        file_path="C.java",
        line=3,
        rule_id="storage-read",
    )
    reads_by_id = {"users": read_src}
    return pgraph, reads_by_id, sink_call_sites


def _tainted_write() -> StorageWritePoint:
    return StorageWritePoint(
        id="w", caller_id="C.java:SaveProfile:1", callee_name="save",
        medium=StorageMedium.DB, storage_token="users",
        written_expr="user.bio",       # not a pure literal -> tainted
        file_path="C.java", line=3, rule_id="java-orm-save",
    )


@pytest.mark.asyncio
async def test_second_order_xss_when_write_tainted_and_read_vuln():
    """write tainted + read vulnerable → emit InjectionVulnerability."""
    writes = [_tainted_write()]
    pgraph, reads_by_id, sink_call_sites = _build_xss_second_order_pgraph()

    async def llm(prompt, **kw):
        return (
            '{"verdict":"vulnerable","witness_payload":"<svg>alert(1)</svg>",'
            '"evidence_chain":"users(Storage) -> innerHTML(C.java:5) unescaped",'
            '"mismatch_reason":"stored value rendered without encoding",'
            '"confidence":"high"}'
        )

    findings = await build_second_order_findings(
        writes, pgraph,
        llm_client=llm, sink_call_sites=sink_call_sites,
        reads_by_id=reads_by_id,
    )
    assert findings, "must emit a second-order XSS finding"
    f = findings[0]
    assert f.ID.startswith("2ND-GN-")
    assert f.source_track == "gitnexus"
    assert f.verdict == "vulnerable"
    assert f.vulnerability_type == "second_order_xss"
    assert "write:" in (f.combined_sources or "")
    assert "read:" in (f.combined_sources or "")
    assert f.externally_exploitable is True
    assert f.sink_call == _SINK_ID
    assert f.witness_payload == "<svg>alert(1)</svg>"
    assert f.confidence == "high"


@pytest.mark.asyncio
async def test_no_finding_when_read_side_safe():
    """Same write, but read-side verdict safe → no finding emitted."""
    writes = [_tainted_write()]
    pgraph, reads_by_id, sink_call_sites = _build_xss_second_order_pgraph()

    async def llm(prompt, **kw):
        return (
            '{"verdict":"safe","witness_payload":"",'
            '"evidence_chain":"users -> innerHTML (encoded)",'
            '"mismatch_reason":"","confidence":"high"}'
        )

    findings = await build_second_order_findings(
        writes, pgraph,
        llm_client=llm, sink_call_sites=sink_call_sites,
        reads_by_id=reads_by_id,
    )
    assert findings == []


@pytest.mark.asyncio
async def test_single_hop_xss_builder_suppresses_storage_sourced_chain():
    """Gap A regression: single-hop builders must NOT emit findings for
    STORAGE-sourced chains - the second-order builder (2ND-GN-*) is the
    authoritative path for stored data. Without this suppression, a
    STORAGE-sourced XSS flow would emit BOTH XSS-GN-01 (mislabeled
    "Reflected") AND 2ND-GN-01 (duplicate + double LLM cost).

    Asserts the suppression end-to-end:
      (a) ``build_xss_findings`` with a vulnerable stub LLM emits NO XSS-GN-*
          finding for the STORAGE-sourced flow (single-hop suppressed).
      (b) ``build_second_order_findings`` with the same stub DOES emit a
          2ND-GN-* finding (second-order is authoritative).
    """
    from supernova_core.code_index.vuln_chain_builders.xss_builder import (
        build_xss_findings,
    )

    writes = [_tainted_write()]
    pgraph, reads_by_id, sink_call_sites = _build_xss_second_order_pgraph()

    async def vuln_llm(prompt, **kw):
        # Stub always-vulnerable: any candidate that reaches judge_chain_verdict
        # would be marked vulnerable - so the ONLY way single-hop emits nothing
        # is if STORAGE chains are filtered before judging.
        return (
            '{"verdict":"vulnerable","witness_payload":"<svg>alert(1)</svg>",'
            '"evidence_chain":"users(Storage) -> innerHTML unescaped",'
            '"mismatch_reason":"stored value rendered without encoding",'
            '"confidence":"high"}'
        )

    # (a) single-hop XSS builder must NOT emit XSS-GN-* for STORAGE chain
    xss_findings = await build_xss_findings(
        pgraph, llm_client=vuln_llm, sink_call_sites=sink_call_sites,
    )
    xss_ids = [f.ID for f in xss_findings]
    assert not any(i.startswith("XSS-GN-") for i in xss_ids), (
        f"single-hop xss builder must not emit for STORAGE-sourced chain, "
        f"got xss_ids={xss_ids}"
    )

    # (b) second-order builder DOES emit 2ND-GN-* for the same fixture
    second_order_findings = await build_second_order_findings(
        writes, pgraph,
        llm_client=vuln_llm, sink_call_sites=sink_call_sites,
        reads_by_id=reads_by_id,
    )
    second_order_ids = [f.ID for f in second_order_findings]
    assert any(i.startswith("2ND-GN-") for i in second_order_ids), (
        f"second-order builder must emit 2ND-GN-* for STORAGE-sourced chain, "
        f"got ids={second_order_ids}"
    )


# ------------------------------------------------------------------
# Task 5 (2026-07-22): write-side taint precision (_looks_user_tainted).
# Recognise config / constant / enum writes as NOT user-controlled (reduce
# false-positive candidates sent to judge_chain_verdict). Conservative
# direction: only removes false-positives, never adds false-negatives.
# ------------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    # config / i18n / env / settings prefix → not tainted
    ("config.timeout", False),
    ("i18n.messages", False),
    ("env.db_url", False),
    ("settings.max_size", False),
    # SCREAMING_SNAKE constants → not tainted
    ("DEFAULT_ROLE", False),
    ("MAX_SIZE", False),
    # Enum-like (Pascal.UPPER) → not tainted
    ("Color.RED", False),
    ("UserRole.ADMIN", False),
    # existing literal handling (regression)
    ("42", False),
    ('"hardcoded"', False),
    ("", False),
    # genuinely user-controlled → still tainted (no regression)
    ("user.name", True),
    ("req.body.x", True),
    ("user.bio", True),
])
def test_looks_user_tainted_precision(expr, expected):
    """config/constant/enum writes are not user-tainted; user data still is."""
    assert _looks_user_tainted(expr) is expected
