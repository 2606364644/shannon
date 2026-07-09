"""扫描存活判定:基于 workspace 下 heartbeat 文件 mtime(host/容器边界的唯一共享存活信号)。

设计见 docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md。

web 容器非 host PID namespace,看不到 host 上 CLI 起的 scan 进程;scan_manager 的 pid 表
只含 web 自己拉起的子进程。故「不在 pid 表」≠「已死」——host 起的 scan 仍活但 web 看不到其
pid(见 kol_mapping_service_20260708-193139 被误判 interrupted 回归)。唯一可靠的跨边界存活信号
是 workspaces bind mount 上的文件:scan worker 以**进程级 HeartbeatManager 每 interval 秒原子写
<ws>/heartbeat**(独立于 Temporal workflow/activity 调度,不受 LLM 卡顿影响),worker 活就跳、
worker 死(退出/崩溃/容器重启)就停写、mtime 转 stale。

窗口默认 90s(=心跳周期 30s × 3 容差),可用 SHANNON_SCAN_LIVENESS_SECONDS 覆盖。取代旧实现
靠 workflow.log mtime(写入频率受 LLM 影响、窗口 900s 过宽致「卡 Running」15min)。

reconciler(orphan_reconciler)与状态判定(workspaces_indexer._status_of)据此避免把活 scan
误判成孤儿/中断。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

HEARTBEAT_FILENAME = "heartbeat"


def _liveness_seconds() -> float:
    """判活窗口从 env 函数内读(非 import 时求值),使 monkeypatch env / per-profile 配置生效。"""
    return float(os.environ.get("SHANNON_SCAN_LIVENESS_SECONDS", "90"))


def is_scan_recently_active(
    ws_dir: Path, threshold_seconds: float | None = None
) -> bool:
    """workspace 的 heartbeat 是否在窗口内被写(= scan worker 进程存活)。

    True = scan 大概率仍存活(heartbeat 被 HeartbeatManager 周期刷新)。
    False = 长时间无心跳(worker 已退出/崩溃,或容器重启杀掉子进程;或非 scan 工作区)。
    """
    if threshold_seconds is None:
        threshold_seconds = _liveness_seconds()
    hb = Path(ws_dir) / HEARTBEAT_FILENAME
    try:
        if hb.exists() and (time.time() - hb.stat().st_mtime) <= threshold_seconds:
            return True
    except OSError:
        return False
    return False
