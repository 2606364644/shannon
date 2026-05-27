import aiofiles
from pathlib import Path
from datetime import datetime, timezone

class LogStream:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    async def append(self, line: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        async with aiofiles.open(self.file_path, "a", encoding="utf-8") as f:
            await f.write(f"[{timestamp}] {line}\n")

    async def append_lines(self, lines: list[str]) -> None:
        for line in lines:
            await self.append(line)
