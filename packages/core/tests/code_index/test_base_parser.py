import pytest
from pathlib import Path

from shannon_core.code_index.parsers.base import BaseParser
from shannon_core.code_index.models import FuncBlock, CallEdge


def test_base_parser_cannot_instantiate():
    with pytest.raises(TypeError):
        BaseParser()


def test_concrete_parser_must_implement_methods():
    class IncompleteParser(BaseParser):
        pass

    with pytest.raises(TypeError):
        IncompleteParser()


def test_concrete_parser_implements_both_methods():
    class DummyParser(BaseParser):
        def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
            return []

        def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
            return []

        def iter_calls(self, block, source):
            return iter([])

        def destructure_call(self, call):
            return ("foo", None)

        def extract_arg_expressions(self, call, source):
            return []

    parser = DummyParser()
    assert parser.parse_file(Path("a.py"), Path(".")) == []
    assert parser.extract_calls(
        FuncBlock(
            id="a:f:1", file_path="a", function_name="f",
            start_line=1, end_line=1, source_code="def f(): pass",
            parameters=[], language="python",
        ),
        b"def f(): pass",
    ) == []


def test_call_node_dataclass():
    from shannon_core.code_index.parsers.base import CallNode
    node = CallNode(
        raw_call_node=None,
        raw_arg_nodes=[],
        line=5,
        column=4,
    )
    assert node.line == 5
    assert node.column == 4
    assert node.raw_arg_nodes == []


def test_concrete_parser_must_implement_iter_calls():
    from shannon_core.code_index.parsers.base import BaseParser

    class IncompleteParser(BaseParser):
        def parse_file(self, file_path, repo_root):
            return []

        def extract_calls(self, block, source):
            return []

        # missing: iter_calls, destructure_call, extract_arg_expressions

    with pytest.raises(TypeError):
        IncompleteParser()


def test_concrete_parser_with_new_methods_instantiates():
    from shannon_core.code_index.parsers.base import BaseParser

    class FullParser(BaseParser):
        def parse_file(self, file_path, repo_root):
            return []

        def extract_calls(self, block, source):
            return []

        def iter_calls(self, block, source):
            return iter([])

        def destructure_call(self, call):
            return ("foo", None)

        def extract_arg_expressions(self, call, source):
            return []

    p = FullParser()
    assert p is not None