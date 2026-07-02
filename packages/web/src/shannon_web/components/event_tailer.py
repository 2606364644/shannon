from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

import aiofiles

OnEvent = Callable[[dict, int], Awaitable[None]]


class EventTailer:
    """Tail an ``events.ndjson`` file with tail -f semantics.

    Records the byte offset of consumed bytes so callers can resume from a
    ``Last-Event-ID`` header. Stops after observing a ``scan_end`` event.
    Corrupt (non-JSON / blank) lines are skipped and counted.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._offset = 0
        self._carry = ""
        self.corrupt_count = 0

    @property
    def offset(self) -> int:
        return self._offset

    @staticmethod
    def encode_sse(data: dict, event_id: int | None = None) -> str:
        """Encode ``data`` as an SSE frame.

        Layout: ``id: <eid>\\n`` (optional) + ``data: <json>\\n`` + blank line.
        """
        body = "data: " + json.dumps(data, ensure_ascii=False) + "\n"
        if event_id is not None:
            body = f"id: {event_id}\n" + body
        return body + "\n"  # 空行 = SSE 事件分隔

    async def tail(
        self,
        on_event: OnEvent,
        last_event_id: int | None = None,
        idle_timeout: float = 300.0,
    ) -> None:
        """Tail the file, dispatching each parsed line to ``on_event``.

        Stops once a line whose ``type`` is ``scan_end`` is observed, or once the
        file fails to appear within ``idle_timeout`` seconds.
        """
        if last_event_id is not None:
            self._offset = last_event_id
        waited = 0.0
        while not self._path.exists():  # 等文件出现
            await asyncio.sleep(0.2)
            waited += 0.2
            if waited > idle_timeout:
                return
        closed = False
        while not closed:
            async with aiofiles.open(self._path, "rb") as fh:
                await fh.seek(self._offset)
                chunk = await fh.read()
            if chunk:
                self._offset += len(chunk)
                self._carry += chunk.decode("utf-8", "replace")
                lines = self._carry.split("\n")
                self._carry = lines.pop()  # 末尾可能不完整，留存
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        self.corrupt_count += 1
                        continue
                    await on_event(data, self._offset)
                    if data.get("type") == "scan_end":
                        closed = True
                        break
            else:
                await asyncio.sleep(0.1)
        self._carry = ""
