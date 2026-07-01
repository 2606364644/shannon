"""Task 5: build_code_index_with_gitnexus progress_cb pass-through tests.

The heavy orchestration (GitNexus MCP + tree-sitter + multi-stage pipeline) is
exercised elsewhere (test_gitnexus_call_graph.py / test_source_detector.py /
test_chain_propagator_backward.py). This file focuses only on Task 5's
contract: threading ``progress_cb`` through the orchestrator into
``discover_sinks_llm`` / ``discover_sources_llm`` and the new per-function
taint-analysis ``ProgressEmitter``.

Strategy: patch the inner functions / class in the orchestrator module
namespace and assert the cb they receive is the one passed in. This proves the
wiring without driving the full MCP + parse pipeline with real SinkCallSites.
"""
import textwrap
import os
import tempfile

import pytest

from shannon_core import code_index as ci
from shannon_core.code_index.parameter_models import IntraResult


class _ShortCircuit(Exception):
    """Raised to abort the pipeline after an assertion point has run."""


def _write_min_repo() -> str:
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "app.py"), "w") as f:
        f.write(textwrap.dedent('''
            def handler(req):
                return query(req.param)
        '''))
    return tmp


@pytest.mark.asyncio
async def test_progress_cb_none_accepted_at_signature(monkeypatch):
    """progress_cb=None must be accepted by the signature (no TypeError).

    Patch the first stage (detect_language) to abort immediately so we don't
    need a real repo; the abort propagates as the patched exception, proving
    the kwarg was accepted (no TypeError) and we reached the body.
    """
    def _boom(*a, **kw):
        raise _ShortCircuit("signature accepted")

    monkeypatch.setattr(ci, "detect_language", _boom)
    with pytest.raises(_ShortCircuit):
        await ci.build_code_index_with_gitnexus(
            "/nonexistent/repo", mcp_client=object(), llm_client=None,
            progress_cb=None,
        )


@pytest.mark.asyncio
async def test_progress_cb_threaded_to_discover_sinks_and_sources(monkeypatch):
    """cb reaches discover_sinks_llm and discover_sources_llm as a kwarg."""
    captured: dict = {}

    async def _fake_discover_sinks(suspicious, llm_client, **kw):
        captured["sink_cb"] = kw.get("progress_cb")
        return [], []

    async def _fake_discover_sources(candidates, llm_client, **kw):
        captured["source_cb"] = kw.get("progress_cb")
        return []

    async def _fake_taint(*a, **kw):
        return IntraResult(tainted_params=set(), hits={}, local_steps=[])

    async def _fake_call_graph(*a, **kw):
        from shannon_core.code_index.models import CallGraphResult
        return CallGraphResult(edges=[], chains=[], entry_points=[])

    monkeypatch.setattr(ci, "build_call_graph_from_gitnexus", _fake_call_graph)
    monkeypatch.setattr(ci, "discover_sinks_llm", _fake_discover_sinks)
    monkeypatch.setattr(ci, "discover_sources_llm", _fake_discover_sources)
    monkeypatch.setattr(ci, "analyze_taint_llm", _fake_taint)

    async def cb(sample):
        pass

    tmp = _write_min_repo()
    await ci.build_code_index_with_gitnexus(
        tmp, mcp_client=object(), llm_client=None, progress_cb=cb,
    )

    assert captured["sink_cb"] is cb
    assert captured["source_cb"] is cb


@pytest.mark.asyncio
async def test_taint_emitter_built_with_cb_and_ticks(monkeypatch):
    """When sinks exist, a taint ProgressEmitter is built with the cb and ticks.

    Rather than fabricate a full SinkCallSite, we patch ``ProgressEmitter`` in
    the orchestrator namespace to record its construction + tick/finalize calls,
    and patch detect_sinks to surface one synthetic sink keyed to a real
    FuncBlock so _taint_one runs exactly once.
    """
    from shannon_core.code_index.models import CallGraphResult

    tmp = tempfile.mkdtemp()
    block_src = "def handler(req):\n    return query(req.param)\n"
    with open(os.path.join(tmp, "app.py"), "w") as f:
        f.write(block_src)

    # Stand-in sink: anything with a .caller_id attribute. _taint_one only
    # reads item[0]=func_id and item[1]=func_sinks; the orchestrator groups
    # sinks_by_func via s.caller_id, so a SimpleNamespace suffices.
    from types import SimpleNamespace
    fake_sink = SimpleNamespace(caller_id="app.py:handler:1")

    async def _fake_call_graph(*a, **kw):
        return CallGraphResult(edges=[], chains=[], entry_points=[])

    def _fake_detect_sinks(blocks, parser, source_provider=None):
        return [fake_sink]

    monkeypatch.setattr(ci, "collect_suspicious_calls", lambda *a, **kw: [])
    async def _fake_discover_sinks(suspicious, llm_client, **kw):
        return [], []
    async def _fake_discover_sources(candidates, llm_client, **kw):
        return []
    async def _fake_taint(*a, **kw):
        return IntraResult(tainted_params={"req"}, hits={"q": 0.9}, local_steps=[])

    monkeypatch.setattr(ci, "build_call_graph_from_gitnexus", _fake_call_graph)
    monkeypatch.setattr(ci, "detect_sinks", _fake_detect_sinks)
    monkeypatch.setattr(ci, "discover_sinks_llm", _fake_discover_sinks)
    monkeypatch.setattr(ci, "discover_sources_llm", _fake_discover_sources)
    monkeypatch.setattr(ci, "analyze_taint_llm", _fake_taint)
    # Abort after the taint stage so the fake SimpleNamespace sink never
    # reaches the CodeIndex assembly (which validates SinkCallSite).
    def _abort_after_taint(*a, **kw):
        raise _ShortCircuit("after taint emitter")
    monkeypatch.setattr(ci, "propagate_backward_across_chains", _abort_after_taint)

    # Capture ProgressEmitter construction + calls by replacing the class.
    instances: list = []

    class _CapturingEmitter:
        def __init__(self, phase, total, cb):
            self.phase = phase
            self.total = total
            self.cb = cb
            self.ticks: list = []
            self.finalized = False
            instances.append(self)

        async def tick(self, detail=None, hits_delta=0):
            self.ticks.append((detail, hits_delta))

        async def finalize(self, summary_detail):
            self.finalized = True
            self.summary = summary_detail

    monkeypatch.setattr(ci, "ProgressEmitter", _CapturingEmitter)

    async def cb(sample):
        pass

    with pytest.raises(_ShortCircuit):
        await ci.build_code_index_with_gitnexus(
            tmp, mcp_client=object(), llm_client=None, progress_cb=cb,
        )

    taint_emitters = [e for e in instances if e.phase == "taint-analysis"]
    assert len(taint_emitters) == 1, instances
    te = taint_emitters[0]
    assert te.cb is cb
    assert te.total == 1  # one func with a sink
    # _taint_one ran once (block resolved) and ticked once with the tainted
    # param counted as a hit (len(tainted_params)=1).
    assert len(te.ticks) == 1
    detail, hits_delta = te.ticks[0]
    assert hits_delta == 1, te.ticks
    assert detail == "taint flow in handler", detail
    assert te.finalized is True
