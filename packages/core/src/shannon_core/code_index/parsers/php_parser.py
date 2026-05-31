import logging
from pathlib import Path

import tree_sitter_php as tsphp
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

PHP_LANGUAGE = Language(tsphp.language_php())


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)


class PhpParser(BaseParser):
    def __init__(self):
        self._parser = Parser(PHP_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "function_definition":
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
            elif node.type == "method_declaration":
                block = self._extract_method_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name_text = name_node.text.decode("utf-8").lstrip("$")
                    if name_text == block.function_name:
                        if node.start_point[0] + 1 == block.start_line:
                            edges = self._extract_call_edges(node, source, block.id)
                            break
        return edges

    def _find_class_name(self, node) -> str | None:
        current = node.parent
        while current is not None:
            if current.type == "class_declaration":
                name_node = current.child_by_field_name("name")
                if name_node:
                    return name_node.text.decode("utf-8")
            current = current.parent
        return None

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8").lstrip("$")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            language="php",
        )

    def _extract_method_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8").lstrip("$")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)
        class_name = self._find_class_name(node)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            class_name=class_name,
            language="php",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "simple_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    # name_node is a variable_name like "$id"; strip the "$"
                    params.append(name_node.text.decode("utf-8").lstrip("$"))
            elif child.type == "variadic_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8").lstrip("$"))
        return params

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "function_call_expression":
                callee_name = self._get_function_call_name(node)
                if callee_name:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=callee_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
            elif node.type == "scoped_call_expression":
                # Static calls like DB::select(...)
                name_node = node.child_by_field_name("name")
                if name_node:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=name_node.text.decode("utf-8").lstrip("$"),
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
            elif node.type == "member_call_expression":
                # Instance method calls like $this->getOrders()
                name_node = node.child_by_field_name("name")
                if name_node:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=name_node.text.decode("utf-8").lstrip("$"),
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges

    def _get_function_call_name(self, call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None

        if func_node.type == "name":
            return func_node.text.decode("utf-8").lstrip("$")
        elif func_node.type == "variable_name":
            return func_node.text.decode("utf-8").lstrip("$")
        return None


register_parser("php", PhpParser)
