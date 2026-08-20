from pathlib import Path

from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
from supernova_core.code_index.parsers import get_parser

FIXTURES = Path(__file__).parent / "fixtures"
EXPRESS_APP = FIXTURES / "typescript" / "express_app.ts"
TS_APP = Path(__file__).parent / "fixtures" / "typescript" / "express_app.ts"


class TestTypeScriptParserFuncBlocks:
    def test_extracts_named_functions(self):
        parser = TypeScriptParser()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listOrders" in names
        assert "getUsers" in names
        assert "saveUser" in names
        assert "getOrders" in names

    def test_extracts_parameters(self):
        parser = TypeScriptParser()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert len(by_name["listOrders"].parameters) >= 2

    def test_block_language_is_typescript(self):
        parser = TypeScriptParser()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        for block in blocks:
            assert block.language == "typescript"


class TestTypeScriptParserRegistry:
    def test_registered_in_parser_registry(self):
        from supernova_core.code_index.parsers import _PARSER_CLASSES
        assert "typescript" in _PARSER_CLASSES


class TestTypescriptParserIterCalls:
    def test_iter_calls_function_body(self):
        """getUsers() body has db.query('SELECT...')."""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        assert len(calls) >= 1

    def test_destructure_member_call(self):
        """db.query(...) → callee=query, receiver=db"""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("query", "db") in callees

    def test_destructure_bare_call(self):
        """getUsers() → callee=getUsers, receiver=None"""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listOrders"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getOrders", None) in callees

    def test_extract_arg_expressions(self):
        """query('SELECT * FROM users') → ['\"SELECT * FROM users\"']"""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        for call in calls:
            callee, _ = parser.destructure_call(call)
            if callee == "query":
                args = parser.extract_arg_expressions(call, source)
                assert len(args) == 1
                assert "SELECT" in args[0]
                return
        assert False, "query call not found"


# ===== spec 2026-08-21 修复点 B: 嵌套函数形参并入 block.parameters =====

class TestNestedFnParamsMerge:
    """NodeGoat 形态根因修复:constructor 内 `this.handler = (req, res) => {}` 的
    req/res 原本不进参数集 → intra LLM 问"db 能否到 sink"问错问题(断点 1)。"""

    def _parse(self, src: str):
        import tempfile
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = Path(td) / "handler.js"
            fpath.write_text(src)
            return parser.parse_file(fpath, Path(td))

    def test_constructor_arrow_params_merged(self):
        """function Handler(db) + this.display=(req,res)=>{} → ['db','req','res']。"""
        blocks = self._parse(
            "function ResearchHandler(db) {\n"
            "  this.displayResearch = (req, res) => {\n"
            "    return res.render('x', {});\n"
            "  };\n"
            "}\n"
        )
        assert len(blocks) == 1
        assert blocks[0].parameters == ["db", "req", "res"]

    def test_nested_function_expression_params_merged(self):
        """嵌套 function 表达式形参同样并入。"""
        blocks = self._parse(
            "function Handler(db) {\n"
            "  this.run = function (req, res) {\n"
            "    return req.query.x;\n"
            "  };\n"
            "}\n"
        )
        assert blocks[0].parameters == ["db", "req", "res"]

    def test_outer_params_priority_and_dedup(self):
        """嵌套形参与外层同名不重复;外层序优先。"""
        blocks = self._parse(
            "function f(req) {\n"
            "  const g = (req, res) => req;\n"
            "}\n"
        )
        assert blocks[0].parameters == ["req", "res"]

    def test_plain_function_without_nested_unchanged(self):
        """无嵌套函数时参数集不变(零副作用)。"""
        blocks = self._parse(
            "function f(a, b) {\n"
            "  return a + b;\n"
            "}\n"
        )
        assert blocks[0].parameters == ["a", "b"]

    def test_named_top_level_arrow_params_extracted(self):
        """const handler = (req, res) => {} 顶层命名 arrow:参数原本恒为 [](连带修复)。"""
        blocks = self._parse(
            "const handler = (req, res) => {\n"
            "  return req.query.x;\n"
            "};\n"
        )
        assert blocks[0].parameters == ["req", "res"]
