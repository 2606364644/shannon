from pathlib import Path
from typing import Any

import aiofiles

from shannon_whitebox.audit.utils import format_timestamp


class LogStream:
    """Async append-only file stream with explicit open/close lifecycle."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._file: Any = None

    async def open(self) -> None:
        """Create parent directories and open the file in append mode."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = await aiofiles.open(self.file_path, "a", encoding="utf-8")

    async def write(self, text: str) -> None:
        """Write raw text to the stream. Caller controls formatting."""
        if self._file is None:
            raise RuntimeError("Stream is not open")
        await self._file.write(text)
        await self._file.flush()

    async def close(self) -> None:
        """Flush and close the stream."""
        if self._file is not None:
            await self._file.flush()
            await self._file.close()
            self._file = None

    @property
    def is_open(self) -> bool:
        return self._file is not None

    @property
    def path(self) -> Path:
        return self.file_path

    async def append(self, line: str) -> None:
        """Backward-compatible helper: write with timestamp prefix."""
        timestamp = format_timestamp()
        await self.write(f"[{timestamp}] {line}\n")

    async def append_lines(self, lines: list[str]) -> None:
        """Write multiple lines, each with a timestamp prefix."""
        for line in lines:
            await self.append(line)
