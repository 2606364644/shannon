"""Prerequisite binary checker with interactive install prompt."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import click

logger = logging.getLogger(__name__)


def _find_bootstrap_script() -> Path | None:
    """Locate scripts/bootstrap.sh relative to this package.

    Resolution order:
      1. ``SHANNON_BOOTSTRAP_SCRIPT`` env var (absolute path).
      2. Walk up from this file:
         runtime/ → shannon_core/ → src/ → core/ → packages/ → repo-root.
    """
    override = os.environ.get("SHANNON_BOOTSTRAP_SCRIPT")
    if override:
        p = Path(override)
        return p if p.exists() else None

    # prerequisites.py lives at:
    #   packages/core/src/shannon_core/runtime/prerequisites.py
    # parents[5] = repo root (shannon-py/)
    repo_root = Path(__file__).resolve().parents[5]
    script = repo_root / "scripts" / "bootstrap.sh"
    return script if script.exists() else None


def _confirm_degraded(name: str) -> None:
    """Ask user to confirm running in degraded mode.  Exits if declined."""
    click.secho(
        f"⚠️  {name} 未安装。扫描将以降级模式运行，结果质量会显著下降。",
        fg="yellow",
        bold=True,
    )
    if not click.confirm("仍要继续运行（降级模式）？", default=False):
        raise SystemExit(1)


def ensure_prerequisite(name: str, *, profile: str) -> None:
    """Check a prerequisite binary; prompt to install via bootstrap.sh if missing.

    If the binary is missing and the user declines installation (or installation
    fails), a degraded-mode confirmation is shown.  Raises ``SystemExit(1)`` if
    the user does not accept degraded mode.

    Environment variables:
        SHANNON_SKIP_PREREQUISITES: Set to ``1`` to skip all checks (CI).
        SHANNON_BOOTSTRAP_SCRIPT:  Override path to bootstrap.sh.
    """
    if os.environ.get("SHANNON_SKIP_PREREQUISITES") == "1":
        logger.debug(
            "Skipping prerequisite check for %s (SHANNON_SKIP_PREREQUISITES=1)",
            name,
        )
        return

    if shutil.which(name):
        return

    # Binary missing — prompt to install
    if click.confirm(
        f"检测到 {name} 未安装。现在自动安装？",
        default=True,
    ):
        script = _find_bootstrap_script()
        if script is None:
            logger.warning(
                "bootstrap.sh not found; skipping install for %s", name,
            )
            _confirm_degraded(name)
            return

        result = subprocess.run(
            ["bash", str(script), profile, "--yes"],
            check=False,
        )
        if result.returncode != 0:
            click.echo(
                f"安装失败（退出码 {result.returncode}）。"
                f"手动安装: bash {script} {profile}"
            )

        # Re-check after install
        if shutil.which(name):
            click.echo(f"✅ {name} 已安装。")
            return

        click.echo(
            f"安装后仍检测不到 {name}。"
            f"手动命令: bash {script} {profile}"
        )

    # Declined install or install failed → degraded confirmation
    _confirm_degraded(name)
