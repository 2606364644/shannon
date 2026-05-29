# shannon-py/packages/whitebox/tests/test_playwright_config_writer.py
import json
from pathlib import Path

import pytest

from shannon_whitebox.services.playwright_config_writer import (
    write_stealth_config,
    cleanup_stealth_config,
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
