from pathlib import Path

from shannon_core.code_index.parsers.php_parser import PhpParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
PHP_FILE = FIXTURES / "php" / "laravel_routes.php"
PHP_FIXTURE = Path(__file__).parent / "fixtures" / "php" / "laravel_routes.php"


class TestPhpParserFuncBlocks:
    def test_extracts_standalone_functions(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "getUsers" in names
        assert "saveUser" in names

    def test_extracts_class_methods(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listOrders" in names
        assert "getOrders" in names

    def test_class_method_has_class_name(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert by_name["listOrders"].class_name == "OrderController"

    def test_extracts_parameters(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        params = by_name["saveUser"].parameters
        assert "id" in params
        assert "data" in params

    def test_block_language_is_php(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        for block in blocks:
            assert block.language == "php"


class TestPhpParserCallEdges:
    def test_extracts_function_calls(self):
        parser = PhpParser()
        source = PHP_FILE.read_bytes()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["getUsers"], source)
        callee_names = [e.callee_name for e in edges]
        assert "select" in callee_names

    def test_extracts_method_calls(self):
        parser = PhpParser()
        source = PHP_FILE.read_bytes()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listOrders"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getOrders" in callee_names


class TestPhpParserRegistry:
    def test_registered(self):
        assert "php" in _PARSER_CLASSES


class TestPhpParserIterCalls:
    def test_iter_calls_function_body(self):
        """getUsers() has DB::select('SELECT...')."""
        parser = PhpParser()
        source = PHP_FIXTURE.read_bytes()
        blocks = parser.parse_file(PHP_FIXTURE, PHP_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        assert len(calls) >= 1

    def test_destructure_scoped_call(self):
        """DB::select('SELECT...') → callee=select, receiver=DB (static)."""
        parser = PhpParser()
        source = PHP_FIXTURE.read_bytes()
        blocks = parser.parse_file(PHP_FIXTURE, PHP_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        # DB::select — static call
        assert ("select", "DB") in callees

    def test_destructure_member_call(self):
        """listOrders body: $this->getOrders() → callee=getOrders, receiver=$this"""
        parser = PhpParser()
        source = PHP_FIXTURE.read_bytes()
        blocks = parser.parse_file(PHP_FIXTURE, PHP_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listOrders"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getOrders", "$this") in callees
