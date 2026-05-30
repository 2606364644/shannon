import json
from pathlib import Path

import pytest

from shannon_core.models.config import Rule, Rules
from shannon_core.services.settings_writer import (
    sync_code_path_deny_rules,
    cleanup_settings,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.claude to a temp directory."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return claude_dir


class TestSyncCodePathDenyRules:
    def test_writes_deny_rules(self, fake_home):
        rules = Rules(
            avoid=[
                Rule(description="secrets", type="code_path", value="secrets/**"),
                Rule(description="env files", type="code_path", value=".env*"),
                Rule(description="skip this URL", type="url_path", value="/admin"),
            ],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        settings_path = fake_home / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "permissions" in data
        deny_list = data["permissions"]["deny"]
        # 2 code_path rules × 2 tools (Read, Edit) = 4 entries
        assert len(deny_list) == 4
        assert "Read(./secrets/**)" in deny_list
        assert "Edit(./secrets/**)" in deny_list
        assert "Read(./.env*)" in deny_list
        assert "Edit(./.env*)" in deny_list

    def test_removes_settings_when_no_code_path_rules(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text('{"permissions": {"deny": []}}')

        rules = Rules(
            avoid=[Rule(description="url", type="url_path", value="/admin")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        assert not settings_path.exists()

    def test_strips_leading_dots_slashes(self, fake_home):
        rules = Rules(
            avoid=[Rule(description="test", type="code_path", value="./secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        settings_path = fake_home / "settings.json"
        data = json.loads(settings_path.read_text())
        deny_list = data["permissions"]["deny"]
        assert "Read(./secrets/**)" in deny_list

    def test_empty_pattern_produces_no_deny_entries(self, fake_home):
        rules = Rules(
            avoid=[
                Rule(description="empty", type="code_path", value=""),
                Rule(description="whitespace", type="code_path", value="   "),
                Rule(description="valid", type="code_path", value="secrets/**"),
            ],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)
        settings_path = fake_home / "settings.json"
        data = json.loads(settings_path.read_text())
        deny_list = data["permissions"]["deny"]
        # Only the valid pattern should produce entries (2 tools)
        assert len(deny_list) == 2
        assert "Read(./secrets/**)" in deny_list
        assert "Edit(./secrets/**)" in deny_list

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        """Verify ~/.claude/ is created when it doesn't exist yet."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert not (tmp_path / ".claude").exists()

        rules = Rules(
            avoid=[Rule(description="secrets", type="code_path", value="secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "Read(./secrets/**)" in data["permissions"]["deny"]

    def test_merges_into_existing_settings(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text(json.dumps({
            "someOtherKey": "preserved",
            "permissions": {"allow": ["Bash(git log)"]},
        }))

        rules = Rules(
            avoid=[Rule(description="secrets", type="code_path", value="secrets/**")],
            focus=[],
        )
        sync_code_path_deny_rules(rules.avoid)

        data = json.loads(settings_path.read_text())
        assert data["someOtherKey"] == "preserved"
        assert data["permissions"]["allow"] == ["Bash(git log)"]
        assert "Read(./secrets/**)" in data["permissions"]["deny"]


class TestCleanupSettings:
    def test_removes_settings_file(self, fake_home):
        settings_path = fake_home / "settings.json"
        settings_path.write_text('{"permissions": {"deny": ["Read(./x)"]}}')
        cleanup_settings()
        assert not settings_path.exists()

    def test_noop_when_no_file(self, fake_home):
        cleanup_settings()  # Should not raise
