"""扫描存活判定：基于 workspace 文件 mtime（host/容器边界的唯一共享存活信号）。

web 容器非 host PID namespace，看不到 host 上 CLI 起的 scan 进程；scan_manager 的
_active_pids 只含 web 自己拉起的子进程。故「不在 _active_pids」≠「已死」——host 起的
scan 仍活着但 web 看不到其 pid（见 kol_mapping_service_20260708-193139 被误判 interrupted
回归：reconciler 据此写了假 scan_end）。唯一可靠的跨边界存活信号是 workspaces bind mount
上的文件 mtime：活跃 scan 持续写 workflow.log（实测每数秒~分钟级），死掉的 scan 停止写。

reconciler（orphan_reconciler）与状态判定（workspaces_indexer._status_of）据此避免把
活 scan 误判成孤儿/中断。容器内 web 自起 scan 在容器重启时子进程同死、workflow.log 停写，
mtime 一样转 stale——故该信号对 web 起的孤儿同样成立。
"""
from __future__ import annotations

import time
from pathlib import Path

# 存活窗口：workflow.log 写入频率每数秒~分钟级（实测样本最大间隔 ~1.5min），单次长 LLM
# 调用可能数分钟不写。取 15min 偏保守——宁可晚判 interrupted（活 scan 仍显 running），
# 也不误杀活 scan（本模块修的就是误杀）。可用 SHANNON_SCAN_LIVENESS_SECONDS 覆盖。
import os

DEFAULT_LIVENESS_SECONDS = float(os.environ.get("SHANNON_SCAN_LIVENESS_SECONDS", "900"))


def is_scan_recently_active(
    ws_dir: Path, threshold_seconds: float | None = None
) -> bool:
    """workspace 是否在 threshold 内被 scan 进程写过（workflow.log 优先，session.json 兜底）。

    True = 扫描大概率仍存活（即便 web 的 scan_manager 看不到其 pid，如 host CLI 起的 scan）。
    False = 长时间无写入，视为已死/停滞（容器重启杀掉的 web scan、或真崩溃的 host scan）。
    """
    if threshold_seconds is None:
        threshold_seconds = DEFAULT_LIVENESS_SECONDS
    now = time.time()
    for name in ("workflow.log", "session.json"):
        f = ws_dir / name
        try:
            if f.exists() and (now - f.stat().st_mtime) <= threshold_seconds:
                return True
        except OSError:
            continue
    return False
