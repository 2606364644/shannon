from shannon_core.code_index.parsers.base import BaseParser

_PARSER_CLASSES: dict[str, type[BaseParser]] = {}


def register_parser(language: str, parser_class: type[BaseParser]) -> None:
    _PARSER_CLASSES[language] = parser_class


def get_parser(language: str) -> BaseParser | None:
    _ensure_registered()
    cls = _PARSER_CLASSES.get(language)
    if cls is None:
        return None
    return cls()


def available_languages() -> list[str]:
    _ensure_registered()
    return list(_PARSER_CLASSES.keys())


_registered = False


def _ensure_registered() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    import shannon_core.code_index.parsers.python_parser  # noqa: F401
    import shannon_core.code_index.parsers.typescript_parser  # noqa: F401
    import shannon_core.code_index.parsers.go_parser  # noqa: F401
    import shannon_core.code_index.parsers.java_parser  # noqa: F401
    import shannon_core.code_index.parsers.php_parser  # noqa: F401
