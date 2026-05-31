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