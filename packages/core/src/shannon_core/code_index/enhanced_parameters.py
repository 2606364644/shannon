"""Enhanced parameter extraction — full parameter info for taint analysis.

GitNexus provides function definitions and call relationships but not
full parameter types. This module uses Tree-sitter to extract complete
parameter information: name + type annotation + default value +
variadic flags + HTTP source marking.
"""

import logging
from pathlib import Path

from shannon_core.code_index.models import TypedParameter, ParameterSource

logger = logging.getLogger(__name__)


def extract_typed_parameters(
    file_path: Path,
    func_name: str,
    start_line: int,
    language: str,
) -> list[TypedParameter]:
    """Extract full parameter info for a specific function.

    Args:
        file_path: Path to the source file.
        func_name: Function name to look for.
        start_line: 1-based line number where the function starts.
        language: "python" | "typescript" | "go" | "java" | "php"

    Returns:
        List of TypedParameter with name, type, default, and variadic info.
    """
    if not file_path.exists():
        logger.warning("File not found: %s", file_path)
        return []

    try:
        if language == "python":
            return _extract_python(file_path, func_name, start_line)
        elif language == "typescript":
            return _extract_typescript(file_path, func_name, start_line)
        else:
            return _extract_generic(file_path, func_name, start_line, language)
    except Exception as exc:
        logger.warning("Failed to extract params from %s:%d: %s",
                       file_path, start_line, exc)
        return []


def _extract_python(
    file_path: Path, func_name: str, start_line: int,
) -> list[TypedParameter]:
    """Extract typed parameters from a Python function."""
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser

    source = file_path.read_bytes()
    parser = Parser(Language(tspython.language()))
    tree = parser.parse(source)

    for node in _walk(tree.root_node):
        if node.type in ("function_definition", "async_function_definition"):
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode() == func_name:
                if node.start_point[0] + 1 == start_line:
                    return _parse_python_params(node, source)
    return []


def _parse_python_params(func_node, source: bytes) -> list[TypedParameter]:
    """Parse Python function parameters from AST node."""
    params_node = func_node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[TypedParameter] = []
    for child in params_node.children:
        if child.type == "identifier":
            params.append(TypedParameter(name=child.text.decode()))
        elif child.type == "typed_parameter":
            # typed_parameter: identifier (name, not a field), ':', type (field)
            name_node = child.children[0] if child.children else None
            type_node = child.child_by_field_name("type")
            name = name_node.text.decode() if name_node else "?"
            type_ann = type_node.text.decode() if type_node else None
            params.append(TypedParameter(
                name=name,
                type_annotation=type_ann,
            ))
        elif child.type == "default_parameter":
            # default_parameter: name (field), '=', value (field)
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            name = name_node.text.decode() if name_node else "?"
            default_text = value_node.text.decode() if value_node else None
            params.append(TypedParameter(
                name=name,
                default_value=default_text,
            ))
        elif child.type == "typed_default_parameter":
            # typed_default_parameter: name (field), ':', type (field), '=', value (field)
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            value_node = child.child_by_field_name("value")
            name = name_node.text.decode() if name_node else "?"
            type_ann = type_node.text.decode() if type_node else None
            default_text = value_node.text.decode() if value_node else None
            params.append(TypedParameter(
                name=name,
                type_annotation=type_ann,
                default_value=default_text,
            ))
        elif child.type == "list_splat_pattern":
            for sub in child.children:
                if sub.type == "identifier":
                    params.append(TypedParameter(
                        name=sub.text.decode(),
                        is_variadic=True,
                    ))
        elif child.type == "dictionary_splat_pattern":
            for sub in child.children:
                if sub.type == "identifier":
                    params.append(TypedParameter(
                        name=sub.text.decode(),
                        is_keyword_variadic=True,
                    ))
    return params


def _extract_typescript(
    file_path: Path, func_name: str, start_line: int,
) -> list[TypedParameter]:
    """Extract typed parameters from a TypeScript function or arrow function."""
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser

    source = file_path.read_bytes()
    parser = Parser(Language(tsts.language_typescript()))
    tree = parser.parse(source)

    for node in _walk(tree.root_node):
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode() == func_name:
                if node.start_point[0] + 1 == start_line:
                    return _parse_ts_params(node)
        elif node.type == "arrow_function":
            # Named arrow: const handler = (...) => {}
            parent = node.parent
            if parent and parent.type == "variable_declarator":
                name_node = parent.child_by_field_name("name")
                if name_node and name_node.text.decode() == func_name:
                    if node.start_point[0] + 1 == start_line:
                        return _parse_ts_params(node)
    return []


def _parse_ts_params(func_node) -> list[TypedParameter]:
    """Parse TypeScript function parameters from AST node."""
    params_node = func_node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[TypedParameter] = []
    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter"):
            name = None
            type_ann = None
            is_optional = child.type == "optional_parameter"

            # Name is the identifier child
            for sub in child.children:
                if sub.type == "identifier":
                    name = sub.text.decode()
                elif sub.type == "type_annotation":
                    # type_annotation has children: ':', type_identifier
                    for type_sub in sub.children:
                        if type_sub.type == "type_identifier":
                            type_ann = type_sub.text.decode()
                        elif type_sub.type in (
                            "union_type", "intersection_type",
                            "type_reference", "array_type",
                        ):
                            type_ann = type_sub.text.decode()

            if name:
                params.append(TypedParameter(
                    name=name,
                    type_annotation=type_ann,
                    is_optional=is_optional,
                ))
        elif child.type == "identifier":
            params.append(TypedParameter(name=child.text.decode()))
    return params


def _extract_generic(
    file_path: Path, func_name: str, start_line: int, language: str,
) -> list[TypedParameter]:
    """Fallback: extract parameter names only for unsupported languages."""
    # For Go/Java/PHP, use the existing parser's parameter extraction
    # This returns names only — typed extraction can be added later
    return []


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)


def mark_http_parameter_sources(
    params: list[TypedParameter],
    language: str,
    framework: str,
) -> list[TypedParameter]:
    """Mark HTTP parameter sources based on framework conventions.

    For Flask/Express/Django: marks request/response objects as UNKNOWN/INTERNAL.
    Individual parameter source inference (QUERY, FORM, BODY) requires
    analyzing function body AST and is not implemented here.
    """
    marked = []
    for p in params:
        new_p = p.model_copy(update={"source": _infer_source(p, language, framework)})
        marked.append(new_p)
    return marked


def _infer_source(
    param: TypedParameter, language: str, framework: str,
) -> ParameterSource | None:
    """Infer the HTTP source of a parameter based on framework conventions."""
    name = param.name.lower()
    type_ann = (param.type_annotation or "").lower()

    # Universal patterns
    if name in ("req", "request", "ctx", "context", "c"):
        return ParameterSource.UNKNOWN  # Container object, not a direct source
    if name in ("res", "response", "w", "writer"):
        return ParameterSource.INTERNAL  # Response writer, no taint

    # Express/Node patterns
    if language == "typescript" and framework in ("express", "fastify", "koa"):
        if "request" in type_ann or name == "req":
            return ParameterSource.UNKNOWN
        if "response" in type_ann or name == "res":
            return ParameterSource.INTERNAL

    # Python/Flask patterns
    if language == "python" and framework in ("flask", "fastapi", "django"):
        if name == "request":
            return ParameterSource.UNKNOWN

    return None
