from pathlib import Path

from shannon_core.code_index.parsers.python_parser import PythonParser
from shannon_core.code_index.parsers import get_parser, available_languages

FIXTURES = Path(__file__).parent / "fixtures"
FLASK_APP = FIXTURES / "python" / "flask_app.py"


class TestPythonParserFuncBlocks:
    def test_extracts_all_functions(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "list_users" in names
        assert "update_user" in names
        assert "process_queue" in names
        assert "get_users" in names
        assert "save_user" in names
        assert "fetch_items" in names
        assert "process_item" in names

    def test_extracts_parameters(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert "user_id" in by_name["update_user"].parameters
        assert "item" in by_name["process_item"].parameters

    def test_extracts_decorators(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert any("@app.route" in d for d in by_name["list_users"].decorators)
        assert any("@shared_task" in d for d in by_name["process_queue"].decorators)

    def test_block_id_format(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        for block in blocks:
            assert block.id.count(":") >= 2
            assert block.language == "python"

    def test_source_code_populated(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        for block in blocks:
            assert "def " in block.source_code

    def test_line_numbers_valid(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        for block in blocks:
            assert block.start_line > 0
            assert block.end_line >= block.start_line


class TestPythonParserCallEdges:
    def test_extracts_function_calls(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["list_users"], source)
        callee_names = [e.callee_name for e in edges]
        assert "get_users" in callee_names
        assert "jsonify" in callee_names

    def test_extracts_method_calls(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["update_user"], source)
        callee_names = [e.callee_name for e in edges]
        assert "save_user" in callee_names

    def test_call_edge_has_line_number(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["list_users"], source)
        for edge in edges:
            assert edge.line > 0

    def test_empty_function_no_calls(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["process_item"], source)
        assert len(edges) == 0


class TestPythonParserRegistry:
    def test_registered_in_parser_registry(self):
        from shannon_core.code_index.parsers import _PARSER_CLASSES
        assert "python" in _PARSER_CLASSES

    def test_get_parser_returns_python_parser(self):
        parser = get_parser("python")
        assert isinstance(parser, PythonParser)
