from pathlib import Path

from shannon_core.code_index.parsers.go_parser import GoParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
GO_FILE = FIXTURES / "go" / "http_handler.go"


class TestGoParserFuncBlocks:
    def test_extracts_all_functions(self):
        parser = GoParser()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listUsers" in names
        assert "updateUser" in names
        assert "getUsers" in names
        assert "saveUser" in names

    def test_extracts_parameters_with_types(self):
        parser = GoParser()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        params = by_name["listUsers"].parameters
        assert len(params) >= 2

    def test_block_language_is_go(self):
        parser = GoParser()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        for block in blocks:
            assert block.language == "go"


class TestGoParserCallEdges:
    def test_extracts_function_calls(self):
        parser = GoParser()
        source = GO_FILE.read_bytes()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listUsers"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getUsers" in callee_names

    def test_extracts_method_calls(self):
        parser = GoParser()
        source = GO_FILE.read_bytes()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["updateUser"], source)
        callee_names = [e.callee_name for e in edges]
        assert "saveUser" in callee_names


class TestGoParserRegistry:
    def test_registered(self):
        assert "go" in _PARSER_CLASSES
