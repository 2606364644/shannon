from pathlib import Path

from shannon_core.code_index.parsers.go_parser import GoParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
GO_FILE = FIXTURES / "go" / "http_handler.go"
GO_FIXTURE = Path(__file__).parent / "fixtures" / "go" / "http_handler.go"


class _CountingParser:
    """Proxy wrapping a tree-sitter Parser, counting parse() calls.

    Used to assert iter_calls caches the per-source parse instead of re-parsing
    the whole file for every function block.
    """

    def __init__(self, real):
        self._real = real
        self.parse_count = 0

    def parse(self, *args, **kwargs):
        self.parse_count += 1
        return self._real.parse(*args, **kwargs)


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


class TestGoParserIterCalls:
    def test_iter_calls_function_body(self):
        """listUsers calls getUsers() and json.NewEncoder(w).Encode(users)."""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        assert len(calls) >= 1

    def test_destructure_bare_call(self):
        """getUsers() → callee=getUsers, receiver=None"""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getUsers", None) in callees

    def test_destructure_selector_call(self):
        """json.NewEncoder(w).Encode(users) — chained: callee=Encode, receiver=users (last selector)"""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        # Encode is the terminal method
        callee_names = [c for c, _ in callees]
        assert "Encode" in callee_names


class TestGoParserIterCallsCaching:
    """Regression (2026-07-08): iter_calls re-parsed the whole file + walked the
    entire AST for every function block. detect_sinks calls iter_calls once per
    block, so an M-function Go file was parsed M times (O(M * file_size)); on a
    1207-file repo this pegged one CPU core for 1.5h and deadlocked pre-recon
    step 0 (py-spy showed the worker stuck in go_parser.iter_calls, MCP idle).
    Cache the parse per source so each file parses once.
    """

    def test_iter_calls_parses_each_source_once(self):
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        assert len(blocks) >= 2, "fixture 应含多个函数"

        counter = _CountingParser(parser._parser)
        parser._parser = counter
        for block in blocks:
            list(parser.iter_calls(block, source))

        assert counter.parse_count == 1, (
            f"同一 source 应只 parse 1 次, 实际 {counter.parse_count} 次 "
            f"(对 {len(blocks)} 个 block 各 re-parse 整个文件)"
        )

    def test_iter_calls_caches_per_distinct_source(self):
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        counter = _CountingParser(parser._parser)
        parser._parser = counter

        list(parser.iter_calls(by_name["listUsers"], source))
        list(parser.iter_calls(by_name["updateUser"], source))
        assert counter.parse_count == 1, "同一 bytes 对象应命中缓存"

        source2 = GO_FIXTURE.read_bytes()  # 新 bytes 对象 → 新 id → 重新 parse
        list(parser.iter_calls(by_name["listUsers"], source2))
        assert counter.parse_count == 2, "不同 source 应各 parse 一次"

    def test_iter_calls_still_returns_correct_calls_after_cache(self):
        """缓存路径必须返回与逐 block parse 一致的 calls(回归保护)。"""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls_list = list(parser.iter_calls(by_name["listUsers"], source))
        calls_update = list(parser.iter_calls(by_name["updateUser"], source))

        callees_list = [parser.destructure_call(c)[0] for c in calls_list]
        callees_update = [parser.destructure_call(c)[0] for c in calls_update]
        assert "getUsers" in callees_list
        assert "saveUser" in callees_update
