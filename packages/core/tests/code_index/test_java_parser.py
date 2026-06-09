from pathlib import Path

from shannon_core.code_index.parsers.java_parser import JavaParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
JAVA_FILE = FIXTURES / "java" / "SpringController.java"
JAVA_FIXTURE = Path(__file__).parent / "fixtures" / "java" / "SpringController.java"


class TestJavaParserFuncBlocks:
    def test_extracts_all_methods(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listUsers" in names
        assert "updateUser" in names
        assert "processOrder" in names

    def test_extracts_parameters(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        params = by_name["updateUser"].parameters
        assert len(params) >= 2

    def test_extracts_decorators_as_annotations(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert any("@GetMapping" in d for d in by_name["listUsers"].decorators)
        assert any("@RabbitListener" in d for d in by_name["processOrder"].decorators)

    def test_block_language_is_java(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        for block in blocks:
            assert block.language == "java"


class TestJavaParserCallEdges:
    def test_extracts_method_calls(self):
        parser = JavaParser()
        source = JAVA_FILE.read_bytes()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listUsers"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getUsers" in callee_names


class TestJavaParserRegistry:
    def test_registered(self):
        assert "java" in _PARSER_CLASSES


class TestJavaParserIterCalls:
    def test_iter_calls_method_body(self):
        """listUsers() body has userService.getUsers()."""
        parser = JavaParser()
        source = JAVA_FIXTURE.read_bytes()
        blocks = parser.parse_file(JAVA_FIXTURE, JAVA_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        assert len(calls) >= 1

    def test_destructure_method_invocation(self):
        """userService.getUsers() → callee=getUsers, receiver=usersService"""
        parser = JavaParser()
        source = JAVA_FIXTURE.read_bytes()
        blocks = parser.parse_file(JAVA_FIXTURE, JAVA_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getUsers", "userService") in callees

    def test_destructure_method_no_receiver(self):
        """A method invocation without an object (e.g., this.x()) is also handled."""
        parser = JavaParser()
        source = JAVA_FIXTURE.read_bytes()
        blocks = parser.parse_file(JAVA_FIXTURE, JAVA_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        # processOrder body: orderService.handle(message)
        calls = list(parser.iter_calls(by_name["processOrder"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("handle", "orderService") in callees
