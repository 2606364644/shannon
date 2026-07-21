"""孤儿 scan 对账：容器重启等场景下 scan_manager._watch 丢失，session 卡 running
且无 scan_end。本模块在启动 / events 端点惰性触发时为这类孤儿补写 scan_end
(interrupted) + 失败原因，让 live SSE 能正常关流、前端显「已中断」。

判定孤儿：session 非完成/失败态 + 进程不存活 + events.ndjson 无 scan_end。
不触碰仍存活（is_running=True）或已结案（completed/failed）的 scan。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from supernova_core.session import SessionManager

from .scan_liveness import is_scan_alive

# activity_failures.log 尾部截断长度（与 scan_manager.stderr_tail 对齐）
_TAIL_BYTES = 2048


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_scan_end(event_file: Path) -> bool:
    """events.ndjson 末 5 行是否含 scan_end（与 ScanManager._has_scan_end 同口径）。"""
    if not event_file.exists():
        return False
    for line in event_file.read_text("utf-8", errors="replace").splitlines()[-5:]:
        try:
            if json.loads(line).get("type") == "scan_end":
                return True
        except json.JSONDecodeError:
            continue
    return False


def _failure_tail(ws_dir: Path) -> str:
    """读 activity_failures.log 尾部（若有）作为失败原因上送 live 页。"""
    f = ws_dir / "activity_failures.log"
    if not f.exists():
        return ""
    return f.read_text("utf-8", errors="replace")[-_TAIL_BYTES:]


async def _write_scan_end(event_file: Path, status: str,
                          returncode: int, stderr_tail: str) -> None:
    payload = {
        "ts": _now_iso(), "category": "CONTROL", "type": "scan_end",
        "status": status, "returncode": returncode, "stderr_tail": stderr_tail,
    }
    async with aiofiles.open(event_file, "a") as fh:
        await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def reconcile_orphaned(ws_dir: Path, is_running: bool) -> bool:
    """若 ws 是孤儿（非完成/失败 + 不存活 + 无 scan_end），补写 scan_end + 标 session 完成。

    返回是否实际写了 scan_end。任何异常都不应阻塞调用方（启动 / 请求），故内部兜底。
    """
    try:
        session_file = ws_dir / "session.json"
        if not session_file.exists():
            return False  # 非 scan 工作区（如 default/ logs/ 等辅助目录）
        if is_running:
            return False  # web 托管且仍存活，绝不干预

        # 跨 host/容器边界存活信号:heartbeat fresh(worker 在跑)OR 提交宽限内(workflow 刚提交、
        # worker 还没写首个 heartbeat 的冷启动窗口)。后者防「提交后 1s 内前端首次 poll /events 即
        # 触发 reconcile 误判 interrupted」(hr_1784014329 即此, _status_of 终态优先致误杀不可逆)。
        # 回归:kol_mapping_service_20260708-193139(host CLI 起的活 scan)被误判即缺 heartbeat 门。
        if is_scan_alive(ws_dir):
            return False

        mgr = SessionManager(ws_dir.parent)
        status = mgr.get_status(ws_dir)
        if status in ("completed", "failed"):
            return False  # 已结案

        event_file = ws_dir / "events.ndjson"
        if _has_scan_end(event_file):
            return False  # 幂等：已有 scan_end（_watch 或 StructuredEventRenderer 已收尾）

        reason = ("扫描未检测到 worker 心跳——worker 容器可能未启动或已退出"
                  "（worker 应在扫描提交后数秒内写首个 heartbeat；持续无心跳请检查"
                  " worker 容器是否运行，如 ./scripts/up.sh 是否已带起 worker）")
        tail = _failure_tail(ws_dir)
        if tail:
            reason = reason + "；activity 失败日志尾部：\n" + tail
        await _write_scan_end(event_file, "interrupted", -1, reason)

        # session.json 标完成时间，让列表/概览不再显永卡 running；status 设 interrupted
        # （_status_of 对非 completed/failed + 不存活一律归 interrupted，设不设都归此；
        #  设上是为了 completed_at + 显式语义，方便排查）。
        mgr.update_session(ws_dir, {
            "completed_at": time.time(),
            "status": "interrupted",
        })
        return True
    except Exception:
        # 对账是兜底增强，绝不因单 ws 异常拖垮启动或请求
        return False
