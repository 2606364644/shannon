import json
from pathlib import Path

import aiofiles
import aiofiles.os

async def async_read_file(path: str | Path) -> str:
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return await f.read()

async def async_write_file(path: str | Path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(p, "w", encoding="utf-8") as f:
        await f.write(content)

async def async_path_exists(path: str | Path) -> bool:
    return await aiofiles.os.path.exists(str(path))

async def async_read_json(path: str | Path) -> dict | list:
    content = await async_read_file(path)
    return json.loads(content)

async def async_write_json(path: str | Path, data: dict | list, indent: int = 2) -> None:
    await async_write_file(path, json.dumps(data, indent=indent, ensure_ascii=False))
