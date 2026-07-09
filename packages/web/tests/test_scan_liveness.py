"""scan_liveness 判活测试:基于 heartbeat 文件 mtime(取代 workflow.log)。

设计见 docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md §4.3/§5。
判活靠 heartbeat:scan worker 进程级 HeartbeatManager 周期写,worker 死则停写、mtime 转 stale。
"""
from __future__ import annotations

import os
import time

from shannon_web.components.scan_liveness import is_scan_recently_active


def test_heartbeat_fresh_is_active(tmp_path):
    """heartbeat 存在且 mtime fresh → scan 存活。"""
    (tmp_path / "heartbeat").write_text(f"{time.time()}\n")
    assert is_scan_recently_active(tmp_path) is True


def test_heartbeat_stale_not_active(tmp_path):
    """heartbeat mtime 远古(>窗口)→ 已死/停滞。"""
    hb = tmp_path / "heartbeat"
    hb.write_text("x\n")
    old = time.time() - 3600
    os.utime(hb, (old, old))
    assert is_scan_recently_active(tmp_path) is False


def test_no_heartbeat_not_active(tmp_path):
    """无 heartbeat 文件 → 不判活(非 scan 或 scan 未起心跳)。"""
    assert is_scan_recently_active(tmp_path) is False


def test_workflow_log_no_longer_signals_active(tmp_path):
    """回归:判活信号源已从 workflow.log 换成 heartbeat。workflow.log fresh 不再判活。"""
    (tmp_path / "workflow.log").write_text("scan running\n")
    assert is_scan_recently_active(tmp_path) is False


def test_threshold_param_overrides_default(tmp_path):
    """threshold_seconds 参数显式覆盖窗口。"""
    hb = tmp_path / "heartbeat"
    hb.write_text("x\n")
    age = time.time() - 5
    os.utime(hb, (age, age))
    assert is_scan_recently_active(tmp_path, threshold_seconds=10) is True
    assert is_scan_recently_active(tmp_path, threshold_seconds=1) is False


def test_default_window_is_90(monkeypatch, tmp_path):
    """窗口默认 90s(原 900s 的回归窗口太宽,改 90 压缩「卡 Running」;spec §5)。"""
    monkeypatch.delenv("SHANNON_SCAN_LIVENESS_SECONDS", raising=False)
    hb = tmp_path / "heartbeat"
    hb.write_text(f"{time.time()}\n")  # <90s → 活
    assert is_scan_recently_active(tmp_path) is True
    old = time.time() - 95  # >90s → 死
    os.utime(hb, (old, old))
    assert is_scan_recently_active(tmp_path) is False
