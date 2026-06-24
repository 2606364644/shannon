"""Detect escaped and unescaped output directives in template files."""

from dataclasses import dataclass
from pathlib import Path
import re


_UNESCAPED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<%-"), "ejs-unescaped"),
    (re.compile(r"\{\{[^}]*\|\s*safe\s*\}\}"), "jinja2-safe"),
    (re.compile(r"\{\{\{"), "mustache-triple"),
]

_ESCAPED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<%="), "ejs-escaped"),
    (re.compile(r"\{\{(?!\{)(?![^}]*\|\s*safe\s*\}\})"), "jinja2-escaped"),
]


@dataclass(frozen=True)
class TemplateEscapeFinding:
    file_path: str
    line: int
    directive: str
    escaping: str
    engine: str


def detect_template_escape(template_file: Path) -> list[TemplateEscapeFinding]:
    """Scan one template file for escaped and unescaped output directives."""
    findings: list[TemplateEscapeFinding] = []
    try:
        content = template_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings

    for line_number, line in enumerate(content.splitlines(), start=1):
        for pattern, engine in _UNESCAPED_PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    TemplateEscapeFinding(
                        file_path=str(template_file),
                        line=line_number,
                        directive=match.group().strip(),
                        escaping="unescaped",
                        engine=engine,
                    )
                )
        for pattern, engine in _ESCAPED_PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    TemplateEscapeFinding(
                        file_path=str(template_file),
                        line=line_number,
                        directive=match.group().strip(),
                        escaping="escaped",
                        engine=engine,
                    )
                )
    return findings


def detect_template_escapes(template_files: list[Path]) -> list[TemplateEscapeFinding]:
    """Scan multiple template files and return all escape findings."""
    findings: list[TemplateEscapeFinding] = []
    for template_file in template_files:
        findings.extend(detect_template_escape(template_file))
    return findings
