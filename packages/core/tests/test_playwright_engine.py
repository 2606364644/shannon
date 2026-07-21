"""Tests for PlaywrightEngine.cleanup_processes."""

from __future__ import annotations

from supernova_core.services.browser_engine import BrowserEngine
from supernova_core.services.engines.playwright_engine import PlaywrightEngine


class TestPlaywrightEngineCleanupProcesses:
    def test_satisfies_protocol(self):
        assert isinstance(PlaywrightEngine(), BrowserEngine)

    def test_returns_summary_dict_shape(self, monkeypatch):
        engine = PlaywrightEngine()
        _record(monkeypatch, returncodes=[0])
        result = engine.cleanup_processes(session_ids=["agent1"])
        assert {"closed", "killed", "errors"} <= set(result.keys())

    def test_graceful_close_before_pkill(self, monkeypatch):
        """close 成功(rc=0)时不 pkill。"""
        engine = PlaywrightEngine()
        cmds = _record(monkeypatch, returncodes=[0])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "playwright-cli" in joined and "close" in joined
        assert "pkill" not in joined

    def test_pkill_fallback_when_close_fails(self, monkeypatch):
        """close 返回非零 -> pkill 兜底。"""
        engine = PlaywrightEngine()
        cmds = _record(monkeypatch, returncodes=[1])
        engine.cleanup_processes(session_ids=["agent1"])
        joined = " ".join(cmds)
        assert "pkill" in joined

    def test_errors_swallowed(self, monkeypatch):
        """subprocess 抛异常时吞掉填 errors,不 raise。"""
        engine = PlaywrightEngine()

        class _FakeSub:
            DEVNULL = -3

            @staticmethod
            def run(cmd, *a, **kw):
                raise FileNotFoundError("no playwright-cli")

        from supernova_core.services.engines import playwright_engine as mod

        monkeypatch.setattr(mod, "subprocess", _FakeSub, raising=False)
        result = engine.cleanup_processes(session_ids=["agent1"])
        assert result["errors"]


def _record(monkeypatch, returncodes):
    """记录 subprocess.run 命令,可控 returncode。raising=False 让 RED 干净。"""
    from supernova_core.services.engines import playwright_engine as mod

    cmds = []

    class _R:
        def __init__(self, rc):
            self.returncode = rc

    it = iter(returncodes)

    class _FakeSub:
        DEVNULL = -3  # 占位,匹配 subprocess.DEVNULL 用法

        @staticmethod
        def run(cmd, *a, **kw):
            cmds.append(" ".join(str(c) for c in cmd))
            try:
                rc = next(it)
            except StopIteration:
                rc = 0
            return _R(rc)

    monkeypatch.setattr(mod, "subprocess", _FakeSub, raising=False)
    return cmds
