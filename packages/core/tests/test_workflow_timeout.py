"""workflow_run_timeout: 整个扫描的 wall-clock 总闸门,默认 3h,env 可配。"""
from datetime import timedelta

from supernova_core.runtime.workflow_timeout import workflow_run_timeout


def test_default_3h(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_WORKFLOW_TIMEOUT_HOURS", raising=False)
    assert workflow_run_timeout() == timedelta(hours=3)


def test_env_override(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WORKFLOW_TIMEOUT_HOURS", "5")
    assert workflow_run_timeout() == timedelta(hours=5)
