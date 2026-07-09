"""blackbox worker 把 browser 进程 cleanup 回调注入 ShutdownController(对称 whitebox)。"""

from __future__ import annotations


def test_build_callback_exists():
    from shannon_blackbox import worker

    assert hasattr(worker, "build_browser_cleanup_callback")


def test_callback_invokes_engine(monkeypatch):
    from shannon_blackbox import worker

    called = {}

    class FakeEngine:
        def cleanup_processes(self, source_dir=None, session_ids=None):
            called["args"] = (source_dir, session_ids)

    monkeypatch.setattr(
        "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine",
        lambda name: FakeEngine(),
    )
    cb = worker.build_browser_cleanup_callback("/tmp/repo", "agent-browser")
    cb(session_ids=None)
    assert called["args"] == ("/tmp/repo", None)


def test_callback_none_engine_name_is_noop(monkeypatch):
    from shannon_blackbox import worker

    called = []
    monkeypatch.setattr(
        "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine",
        lambda name: called.append(name),
    )
    cb = worker.build_browser_cleanup_callback("/tmp/repo", None)
    cb(session_ids=None)
    assert called == []


def test_callback_swallows_errors(monkeypatch):
    from shannon_blackbox import worker

    def boom(name):
        raise KeyError("no engine")

    monkeypatch.setattr(
        "shannon_core.services.browser_engine.BrowserEngineFactory.get_engine", boom
    )
    cb = worker.build_browser_cleanup_callback("/tmp/repo", "agent-browser")
    cb(session_ids=None)  # 不抛即通过
