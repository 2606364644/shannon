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


class BaseParser(ABC):
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
