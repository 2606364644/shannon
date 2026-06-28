# Prerequisite Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a prerequisite detection and installation mechanism that prompts users to install missing external dependencies (gitnexus, playwright-cli) before `shannon-whitebox start` / `shannon-blackbox start` runs, and provides a standalone `scripts/bootstrap.sh` for batch installation.

**Architecture:** A thin Python helper (`ensure_prerequisite`) in `shannon_core.runtime.prerequisites` detects missing binaries via `shutil.which`, prompts the user via `click.confirm`, then delegates to `scripts/bootstrap.sh --yes <profile>` for the actual install. The bash script is the single source of truth for all install commands. Both whitebox and blackbox `start` commands call `ensure_prerequisite` between `ensure_infra` and `run_scan`.

**Tech Stack:** Python 3.12, click (already a dep), subprocess, bash, pnpm/npm.

**Spec:** `docs/superpowers/specs/2026-06-11-prerequisite-bootstrap-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/core/src/shannon_core/runtime/__init__.py` | Create | Package init |
| `packages/core/src/shannon_core/runtime/prerequisites.py` | Create | `ensure_prerequisite()` helper — detect, prompt, call script, degraded fallback |
| `packages/core/tests/test_prerequisites.py` | Create | Unit tests for `ensure_prerequisite` (6 cases) |
| `scripts/bootstrap.sh` | Create | Standalone bash installer — node/pnpm/gitnexus/playwright-cli/chromium |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Modify | Add `ensure_prerequisite("gitnexus", profile="whitebox")` at line 47 |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Modify | Add `ensure_prerequisite("playwright-cli", profile="blackbox")` at line 128 |

---

### Task 1: Create runtime package and write failing tests

**Files:**
- Create: `packages/core/src/shannon_core/runtime/__init__.py`
- Create: `packages/core/tests/test_prerequisites.py`

- [ ] **Step 1: Create the runtime package**

```bash
mkdir -p packages/core/src/shannon_core/runtime
```

Write `packages/core/src/shannon_core/runtime/__init__.py`:

```python
"""Runtime prerequisite detection and installation."""
```

- [ ] **Step 2: Write the test file with all 6 test cases**

Write `packages/core/tests/test_prerequisites.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they all fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prerequisites.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.runtime.prerequisites'`

---

### Task 2: Implement ensure_prerequisite

**Files:**
- Create: `packages/core/src/shannon_core/runtime/prerequisites.py`

- [ ] **Step 1: Write the implementation**

Write `packages/core/src/shannon_core/runtime/prerequisites.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they all pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prerequisites.py -v`

Expected: All 6 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/runtime/__init__.py \
        packages/core/src/shannon_core/runtime/prerequisites.py \
        packages/core/tests/test_prerequisites.py
git commit -m "feat(core): add ensure_prerequisite helper with interactive install prompt"
```

---

### Task 3: Create bootstrap.sh

**Files:**
- Create: `scripts/bootstrap.sh`

- [ ] **Step 1: Write the bootstrap script**

Write `scripts/bootstrap.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# scripts/bootstrap.sh — Install external dependencies for shannon-py.
# Usage: bash scripts/bootstrap.sh [whitebox|blackbox|all] [--yes]

PROFILE="${1:-all}"
AUTO_YES=false
[[ "${2:-}" == "--yes" ]] && AUTO_YES=true

# ── Colors ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✅ $*${NC}"; }
warn() { echo -e "  ${YELLOW}⚠  $*${NC}"; }
fail() { echo -e "  ${RED}❌ $*${NC}"; }

# ── Helpers ─────────────────────────────────────────────────────────
has() { command -v "$1" &>/dev/null; }

confirm() {
    local msg="$1"
    if $AUTO_YES; then return 0; fi
    read -rp "$msg [Y/n] " ans
    [[ "${ans,,}" =~ ^(y|yes|)$ ]]
}

ensure_pnpm_in_path() {
    # pnpm global bin may not be on PATH after first install
    local pnpm_bin
    pnpm_bin="$(pnpm bin -g 2>/dev/null || true)"
    if [[ -n "$pnpm_bin" && ":$PATH:" != *":$pnpm_bin:"* ]]; then
        export PATH="$pnpm_bin:$PATH"
        pnpm setup 2>/dev/null || true
    fi
}

# ── Preflight: node / npm ───────────────────────────────────────────
if ! has npm; then
    fail "Node.js/npm is required but not found."
    echo "  Install from: https://nodejs.org/"
    echo "  On Ubuntu/Debian: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs"
    exit 1
fi
ok "npm $(npm --version)"

# ── Preflight: pnpm ────────────────────────────────────────────────
if ! has pnpm; then
    echo "Installing pnpm..."
    npm install -g pnpm
fi
ok "pnpm $(pnpm --version)"
ensure_pnpm_in_path

# ── Install functions ───────────────────────────────────────────────

install_gitnexus() {
    if has gitnexus; then
        ok "gitnexus (already installed)"
        return 0
    fi
    if ! confirm "Install gitnexus (whitebox call graph engine)?"; then
        warn "gitnexus skipped"
        return 0
    fi
    echo "Installing gitnexus via pnpm..."
    pnpm config set --global onlyBuiltDependencies \
        "@ladybugdb/core" "gitnexus" "tree-sitter" 2>/dev/null || true
    pnpm add -g gitnexus@latest
    ensure_pnpm_in_path
    if has gitnexus; then
        ok "gitnexus installed"
    else
        fail "gitnexus installation failed."
        echo "  Manual: pnpm config set --global onlyBuiltDependencies @ladybugdb/core gitnexus tree-sitter"
        echo "          pnpm add -g gitnexus@latest"
        return 1
    fi
}

install_playwright_cli() {
    if has playwright-cli; then
        ok "playwright-cli (already installed)"
        return 0
    fi
    if ! confirm "Install playwright-cli (blackbox browser automation)?"; then
        warn "playwright-cli skipped"
        return 0
    fi
    echo "Installing playwright-cli..."
    # Try the most likely package first, fallback to bare name.
    # Exact package name to be verified at install time — see spec §实现时需核实项.
    npm install -g @anthropic-ai/playwright-mcp@latest 2>/dev/null \
        || npm install -g playwright-cli@latest 2>/dev/null \
        || {
            fail "playwright-cli installation failed."
            echo "  Manual: npm install -g playwright-cli"
            return 1
        }
    if has playwright-cli; then
        ok "playwright-cli installed"
    else
        fail "playwright-cli not found after install."
        echo "  Manual: npm install -g playwright-cli"
        return 1
    fi
}

install_chromium() {
    if ! confirm "Install Chromium browser for playwright?"; then
        warn "chromium skipped"
        return 0
    fi
    echo "Installing Chromium for playwright..."
    npx playwright install chromium
    ok "chromium installed"
}

check_docker() {
    if has docker; then
        ok "docker"
    else
        warn "docker not found. Start infrastructure with: shannon-whitebox infra up"
    fi
}

# ── Run by profile ──────────────────────────────────────────────────

FAILED=0

echo ""
echo "=== Shannon Prerequisites Bootstrap (profile: $PROFILE) ==="
echo ""

case "$PROFILE" in
    whitebox)
        install_gitnexus || FAILED=1
        ;;
    blackbox)
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        ;;
    all)
        install_gitnexus || FAILED=1
        install_playwright_cli || FAILED=1
        install_chromium || FAILED=1
        check_docker
        ;;
    *)
        fail "Unknown profile: $PROFILE. Use: whitebox, blackbox, or all"
        exit 1
        ;;
esac

echo ""
if [[ $FAILED -eq 1 ]]; then
    fail "Some installations failed. See manual commands above."
    exit 1
else
    ok "All dependencies satisfied."
fi
```

- [ ] **Step 2: Make it executable and verify syntax**

```bash
chmod +x scripts/bootstrap.sh
bash -n scripts/bootstrap.sh
```

Expected: No output (syntax OK).

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat: add scripts/bootstrap.sh for external dependency installation"
```

---

### Task 4: Integrate into whitebox start

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:46-47`

- [ ] **Step 1: Add the ensure_prerequisite call**

In `packages/whitebox/src/shannon_whitebox/cli/main.py`, between line 46 (`asyncio.run(ensure_infra(address=temporal_address))`) and line 47 (`result = asyncio.run(run_scan(input, temporal_address))`), insert:

```python
    from shannon_core.runtime.prerequisites import ensure_prerequisite
    ensure_prerequisite("gitnexus", profile="whitebox")
```

The resulting block (lines 45–50) should read:

```python
    click.echo(f"Starting white-box scan on {repo}")
    asyncio.run(ensure_infra(address=temporal_address))
    from shannon_core.runtime.prerequisites import ensure_prerequisite
    ensure_prerequisite("gitnexus", profile="whitebox")
    result = asyncio.run(run_scan(input, temporal_address))
```

- [ ] **Step 2: Verify the import resolves**

Run: `cd /root/shannon-py && python -c "from shannon_core.runtime.prerequisites import ensure_prerequisite; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py
git commit -m "feat(whitebox): add gitnexus prerequisite check before scan start"
```

---

### Task 5: Integrate into blackbox start

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py:127-128`

- [ ] **Step 1: Add the ensure_prerequisite call**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, between line 127 (`asyncio.run(ensure_infra(address=temporal_address))`) and line 128 (`result = asyncio.run(run_scan(input, temporal_address))`), insert:

```python
    from shannon_core.runtime.prerequisites import ensure_prerequisite
    ensure_prerequisite("playwright-cli", profile="blackbox")
```

The resulting block (lines 126–130) should read:

```python
    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    from shannon_core.runtime.prerequisites import ensure_prerequisite
    ensure_prerequisite("playwright-cli", profile="blackbox")
    result = asyncio.run(run_scan(input, temporal_address))
```

- [ ] **Step 2: Verify the import resolves**

Run: `cd /root/shannon-py && python -c "from shannon_core.runtime.prerequisites import ensure_prerequisite; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py
git commit -m "feat(blackbox): add playwright-cli prerequisite check before scan start"
```

---

### Task 6: End-to-end verification

- [ ] **Step 1: Run all core tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prerequisites.py -v`

Expected: All 6 tests PASS.

- [ ] **Step 2: Run whitebox test suite to verify no regressions**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/ -v --timeout=30 2>&1 | tail -20`

Expected: No new failures.

- [ ] **Step 3: Run blackbox test suite to verify no regressions**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/ -v --timeout=30 2>&1 | tail -20`

Expected: No new failures.

- [ ] **Step 4: Verify bootstrap.sh help output**

Run: `cd /root/shannon-py && bash scripts/bootstrap.sh help 2>&1 || true`

Expected: Prints "Unknown profile: help" message (confirms script runs).

- [ ] **Step 5: Final commit if any remaining changes**

```bash
git add -A
git diff --cached --quiet || git commit -m "chore: prerequisite bootstrap final cleanup"
```
