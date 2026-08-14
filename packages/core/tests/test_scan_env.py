"""scan_env：per-scan env 覆盖层单测。

覆盖：os.environ 回落、覆盖命中、set/clear、两 workflow 隔离、activity 上下文解析、
CLI 回落、bool 语义、未注入时与 os.environ.get 等价（零行为变化不变量）。
"""
from __future__ import annotations

import sys
import types

import pytest

from supernova_core.config import scan_env
from supernova_core.config.scan_env import (
    _SCAN_ENV,
    _resolve_wf_id,
    clear_scan_env,
    get_scan_env,
    set_scan_env,
    ws_getenv,
    ws_getenv_bool,
)


@pytest.fixture(autouse=True)
def _clean_scan_env():
    """每个测试前后清空覆盖层，避免跨测试串扰。"""
    _SCAN_ENV.clear()
    yield
    _SCAN_ENV.clear()


def _install_fake_activity(monkeypatch, workflow_id: str | None, *, raises: bool = False):
    """注入假 temporalio.activity，模拟 worker activity 上下文。"""
    fake = types.ModuleType("temporalio")
    fake_activity = types.ModuleType("temporalio.activity")

    def _info():
        if raises:
            raise RuntimeError("not in activity context")
        return types.SimpleNamespace(workflow_id=workflow_id)

    fake_activity.info = _info
    fake.activity = fake_activity
    monkeypatch.setitem(sys.modules, "temporalio", fake)
    monkeypatch.setitem(sys.modules, "temporalio.activity", fake_activity)


# ---- 不变量：未注入覆盖层时 ws_getenv == os.environ.get ----

def test_ws_getenv_falls_back_to_os_environ(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_TEST_X", "from-env")
    assert ws_getenv("SUPERNOVA_TEST_X") == "from-env"
    assert ws_getenv("SUPERNOVA_TEST_MISSING") is None
    assert ws_getenv("SUPERNOVA_TEST_MISSING", "def") == "def"


def test_ws_getenv_equals_os_environ_when_no_override(monkeypatch):
    """未注入覆盖层时 ws_getenv 与 os.environ.get 行为完全一致。"""
    import os

    monkeypatch.setenv("SUPERNOVA_TEST_Y", "v")
    for key in ("SUPERNOVA_TEST_Y", "DOES_NOT_EXIST"):
        assert ws_getenv(key) == os.environ.get(key)


# ---- 显式 workflow_id 注入（不依赖 temporalio）----

def test_set_and_get_scan_env_explicit_wf():
    set_scan_env({"SUPERNOVA_K": "ws-value"}, workflow_id="wf-A")
    assert _SCAN_ENV["wf-A"] == {"SUPERNOVA_K": "ws-value"}


def test_clear_scan_env_explicit_wf():
    set_scan_env({"SUPERNOVA_K": "ws-value"}, workflow_id="wf-A")
    clear_scan_env(workflow_id="wf-A")
    assert "wf-A" not in _SCAN_ENV


def test_clear_scan_env_missing_wf_is_noop():
    clear_scan_env(workflow_id="never-set")  # 不应抛


def test_two_workflows_isolated():
    set_scan_env({"SUPERNOVA_K": "A"}, workflow_id="wf-A")
    set_scan_env({"SUPERNOVA_K": "B"}, workflow_id="wf-B")
    assert _SCAN_ENV["wf-A"]["SUPERNOVA_K"] == "A"
    assert _SCAN_ENV["wf-B"]["SUPERNOVA_K"] == "B"


def test_set_scan_env_none_overrides_yields_empty_dict():
    set_scan_env(None, workflow_id="wf-A")
    assert _SCAN_ENV["wf-A"] == {}


# ---- activity 上下文：_resolve_wf_id 从 activity.info() 拿 workflow_id ----

def test_resolve_wf_id_from_activity(monkeypatch):
    _install_fake_activity(monkeypatch, workflow_id="wf-from-activity")
    assert _resolve_wf_id() == "wf-from-activity"


def test_resolve_wf_id_explicit_wins(monkeypatch):
    _install_fake_activity(monkeypatch, workflow_id="wf-from-activity")
    assert _resolve_wf_id("explicit") == "explicit"


def test_resolve_wf_id_cli_fallback_when_no_activity(monkeypatch):
    _install_fake_activity(monkeypatch, workflow_id=None, raises=True)
    assert _resolve_wf_id() is None


def test_ws_getenv_reads_override_in_activity_context(monkeypatch):
    """worker activity 内：set_scan_env 不传 wf（靠 activity.info()）→ ws_getenv 命中覆盖。"""
    monkeypatch.setenv("SUPERNOVA_K", "from-env")
    _install_fake_activity(monkeypatch, workflow_id="wf-ctx")
    set_scan_env({"SUPERNOVA_K": "from-ws"})  # workflow_id 省略 → activity.info()
    assert ws_getenv("SUPERNOVA_K") == "from-ws"
    assert get_scan_env() == {"SUPERNOVA_K": "from-ws"}


def test_ws_getenv_falls_back_when_override_cleared(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_K", "from-env")
    _install_fake_activity(monkeypatch, workflow_id="wf-ctx")
    set_scan_env({"SUPERNOVA_K": "from-ws"})
    clear_scan_env()
    assert ws_getenv("SUPERNOVA_K") == "from-env"  # 回落 os.environ


def test_ws_getenv_key_not_in_override_falls_back(monkeypatch):
    """覆盖层存在但不含该 key → 回落 os.environ（部分覆盖语义）。"""
    monkeypatch.setenv("SUPERNOVA_OTHER", "env-val")
    _install_fake_activity(monkeypatch, workflow_id="wf-ctx")
    set_scan_env({"SUPERNOVA_K": "ws-val"})  # 只覆盖 K
    assert ws_getenv("SUPERNOVA_OTHER") == "env-val"  # OTHER 仍走 env
    assert ws_getenv("SUPERNOVA_K") == "ws-val"


def test_concurrent_workflows_do_not_cross_contaminate(monkeypatch):
    """模拟两扫描并发：各自 activity 上下文读各自的覆盖，不串台。"""
    monkeypatch.setenv("SUPERNOVA_K", "global")
    _install_fake_activity(monkeypatch, workflow_id="wf-A")
    set_scan_env({"SUPERNOVA_K": "A"})

    # 切到 wf-B 上下文（worker 不同 activity）
    _install_fake_activity(monkeypatch, workflow_id="wf-B")
    set_scan_env({"SUPERNOVA_K": "B"})

    _install_fake_activity(monkeypatch, workflow_id="wf-A")
    assert ws_getenv("SUPERNOVA_K") == "A"
    _install_fake_activity(monkeypatch, workflow_id="wf-B")
    assert ws_getenv("SUPERNOVA_K") == "B"


# ---- ws_getenv_bool ----

@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("NO", False), ("off", False),
    ("anything", True),
])
def test_ws_getenv_bool_truthy(monkeypatch, raw, expected):
    monkeypatch.setenv("SUPERNOVA_B", raw)
    assert ws_getenv_bool("SUPERNOVA_B", default=True) is expected


def test_ws_getenv_bool_default_when_unset(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_B", raising=False)
    assert ws_getenv_bool("SUPERNOVA_B", default=True) is True
    assert ws_getenv_bool("SUPERNOVA_B", default=False) is False


def test_ws_getenv_bool_reads_override(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_B", "true")  # 全局开
    _install_fake_activity(monkeypatch, workflow_id="wf-ctx")
    set_scan_env({"SUPERNOVA_B": "0"})  # 工作区关
    assert ws_getenv_bool("SUPERNOVA_B", default=True) is False
