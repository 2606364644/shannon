"""authz GitNexus 轨 `find_unguarded_sink_paths` 回归锚点(spec 子项④)。

本测试直接驱动 ``find_unguarded_sink_paths``(非 ``build_authz_gitnexus_track`` 的
JSON 文件 round-trip),构造最小但**真实**的 ``CodeIndex`` Python 对象,逐一锁定
`find_unguarded_sink_paths` 的门控行为:

  - 正向:req.* IDOR 风味源 / SourcePoint 注入风味源 → 候选产出(`len == 1`)
  - 负向:ownership 谓词短路 / 无源无 req.* / chains 空 → 候选为 0(`len == 0`)
  - 不变量:本轨**不消费** ``sink_call_sites``(Task 2 hunter sinks 与本轨解耦)

锁定的门控(按 ``authz_gitnexus_track.py`` 行号):
  entry 过滤 :266-270 → chain match :289 → re-anchor :292 →
  IDOR 源门 :297 → ownership 短路 :300 → sink 扫描 :304 →
  segment ownership :313 → ``_idor_reaches_sink`` :316。

排查结论(子项④): ``OWNERSHIP_PREDICATE_RE`` alt 5 误匹配 IDOR 源赋值
``const userId = req.params.userId``(把 IDOR 目标资源 id 当成 ownership 谓词)→
:300 短路真 IDOR 候选 → queue 空。``test_idor_source_assignment_not_flagged_as_ownership``
锁定该修复(窄化 alt 5 的 RHS,要求 auth context: `req.user`/`ctx`/`currentUser`)。
"""
from __future__ import annotations

import json

from supernova_core.code_index.authz_gitnexus_track import (
    build_authz_gitnexus_track,
    find_unguarded_sink_paths,
)
from supernova_core.code_index.models import (
    CallChain,
    CodeIndex,
    EntryPoint,
    FuncBlock,
)
from supernova_core.code_index.parameter_models import (
    ParameterSource,
    SinkCallSite,
    SinkCategory,
    SourcePoint,
)

# ---------------------------------------------------------------------------
# Fixture builders — construct minimal but REAL CodeIndex objects.
# ---------------------------------------------------------------------------

_HANDLER_ID = "u.js:getUser:1"
_SINK_ID = "db.js:persist:10"


def _handler(source: str, *, hid: str = _HANDLER_ID) -> FuncBlock:
    return FuncBlock(
        id=hid,
        file_path="u.js",
        function_name="getUser",
        start_line=1,
        end_line=6,
        source_code=source,
        parameters=[],
        class_name=None,
        decorators=[],
        language="typescript",
    )


def _sink(source: str, params: list[str], *, sid: str = _SINK_ID) -> FuncBlock:
    return FuncBlock(
        id=sid,
        file_path="db.js",
        function_name="persist",
        start_line=10,
        end_line=15,
        source_code=source,
        parameters=params,
        class_name=None,
        decorators=[],
        language="typescript",
    )


def _ep(hid: str = _HANDLER_ID) -> EntryPoint:
    return EntryPoint(
        func_block_id=hid,
        entry_type="http_route",
        route="/api/users/:userId",
        http_method="PUT",
        confidence=0.9,
        evidence="",
        needs_llm_review=False,
    )


def _chain(hid: str = _HANDLER_ID, sid: str = _SINK_ID) -> CallChain:
    return CallChain(
        entry_point_id=hid,
        path=[hid, sid],
        depth=1,
        has_unresolved=False,
    )


def _source_point(hid: str = _HANDLER_ID) -> SourcePoint:
    return SourcePoint(
        id=f"{hid}::userId::1",
        entry_point_id=hid,
        param_name="userId",
        source_type=ParameterSource.PATH_PARAM,
        expression="userId",
        file_path="u.js",
        line=1,
        confidence=0.9,
        rule_id="test-rule",
    )


def _sink_call_site(caller: str = _HANDLER_ID) -> SinkCallSite:
    """A Task-2-style hunter sink (sql) — MUST be ignored by the authz track."""
    return SinkCallSite(
        id=f"llm:u.js:1",
        caller_id=caller,
        callee_name="persist",
        callee_receiver="db",
        category=SinkCategory.SQL,
        sink_subtype="sql_raw_query",
        file_path="db.js",
        line=11,
        column=0,
        dangerous_slots=[],
        rule_id="llm-discovered-sink",
        needs_review=True,
    )


def _make_index(
    *,
    handler_src: str,
    sink_src: str = "function persist(userId){ db.users.save(userId); }",
    sink_params: list[str] | None = None,
    chains: list[CallChain] | None = None,
    source_points: list[SourcePoint] | None = None,
    sink_call_sites: list[SinkCallSite] | None = None,
    hid: str = _HANDLER_ID,
    sid: str = _SINK_ID,
) -> CodeIndex:
    """Build a minimal CodeIndex that drives find_unguarded_sink_paths meaningfully."""
    handler = _handler(handler_src, hid=hid)
    sink = _sink(sink_src, sink_params if sink_params is not None else ["userId"], sid=sid)
    return CodeIndex(
        repository="r",
        language="typescript",
        total_blocks=2,
        total_entry_points=1,
        total_chains=len(chains) if chains is not None else 1,
        blocks=[handler, sink],
        edges=[],
        entry_points=[_ep(hid)],
        chains=chains if chains is not None else [_chain(hid, sid)],
        source_points=source_points or [],
        sink_call_sites=sink_call_sites or [],
    )


# ---------------------------------------------------------------------------
# Positive cases — candidate IS produced.
# ---------------------------------------------------------------------------


def test_candidate_produced_via_req_ref_seed():
    """IDOR 风味源 via req.params in handler (no SourcePoint) → 1 candidate.

    Exercises: gate :297 (req.* ref passes IDOR-source gate) + :316 (_idor_reaches_sink
    forward propagation with req.* seed). Handler does direct pass-through to avoid
    the (now-fixed) alt-5 ownership over-match on `userId = req.*` assignments.
    """
    index = _make_index(
        handler_src="function getUser(req) { return persist(req.params.userId); }",
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 1
    cand = result[0]
    assert cand.endpoint_id == _HANDLER_ID
    assert cand.sink_id == _SINK_ID
    assert cand.handler_id == _HANDLER_ID


def test_candidate_produced_via_source_point():
    """Injection-flavor SourcePoint (no req.* in handler) → 1 candidate.

    Exercises: gate :297 alternative path — ep_sources non-empty passes the IDOR-source
    gate even when the handler has no req.* reference. This is the Java @PathVariable
    path (Task 4's java-path-variable rule produces SourcePoints).
    """
    index = _make_index(
        handler_src="function getUser(userId) { return persist(userId); }",
        source_points=[_source_point()],
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 1
    # source evidence is captured
    assert result[0].source_point_ids == (f"{_HANDLER_ID}::userId::1",)


# ---------------------------------------------------------------------------
# Ownership short-circuit — NEGATIVE.
# ---------------------------------------------------------------------------


def test_ownership_predicate_in_handler_short_circuits():
    """Legitimate ownership predicate in handler → 0 candidates (gate :300).

    `findByOwnerId(req.user.id)` is a real ownership check (alt 3 + alt 4 of
    OWNERSHIP_PREDICATE_RE). The dominance heuristic correctly trusts it and drops
    the candidate for the LLM judge to re-confirm.
    """
    index = _make_index(
        handler_src=(
            "function updateUser(req) { "
            "  return repo.findByOwnerId(req.user.id); "
            "}"
        ),
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 0


def test_idor_source_assignment_not_flagged_as_ownership():
    """IDOR-flavor source assignment MUST NOT trip the ownership short-circuit.

    Regression for OWNERSHIP_PREDICATE_RE alt-5 over-match: the IDOR vector is
    `const userId = req.params.userId;` (target resource id from user input). The
    legacy regex matched `userId = req` via alt 5 and false-positively flagged this
    as ownership → gate :300 short-circuited a real IDOR → queue empty (spec 子项④
    真根因之一). After narrowing alt 5's RHS to require an auth context
    (`req.user` / `ctx` / `currentUser`), this handler must produce a candidate.
    """
    index = _make_index(
        handler_src=(
            "function getUser(req) { "
            "  const userId = req.params.userId; "
            "  return persist(userId); "
            "}"
        ),
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 1, (
        "IDOR source assignment `userId = req.params.userId` must NOT be flagged as "
        "ownership (regression: OWNERSHIP_PREDICATE_RE alt-5 over-match, spec 子项④)"
    )


# ---------------------------------------------------------------------------
# Other gates — NEGATIVE.
# ---------------------------------------------------------------------------


def test_no_source_no_req_ref_yields_zero_candidates():
    """No SourcePoint AND no req.* in handler → gate :297 trips → 0 candidates."""
    index = _make_index(
        handler_src="function getUser() { return persist(42); }",
        sink_src="function persist(n){ db.users.save(n); }",
        sink_params=["n"],
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 0


def test_empty_chains_yields_zero_candidates():
    """Empty chains → 0 iterations of the chain loop → 0 candidates.

    This is the HONEST version of the brief's vacuous `>= 0` assertion. With
    `chains=[]`, find_unguarded_sink_paths produces zero candidates regardless of
    sinks/sources — so the brief's `has_sink=True & has_idor_source=True` fixture was
    vacuous. This test locks the real behavior (0, not `>= 0`).
    """
    index = _make_index(
        handler_src="function getUser(req) { return persist(req.params.userId); }",
        chains=[],
        source_points=[_source_point()],
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Independence invariant — authz track does NOT consume sink_call_sites.
# ---------------------------------------------------------------------------


def test_sink_call_sites_not_consumed_by_authz_track():
    """Task 2's hunter sinks (sink_call_sites) are NOT read by this track.

    The authz track identifies side-effect sinks via _SIDE_EFFECT_SINK_RE applied to
    FuncBlock source along index.chains — NOT via sink_call_sites. A chain whose
    terminal block is NOT a side-effect sink yields 0 candidates even when
    sink_call_sites is populated with a sql sink at the same block. This locks the
    recon correction: Task 2 hunter sinks do not directly feed this track (the plan's
    R1 hypothesis was misframed).
    """
    # Sink block source has NO side-effect keyword → _is_side_effect_sink returns False.
    no_side_effect_sink = _sink(
        source="function persist(userId){ return userId; }",
        params=["userId"],
    )
    index = CodeIndex(
        repository="r",
        language="typescript",
        total_blocks=2,
        total_entry_points=1,
        total_chains=1,
        blocks=[_handler("function getUser(req) { return persist(req.params.userId); }"), no_side_effect_sink],
        edges=[],
        entry_points=[_ep()],
        chains=[_chain()],
        source_points=[_source_point()],
        # Task 2 hunter sink present — but authz track must NOT consult it.
        sink_call_sites=[_sink_call_site()],
    )
    result = find_unguarded_sink_paths(index)
    assert len(result) == 0, (
        "sink_call_sites must NOT feed find_unguarded_sink_paths; side-effect detection "
        "is via _SIDE_EFFECT_SINK_RE on chain blocks only"
    )


def test_build_track_reads_intermediate_paths(tmp_path):
    """tiering 回归：code_index.json / framework_analysis.json 落 intermediate/
    （写侧 write_index_files / framework_analyzer）→ build_authz_gitnexus_track 必须
    读到，不再静默降级（曾只读平铺 → dominance/framework 候选全空 → authz GitNexus
    轨空壳）。"""
    index = _make_index(
        handler_src="function getUser(req) { return persist(req.params.userId); }",
    )
    (tmp_path / "intermediate").mkdir()
    (tmp_path / "intermediate" / "code_index.json").write_text(
        index.model_dump_json(indent=2)
    )
    (tmp_path / "intermediate" / "framework_analysis.json").write_text(json.dumps({
        "detected_framework": {"name": "express"},
        "inferred_endpoints": [
            {
                "method": "GET", "path": "/api/users/:id",
                "source": "framework-auto-generated",
                "model": "User",
                "vulnerability_indicators": ["no-ownership-check"],
            }
        ],
    }))

    result = build_authz_gitnexus_track(str(tmp_path))

    assert len(result.dominance_candidates) == 1, "intermediate/code_index.json 应被读到"
    assert len(result.framework_candidates) == 1, "intermediate/framework_analysis.json 应被读到"
    assert result.entry_point_total == 1
