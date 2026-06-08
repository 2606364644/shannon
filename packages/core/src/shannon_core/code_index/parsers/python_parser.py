import logging
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser, CallNode

logger = logging.getLogger(__name__)

PY_LANGUAGE = Language(tspython.language())


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)


class PythonParser(BaseParser):
    def __init__(self):
        self._parser = Parser(PY_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "async_function_definition"):
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        edges = self._extract_call_edges(node, source, block.id)
                        break
        return edges

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        parameters = self._extract_parameters(node, source)
        decorators = self._extract_decorators(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            decorators=decorators,
            language="python",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "identifier":
                params.append(child.text.decode("utf-8"))
            elif child.type == "typed_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8"))
            elif child.type == "default_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8"))
            elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                for sub in child.children:
                    if sub.type == "identifier":
                        params.append(sub.text.decode("utf-8"))
        return params

    def _extract_decorators(self, func_node, source: bytes) -> list[str]:
        decorators: list[str] = []
        # Decorators may be on a parent `decorated_definition` node
        parent = func_node.parent
        if parent is not None and parent.type == "decorated_definition":
            for child in parent.children:
                if child.type == "decorator":
                    decorators.append(child.text.decode("utf-8"))
        return decorators

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "call":
                callee_name = self._get_callee_name(node)
                if callee_name:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=callee_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges

    def _get_callee_name(self, call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None

        if func_node.type == "identifier":
            return func_node.text.decode("utf-8")
        elif func_node.type == "attribute":
            attr = func_node.child_by_field_name("attribute")
            if attr:
                return attr.text.decode("utf-8")
        return None

    def iter_calls(self, block: FuncBlock, source: bytes):
        """Yield CallNode for every `call` node inside this function."""
        tree = self._parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        for call_node in self._iter_call_nodes(node):
                            yield call_node
                        break

    def _iter_call_nodes(self, func_node):
        """Walk function body and yield CallNode for each `call`."""
        for node in _walk(func_node):
            if node.type == "call":
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        # Skip punctuation: '(' ')' ','
                        if child.type in ("(", ")", ","):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        """For Python: `cursor.execute(x)` → ('execute', 'cursor'); `eval(x)` → ('eval', None)."""
        func_node = call.raw_call_node.child_by_field_name("function")
        if func_node is None:
            return ("", None)
        if func_node.type == "identifier":
            return (func_node.text.decode("utf-8"), None)
        if func_node.type == "attribute":
            method = func_node.child_by_field_name("attribute")
            obj = func_node.child_by_field_name("object")
            method_name = method.text.decode("utf-8") if method else ""
            receiver = obj.text.decode("utf-8") if obj else None
            return (method_name, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        """Slice source bytes for each argument subnode."""
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result


# Register in the parser registry
register_parser("python", PythonParser)
