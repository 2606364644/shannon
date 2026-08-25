# packages/core/src/supernova_core/services/code_snippet.py
"""问题代码片段确定性提取（spec §5/§10.4）：sink 行 ±width 行，零 LLM 成本。"""
from __future__ import annotations

import re
from pathlib import Path

from supernova_core.utils.file_io import async_read_file

_FILE_LINE = re.compile(r"([\w./-]+\.[A-Za-z]{1,5}):(\d+)")

async def extract_snippet(repo_root: Path | None, sink_location: str | None,
                          width: int = 3) -> str | None:
    if repo_root is None or not isinstance(sink_location, str):
        return None
    m = _FILE_LINE.search(sink_location)
    if not m:
        return None
    path = repo_root / m.group(1)
    try:
        if not path.is_file():
            return None
        content = await async_read_file(path)
    except Exception:  # noqa: BLE001 — snippet 是增强项，任何失败静默跳过
        return None
    lines = content.splitlines()
    line_no = int(m.group(2))
    if not 1 <= line_no <= len(lines):
        return None
    start, end = max(1, line_no - width), min(len(lines), line_no + width)
    return "\n".join(lines[start - 1:end])

def annotate_direct(entries: list[dict] | None, snippet: str | None) -> None:
    if not entries or not snippet:
        return
    for e in entries:
        param = e.get("parameter")
        e["direct"] = bool(isinstance(param, str) and param and param in snippet)
