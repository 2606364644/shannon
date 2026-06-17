"""文件系统类工具：read_file / write_file / edit_file / glob。"""
from __future__ import annotations

from pathlib import Path

from agents import RunContextWrapper, function_tool

from . import ToolContext

_MAX_FILE_OUTPUT = 30000


def _resolve(ctx: RunContextWrapper[ToolContext], path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(ctx.context.cwd) / p
    return p


def _truncate(text: str) -> str:
    return text[:_MAX_FILE_OUTPUT] + ("...[truncated]" if len(text) > _MAX_FILE_OUTPUT else "")


async def _read_file_impl(
    ctx: RunContextWrapper[ToolContext],
    path: str,
    offset: int = 0,
    limit: int | None = None,
) -> str:
    """Read a text file and return it with 1-based line numbers (cat -n style).

    Args:
        path: File path, relative to the working directory or absolute.
        offset: Number of leading lines to skip (default 0).
        limit: Max number of lines to return (default all).
    """
    p = _resolve(ctx, path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"[read_file error] file not found: {path}"
    lines = text.splitlines()
    start = max(0, int(offset))
    end = len(lines) if limit is None else start + int(limit)
    numbered = [f"{i + 1}\t{line}" for i, line in enumerate(lines[start:end], start=start)]
    return _truncate("\n".join(numbered))


async def _write_file_impl(
    ctx: RunContextWrapper[ToolContext],
    path: str,
    content: str,
) -> str:
    """Write content to a file (overwrite), creating parent directories.

    Args:
        path: File path, relative to the working directory or absolute.
        content: Full file content to write.
    """
    p = _resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


async def _edit_file_impl(
    ctx: RunContextWrapper[ToolContext],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Replace occurrences of old_string with new_string in a file.

    Args:
        path: File path.
        old_string: Exact text to find.
        new_string: Replacement text.
        replace_all: If False (default), old_string must appear exactly once.
    """
    p = _resolve(ctx, path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[edit_file error] file not found: {path}"
    count = text.count(old_string)
    if count == 0:
        return f"[edit_file error] old_string not found in {path}"
    if not replace_all and count > 1:
        return f"[edit_file error] old_string not unique ({count} matches) in {path}"
    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return f"edited {path} ({count} replacement(s))"


async def _glob_impl(
    ctx: RunContextWrapper[ToolContext],
    pattern: str,
    path: str = ".",
) -> str:
    """List file paths matching a glob pattern, newest-first.

    Args:
        pattern: Glob pattern, e.g. "**/*.py".
        path: Directory to search (default working directory).
    """
    base = _resolve(ctx, path)
    matches = sorted(base.glob(pattern), key=lambda f: f.stat().st_mtime if f.exists() else 0, reverse=True)
    return _truncate("\n".join(str(m.relative_to(base)) if m.is_relative_to(base) else str(m) for m in matches))


read_file = function_tool(_read_file_impl)
write_file = function_tool(_write_file_impl)
edit_file = function_tool(_edit_file_impl)
glob = function_tool(_glob_impl)