from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from shannon_core.code_index.models import CallEdge, FuncBlock


@dataclass(frozen=True)
class CallNode:
    """A tree-sitter call node plus its pre-extracted argument nodes.

    `raw_call_node` and `raw_arg_nodes` are language-specific tree_sitter Node
    objects. The parser methods `destructure_call()` and
    `extract_arg_expressions()` know how to handle them.
    """
    raw_call_node: object
    raw_arg_nodes: list[object] = field(default_factory=list)
    line: int = 0       # 1-based
    column: int = 0     # 0-based


def _walk(node):
    """Yield node then all descendants, depth-first. Shared tree-sitter walk."""
    yield node
    for child in node.children:
        yield from _walk(child)


class BaseParser(ABC):
    # Subclasses set this to their tree-sitter function/method declaration
    # node types, e.g. ("function_declaration", "method_declaration").
    _FUNC_NODE_TYPES: tuple[str, ...] = ()

    def __init__(self):
        # id(source) -> {(func_name, start_line): [CallNode]}. iter_calls is
        # invoked once per function block by detect_sinks; without this cache every
        # call re-parses the whole file (O(M*file_size) per file). On a 1207-file
        # Go repo this pegged a CPU core for 1.5h and deadlocked pre-recon step 0
        # (2026-07-08, py-spy showed worker stuck in iter_calls, MCP idle). Each
        # file parses+walks exactly once; every block in it reuses the index.
        self._call_index_cache: dict[int, dict[tuple[str, int], list[CallNode]]] = {}

    def _normalize_name(self, raw: str) -> str:
        """Normalize a function name extracted from the AST before indexing.

        Default is a no-op; PhpParser overrides to strip the leading '$' (PHP
        source names may carry it; FuncBlock stores it stripped).
        """
        return raw

    def _iter_calls_cached(self, block: FuncBlock, source: bytes) -> list[CallNode]:
        """Shared per-source cache backing iter_calls.

        Returns the call nodes inside ``block``'s function. Parses + walks the
        source once per distinct bytes object (keyed by id(source)); subsequent
        blocks in the same file hit the cache. Subclasses implement iter_calls as
        ``yield from self._iter_calls_cached(block, source)``.
        """
        key = id(source)
        index = self._call_index_cache.get(key)
        if index is None:
            tree = self._parser.parse(source)
            index = {}
            for node in _walk(tree.root_node):
                if node.type in self._FUNC_NODE_TYPES:
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        name = self._normalize_name(name_node.text.decode("utf-8"))
                        line = node.start_point[0] + 1
                        index[(name, line)] = list(self._iter_call_nodes(node))
            self._call_index_cache[key] = index
        return index.get((block.function_name, block.start_line), [])

    @abstractmethod
    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        """Parse a source file and return all function blocks found."""
        ...

    @abstractmethod
    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        """Extract call edges from a function block's source."""
        ...

    @abstractmethod
    def iter_calls(self, block: FuncBlock, source: bytes) -> Iterator[CallNode]:
        """Iterate call nodes within a function block.

        Each yielded CallNode must carry the raw tree-sitter call node and
        the raw argument subnodes (in positional order). line/column point at
        the call site.
        """
        ...

    @abstractmethod
    def destructure_call(self, call: CallNode) -> tuple[str, str | None]:
        """Return (callee_name, receiver_text) for a call.

        receiver_text is None for bare function calls (e.g. `eval(x)`).
        For `cursor.execute(sql)`, callee_name="execute", receiver_text="cursor".
        """
        ...

    @abstractmethod
    def extract_arg_expressions(self, call: CallNode, source: bytes) -> list[str]:
        """Return the source text of each positional argument.

        For `f(a, b=c)`, returns ["a", "b=c"]. Keyword args are kept verbatim;
        sink_detector decides how to interpret them.
        """
        ...
