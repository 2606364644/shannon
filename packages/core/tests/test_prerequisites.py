"""Tests for shannon_core.runtime.prerequisites.ensure_prerequisite."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestEnsurePrerequisite:
    """Tests for the ensure_prerequisite function."""

    def test_already_installed(self):
        """If binary is on PATH, return immediately without prompting."""
        from shannon_core.runtime.prerequisites import ensure_prerequisite

        with patch(
            "shannon_core.runtime.prerequisites.shutil.which",
            return_value="/usr/bin/gitnexus",
        ):
            ensure_prerequisite("gitnexus", profile="whitebox")

    def test_skip_prerequisites_env(self):
        """SHANNON_SKIP_PREREQUISITES=1 skips all checks."""
        from shannon_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch.dict("os.environ", {"SHANNON_SKIP_PREREQUISITES": "1"}),
            patch("shannon_core.runtime.prerequisites.shutil.which") as mock_which,
        ):
            ensure_prerequisite("gitnexus", profile="whitebox")
            mock_which.assert_not_called()

    def test_user_confirms_install_success(self):
        """User confirms install, bootstrap succeeds, binary appears on PATH."""
        from shannon_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "shannon_core.runtime.prerequisites.shutil.which",
                side_effect=[None, "/usr/local/bin/gitnexus"],
            ),
            patch(
                "shannon_core.runtime.prerequisites.click.confirm",
                return_value=True,
            ),
            patch(
                "shannon_core.runtime.prerequisites._find_bootstrap_script",
                return_value=Path("/fake/scripts/bootstrap.sh"),
            ),
            patch(
                "shannon_core.runtime.prerequisites.subprocess.run"
            ) as mock_run,
            patch("shannon_core.runtime.prerequisites.click.echo"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            ensure_prerequisite("gitnexus", profile="whitebox")
            mock_run.assert_called_once_with(
                ["bash", "/fake/scripts/bootstrap.sh", "whitebox", "--yes"],
                check=False,
            )

    def test_user_declines_install_exit(self):
        """User declines install, then declines degraded mode → SystemExit(1)."""
        from shannon_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "shannon_core.runtime.prerequisites.shutil.which",
                return_value=None,
            ),
            patch(
                "shannon_core.runtime.prerequisites.click.confirm",
                side_effect=[False, False],
            ),
            patch("shannon_core.runtime.prerequisites.click.secho"),
        ):
            with pytest.raises(SystemExit, match="1"):
                ensure_prerequisite("gitnexus", profile="whitebox")

    def test_user_declines_accepts_degraded(self):
        """User declines install, accepts degraded mode → returns normally."""
        from shannon_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "shannon_core.runtime.prerequisites.shutil.which",
                return_value=None,
            ),
            patch(
                "shannon_core.runtime.prerequisites.click.confirm",
                side_effect=[False, True],
            ),
            patch("shannon_core.runtime.prerequisites.click.secho"),
        ):
            ensure_prerequisite("gitnexus", profile="whitebox")

    def test_install_fails_then_degraded(self):
        """Install script fails, re-check fails, user accepts degraded."""
        from shannon_core.runtime.prerequisites import ensure_prerequisite

        with (
            patch(
                "shannon_core.runtime.prerequisites.shutil.which",
                return_value=None,
            ),
            patch(
                "shannon_core.runtime.prerequisites.click.confirm",
                side_effect=[True, True],
            ),
            patch(
                "shannon_core.runtime.prerequisites._find_bootstrap_script",
                return_value=Path("/fake/scripts/bootstrap.sh"),
            ),
            patch(
                "shannon_core.runtime.prerequisites.subprocess.run"
            ) as mock_run,
            patch("shannon_core.runtime.prerequisites.click.echo"),
            patch("shannon_core.runtime.prerequisites.click.secho"),
        ):
            mock_run.return_value = MagicMock(returncode=1)
            ensure_prerequisite("gitnexus", profile="whitebox")
