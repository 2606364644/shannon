from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.chain_propagator import _map_call_site_params_reverse


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln)+5, source_code=source, parameters=params,
                     language="typescript")


def test_reverse_map_propagates_tainted_callee_param_to_caller_arg():
    # caller 调用 callee(taintedParam),实参 req.query.x → caller 端 tainted = {req.query.x}
    caller = _blk("a.js:handler:1",
                  "function handler(req){ callee(req.query.x); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(taintedParam){ eval(taintedParam); }",
                  ["taintedParam"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"taintedParam"}, caller_block=caller)
    assert out == {"req.query.x"}  # caller 传入的实参表达式


def test_reverse_map_empty_when_callee_param_not_tainted():
    caller = _blk("a.js:handler:1", "function handler(req){ callee('literal'); }", ["req"])
    callee = _blk("a.js:callee:5", "function callee(p){}", ["p"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"p"}, caller_block=caller)
    # 实参 'literal' 不引用 tainted → 空
    assert out == set()


def test_reverse_map_conservative_when_no_call_args_found():
    caller = _blk("a.js:handler:1", "function handler(req){ /* no call */ }", ["req"])
    callee = _blk("a.js:callee:5", "function callee(p){}", ["p"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"p"}, caller_block=caller)
    # 找不到调用实参 → 保守:caller 所有 params 视为 tainted
    assert out == {"req"}
