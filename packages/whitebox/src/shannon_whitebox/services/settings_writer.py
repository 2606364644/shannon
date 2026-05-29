"""Write ~/.claude/settings.json with permissions.deny rules from code_path avoid patterns.

Direct port of shannon/apps/worker/src/ai/settings-writer.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

from shannon_core.models.config import Rule

_FILE_TOOLS = ("Read", "Edit")


def _settings_path() -> Path:
    """Compute the settings path at call time so monkeypatched Path.home works in tests."""
    return Path.home() / ".claude" / "settings.json"


def _strip_leading_dotslash(pattern: str) -> str:
    """Remove a leading './' prefix from the pattern, preserving dots that are part of the name (e.g. '.env')."""
    if pattern.startswith("./"):
        return pattern[2:]
    return pattern


def _deny_entries_for(pattern: str) -> list[str]:
    arg = f"./{_strip_leading_dotslash(pattern)}"
    return [f"{tool}({arg})" for tool in _FILE_TOOLS]


def sync_code_path_deny_rules(avoid_rules: list[Rule]) -> None:
    """Write deny rules for all code_path avoid patterns; remove file when none."""
    code_path_patterns = [r.value for r in avoid_rules if r.type == "code_path"]

    settings_path = _settings_path()

    if not code_path_patterns:
        if settings_path.exists():
            settings_path.unlink()
        return

    settings = {
        "permissions": {
            "deny": [
                entry
                for pattern in code_path_patterns
                for entry in _deny_entries_for(pattern)
            ],
        },
    }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2))


def cleanup_settings() -> None:
    """Remove the settings file created by sync_code_path_deny_rules."""
    settings_path = _settings_path()
    if settings_path.exists():
        settings_path.unlink()
