import logging
from pathlib import Path

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

GO_LANGUAGE = Language(tsgo.language())


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class GoParser(BaseParser):
    def __init__(self):
        self._parser = Parser(GO_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "function_declaration":
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
            if node.type in ("function_declaration", "method_declaration"):
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

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            language="go",
        )

    def _extract_method_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)

        receiver_node = node.child_by_field_name("receiver")
        class_name = None
        if receiver_node:
            for child in receiver_node.children:
                if child.type == "parameter_list":
                    for param in child.children:
                        if param.type == "parameter_declaration":
                            type_node = param.child_by_field_name("type")
                            if type_node:
                                class_name = type_node.text.decode("utf-8").lstrip("*")
                                break

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            class_name=class_name,
            language="go",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "parameter_declaration":
                name_node = child.child_by_field_name("name")
                type_node = child.child_by_field_name("type")
                if name_node and type_node:
                    params.append(f"{name_node.text.decode('utf-8')} {type_node.text.decode('utf-8')}")
                elif type_node:
                    params.append(type_node.text.decode("utf-8"))
        return params

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "call_expression":
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
        elif func_node.type == "selector_expression":
            field = func_node.child_by_field_name("field")
            if field:
                return field.text.decode("utf-8")
        return None


register_parser("go", GoParser)
