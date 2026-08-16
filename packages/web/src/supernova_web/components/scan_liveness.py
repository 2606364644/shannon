"""扫描存活判定:基于 workspace 下 heartbeat 文件 mtime(host/容器边界的唯一共享存活信号)。

设计见 docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md。

web 容器非 host PID namespace,看不到 host 上 CLI 起的 scan 进程;scan_manager 的 pid 表
只含 web 自己拉起的子进程。故「不在 pid 表」≠「已死」——host 起的 scan 仍活但 web 看不到其
pid(见 kol_mapping_service_20260708-193139 被误判 interrupted 回归)。唯一可靠的跨边界存活信号
是 workspaces bind mount 上的文件:scan worker 以**进程级 HeartbeatManager 每 interval 秒原子写
<ws>/heartbeat**(独立于 Temporal workflow/activity 调度,不受 LLM 卡顿影响),worker 活就跳、
worker 死(退出/崩溃/容器重启)就停写、mtime 转 stale。组合扫描的黑盒 run 阶段 worker 的
workspace_path 是 run 子目录,heartbeat 落 blackbox-runs/run-K/heartbeat(预验证阶段落
.authcheck/heartbeat)——判活候选覆盖这些子目录,任一 fresh 即活。

窗口默认 90s(=心跳周期 30s × 3 容差),可用 SUPERNOVA_SCAN_LIVENESS_SECONDS 覆盖。取代旧实现
靠 workflow.log mtime(写入频率受 LLM 影响、窗口 900s 过宽致「卡 Running」15min)。

reconciler(orphan_reconciler)与状态判定(workspaces_indexer._status_of)据此避免把活 scan
误判成孤儿/中断。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HEARTBEAT_FILENAME = "heartbeat"

# 除任务根 heartbeat 外，组合扫描的活信号还可能落在子目录（run 级 heartbeat）：
# - blackbox-runs/run-K/heartbeat：黑盒 run 阶段 worker 的 workspace_path=run 子目录
#   （按 event_file.parent 推导），任务根 heartbeat 已随白盒 finalize stop 而 stale；
# - .authcheck/heartbeat：t0 认证预验证 workflow 的隔离 scratch 目录。
# 任一 fresh 即活——否则黑盒阶段 > 提交宽限(120s) 后任务被误判 interrupted。
_SUB_HEARTBEAT_GLOBS = ("blackbox-runs/*/heartbeat", ".authcheck/heartbeat")


def _heartbeat_candidates(ws_dir: Path) -> list[Path]:
    """scan 判活的 heartbeat 候选集：任务根 + run 级 + 预验证 scratch。"""
    ws_dir = Path(ws_dir)
    candidates = [ws_dir / HEARTBEAT_FILENAME]
    for pattern in _SUB_HEARTBEAT_GLOBS:
        candidates.extend(ws_dir.glob(pattern))
    return candidates


def _liveness_seconds() -> float:
    """判活窗口从 env 函数内读(非 import 时求值),使 monkeypatch env / per-profile 配置生效。"""
    return float(os.environ.get("SUPERNOVA_SCAN_LIVENESS_SECONDS", "90"))


def is_scan_recently_active(
    ws_dir: Path, threshold_seconds: float | None = None
) -> bool:
    """scan 的任一 heartbeat 是否在窗口内被写(= scan worker 进程存活)。

    候选含任务根 heartbeat + 黑盒 run 级(blackbox-runs/*/heartbeat) + 认证预验证
    scratch(.authcheck/heartbeat)：组合扫描的黑盒阶段 worker 落在 run 子目录，任务根
    heartbeat 不再刷新，须看子目录才不误判 interrupted。

    True = scan 大概率仍存活(heartbeat 被 HeartbeatManager 周期刷新)。
    False = 长时间无心跳(worker 已退出/崩溃,或容器重启杀掉子进程;或非 scan 工作区)。
    """
    if threshold_seconds is None:
        threshold_seconds = _liveness_seconds()
    for hb in _heartbeat_candidates(ws_dir):
        try:
            if hb.exists() and (time.time() - hb.stat().st_mtime) <= threshold_seconds:
                return True
        except OSError:
            continue
    return False


# ---- 提交宽限门(C1 Phase B: worker 冷启动窗口防误杀) ----
# 根因:web 提交 workflow 后,worker 容器 poll 到 task + 写首个 heartbeat 前有数秒冷启动窗口。
# 此前 reconcile/_status_of 仅看 heartbeat → 提交后 1s 内前端首次 poll /events 即误判 interrupted,
# 且 _status_of 终态优先致误杀不可逆。is_scan_alive = heartbeat fresh OR 提交宽限内。

# 提交宽限默认秒数:覆盖 temporal workflow task 调度 + worker long-poll(~1s) + 首个 heartbeat。
# worker 常驻已起时该窗口通常数秒;留足 120s 容 worker 首启抖动。env 可调。
SUBMIT_GRACE_DEFAULT_SECONDS = 120


def _submit_grace_seconds() -> float:
    """提交宽限窗口从 env 函数内读(非 import 时求值),使 monkeypatch env / per-profile 生效。"""
    return float(os.environ.get("SUPERNOVA_SCAN_LIVENESS_SUBMIT_GRACE_SECONDS",
                                str(SUBMIT_GRACE_DEFAULT_SECONDS)))


def _ws_submitted_at(ws_dir: Path) -> float | None:
    """读 session.json 的提交时间锚点:submitted_at(web 提交端写)优先,缺失回退 created_at
    (历史 session 兼容)。返 unix float;无/异常 → None。

    用于「提交宽限」门:web 提交 workflow 后、worker 写首个 heartbeat 前的冷启动窗口,
    据此判 scan 仍活跃(避免误判 interrupted)。submitted_at 每次 start_workflow 提交刷新,
    resume 场景也准确(resume 时 created_at 是老的,不能用)。
    """
    session_file = Path(ws_dir) / "session.json"
    try:
        data = json.loads(session_file.read_text("utf-8"))
        if isinstance(data, dict):
            for key in ("submitted_at", "created_at"):
                v = data.get(key)
                # bool 是 int 子类,排除(与 workspaces_indexer._to_unix 同口径)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
    except (OSError, ValueError):
        return None
    return None


def is_scan_within_submit_grace(ws_dir: Path) -> bool:
    """scan 是否在提交宽限窗口内(submitted_at/created_at 距今 <= grace)。

    True = workflow 刚提交不久,worker 可能仍在冷启动(尚未写首个 heartbeat)。
    False = 提交已久/无锚点 → 退回 heartbeat 判活(底层 is_scan_recently_active)。
    """
    anchor = _ws_submitted_at(ws_dir)
    if anchor is None:
        return False
    return (time.time() - anchor) <= _submit_grace_seconds()


def is_scan_alive(ws_dir: Path) -> bool:
    """scan 是否活跃:heartbeat fresh OR 提交宽限内(冷启动窗口)。

    高层判活;reconcile_orphaned / workspaces_indexer._status_of 据此避免误杀刚提交的 scan。
    is_scan_recently_active 保留纯 heartbeat 语义,scan_manager.cancel(host 协作式)等仍用底层。
    """
    return is_scan_recently_active(ws_dir) or is_scan_within_submit_grace(ws_dir)
