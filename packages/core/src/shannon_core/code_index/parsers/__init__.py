from shannon_core.code_index.parsers.base import BaseParser

_PARSER_CLASSES: dict[str, type[BaseParser]] = {}


def register_parser(language: str, parser_class: type[BaseParser]) -> None:
    _PARSER_CLASSES[language] = parser_class


def get_parser(language: str) -> BaseParser | None:
    cls = _PARSER_CLASSES.get(language)
    if cls is None:
        return None
    return cls()


def available_languages() -> list[str]:
    return list(_PARSER_CLASSES.keys())