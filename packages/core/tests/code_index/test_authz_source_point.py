from supernova_core.code_index.models import CallChain, CodeIndex, EntryPoint, FuncBlock, ParameterSource
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.authz_gitnexus_track import (
    IDORCandidateChain, _source_reaches_sink, find_unguarded_sink_paths,
)


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln)+5, source_code=source, parameters=params,
                     language="typescript")


def test_source_reaches_sink_when_param_flows_to_callee():
    handler = _blk("a.js:h:1",
                   "function h(req){ dao(req.params.userId); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(id){ db.update({userId: id}); }", ["id"])
    sp = SourcePoint(
        id="a.js:h:1::userId::1", entry_point_id=handler.id, param_name="userId",
        source_type=ParameterSource.PATH_PARAM, expression="req.params.userId",
        file_path="a.js", line=1, confidence=0.9, rule_id="ts-express-path",
    )
    blocks_by_id = {handler.id: handler, sink_func.id: sink_func}
    # segment: handler → sink_func
    assert _source_reaches_sink([sp], [handler.id, sink_func.id], blocks_by_id) is True


def test_source_does_not_reach_when_no_flow():
    handler = _blk("a.js:h:1",
                   "function h(req){ dao('constant'); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(id){ db.update({userId: id}); }", ["id"])
    sp = SourcePoint(
        id="a.js:h:1::userId::1", entry_point_id=handler.id, param_name="userId",
        source_type=ParameterSource.PATH_PARAM, expression="req.params.userId",
        file_path="a.js", line=1, confidence=0.9, rule_id="ts-express-path",
    )
    blocks_by_id = {handler.id: handler, sink_func.id: sink_func}
    assert _source_reaches_sink([sp], [handler.id, sink_func.id], blocks_by_id) is False


def _ep(func_id, entry_type="http_route", route="/r"):
    return EntryPoint(func_block_id=func_id, entry_type=entry_type, route=route,
                      http_method="GET", confidence=0.9, evidence="e",
                      needs_llm_review=False, source="code_index")


def test_find_unguarded_filters_entry_without_sourcepoint():
    # entry 无 SourcePoint → 不产候选（降过报）
    handler = _blk("a.js:h:1",
                   "function h(req){ dao.update({a:1}); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(){ this.update(); }", [])
    index = CodeIndex(
        repository="r", language="typescript", total_blocks=2,
        total_entry_points=1, total_chains=1,
        blocks=[handler, sink_func], edges=[],
        entry_points=[_ep(handler.id)],
        chains=[CallChain(entry_point_id=handler.id,
                          path=[handler.id, sink_func.id], depth=1, has_unresolved=False)],
        source_points=[],  # 无 SourcePoint
    )
    out = find_unguarded_sink_paths(index)
    assert out == []  # 三重过滤第①层：无 SourcePoint → 跳过


def test_find_unguarded_yields_candidate_with_source_point_ids():
    handler = _blk("a.js:h:1",
                   "function h(req){ dao(req.params.userId); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(id){ db.users.update({userId:id}); }", ["id"])
    sp = SourcePoint(
        id="a.js:h:1::userId::1", entry_point_id=handler.id, param_name="userId",
        source_type=ParameterSource.PATH_PARAM, expression="req.params.userId",
        file_path="a.js", line=1, confidence=0.9, rule_id="ts-express-path",
    )
    index = CodeIndex(
        repository="r", language="typescript", total_blocks=2,
        total_entry_points=1, total_chains=1,
        blocks=[handler, sink_func], edges=[],
        entry_points=[_ep(handler.id, route="/u/:userId")],
        chains=[CallChain(entry_point_id=handler.id,
                          path=[handler.id, sink_func.id], depth=1, has_unresolved=False)],
        source_points=[sp],
    )
    out = find_unguarded_sink_paths(index)
    assert len(out) == 1
    assert sp.id in out[0].source_point_ids  # 附 source 证据
