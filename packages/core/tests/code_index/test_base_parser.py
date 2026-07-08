import pytest
from pathlib import Path

from shannon_core.code_index.parsers.base import BaseParser
from shannon_core.code_index.models import FuncBlock


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

        def iter_calls(self, block, source):
            return iter([])

        def destructure_call(self, call):
            return ("foo", None)

        def extract_arg_expressions(self, call, source):
            return []

    parser = DummyParser()
    assert parser.parse_file(Path("a.py"), Path(".")) == []


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

        # missing: iter_calls, destructure_call, extract_arg_expressions

    with pytest.raises(TypeError):
        IncompleteParser()


def test_concrete_parser_with_new_methods_instantiates():
    from shannon_core.code_index.parsers.base import BaseParser

    class FullParser(BaseParser):
        def parse_file(self, file_path, repo_root):
            return []

        def iter_calls(self, block, source):
            return iter([])

        def destructure_call(self, call):
            return ("foo", None)

        def extract_arg_expressions(self, call, source):
            return []

    p = FullParser()
    assert p is not None


def test_iter_calls_cached_parses_each_source_once():
    """BaseParser._iter_calls_cached caches per source.

    detect_sinks calls iter_calls once per function block; without caching an
    M-function file is parsed M times (O(M*file_size)) — the 2026-07-08 Go-repo
    pre-recon deadlock (worker stuck in iter_calls, CPU 1.5h, MCP idle). Each
    distinct source must parse exactly once, every block in it reuses the index.
    """
    from shannon_core.code_index.parsers.python_parser import PythonParser

    fixtures = Path(__file__).parent / "fixtures"
    flask = fixtures / "python" / "flask_app.py"

    parser = PythonParser()
    source = flask.read_bytes()
    blocks = parser.parse_file(flask, flask.parent.parent.parent)
    assert len(blocks) >= 2, "fixture should have multiple functions"

    class _Counter:
        def __init__(self, real):
            self._real = real
            self.count = 0

        def parse(self, *args, **kwargs):
            self.count += 1
            return self._real.parse(*args, **kwargs)

    counter = _Counter(parser._parser)
    parser._parser = counter

    for block in blocks:
        list(parser.iter_calls(block, source))
    assert counter.count == 1, (
        f"同一 source 应只 parse 1 次, 实际 {counter.count} 次 "
        f"(对 {len(blocks)} 个 block 各 re-parse 整个文件)"
    )

    source2 = flask.read_bytes()  # 新 bytes 对象 → 新 id → 重新 parse
    list(parser.iter_calls(blocks[0], source2))
    assert counter.count == 2, "不同 source 应各 parse 一次"