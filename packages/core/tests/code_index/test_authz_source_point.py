from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.authz_gitnexus_track import _source_reaches_sink


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
