from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.models import CodeIndex, ParameterSource


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


from supernova_core.code_index.source_detector import detect_sources, DEFAULT_SOURCE_RULES
from supernova_core.code_index.models import FuncBlock


def test_default_source_rules_externalized_stable():
    """外部化锚点:source 规则库搬迁到 YAML 后数量不退化(原 18 条)。

    detect_sources 硬编码迭代 DEFAULT_SOURCE_RULES,若 YAML 写错(漏语言分组)此前无任何
    回归保护 —— 此断言是唯一护栏。
    """
    assert len(DEFAULT_SOURCE_RULES) >= 18
    ids = {r.rule_id for r in DEFAULT_SOURCE_RULES}
    for rid in ("ts-express-path", "py-django-get", "php-get", "go-gin-query", "java-request-param"):
        assert rid in ids, f"missing source rule {rid}"


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


def test_build_code_index_populates_source_points():
    """pipeline 冒烟:build_code_index_with_gitnexus ⑧b 填充 source_points。

    route 注册放在 setup 函数体内(Express Pass 1: app.get 在 FuncBlock.source_code
    内 → func_block_id 是真实 block id,非 Pass 2 合成 "::0"),使 entry handler 真实
    被识别并落入 detect_sources 的扫描范围。断言 source_points 真实被填充(无 mock)。
    """
    import asyncio
    from unittest.mock import AsyncMock
    from supernova_core.code_index import build_code_index_with_gitnexus
    import tempfile, os

    # route 注册 + handler 取用都在 setupRoutes 函数体内 → detect_entry_points Pass 1 命中
    src = (
        "function setupRoutes(app) {\n"
        "  app.get('/allocations/:userId', function displayAllocations(req, res){\n"
        "    const userId = req.params.userId;\n"
        "    const threshold = req.query.threshold;\n"
        "  });\n"
        "}\n"
    )

    with tempfile.TemporaryDirectory() as repo:
        f = os.path.join(repo, "app.js")
        with open(f, "w") as fh:
            fh.write(src)
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)

        fake_mcp = AsyncMock()
        fake_mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
        fake_llm = AsyncMock(return_value="[]")  # LLM soft 无产出

        index, _rule_gaps, _source_gaps, _storage_gaps = asyncio.run(build_code_index_with_gitnexus(
            repo, mcp_client=fake_mcp, llm_client=fake_llm,
        ))
        # entry handler 的 req.params.userId / req.query.threshold 应被识别
        names = {(s.param_name, s.source_type.value) for s in index.source_points}
        assert ("userId", "path") in names
        assert ("threshold", "query") in names


# ===== Koa(ctx.*)source 规则(trip 等 Koa+Sequelize 项目治本,改动1)=====


def test_koa_ctx_query_yields_query_source():
    """Koa ctx.query.userId → ts-koa-query-direct 命中产 query source(改动1)。

    回归锚点:trip 104/141 controller 用 ctx.*,原 Express-only(req.*)规则全漏 →
    加 5 条 ts-koa-* 规则覆盖 ctx.request.body / ctx.query / ctx.params / ctx.headers。
    """
    src = (
        "function getUser(ctx) {\n"
        "  const userId = ctx.query.userId;\n"      # line 2
        "  const name = ctx.request.body.name;\n"   # line 3
        "}\n"
    )
    block = _block("user.ts", "getUser", 11, src, "typescript", ["ctx"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    names = {(s.param_name, s.source_type.value, s.rule_id) for s in out}
    assert ("userId", "query", "ts-koa-query-direct") in names
    assert ("name", "body", "ts-koa-body") in names


def test_koa_ctx_params_yields_path_source():
    """Koa ctx.params.id → ts-koa-params 命中产 path source(改动1)。"""
    src = "function f(ctx){ const id = ctx.params.id; }\n"
    block = _block("r.ts", "f", 1, src, "typescript", ["ctx"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    assert any(s.param_name == "id" and s.source_type.value == "path"
               and s.rule_id == "ts-koa-params" for s in out)


def test_koa_ctx_headers_yields_header_source():
    """Koa ctx.headers.token → ts-koa-headers 命中产 header source(改动1)。"""
    src = "function f(ctx){ const t = ctx.headers.token; }\n"
    block = _block("r.ts", "f", 1, src, "typescript", ["ctx"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    assert any(s.param_name == "token" and s.source_type.value == "header"
               and s.rule_id == "ts-koa-headers" for s in out)
