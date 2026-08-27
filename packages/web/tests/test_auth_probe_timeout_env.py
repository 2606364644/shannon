"""authcheck probe 窗口 env 化（2026-08-28）：SUPERNOVA_AUTH_VALIDATION_TIMEOUT_SECONDS。

容量铁律（CLAUDE.md §1）同型：authcheck 3×10min 全超时白烧 30 分钟
（NodeGoat-20260827-152204），窗口须可按 provider 实测重估。sandbox 禁 os.getenv
（RestrictedWorkflowAccessError），env 由 web 构造 input 处解析：
ws env 段（经 SCAN_ENV_KEYS 白名单）优先 → web 进程 env → 默认 600s（=原 10min，
不改变现有行为）。
"""
from supernova_web.components.scan_manager import _auth_probe_timeout_seconds
from supernova_web.components.ws_env_codec import SCAN_ENV_KEYS

KEY = "SUPERNOVA_AUTH_VALIDATION_TIMEOUT_SECONDS"


def test_key_in_scan_env_whitelist():
    """key 必须在 SCAN_ENV_KEYS——不在则 ws env 段解析时落 unknown 被拒，ws 覆盖失效。"""
    assert KEY in SCAN_ENV_KEYS


def test_ws_env_overrides_take_priority(monkeypatch):
    monkeypatch.setenv(KEY, "999")
    assert _auth_probe_timeout_seconds({KEY: "300"}) == 300


def test_falls_back_to_process_env(monkeypatch):
    monkeypatch.setenv(KEY, "888")
    assert _auth_probe_timeout_seconds({}) == 888


def test_defaults_to_600(monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    assert _auth_probe_timeout_seconds(None) == 600
    assert _auth_probe_timeout_seconds({}) == 600


def test_garbage_or_nonpositive_falls_back_to_600(monkeypatch):
    monkeypatch.setenv(KEY, "abc")
    assert _auth_probe_timeout_seconds({}) == 600
    monkeypatch.setenv(KEY, "0")
    assert _auth_probe_timeout_seconds({}) == 600
    monkeypatch.setenv(KEY, "-5")
    assert _auth_probe_timeout_seconds({}) == 600
