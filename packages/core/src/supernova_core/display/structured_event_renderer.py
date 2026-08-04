"""Web 事件落盘 renderer：把原子 DisplayEvent 序列化成 ndjson 行。

env SUPERNOVA_WEB_EVENT_FILE 启用（由 workflow_logger.initialize 挂载）。
收到 SummaryEvent 时额外写一行 scan_end 收尾（双路兜底之一，另一路在 web 的 ScanManager）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime as _dt, timezone
from pathlib import Path
from typing import Any

import aiofiles

from supernova_core.display.events import DisplayEvent, SummaryEvent

# 结尾时区偏移 ±HH:MM（web 回退 _now_iso 产 +00:00）。与前端 parseEventTs 检测对称。
_TZ_OFFSET_RE = re.compile(r"[+-]\d{2}:\d{2}$")
# 日期时间前缀（YYYY-MM-DD[T HH:MM:SS）—— 守卫：非日期串（占位符）原样透传。
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")


def _normalize_ts(ts: str) -> str:
    """把 event.timestamp 归一化为 UTC ISO8601 带 Z（写进 ndjson 的 ts）。

    背景：workflow_logger 的 log_* 用 format_log_time() 给 event.timestamp 赋值 = worker
    容器 UTC 墙钟，格式 "2026-08-04 02:49:13"（无时区后缀、空格分隔）。前端裸 Date.parse
    按浏览器本地时区解释 -> 跨时区漂移（UTC+8 用户多算 8h，见 utils/eventTs.parseEventTs
    前端归一化互补）。ndjson 的 ts 自描述时区（带 Z）后不依赖容器/浏览器时区。

    与前端 parseEventTs 对称：
    - 带 Z -> 原样。
    - 带 ±HH:MM 偏移（+00:00，web 回退 _now_iso 产物）-> 解析后重格式化为 Z。
    - 无时区（"YYYY-MM-DD HH:MM:SS" 空格 / "YYYY-MM-DDTHH:MM:SS" T 分隔）-> 当 UTC
      （worker 容器历来 UTC，与 time.time()/format_timestamp 的 UTC 一致），输出 ISO 带 Z。
    - 空/异常 -> 原样回退（不阻断写盘）。
    """
    if not ts:
        return ts
    s = ts.strip()
    # 仅归一化「看起来像日期时间」的串；占位符（测试用 "t1"/"t2" 等）原样透传。
    if not _DATE_PREFIX_RE.match(s):
        return s
    if s.endswith("Z"):
        return s
    # 带 ±HH:MM 偏移 -> 把尾部偏移替换成 Z（保留原始精度，不引入毫秒）。
    # 例：web 回退 _now_iso 产 "2026-08-04T02:49:13.547577+00:00" -> "2026-08-04T02:49:13.547577Z"。
    # 仅当偏移确实是 UTC（+00:00）时数值不变；非 UTC 偏移 rare，从isoformat 兜底转换。
    if _TZ_OFFSET_RE.search(s):
        m = _TZ_OFFSET_RE.search(s)
        offset = m.group(0)
        try:
            if offset in ("+00:00", "-00:00"):
                return s[:m.start()] + "Z"
            # 非 UTC 偏移：解析后转 UTC（保留毫秒级精度，符合输入）。
            dt = _dt.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return s
    # 无时区：空格分隔归一成 T，补 Z。
    iso = s.replace(" ", "T")
    return iso if iso.endswith("Z") else iso + "Z"


class StructuredEventRenderer:
    def __init__(self, path: str) -> None:
        self._path = path
        self._fh: Any = None  # lazy open
        self._lock = asyncio.Lock()

    async def _ensure_open(self) -> Any:
        if self._fh is None:
            self._fh = await aiofiles.open(self._path, "a")
        return self._fh

    @staticmethod
    def _serialize(event: DisplayEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ts": _normalize_ts(event.timestamp),
            "category": event.category,
            "type": type(event).__name__,
        }
        if is_dataclass(event):
            extra = asdict(event)
            extra.pop("timestamp", None)  # 已并入 ts
            extra.pop("category", None)
            payload.update(extra)
        return payload

    async def render(self, event: DisplayEvent) -> None:
        async with self._lock:
            fh = await self._ensure_open()
            await fh.write(json.dumps(self._serialize(event), default=str, ensure_ascii=False) + "\n")
            await fh.flush()
            if isinstance(event, SummaryEvent):
                await fh.write(json.dumps({
                    "ts": _normalize_ts(event.timestamp),
                    "category": "CONTROL",
                    "type": "scan_end",
                    "status": event.status,
                }, ensure_ascii=False) + "\n")
                await fh.flush()

    async def close(self) -> None:
        async with self._lock:
            if self._fh is not None:
                await self._fh.flush()
                await self._fh.close()
                self._fh = None


def wire_web_event_file(workspaces_dir: Path, workspace_name: str | None) -> None:
    """若 SUPERNOVA_WEB_EVENT_FILE 未设,默认指向 <workspaces_dir>/<workspace>/events.ndjson。

    让 CLI(uv run supernova-whitebox start,无 -w)启动的扫描也能在 supernova-web 实时页
    (LiveTab 经 SSE tail events.ndjson)可见:
    - WEB 启动时 scan_manager 已注入该 env → setdefault 不覆盖,行为零变化;
    - CLI 启动 env 未设 → 这里补上 → WorkflowLogger.initialize 挂载 StructuredEventRenderer
      → events.ndjson 持续写入 → SSE 有数据 → 实时页可见。

    由各 track 的 worker 在 workspace 名回填后、Client.connect 前调用(此时 temporal Worker
    尚未构造,后续 activity 在同进程读 env 拿到该值)。workspace_name 为空时不注入(防御:
    调用方此刻应已回填 name)。
    """
    if not workspace_name:
        return
    os.environ.setdefault(
        "SUPERNOVA_WEB_EVENT_FILE",
        str(Path(workspaces_dir) / workspace_name / "events.ndjson"),
    )
