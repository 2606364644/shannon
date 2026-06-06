"""Tests for BrowserEngine Protocol and BrowserEngineFactory."""

from __future__ import annotations

from typing import Protocol

import pytest

from shannon_core.services.browser_engine import (
    BrowserEngine,
    BrowserEngineFactory,
)


# ---------------------------------------------------------------------------
# Helpers – a minimal concrete implementation for testing
# ---------------------------------------------------------------------------


class _StubEngine:
    """Minimal object that satisfies the BrowserEngine Protocol."""

    @property
    def name(self) -> str:  # pragma: no cover – simple property
        return "stub"

    def session_flag(self, session_id: str) -> str:
        return f"--stub-session {session_id}"

    def commands_reference(self) -> str:
        return "stub commands reference"

    def auth_save_command(self, session_id: str, path: str) -> str:
        return f"stub auth save --session {session_id} --path {path}"

    def auth_load_command(self, session_id: str, path: str) -> str:
        return f"stub auth load --session {session_id} --path {path}"

    def write_config(self, source_dir: str, session_id: str | None = None) -> dict:
        return {"result": "wrote", "configPath": f"{source_dir}/stub.config.json"}

    def cleanup_config(self, source_dir: str, session_id: str | None = None) -> None:
        pass

    def check_available(self) -> bool:
        return True


# A second stub so we can test multi-engine registration


class _AltEngine(_StubEngine):
    @property
    def name(self) -> str:  # pragma: no cover
        return "alt"

    def check_available(self) -> bool:  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    """Ensure each test starts with an empty factory registry."""
    saved = BrowserEngineFactory._engines.copy()
    BrowserEngineFactory._engines.clear()
    yield
    BrowserEngineFactory._engines.clear()
    BrowserEngineFactory._engines.update(saved)


# ---------------------------------------------------------------------------
# BrowserEngine Protocol tests
# ---------------------------------------------------------------------------


class TestBrowserEngineProtocol:
    def test_stub_satisfies_protocol(self):
        """_StubEngine should be recognised as a BrowserEngine."""
        assert isinstance(_StubEngine(), BrowserEngine)

    def test_plain_object_fails_protocol(self):
        """A plain object missing required methods is NOT a BrowserEngine."""
        assert not isinstance(object(), BrowserEngine)

    def test_incomplete_object_fails_protocol(self):
        """An object missing even one required method fails the check."""

        class _Incomplete:
            @property
            def name(self) -> str:  # pragma: no cover
                return "incomplete"

            # Missing everything else ...

        assert not isinstance(_Incomplete(), BrowserEngine)

    def test_name_property(self):
        engine = _StubEngine()
        assert engine.name == "stub"

    def test_session_flag(self):
        engine = _StubEngine()
        flag = engine.session_flag("sess-123")
        assert "sess-123" in flag

    def test_commands_reference(self):
        engine = _StubEngine()
        ref = engine.commands_reference()
        assert isinstance(ref, str) and len(ref) > 0

    def test_auth_save_command(self):
        engine = _StubEngine()
        cmd = engine.auth_save_command("sess-1", "/tmp/auth.json")
        assert "sess-1" in cmd
        assert "/tmp/auth.json" in cmd

    def test_auth_load_command(self):
        engine = _StubEngine()
        cmd = engine.auth_load_command("sess-1", "/tmp/auth.json")
        assert "sess-1" in cmd
        assert "/tmp/auth.json" in cmd

    def test_write_config(self):
        engine = _StubEngine()
        result = engine.write_config("/tmp/project")
        assert result["result"] in ("wrote", "skipped-existing")
        assert "configPath" in result

    def test_write_config_with_session(self):
        engine = _StubEngine()
        result = engine.write_config("/tmp/project", session_id="agent-xss")
        assert result["result"] in ("wrote", "skipped-existing")
        assert "configPath" in result

    def test_cleanup_config(self):
        engine = _StubEngine()
        # Should not raise
        engine.cleanup_config("/tmp/project")

    def test_cleanup_config_with_session(self):
        engine = _StubEngine()
        engine.cleanup_config("/tmp/project", session_id="agent-xss")

    def test_check_available(self):
        engine = _StubEngine()
        assert engine.check_available() is True


# ---------------------------------------------------------------------------
# BrowserEngineFactory tests
# ---------------------------------------------------------------------------


class TestBrowserEngineFactory:
    def test_register_and_get(self):
        BrowserEngineFactory.register("stub", _StubEngine)
        engine = BrowserEngineFactory.get_engine("stub")
        assert isinstance(engine, _StubEngine)
        assert engine.name == "stub"

    def test_get_engine_returns_browser_engine(self):
        BrowserEngineFactory.register("stub", _StubEngine)
        engine = BrowserEngineFactory.get_engine("stub")
        assert isinstance(engine, BrowserEngine)

    def test_register_duplicate_raises(self):
        BrowserEngineFactory.register("stub", _StubEngine)
        with pytest.raises(ValueError, match="already registered"):
            BrowserEngineFactory.register("stub", _StubEngine)

    def test_get_unknown_engine_raises(self):
        with pytest.raises(KeyError, match="No browser engine"):
            BrowserEngineFactory.get_engine("nonexistent")

    def test_multiple_engines(self):
        BrowserEngineFactory.register("stub", _StubEngine)
        BrowserEngineFactory.register("alt", _AltEngine)

        stub = BrowserEngineFactory.get_engine("stub")
        alt = BrowserEngineFactory.get_engine("alt")

        assert stub.name == "stub"
        assert alt.name == "alt"
        assert stub.check_available() is True
        assert alt.check_available() is False

    def test_get_engine_returns_new_instance_each_time(self):
        BrowserEngineFactory.register("stub", _StubEngine)
        a = BrowserEngineFactory.get_engine("stub")
        b = BrowserEngineFactory.get_engine("stub")
        assert a is not b

    def test_engines_dict_starts_empty(self):
        """The registry is cleared by the autouse fixture, so it should be empty."""
        assert BrowserEngineFactory._engines == {}

    def test_error_message_lists_available(self):
        BrowserEngineFactory.register("stub", _StubEngine)
        with pytest.raises(KeyError, match="stub"):
            BrowserEngineFactory.get_engine("nonexistent")


# ---------------------------------------------------------------------------
# Import / subpackage smoke test
# ---------------------------------------------------------------------------


class TestEnginesSubpackage:
    def test_engines_init_importable(self):
        from shannon_core.services.engines import BrowserEngineFactory as Factory

        assert Factory is BrowserEngineFactory


# ---------------------------------------------------------------------------
# Registered engine tests (after importing engines subpackage)
# ---------------------------------------------------------------------------


class TestRegisteredEngines:
    """Tests that the engines subpackage registers PlaywrightEngine and AgentBrowserEngine."""

    @pytest.fixture(autouse=True)
    def _register_engines(self):
        """Register the real engines after the autouse _clear_registry has emptied the registry."""
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine
        BrowserEngineFactory.register("playwright", PlaywrightEngine)
        BrowserEngineFactory.register("agent-browser", AgentBrowserEngine)

    def test_factory_returns_playwright_engine(self):
        engine = BrowserEngineFactory.get_engine("playwright")
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        assert isinstance(engine, PlaywrightEngine)
        assert engine.name == "playwright"

    def test_factory_returns_agent_browser_engine(self):
        engine = BrowserEngineFactory.get_engine("agent-browser")
        from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine
        assert isinstance(engine, AgentBrowserEngine)
        assert engine.name == "agent-browser"

    def test_factory_raises_keyerror_for_unknown(self):
        with pytest.raises(KeyError, match="No browser engine"):
            BrowserEngineFactory.get_engine("nonexistent-engine")

    def test_playwright_satisfies_protocol(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        assert isinstance(engine, BrowserEngine)

    def test_agent_browser_satisfies_protocol(self):
        from shannon_core.services.engines.agent_browser_engine import AgentBrowserEngine
        engine = AgentBrowserEngine()
        assert isinstance(engine, BrowserEngine)
