"""Dedicated tests for AgentBrowserEngine."""

from __future__ import annotations

from pathlib import Path

from shannon_core.services.browser_engine import BrowserEngine
from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine


# ---------------------------------------------------------------------------
# Engine identity
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineIdentity:
    def test_name_returns_agent_browser(self):
        engine = AgentBrowserEngine()
        assert engine.name == "agent-browser"

    def test_satisfies_browser_engine_protocol(self):
        engine = AgentBrowserEngine()
        assert isinstance(engine, BrowserEngine)


# ---------------------------------------------------------------------------
# Session flag
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineSessionFlag:
    def test_session_flag_format(self):
        """Session flag should be space-separated and include --session."""
        engine = AgentBrowserEngine()
        flag = engine.session_flag("sess-123")
        assert "--session sess-123" in flag

    def test_session_flag_includes_profile_path(self):
        """Session flag must include --profile .agent-browser/profiles/{sid}."""
        engine = AgentBrowserEngine()
        flag = engine.session_flag("my-session")
        assert "--profile .agent-browser/profiles/my-session" in flag

    def test_session_flag_uses_session_id(self):
        engine = AgentBrowserEngine()
        flag = engine.session_flag("abc")
        assert "abc" in flag


# ---------------------------------------------------------------------------
# Commands reference
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineCommandsReference:
    def test_commands_reference_not_empty(self):
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert isinstance(ref, str)
        assert len(ref) > 0

    def test_commands_reference_mentions_snapshot_and_ref(self):
        """Key agent-browser concepts (snapshot, @ref) should be present."""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "snapshot" in ref.lower()
        assert "@ref" in ref

    def test_commands_reference_no_playwright_references(self):
        """Agent-browser reference should NOT mention 'playwright'."""
        engine = AgentBrowserEngine()
        ref = engine.commands_reference()
        assert "playwright" not in ref.lower()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineAuth:
    def test_auth_save_command_returns_empty(self):
        engine = AgentBrowserEngine()
        result = engine.auth_save_command("sess-1", "/tmp/auth.json")
        assert result == ""

    def test_auth_load_command_returns_empty(self):
        engine = AgentBrowserEngine()
        result = engine.auth_load_command("sess-1", "/tmp/auth.json")
        assert result == ""


# ---------------------------------------------------------------------------
# Config management – write_config
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineWriteConfig:
    def test_write_config_creates_profile_dir(self, tmp_path):
        """Default session creates .agent-browser/profiles/default/."""
        engine = AgentBrowserEngine()
        result = engine.write_config(str(tmp_path))
        assert result["result"] == "wrote"
        profile_dir = Path(result["configPath"])
        assert profile_dir.exists()
        assert ".agent-browser/profiles/default" in str(profile_dir)

    def test_write_config_creates_named_session_dir(self, tmp_path):
        """Named session creates .agent-browser/profiles/{session_id}/."""
        engine = AgentBrowserEngine()
        result = engine.write_config(str(tmp_path), session_id="my-session")
        assert result["result"] == "wrote"
        profile_dir = Path(result["configPath"])
        assert profile_dir.exists()
        assert ".agent-browser/profiles/my-session" in str(profile_dir)

    def test_write_config_skips_existing(self, tmp_path):
        """Writing config twice should be idempotent (skips-existing)."""
        engine = AgentBrowserEngine()
        first = engine.write_config(str(tmp_path), session_id="dup")
        assert first["result"] == "wrote"
        second = engine.write_config(str(tmp_path), session_id="dup")
        assert second["result"] == "skipped-existing"
        assert first["configPath"] == second["configPath"]


# ---------------------------------------------------------------------------
# Config management – cleanup_config
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineCleanupConfig:
    def test_cleanup_config_removes_session_dir(self, tmp_path):
        """Session-specific cleanup removes only that session's profile dir."""
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="cleanup-me")
        profile_dir = tmp_path / ".agent-browser" / "profiles" / "cleanup-me"
        assert profile_dir.exists()

        engine.cleanup_config(str(tmp_path), session_id="cleanup-me")
        assert not profile_dir.exists()
        # The .agent-browser parent dir should still exist
        assert (tmp_path / ".agent-browser").exists()

    def test_cleanup_config_removes_all_when_no_session(self, tmp_path):
        """No session_id removes the entire .agent-browser/ directory."""
        engine = AgentBrowserEngine()
        engine.write_config(str(tmp_path), session_id="sess-a")
        engine.write_config(str(tmp_path), session_id="sess-b")
        assert (tmp_path / ".agent-browser").exists()

        engine.cleanup_config(str(tmp_path), session_id=None)
        assert not (tmp_path / ".agent-browser").exists()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestAgentBrowserEngineAvailability:
    def test_check_available_returns_bool(self):
        engine = AgentBrowserEngine()
        result = engine.check_available()
        assert isinstance(result, bool)
