import logging
from pathlib import Path

import tree_sitter_php as tsphp
from tree_sitter import Language, Parser

from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser, CallNode

logger = logging.getLogger(__name__)

PHP_LANGUAGE = Language(tsphp.language_php())


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)


class PhpParser(BaseParser):
    _FUNC_NODE_TYPES = ("function_definition", "method_declaration")

    def _normalize_name(self, raw: str) -> str:
        # PHP source names may carry a leading '$' (e.g. variable-named funcs);
        # FuncBlock stores them stripped, so strip before indexing/matching.
        return raw.lstrip("$")

    def __init__(self):
        super().__init__()
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

    def iter_calls(self, block: FuncBlock, source: bytes):
        yield from self._iter_calls_cached(block, source)

    def _iter_call_nodes(self, func_node):
        call_types = (
            "function_call_expression",
            "member_call_expression",
            "scoped_call_expression",
        )
        for node in _walk(func_node):
            if node.type in call_types:
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
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
        node = call.raw_call_node
        if node.type == "function_call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is None:
                return ("", None)
            name = func_node.text.decode("utf-8").lstrip("$")
            return (name, None)
        if node.type == "member_call_expression":
            name_node = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            callee = name_node.text.decode("utf-8").lstrip("$") if name_node else ""
            receiver = obj.text.decode("utf-8").lstrip("$") if obj else None   # 与 name 一致去 $
            return (callee, receiver)
        if node.type == "scoped_call_expression":
            name_node = node.child_by_field_name("name")
            scope = node.child_by_field_name("scope")
            callee = name_node.text.decode("utf-8").lstrip("$") if name_node else ""
            receiver = scope.text.decode("utf-8").lstrip("$") if scope else None   # 与 name 一致去 $
            return (callee, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result


register_parser("php", PhpParser)
