import logging
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

JAVA_LANGUAGE = Language(tsjava.language())


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class JavaParser(BaseParser):
    def __init__(self):
        self._parser = Parser(JAVA_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "method_declaration":
                block = self._extract_method_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        edges = self._extract_call_edges(node, source, block.id)
                        break
        return edges

    def _extract_method_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)
        decorators = self._extract_annotations(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            decorators=decorators,
            language="java",
        )

    def _extract_parameters(self, method_node, source: bytes) -> list[str]:
        params_node = method_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "formal_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8"))
        return params

    def _extract_annotations(self, method_node, source: bytes) -> list[str]:
        annotations: list[str] = []
        for child in method_node.children:
            if child.type == "modifiers":
                for modifier in child.children:
                    if modifier.type in ("marker_annotation", "annotation"):
                        annotations.append(modifier.text.decode("utf-8"))
        return annotations

    def _extract_call_edges(self, method_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(method_node):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                if name_node:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=name_node.text.decode("utf-8"),
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges


register_parser("java", JavaParser)
