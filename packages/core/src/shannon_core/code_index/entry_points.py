import re
import logging

from shannon_core.code_index.models import EntryPoint, FuncBlock

logger = logging.getLogger(__name__)

LLM_REVIEW_THRESHOLD = 0.8


def detect_entry_points(blocks: list[FuncBlock], language: str) -> list[EntryPoint]:
    """Detect entry points from function blocks using per-language rules."""
    if language == "python":
        return _detect_python(blocks)
    elif language == "go":
        return _detect_go(blocks)
    elif language == "typescript":
        return _detect_typescript(blocks)
    elif language == "java":
        return _detect_java(blocks)
    elif language == "php":
        return _detect_php(blocks)
    else:
        return []


# Python
_PYTHON_RULES: list[tuple[str, re.Pattern, str | None, float]] = [
    ("http_route", re.compile(r"@.*\.route\(\s*['\"](.+?)['\"]"), None, 0.95),
    ("http_route", re.compile(r"@router\.(get|post|put|delete|patch)\(\s*['\"](.+?)['\"]"), None, 0.95),
    ("http_route", re.compile(r"@(api_view|require_http_methods)"), None, 0.90),
    ("message_consumer", re.compile(r"@(celery\.task|app\.task|shared_task)"), None, 0.90),
]


def _detect_python(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        matched = False
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _PYTHON_RULES:
                m = pattern.search(decorator)
                if m:
                    route = None
                    http_method = None

                    if ".route(" in decorator:
                        route = m.group(1) if m.lastindex >= 1 else None
                        method_match = re.search(r"""methods\s*=\s*\[['"](\w+)['"]\]""", decorator)
                        http_method = method_match.group(1) if method_match else "GET"

                    elif re.match(r"@router\.(get|post|put|delete|patch)", decorator):
                        http_method = m.group(1).upper()
                        route = m.group(2) if m.lastindex >= 2 else None

                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        route=route,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Decorated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    matched = True
                    break
            if matched:
                break

        if not matched and block.source_code.strip().startswith("async def "):
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="unknown",
                confidence=0.30,
                evidence="async def with no known decorator",
                needs_llm_review=True,
            ))

    return entry_points


# Go
def _detect_go(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        params_str = " ".join(block.parameters)

        if "http.ResponseWriter" in params_str and "http.Request" in params_str:
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                confidence=0.95,
                evidence="Parameters include http.ResponseWriter and *http.Request",
                needs_llm_review=False,
            ))
            continue

        if "gin.Context" in params_str:
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                confidence=0.95,
                evidence="Parameter includes *gin.Context",
                needs_llm_review=False,
            ))
            continue

        if block.function_name == "main":
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="cli",
                confidence=0.30,
                evidence="func main()",
                needs_llm_review=True,
            ))

    return entry_points


# TypeScript
_TS_DECORATOR_RULES: list[tuple[str, re.Pattern, str | None, float]] = [
    ("http_route", re.compile(r"@(Get|Post|Put|Delete|Patch)\b"), None, 0.95),
]


def _detect_typescript(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _TS_DECORATOR_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = m.group(1).upper()
                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Decorated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    return entry_points


# Java
_JAVA_ANNOTATION_RULES: list[tuple[str, re.Pattern, str, float]] = [
    ("http_route", re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)"), "http_route", 0.95),
    ("message_consumer", re.compile(r"@RabbitListener"), "message_consumer", 0.90),
]


def _detect_java(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _JAVA_ANNOTATION_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = None
                    if m.lastindex and m.lastindex >= 1:
                        ann = m.group(1)
                        method_map = {
                            "GetMapping": "GET", "PostMapping": "POST",
                            "PutMapping": "PUT", "DeleteMapping": "DELETE",
                            "PatchMapping": "PATCH", "RequestMapping": None,
                        }
                        http_method = method_map.get(ann)

                    route = None
                    route_match = re.search(r'[\'"](/[^\'"]+)[\'"]', decorator)
                    if route_match:
                        route = route_match.group(1)

                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        route=route,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Annotated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    return entry_points


# PHP
_PHP_DECORATOR_RULES: list[tuple[str, re.Pattern, float]] = [
    ("http_route", re.compile(r"#\[Route\("), 0.95),
]


def _detect_php(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, confidence in _PHP_DECORATOR_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = None
                    method_match = re.search(r"methods:\s*\[\s*['\"](\w+)['\"]", decorator)
                    if method_match:
                        http_method = method_match.group(1)

                    route = None
                    route_match = re.search(r"[\'\"](/[^\'\"]+)[\'\"]", decorator)
                    if route_match:
                        route = route_match.group(1)

                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        route=route,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Attribute: {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    return entry_points
