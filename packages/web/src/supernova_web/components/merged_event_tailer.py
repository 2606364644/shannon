"""多源归并事件 tailer：认证/白盒/黑盒 run-K 的 ndjson 按时间归并为一条流。

设计要点（详见 live 全量合并方案）：

- **源**：``ac``=authcheck-events.ndjson（认证预检）、``wb``=events.ndjson（任务根，
  组合扫描即白盒段，独立黑盒扫描即其本体）、``run-K``=blackbox-runs/run-K/events.ndjson。
  run 目录周期重扫，流开着时新增 run（rerun/add run）自动纳入。
- **SSE id = 全源 emit offset 快照**（``ac=0&wb=123&run-1=456``）：重连只需单个
  Last-Event-ID 即可恢复每源各自断点，无服务端会话状态；重放幂等（前端按 id 去重）。
  offset 全程按**字节**计（ndjson 含多字节 UTF-8，按字符计会漂移丢事件）。
- **scan_end 语义**：
  - ``ac`` 的 scan_end 丢弃（预验证收尾非扫描终态，混入会提前关流）；
  - ``wb`` 的 scan_end **扣住不发**，待「wb 终态 + 所有已见 run 终态 + 宽限期」
    全部满足后作为流的最后一条发出（前端见 scan_end 关流）。NodeGoat-20260817-132940
    事故：编排器 15:31 误写任务级 scan_end failed 而黑盒 run 实际跑到 16:04——扣住
    后 run 的事件仍持续可推；
  - ``run-K`` 的 scan_end 改写 type=run_end 转发（run 收尾对全量流非终态）。
- **顺序**：每轮 poll 内各源新增行按 ``ts`` 排序输出（解析失败/缺失按源优先级稳定
  兜底：ac < wb < run-K）。各段（认证→白盒→黑盒）时间上不重叠，ts 排序为防御。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiofiles

from .event_tailer import EventTailer

RUN_DIR_RE = re.compile(r"^run-(\d+)$")
ID_SEP = "&"

OnEvent = Callable[[dict, Any], Awaitable[None]]


def _parse_ts(value: Any) -> datetime | None:
    """事件 ts → datetime；非法/缺失返 None（排序时按源优先级兜底）。"""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class _Source:
    label: str
    path: Path
    priority: int
    file_off: int = 0      # 已从磁盘读到的字节（截断检测/续读基准）
    line_pos: int = 0      # 下一行起始字节（per-line end offset 的累积基准）
    emit_off: int = 0      # 已计入 SSE id 快照的字节（≤ line_pos）
    carry: bytes = b""     # 未终结行的半截字节
    # 待发事件：(ts or None, 行尾字节 offset, data)
    pending: list[tuple[datetime | None, int, dict]] = field(default_factory=list)
    seen_end: bool = False  # 已见本源 scan_end

    def reset(self) -> None:
        """文件被截断/重建：归零重读（重放由前端 id 去重吸收）。"""
        self.file_off = self.line_pos = self.emit_off = 0
        self.carry = b""
        self.pending.clear()


class MergedEventTailer:
    """tail scan_dir 下多事件源并归并推送；``tail()`` 返回即流结束。"""

    def __init__(self, scan_dir: Path) -> None:
        self._dir = Path(scan_dir)
        self._sources: dict[str, _Source] = {}
        self._held_end: dict | None = None  # 扣住的 wb scan_end（终态判定满足后最后发）

    # ---- 源发现与断点恢复 ----

    def _discover(self, resume: dict[str, int]) -> None:
        """补齐源表：固定 ac/wb + 重扫 blackbox-runs/run-K（新 run 流中自动纳入）。"""
        fixed = [
            ("ac", self._dir / "authcheck-events.ndjson", 0),
            ("wb", self._dir / "events.ndjson", 1),
        ]
        for label, path, priority in fixed:
            if label not in self._sources:
                self._sources[label] = _Source(label, path, priority,
                                               file_off=resume.get(label, 0),
                                               line_pos=resume.get(label, 0),
                                               emit_off=resume.get(label, 0))
        runs_root = self._dir / "blackbox-runs"
        if runs_root.is_dir():
            for entry in runs_root.iterdir():
                m = RUN_DIR_RE.match(entry.name)
                if not m or not (entry / "events.ndjson").is_file():
                    continue
                label = entry.name
                if label not in self._sources:
                    self._sources[label] = _Source(
                        label, entry / "events.ndjson", 1000 + int(m.group(1)),
                        file_off=resume.get(label, 0),
                        line_pos=resume.get(label, 0),
                        emit_off=resume.get(label, 0))

    @staticmethod
    def parse_last_event_id(raw: str | None) -> dict[str, int]:
        """Last-Event-ID（"ac=0&wb=123&run-1=45"）→ {label: offset}；容忍畸形段。"""
        if not raw:
            return {}
        offsets: dict[str, int] = {}
        for part in raw.split(ID_SEP):
            label, sep, value = part.partition("=")
            if sep and label and value.isdigit():
                offsets[label] = int(value)
        return offsets

    def _id_snapshot(self) -> str:
        return ID_SEP.join(
            f"{self._sources[s].label}={self._sources[s].emit_off}"
            for s in sorted(self._sources, key=lambda k: self._sources[k].priority))

    # ---- 主循环 ----

    async def tail(self, on_event: OnEvent, last_event_id: str | None = None,
                   poll_interval: float = 0.2, close_grace: float = 10.0,
                   source_wait_timeout: float = 300.0) -> None:
        """归并推送直到终态（wb scan_end 扣发 + 全 run 终态 + 宽限）或源等待超时。

        - ``close_grace``：终态条件首次全满足后再等的窗口——覆盖「白盒刚收尾、
          run-1 目录尚未建」的创建竞态，也留出用户点续跑的缝隙。
        - ``source_wait_timeout``：所有源文件都未出现时的等待上限（对齐 EventTailer
          的文件出现等待；超时返程，前端 EventSource 重连后重试）。
        """
        resume = self.parse_last_event_id(last_event_id)
        waited = 0.0
        closable_since: float | None = None
        while True:
            self._discover(resume)
            for source in self._sources.values():
                await self._pump(source)
            emitted = await self._drain(on_event)
            if emitted:
                waited = 0.0
            # 终态判定：wb scan_end 已见（扣住）+ 所有已见 run 源各自见过 scan_end
            runs = [s for s in self._sources.values() if s.label not in ("ac", "wb")]
            closable = (self._held_end is not None
                        and all(s.seen_end for s in runs))
            if closable:
                if closable_since is None:
                    closable_since = asyncio.get_running_loop().time()
                elif asyncio.get_running_loop().time() - closable_since >= close_grace:
                    await on_event(self._held_end, self._id_snapshot())
                    return
            else:
                closable_since = None
            if not emitted:
                if not any(s.path.exists() for s in self._sources.values()):
                    # 全量源文件都未出现（扫描尚未启动/预检中）——对齐 EventTailer 等待语义
                    waited += poll_interval
                    if waited > source_wait_timeout:
                        return
                await asyncio.sleep(poll_interval)

    # ---- 单源读取 ----

    async def _pump(self, source: _Source) -> None:
        """读源文件新增字节 → 按**字节**切完整行入 pending（跨源排序由 _drain 统一做）。"""
        if not source.path.exists():
            return
        try:
            async with aiofiles.open(source.path, "rb") as fh:
                size = await fh.seek(0, 2)
                if size < source.file_off:
                    source.reset()
                await fh.seek(source.file_off)
                chunk = await fh.read()
        except OSError:
            return  # 读取竞态（写入中重建等）：下轮再试
        if not chunk:
            return
        source.file_off += len(chunk)
        lines = (source.carry + chunk).split(b"\n")
        source.carry = lines.pop()  # 未终结的半截行留待下轮
        for raw in lines:
            source.line_pos += len(raw) + 1
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            source.pending.append((_parse_ts(data.get("ts")), source.line_pos, data))

    async def _drain(self, on_event: OnEvent) -> int:
        """跨源按 ts 归并发出 pending 事件（含 scan_end 三态处理），返发出条数。"""
        batch: list[tuple[_Source, datetime | None, int, dict]] = []
        for source in self._sources.values():
            batch.extend((source, ts, end_off, data)
                         for ts, end_off, data in source.pending)
            source.pending.clear()
        if not batch:
            return 0
        batch.sort(key=lambda item: (
            item[1] is None, item[1] or datetime.min, item[0].priority, item[2]))
        emitted = 0
        for source, _ts, end_off, data in batch:
            source.emit_off = end_off
            if data.get("type") == "scan_end":
                if source.label == "ac":
                    continue  # 预验证收尾：跳过不发（emit_off 已推进，续传不重读）
                if source.label == "wb":
                    self._held_end = data  # 扣住，终态判定满足后最后发
                    source.seen_end = True
                    continue
                # run-K 收尾：改写 type 转发（对全量流非终态），标该 run 终态
                await on_event(dict(data, type="run_end", run=source.label),
                               self._id_snapshot())
                source.seen_end = True
                emitted += 1
                continue
            await on_event(data, self._id_snapshot())
            emitted += 1
        return emitted
