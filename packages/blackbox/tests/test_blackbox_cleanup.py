"""Blackbox cleanup_engine_configs 同时清理 config 文件 + browser 进程。"""

from __future__ import annotations


class TestCleanupEngineConfigsAlsoKillsProcesses:
    async def test_cleanup_calls_cleanup_processes(self, monkeypatch):
        """cleanup_engine_configs 应在删 config 后调 engine.cleanup_processes。"""
        from supernova_blackbox.pipeline import activities as act

        calls = {}

        class FakeEngine:
            def cleanup_config(self, source_dir, session_id=None):
                calls.setdefault("cleanup_config", []).append(session_id)

            def cleanup_processes(self, source_dir=None, session_ids=None):
                calls["cleanup_processes"] = (source_dir, session_ids)
                return {"closed": [], "killed": [], "errors": []}

        monkeypatch.setattr(
            "supernova_core.services.browser_engine.BrowserEngineFactory.get_engine",
            lambda name: FakeEngine(),
        )
        monkeypatch.setattr(
            "supernova_core.services.playwright_config_writer.AGENT_SESSION_MAPPING",
            {"a": "agent1", "b": "agent2"},
        )

        await act.cleanup_engine_configs("/tmp/repo", "agent-browser")

        assert "cleanup_processes" in calls
        src, sids = calls["cleanup_processes"]
        assert src == "/tmp/repo"
        assert set(sids) == {"agent1", "agent2"}
