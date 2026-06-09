from pathlib import Path

from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
from shannon_core.code_index.parsers import get_parser

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


class TestTypeScriptParserCallEdges:
    def test_extracts_function_calls(self):
        parser = TypeScriptParser()
        source = EXPRESS_APP.read_bytes()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listOrders"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getOrders" in callee_names

    def test_call_edge_has_line_number(self):
        parser = TypeScriptParser()
        source = EXPRESS_APP.read_bytes()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listOrders"], source)
        for edge in edges:
            assert edge.line > 0


class TestTypeScriptParserRegistry:
    def test_registered_in_parser_registry(self):
        from shannon_core.code_index.parsers import _PARSER_CLASSES
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
