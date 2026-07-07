"""TDD tests for ``scripts/bootstrap.sh`` — ``install_gitnexus`` install path.

Drives the bash script via ``subprocess``: ``source`` it (preflight runs with
the real npm), then inject shell-function mocks for ``has`` / ``confirm`` /
``npm`` / ``node`` and invoke ``install_gitnexus``. Mock calls are appended to
a log file; ``install_gitnexus``' own output goes to stdout. Each test asserts
on one of those two channels.

Why pytest-driven-bash (not bats): the project has no bats / shellcheck and
all bash-script tests so far are zero (``test_prerequisites.py`` only mocks
``subprocess.run`` and never executes ``bootstrap.sh``). Reusing pytest keeps
the dependency surface at zero and matches the existing ``testpaths``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]  # packages/core/tests/ -> repo root
BOOTSTRAP = REPO / "scripts" / "bootstrap.sh"

# Bash driver: source bootstrap.sh (preflight uses real npm), then override
# the four touch-points with mocks and call install_gitnexus. ``NPM_FAIL``
# flips npm to fail (for the failure-hint test); ``NPM_GLOBAL_ROOT`` is the
# fake global node_modules root (where we plant a fake install.js).
_DRIVER = r'''
set +u
MOCK_LOG="${MOCK_LOG:?}"
NPM_GLOBAL_ROOT="${NPM_GLOBAL_ROOT:?}"
BOOTSTRAP="${BOOTSTRAP:?}"
_GITNEXUS_INSTALLED=0

# Source the script. Preflight (`has npm` + `npm --version`) runs against the
# real npm here — our mocks are installed *after*, so they only affect
# install_gitnexus' internals. Suppress preflight chatter.
source "$BOOTSTRAP" >/dev/null 2>&1 || true

has() {
    if [[ "$1" == "gitnexus" ]]; then
        if (( _GITNEXUS_INSTALLED == 1 )); then return 0; fi
        return 1
    fi
    command -v "$1" &>/dev/null
}
confirm() { return 0; }                       # --yes
npm() {
    echo "NPM_CALL: $*" >> "$MOCK_LOG"
    if [[ "$1 $2" == "root -g" ]]; then
        echo "$NPM_GLOBAL_ROOT"
        return 0
    fi
    if [[ "${NPM_FAIL:-0}" == "1" ]]; then return 1; fi
    _GITNEXUS_INSTALLED=1
    return 0
}
node() {
    echo "NODE_CALL: $*" >> "$MOCK_LOG"
    return 0
}

echo "MARKER_INSTALL_START"
rc=0
install_gitnexus || rc=$?
echo "MARKER_INSTALL_END rc=$rc"
'''


def _run_install(tmp_path: Path, *, npm_fail: bool = False) -> tuple[str, str]:
    """Run install_gitnexus under mock; return (stdout, mock-log contents)."""
    mock_log = tmp_path / "mock.log"
    # Plant a fake @ladybugdb/core/install.js under the fake global root so
    # the `-f "$install_js"` check passes and `node install.js` is invoked.
    fake_root = tmp_path / "npm-global"
    install_js = fake_root / "gitnexus" / "node_modules" / "@ladybugdb" / "core" / "install.js"
    install_js.parent.mkdir(parents=True, exist_ok=True)
    install_js.write_text("// fake")

    env = {
        **os.environ,
        "MOCK_LOG": str(mock_log),
        "NPM_GLOBAL_ROOT": str(fake_root),
        "BOOTSTRAP": str(BOOTSTRAP),
        "NPM_FAIL": "1" if npm_fail else "0",
    }
    proc = subprocess.run(
        ["bash", "-c", _DRIVER],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    log = mock_log.read_text() if mock_log.exists() else ""
    return proc.stdout, log


def _install_section(stdout: str) -> str:
    """Slice the stdout lines between the two markers (install_gitnexus only)."""
    lines = stdout.splitlines()
    start = end = -1
    for i, ln in enumerate(lines):
        if "MARKER_INSTALL_START" in ln:
            start = i
        elif "MARKER_INSTALL_END" in ln:
            end = i
    if start == -1 or end == -1:
        return stdout  # fall back to whole output
    return "\n".join(lines[start + 1 : end])


class TestInstallGitnexus:
    """Three red tests pinning the install behavior we want."""

    def test_install_uses_ignore_scripts(self, tmp_path):
        """npm install must pass --ignore-scripts (onnxruntime-node 302 fix)."""
        _, log = _run_install(tmp_path)
        npm_calls = [l for l in log.splitlines() if l.startswith("NPM_CALL:")]
        assert npm_calls, "npm was never invoked"
        # The `npm install -g ...` call must carry --ignore-scripts.
        install_calls = [c for c in npm_calls if "install" in c and "-g" in c]
        assert install_calls, f"no `npm install -g` call recorded: {npm_calls}"
        assert any("--ignore-scripts" in c for c in install_calls), (
            f"--ignore-scripts missing from npm install: {install_calls}"
        )

    def test_install_runs_ladybugdb_install_js(self, tmp_path):
        """After install, run node @ladybugdb/core/install.js (restore native binary)."""
        _, log = _run_install(tmp_path)
        node_calls = [l for l in log.splitlines() if l.startswith("NODE_CALL:")]
        assert node_calls, "node was never invoked (install.js not run)"
        assert any("@ladybugdb/core/install.js" in c for c in node_calls), (
            f"install.js not invoked: {node_calls}"
        )

    def test_failure_hint_mentions_network_not_cpp(self, tmp_path, capsys):
        """On npm failure, hint about --ignore-scripts/network (302), not C++ toolchain."""
        stdout, _ = _run_install(tmp_path, npm_fail=True)
        section = _install_section(stdout)
        assert "--ignore-scripts" in section or "302" in section.lower() or "network" in section.lower(), (
            f"failure hint does not point at --ignore-scripts/network: {section!r}"
        )
        assert "C++" not in section, (
            f"failure hint wrongly suggests a C++ toolchain error: {section!r}"
        )
