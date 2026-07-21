"""Tests for supernova_core.runtime.prerequisites.ensure_prerequisite."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestEnsurePrerequisite:
    """Tests for the ensure_prerequisite function."""

    def test_already_installed(self):
        """If binary is on PATH, return immediately without prompting."""
        from supernova_core.runtime.prerequisites import ensure_prerequisite

        with patch(
            "supernova_core.runtime.prerequisites.shutil.which",
            return_value="/usr/bin/gitnexus",
        ):
            ensure_prerequisite("gitnexus", profile="whitebox")

    def test_skip_prerequisites_env(self):
        """SUPERNOVA_SKIP_PREREQUISITES=1 skips all checks."""
        from supernova_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch.dict("os.environ", {"SUPERNOVA_SKIP_PREREQUISITES": "1"}),
            patch("supernova_core.runtime.prerequisites.shutil.which") as mock_which,
        ):
            ensure_prerequisite("gitnexus", profile="whitebox")
            mock_which.assert_not_called()

    def test_user_confirms_install_success(self):
        """User confirms install, bootstrap succeeds, binary appears on PATH."""
        from supernova_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "supernova_core.runtime.prerequisites.shutil.which",
                side_effect=[None, "/usr/local/bin/gitnexus"],
            ),
            patch(
                "supernova_core.runtime.prerequisites.click.confirm",
                return_value=True,
            ),
            patch(
                "supernova_core.runtime.prerequisites._find_bootstrap_script",
                return_value=Path("/fake/scripts/bootstrap.sh"),
            ),
            patch(
                "supernova_core.runtime.prerequisites.subprocess.run"
            ) as mock_run,
            patch("supernova_core.runtime.prerequisites.click.echo"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            ensure_prerequisite("gitnexus", profile="whitebox")
            mock_run.assert_called_once_with(
                ["bash", "/fake/scripts/bootstrap.sh", "whitebox", "--yes"],
                check=False,
            )

    def test_user_declines_install_exit(self):
        """User declines install, then declines degraded mode → SystemExit(1)."""
        from supernova_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "supernova_core.runtime.prerequisites.shutil.which",
                return_value=None,
            ),
            patch(
                "supernova_core.runtime.prerequisites.click.confirm",
                side_effect=[False, False],
            ),
            patch("supernova_core.runtime.prerequisites.click.secho"),
        ):
            with pytest.raises(SystemExit, match="1"):
                ensure_prerequisite("gitnexus", profile="whitebox")

    def test_user_declines_accepts_degraded(self):
        """User declines install, accepts degraded mode → returns normally."""
        from supernova_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "supernova_core.runtime.prerequisites.shutil.which",
                return_value=None,
            ),
            patch(
                "supernova_core.runtime.prerequisites.click.confirm",
                side_effect=[False, True],
            ),
            patch("supernova_core.runtime.prerequisites.click.secho"),
        ):
            ensure_prerequisite("gitnexus", profile="whitebox")

    def test_install_fails_then_degraded(self):
        """Install script fails, re-check fails, user accepts degraded."""
        from supernova_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "supernova_core.runtime.prerequisites.shutil.which",
                return_value=None,
            ),
            patch(
                "supernova_core.runtime.prerequisites.click.confirm",
                side_effect=[True, True],
            ),
            patch(
                "supernova_core.runtime.prerequisites._find_bootstrap_script",
                return_value=Path("/fake/scripts/bootstrap.sh"),
            ),
            patch(
                "supernova_core.runtime.prerequisites.subprocess.run"
            ) as mock_run,
            patch("supernova_core.runtime.prerequisites.click.echo"),
            patch("supernova_core.runtime.prerequisites.click.secho"),
        ):
            mock_run.return_value = MagicMock(returncode=1)
            ensure_prerequisite("gitnexus", profile="whitebox")


class TestEnsureBrowserEngine:
    """Tests for the engine-aware ensure_browser_engine helper."""

    def test_checks_agent_browser_by_default(self, monkeypatch):
        """No config, no env → default agent-browser → check 'agent-browser' binary."""
        import supernova_core.services.engines  # noqa: F401  (register engines)
        monkeypatch.delenv("SUPERNOVA_BROWSER_ENGINE", raising=False)

        captured = {}
        monkeypatch.setattr(
            "supernova_core.runtime.prerequisites.ensure_prerequisite",
            lambda name, *, profile: captured.update(name=name, profile=profile),
        )

        from supernova_core.runtime.prerequisites import ensure_browser_engine
        ensure_browser_engine(None)

        assert captured["name"] == "agent-browser"
        assert captured["profile"] == "blackbox"

    def test_env_selects_playwright(self, monkeypatch):
        """SUPERNOVA_BROWSER_ENGINE=playwright → check 'playwright-cli' binary."""
        import supernova_core.services.engines  # noqa: F401
        monkeypatch.setenv("SUPERNOVA_BROWSER_ENGINE", "playwright")

        captured = {}
        monkeypatch.setattr(
            "supernova_core.runtime.prerequisites.ensure_prerequisite",
            lambda name, *, profile: captured.update(name=name, profile=profile),
        )

        from supernova_core.runtime.prerequisites import ensure_browser_engine
        ensure_browser_engine(None)

        assert captured["name"] == "playwright-cli"
