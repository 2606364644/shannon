# shannon-py/packages/core/tests/test_playwright_config_writer.py
import json
from pathlib import Path

import pytest

from shannon_core.services.playwright_config_writer import (
    write_stealth_config,
    cleanup_stealth_config,
    get_session_id,
    cleanup_session_config,
    AGENT_SESSION_MAPPING,
)


class TestWriteStealthConfig:
    def test_creates_config_and_script(self, tmp_path):
        result = write_stealth_config(str(tmp_path))
        assert result["result"] == "wrote"

        config_path = Path(result["configPath"])
        assert config_path.exists()

        # Config references init script by absolute path
        config = json.loads(config_path.read_text())
        assert config["browser"]["browserName"] == "chromium"
        assert config["browser"]["launchOptions"]["headless"] is True
        init_scripts = config["browser"]["initScript"]
        assert len(init_scripts) == 1
        assert Path(init_scripts[0]).exists()

    def test_stealth_script_content(self, tmp_path):
        write_stealth_config(str(tmp_path))
        script = tmp_path / ".playwright" / "scripts" / "stealth.js"
        content = script.read_text()
        assert "navigator.webdriver" in content
        assert "chrome.runtime" in content
        assert "navigator.plugins" in content

    def test_skips_existing_config(self, tmp_path):
        playwright_dir = tmp_path / ".playwright"
        playwright_dir.mkdir()
        (playwright_dir / "cli.config.json").write_text('{"existing": true}')

        result = write_stealth_config(str(tmp_path))
        assert result["result"] == "skipped-existing"
        # Verify it didn't overwrite
        config = json.loads((playwright_dir / "cli.config.json").read_text())
        assert config == {"existing": True}


class TestCleanupStealthConfig:
    def test_removes_playwright_dir(self, tmp_path):
        write_stealth_config(str(tmp_path))
        assert (tmp_path / ".playwright").exists()

        cleanup_stealth_config(str(tmp_path))
        assert not (tmp_path / ".playwright").exists()

    def test_noop_when_no_dir(self, tmp_path):
        cleanup_stealth_config(str(tmp_path))  # Should not raise


class TestGetSessionId:
    def test_known_agent(self):
        assert get_session_id("injection-exploit") == "agent-injection"

    def test_known_agent_xss(self):
        assert get_session_id("xss-exploit") == "agent-xss"

    def test_unknown_agent_returns_default(self):
        assert get_session_id("unknown-agent") == "default"


class TestWriteSessionConfig:
    def test_creates_session_specific_config(self, tmp_path):
        result = write_stealth_config(str(tmp_path), session_id="agent-injection")
        assert result["result"] == "wrote"
        config_path = Path(result["configPath"])
        assert "agent-injection" in str(config_path)
        assert config_path.exists()

    def test_session_config_has_isolated_storage(self, tmp_path):
        result = write_stealth_config(str(tmp_path), session_id="agent-xss")
        config_path = Path(result["configPath"])
        config = json.loads(config_path.read_text())
        # Verify storageState path is session-specific
        storage = config["browser"].get("contextOptions", {}).get("storageState", "")
        assert "agent-xss" in storage

    def test_no_session_creates_default_config(self, tmp_path):
        result = write_stealth_config(str(tmp_path))
        config_path = Path(result["configPath"])
        assert "agent-" not in str(config_path)


class TestCleanupSessionConfig:
    def test_cleanup_session_config(self, tmp_path):
        write_stealth_config(str(tmp_path), session_id="agent-ssrf")
        config_path = tmp_path / ".playwright" / "cli.config.agent-ssrf.json"
        assert config_path.exists()
        cleanup_session_config(str(tmp_path), "agent-ssrf")
        assert not config_path.exists()

    def test_cleanup_noop_when_no_config(self, tmp_path):
        cleanup_session_config(str(tmp_path), "agent-auth")  # Should not raise


# ---------------------------------------------------------------------------
# Facade delegation and engine property tests
# ---------------------------------------------------------------------------


class TestFacadeDelegation:
    """Verify the playwright_config_writer facade delegates to PlaywrightEngine."""

    def test_facade_delegates_to_playwright_engine(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        from shannon_core.services.playwright_config_writer import _engine
        assert isinstance(_engine, PlaywrightEngine)

    def test_playwright_engine_name(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        assert engine.name == "playwright"

    def test_playwright_engine_session_flag(self):
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        flag = engine.session_flag("sess-42")
        assert flag == "-s=sess-42"

    def test_playwright_engine_session_flag_format(self):
        """Session flag should use -s={sid} format."""
        from shannon_core.services.engines.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        flag = engine.session_flag("my-session")
        assert flag.startswith("-s=")
        assert "my-session" in flag
