# packages/core/tests/code_index/test_authz_dominance.py
from supernova_core.code_index.models import (
    CallChain, CallEdge, CodeIndex, EntryPoint, FuncBlock, ParameterSource,
)
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.authz_gitnexus_track import (
    IDORCandidateChain,
    find_unguarded_sink_paths,
)


def _block(bid, source, name=None, params=None):
    file_path, func_name, line = bid.rsplit(":", 2)
    return FuncBlock(
        id=bid, file_path=file_path, function_name=name or func_name,
        start_line=int(line), end_line=int(line) + 5, source_code=source,
        parameters=params or [], language="typescript",
    )


def _idx(blocks, edges, chains, entry_points=None, source_points=None):
    return CodeIndex(
        repository="r", language="typescript", total_blocks=len(blocks),
        total_entry_points=len(entry_points or []), total_chains=len(chains),
        blocks=blocks, edges=edges, entry_points=entry_points or [],
        chains=chains, source_points=source_points or [],
    )


def _sp(handler_id, expression, param_name, sp_id=None):
    """Build a SourcePoint anchored at handler_id with given user-controlled expression."""
    return SourcePoint(
        id=sp_id or f"{handler_id}::{param_name}::1",
        entry_point_id=handler_id, param_name=param_name,
        source_type=ParameterSource.PATH_PARAM, expression=expression,
        file_path="x", line=1, confidence=0.9, rule_id="test-rule",
    )


def _ep(handler_id, route, method="DELETE"):
    return EntryPoint(
        func_block_id=handler_id, entry_type="http_route", route=route,
        http_method=method, confidence=0.9, evidence="",
        needs_llm_review=False, authentication="required",
    )


def _proc_ep(handler_id):
    """process entry: entry_type='gitnexus_process', route=None（SRPC 业务入口）。"""
    return EntryPoint(
        func_block_id=handler_id, entry_type="gitnexus_process", route=None,
        http_method=None, confidence=0.9, evidence="GitNexus process entry",
        needs_llm_review=False, source="gitnexus",
    )


def test_no_candidates_when_handler_has_ownership_guard():
    """Handler body has ORM ownership predicate → no unguarded sink path."""
    handler = _block(
        "u.js:update:10",
        "async function update(req){\n"
        "  const row = await db.user.findFirst({ where: { userId: req.user.id } });\n"
        "  await db.user.update(row);\n"
        "}",
    )
    sink = _block("db.js:update:1", "function update(){ model.save(); }")
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/users/:id")])
    cands = find_unguarded_sink_paths(index)
    assert cands == []  # ownership guard present in handler


def test_candidate_when_no_ownership_guard_reaches_sink():
    """Handler reaches a side-effect sink (ORM update) with no ownership predicate."""
    handler = _block(
        "u.js:update:10",
        "async function update(req){ await persist(req.params.id, req.body); }",
    )
    sink = _block("repo.js:persist:1", "function persist(id, body){ db.user.update(); }", params=["id", "body"])
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    sp = _sp(handler.id, "req.params.id", "id")
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/Feedbacks/:id")],
                 source_points=[sp])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    c = cands[0]
    assert c.handler_id == handler.id
    assert c.sink_id == sink.id
    assert c.guard_nodes_on_path == ()  # no guard
    assert sp.id in c.source_point_ids  # source 证据


def test_candidate_dedup_by_endpoint_sink():
    """Two chains to the same sink from the same endpoint → one candidate."""
    handler = _block("h.js:f:1", "async function f(req){ await s(req.id); }")
    sink = _block("s.js:g:1", "function g(id){ db.user.remove(); }", params=["id"])
    ch1 = CallChain(entry_point_id=handler.id, path=[handler.id, sink.id], depth=1, has_unresolved=False)
    ch2 = CallChain(entry_point_id=handler.id, path=[handler.id, "x.js:m:1", sink.id], depth=2, has_unresolved=False)
    sp = _sp(handler.id, "req.id", "id")
    index = _idx([handler, sink], [], [ch1, ch2], [_ep(handler.id, "/api/u/:id")],
                 source_points=[sp])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1  # deduped by (endpoint, sink)


def test_chains_without_side_effect_sink_are_skipped():
    """Chain ending in a non-sink (e.g. logger) → not a candidate."""
    handler = _block("h.js:f:1", "function f(){ log('x'); }")
    leaf = _block("log.js:l:1", "function l(){ console.log(); }")  # not a side-effect sink
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, leaf.id], depth=1, has_unresolved=False)
    index = _idx([handler, leaf], [], [chain], [_ep(handler.id, "/api/x")])
    assert find_unguarded_sink_paths(index) == []


def test_respects_max_paths_per_endpoint():
    """Cap candidate count per endpoint to bound the judge-LLM cost."""
    # handler 把 tainted 传给每个 sink（gi 接 id 参数）
    handler = _block("h.js:f:1", "function f(req){ sink0(req.id); sink1(req.id); sink2(req.id); sink3(req.id); sink4(req.id); }")
    sinks = [
        _block(f"s.js:g{i}:1", f"function g{i}(id){{ db.user.update(); }}", params=["id"])
        for i in range(5)
    ]
    chains = [
        CallChain(entry_point_id=handler.id, path=[handler.id, s.id],
                  depth=1, has_unresolved=False)
        for s in sinks
    ]
    sp = _sp(handler.id, "req.id", "id")
    index = _idx([handler, *sinks], [], chains, [_ep(handler.id, "/api/u/:id")],
                 source_points=[sp])
    cands = find_unguarded_sink_paths(index, max_paths_per_endpoint=2)
    assert len(cands) == 2


def test_empty_index_yields_no_candidates():
    index = _idx([], [], [], [])
    assert find_unguarded_sink_paths(index) == []


def test_process_entry_route_none_is_admitted():
    """断点①: process entry route=None 必须进候选（不能被 route is not None 挡）。"""
    handler = _block("h.js:f:1", "function f(req){ await s(req.id); }")
    sink = _block("s.js:g:1", "function g(id){ db.user.update(); }", params=["id"])
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, sink.id], depth=1, has_unresolved=False)
    sp = _sp(handler.id, "req.id", "id")
    index = _idx([handler, sink], [], [chain], [_proc_ep(handler.id)],
                 source_points=[sp])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    assert cands[0].endpoint_id == handler.id


def test_side_effect_sink_in_middle_of_chain_is_found():
    """断点②(决策7): sink 在链中间(非 terminal) → 扫全链命中。模拟 0→21 的核心。
    链: entry → middle(side-effect sink) → leaf(非 sink)。terminal 非 sink。"""
    entry = _block("e.js:e:1", "function e(req){ m(req); leaf(); }")
    middle = _block("m.js:m:1", "function m(arg){ db.user.update(); }", params=["arg"])   # side-effect sink 在中间
    leaf = _block("l.js:l:1", "function l(){ return 1; }")             # terminal 非 sink
    chain = CallChain(entry_point_id=entry.id, path=[entry.id, middle.id, leaf.id], depth=2, has_unresolved=False)
    sp = _sp(entry.id, "req", "req")
    index = _idx([entry, middle, leaf], [], [chain], [_ep(entry.id, "/api/x")],
                 source_points=[sp])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    c = cands[0]
    assert c.sink_id == middle.id
    assert c.sink_step_idx == 1   # middle 是 path[1]


def test_ownership_guard_on_segment_blocks_candidate():
    """决策6: ownership 守卫出现在 entry→sink_step 段 → 不产候选。"""
    entry = _block("e.js:e:1", "function e(req){ const o = db.find({where:{userId:req.user.id}}); m(o); }")
    middle = _block("m.js:m:1", "function m(){ db.user.update(); }")
    chain = CallChain(entry_point_id=entry.id, path=[entry.id, middle.id], depth=1, has_unresolved=False)
    index = _idx([entry, middle], [], [chain], [_ep(entry.id, "/api/x")])
    # entry 源码含 ownership 谓词 → handler_has_ownership_guard 短路（既有逻辑）→ 无候选
    assert find_unguarded_sink_paths(index) == []


# ---------------------------------------------------------------------------
# 注册块 re-anchor + IDOR 源补召回（velvety-wibbling-candy）。
# Express 注册式路由让 entry 塌缩到注册块（含 ≥2 条 app/router.(get|post|…) 注册），
# 破坏 (1) source 传播 (2) SourcePoint 锚定 (3) IDOR 风味源识别。修复：复用 GitNexus
# 已追的 chain path[1] 真实 handler，把判定 anchor 从注册块下移到真实 handler，并补
# IDOR 风味源 req.params/body/query。
# ---------------------------------------------------------------------------

# 注册块源码：含 3 条 Express 路由注册（≥2 即判注册块）。
_REG_BLOCK_SRC = (
    "(app, db) => {\n"
    '  app.get("/api/a", aHandler);\n'
    '  app.post("/api/b", bHandler);\n'
    '  app.delete("/api/c", cHandler);\n'
    "}\n"
)


def test_registration_block_reanchors_to_real_handler():
    """注册块 head（≥2 路由注册）+ path[1] 真实 handler 引 req.params →
    anchor 下移到真实 handler：候选 handler_id=真实 handler（非注册块）。"""
    reg = _block("app/routes/index.js:index:11", _REG_BLOCK_SRC)
    real_handler = _block(
        "app/routes/alloc.js:AllocHandler:6",
        "function AllocHandler(db){ this.display=(req,res)=>{ dao.get(req.params.id); }; }",
    )
    sink = _block("app/data/dao.js:get:4", "function get(id){ db.user.update(); }", params=["id"])
    chain = CallChain(
        entry_point_id=reg.id, path=[reg.id, real_handler.id, sink.id],
        depth=2, has_unresolved=False,
    )
    # 注册块无 SourcePoint；真实 handler 引 req.params → has_idor_source 经 req.* 成立
    index = _idx([reg, real_handler, sink], [], [chain], [_ep(reg.id, "/api/a")])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    assert cands[0].handler_id == real_handler.id   # re-anchored，非注册块
    assert cands[0].endpoint_id == reg.id
    assert cands[0].sink_id == sink.id


def test_registration_block_without_resolvable_handler_skipped():
    """注册块 head + path[1] 不可解析（缺失）+ path[2] 是 sink →
    无可 anchor 的真实 handler → 0 候选，不崩。注册块带 SourcePoint 迫使 re-anchor
    逻辑运行（否则被 ① 无 source 丢弃，测不到 re-anchor）。"""
    reg = _block("app/routes/index.js:index:11", _REG_BLOCK_SRC)
    sink = _block("app/data/dao.js:get:4", "function get(id){ db.user.update(); }", params=["id"])
    chain = CallChain(
        entry_point_id=reg.id,
        path=[reg.id, "app/data/missing.js:DAO:1", sink.id],   # path[1] 缺失中间块
        depth=2, has_unresolved=False,
    )
    sp = _sp(reg.id, "req.query.url", "url")
    index = _idx([reg, sink], [], [chain], [_ep(reg.id, "/api/a")], source_points=[sp])
    assert find_unguarded_sink_paths(index) == []


def test_gitnexus_process_entry_seeds_idor_source_from_req_params():
    """gitnexus_process entry + req.params.userId + 无 SourcePoint → 候选。
    回归 A4：IDOR 风味源 req.params 不被 source 检测识别（只认注入风味 query/body），
    旧逻辑 ① 无 SourcePoint 即丢弃 → 0 候选。修复后认 req.* 为 IDOR 源。"""
    handler = _block(
        "app/routes/alloc.js:AllocHandler:6",
        "function AllocHandler(db){ this.display=(req,res)=>{ dao.get(req.params.userId); }; }",
    )
    sink = _block("app/data/user-dao.js:get:4", "function get(uid){ db.user.insert(); }", params=["uid"])
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    # _proc_ep（route=None）+ 无 SourcePoint
    index = _idx([handler, sink], [], [chain], [_proc_ep(handler.id)])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    assert cands[0].handler_id == handler.id
    assert cands[0].source_point_ids == ()   # 无 SourcePoint 命中


def test_idor_reaches_sink_tolerates_missing_intermediate():
    """path=[handler, MISSING_DAO, sink] → 缺失中间块不杀传播，保守继续（宁过报）。
    模拟 NodeGoat chain[6]：AllocationsHandler→AllocationsDAO(类节点无行号)→UserDAO。"""
    handler = _block(
        "app/routes/alloc.js:AllocHandler:6",
        "function AllocHandler(db){ this.display=(req,res)=>{ dao.exec(req.params.id); }; }",
    )
    sink = _block("app/data/user-dao.js:exec:4", "function exec(x){ db.user.update(); }", params=["x"])
    chain = CallChain(
        entry_point_id=handler.id,
        path=[handler.id, "app/data/missing.js:DAO:1", sink.id],   # 中间缺失
        depth=2, has_unresolved=False,
    )
    sp = _sp(handler.id, "req.params.id", "id")
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/alloc/:id")],
                 source_points=[sp])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    assert cands[0].sink_id == sink.id


def test_real_handler_with_ownership_guard_still_blocked():
    """re-anchor 到真实 handler 后，真实 handler 含 ownership 谓词 → 仍阻断。
    回归保护：re-anchor 下移 anchor 不破坏 dominance 守卫。注册块带 SourcePoint
    迫使 re-anchor 逻辑运行。"""
    reg = _block("app/routes/index.js:index:11", _REG_BLOCK_SRC)
    guarded = _block(
        "app/routes/x.js:XHandler:8",
        "function XHandler(db){ const r = db.findFirst({where:{userId:req.user.id}}); return render(r); }",
    )
    sink = _block("app/data/dao.js:save:4", "function save(r){ db.user.update(); }", params=["r"])
    chain = CallChain(
        entry_point_id=reg.id, path=[reg.id, guarded.id, sink.id],
        depth=2, has_unresolved=False,
    )
    sp = _sp(reg.id, "req.query.url", "url")
    index = _idx([reg, guarded, sink], [], [chain], [_ep(reg.id, "/api/a")], source_points=[sp])
    assert find_unguarded_sink_paths(index) == []


def test_session_chain_with_ownership_still_blocked():
    """注册块 head + path[1]=SessionHandler 含 ownership（findByUserId，session-sourced）
    → 0 候选。回归保护：session 系（handler 自带 ownership）不被 re-anchor 破坏守卫。"""
    reg = _block("app/routes/index.js:index:11", _REG_BLOCK_SRC)
    session = _block(
        "app/routes/session.js:SessionHandler:8",
        "function SessionHandler(db){ const u = userDAO.findByUserId(req.session.userId); return u; }",
    )
    sink = _block("app/data/user-dao.js:get:4", "function get(uid){ db.user.update(); }", params=["uid"])
    chain = CallChain(
        entry_point_id=reg.id, path=[reg.id, session.id, sink.id],
        depth=2, has_unresolved=False,
    )
    sp = _sp(reg.id, "req.query.url", "url")
    index = _idx([reg, session, sink], [], [chain], [_ep(reg.id, "/api/session")], source_points=[sp])
    assert find_unguarded_sink_paths(index) == []
