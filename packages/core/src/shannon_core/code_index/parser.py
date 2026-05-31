import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py", ".pyw", ".pyx"],
    "typescript": [".ts", ".tsx", ".js", ".jsx"],
    "go": [".go"],
    "java": [".java"],
    "php": [".php"],
}

SKIP_DIRS: set[str] = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".venv",
    "venv", "env", ".eggs", "eggs",
}


def detect_language(repo_root: Path) -> str:
    """Detect the primary language by counting source file extensions."""
    ext_counts: Counter[str] = Counter()
    for ext_list in LANGUAGE_EXTENSIONS.values():
        for ext in ext_list:
            count = sum(1 for _ in repo_root.rglob(f"*{ext}"))
            if count > 0:
                for lang, lang_exts in LANGUAGE_EXTENSIONS.items():
                    if ext in lang_exts:
                        ext_counts[lang] += count
                        break

    if not ext_counts:
        raise ValueError(
            f"No source files found in {repo_root}. "
            "Could not detect programming language."
        )

    return ext_counts.most_common(1)[0][0]


def discover_source_files(repo_root: Path, language: str) -> list[Path]:
    """Find all source files for the given language, skipping vendored/hidden dirs."""
    extensions = LANGUAGE_EXTENSIONS.get(language, [])
    if not extensions:
        return []

    files: list[Path] = []
    for ext in extensions:
        for path in repo_root.rglob(f"*{ext}"):
            relative_path = path.relative_to(repo_root)
            skip = False
            for part in relative_path.parts:
                if part in SKIP_DIRS or part.startswith("."):
                    skip = True
                    break
            if not skip:
                files.append(path)

    return sorted(files)