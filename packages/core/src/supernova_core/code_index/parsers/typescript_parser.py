import logging
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser

from supernova_core.code_index.models import FuncBlock
from supernova_core.code_index.parsers import register_parser
from supernova_core.code_index.parsers.base import BaseParser, CallNode

logger = logging.getLogger(__name__)

TS_LANGUAGE = Language(tsts.language_typescript())


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)


class TypeScriptParser(BaseParser):
    _FUNC_NODE_TYPES = ("function_declaration", "method_definition")

    def __init__(self):
        super().__init__()
        self._parser = Parser(TS_LANGUAGE)

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
            elif node.type == "method_definition":
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
            elif node.type == "arrow_function":
                # Only capture named arrow functions (const handler = (...) => {})
                block = self._extract_arrow_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)
        # spec 2026-08-21 修复点 B: 并入嵌套 arrow/function 表达式形参 —— JS 惯用
        # `function Handler(db) { this.h = (req, res) => {...} }` 形态下 req/res
        # 不进参数集,intra 分析从源头问错问题(NodeGoat 断点 1)。只并参数不动切分。
        parameters += self._collect_nested_fn_params(node, source, skip=parameters)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            language="typescript",
        )

    def _extract_arrow_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        parent = node.parent
        if parent is None or parent.type != "variable_declarator":
            return None

        name_node = parent.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            # spec 2026-08-21 修复点 B(连带): 顶层命名 arrow 原本恒 parameters=[],
            # 补签名提取(与 _extract_func_block 同款)。
            parameters=self._extract_parameters(node, source),
            language="typescript",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type in ("required_parameter", "optional_parameter"):
                pattern = child.child_by_field_name("pattern")
                if pattern:
                    params.append(pattern.text.decode("utf-8"))
                else:
                    # Fallback: grab the first identifier child
                    for sub in child.children:
                        if sub.type == "identifier":
                            params.append(sub.text.decode("utf-8"))
                            break
            elif child.type == "identifier":
                params.append(child.text.decode("utf-8"))
        return params

    def _collect_nested_fn_params(
        self, func_node, source: bytes, *, skip: list[str],
    ) -> list[str]:
        """收集函数体内嵌套 arrow/function 表达式的形参(去重,外层已有者跳过)。

        spec 2026-08-21 修复点 B:NodeGoat 形态 `this.h = (req, res) => {}` 的
        req/res 只存在于嵌套函数签名,顶层签名提取拿不到 → intra prompt 参数集
        缺污点参数。只并入名字,不改变块切分/调用归属。
        """
        collected: list[str] = []
        seen = set(skip)
        for node in _walk(func_node):
            if node is func_node:
                continue
            if node.type not in ("arrow_function", "function_expression"):
                continue
            for p in self._extract_parameters(node, source):
                if p not in seen:
                    seen.add(p)
                    collected.append(p)
        return collected

    def iter_calls(self, block: FuncBlock, source: bytes):
        yield from self._iter_calls_cached(block, source)

    def _iter_call_nodes(self, func_node):
        for node in _walk(func_node):
            if node.type == "call_expression":
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        if child.type in ("(", ")", ",", ";"):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        func_node = call.raw_call_node.child_by_field_name("function")
        if func_node is None:
            return ("", None)
        if func_node.type == "identifier":
            return (func_node.text.decode("utf-8"), None)
        if func_node.type == "member_expression":
            prop = func_node.child_by_field_name("property")
            obj = func_node.child_by_field_name("object")
            callee = prop.text.decode("utf-8") if prop else ""
            receiver = obj.text.decode("utf-8") if obj else None
            return (callee, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result


register_parser("typescript", TypeScriptParser)
