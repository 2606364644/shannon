from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.models import CodeIndex, ParameterSource


def test_source_point_basic_fields():
    sp = SourcePoint(
        id="app/routes/allocations.js:displayAllocations:11::userId::18",
        entry_point_id="app/routes/allocations.js:displayAllocations:11",
        param_name="userId",
        source_type=ParameterSource.PATH_PARAM,
        expression="req.params.userId",
        file_path="app/routes/allocations.js",
        line=18,
        validation="parseInt()",
        confidence=0.9,
        rule_id="ts-express-path",
    )
    assert sp.param_name == "userId"
    assert sp.source_type == ParameterSource.PATH_PARAM
    assert sp.validation == "parseInt()"
    assert sp.needs_review is False  # default


def test_code_index_has_source_points_field():
    ci = CodeIndex(
        repository="r", language="typescript", total_blocks=0,
        total_entry_points=0, total_chains=0, blocks=[], edges=[],
        entry_points=[], chains=[],
    )
    assert ci.source_points == []  # default empty list


from shannon_core.code_index.source_detector import detect_sources, DEFAULT_SOURCE_RULES
from shannon_core.code_index.models import FuncBlock


def _block(file_path, func_name, start_line, source, language="typescript", params=None):
    return FuncBlock(
        id=f"{file_path}:{func_name}:{start_line}", file_path=file_path,
        function_name=func_name, start_line=start_line, end_line=start_line + 10,
        source_code=source, parameters=params or [], language=language,
    )


def _provider_from(block):
    return lambda b: block.source_code.encode("utf-8") if b.id == block.id else None


def test_express_req_params_yields_path_source():
    src = (
        "function displayAllocations(req, res) {\n"
        "  const userId = req.params.userId;\n"   # line 2
        "  const threshold = req.query.threshold;\n"
        "}\n"
    )
    block = _block("allocations.js", "displayAllocations", 11, src, "typescript", ["req", "res"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    sp = next(s for s in out if s.param_name == "userId")
    assert sp.source_type.value == "path"
    assert sp.expression == "req.params.userId"
    assert sp.line == 12  # start_line(11) + 行内偏移(1) → 第 2 行
    assert sp.rule_id.startswith("ts-express")


def test_express_req_query_and_body_distinct_source_types():
    src = "function f(req){ const q=req.query.q; const b=req.body.b; }\n"
    block = _block("f.js", "f", 1, src, "typescript", ["req"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    types = {(s.param_name, s.source_type.value) for s in out}
    assert ("q", "query") in types
    assert ("b", "body") in types


def test_django_request_get_yields_query():
    src = "def view(request):\n    q = request.GET['q']\n    return HttpResponse(q)\n"
    block = _block("views.py", "view", 5, src, "python", ["request"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    assert any(s.param_name == "q" and s.source_type.value == "query" for s in out)


def test_php_get_yields_query():
    src = "<?php $id = $_GET['id']; ?>\n"
    block = _block("index.php", "handler", 1, src, "php", [])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    assert any(s.param_name == "id" and s.source_type.value == "query" for s in out)


def test_non_entry_block_skipped():
    src = "function helper(req){ return req.query.x; }\n"
    block = _block("util.js", "helper", 1, src, "typescript", ["req"])
    # entry_point_ids 为空 → 该 block 不被扫
    out = detect_sources([block], parser=None, entry_point_ids=set(),
                         source_provider=_provider_from(block))
    assert out == []


def test_dedup_same_field_same_type():
    # 同一 handler 里 userId 被 req.params 取用两次 → 去重为一个 SourcePoint
    src = "function f(req){ let a=req.params.id; let b=req.params.id; }\n"
    block = _block("f.js", "f", 1, src, "typescript", ["req"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    ids = [(s.entry_point_id, s.param_name, s.source_type) for s in out]
    assert len(ids) == len(set(ids))  # no duplicates
