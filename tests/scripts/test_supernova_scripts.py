"""Regression tests for the repository's Docker lifecycle scripts."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLEANUP = ROOT / "scripts" / "cleanup-supernova.sh"
UP = ROOT / "scripts" / "up.sh"


def test_cleanup_resolves_the_actual_repository_from_script_location() -> None:
    """The checkout is /root/shannon-py; cleanup must not depend on /root/supernova."""
    source = CLEANUP.read_text()

    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in source
    assert 'REPO="$(cd "$SCRIPT_DIR/.." && pwd)"' in source
    assert "REPO=/root/supernova" not in source
    assert '"$REPO/packages/web"' in source


def test_up_waits_for_an_in_progress_container_removal(tmp_path: Path) -> None:
    """A transient Docker removal race must not prevent the compose up command."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "state"
    up_marker = tmp_path / "up-called"
    docker = fake_bin / "docker"
    docker.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -u
            state={state!s}
            up_marker={up_marker!s}
            mkdir -p "$state"
            if [[ "$1" == "buildx" && "$2" == "version" ]]; then
              echo 'github.com/docker/buildx v0.0.0 test'
              exit 0
            fi
            if [[ "$1" == "context" && "$2" == "ls" ]]; then
              exit 0
            fi
            if [[ "$1" == "compose" && "$2" == "version" ]]; then
              echo 'Docker Compose version v0.0.0-test'
              exit 0
            fi
            if [[ "$1" == "compose" ]]; then
              if [[ " $* " == *" ps -q temporal "* ]]; then
                exit 0
              fi
              if [[ " $* " == *" ps -a "* ]]; then
                printf 'supernova-web\\texited\\n'
                printf 'supernova-worker\\texited\\n'
                exit 0
              fi
              if [[ " $* " == *" up -d "* ]]; then
                : > "$up_marker"
                exit 0
              fi
              exit 0
            fi
            if [[ "$1" == "inspect" ]]; then
              [[ -e "$state/$2" ]]
              exit $?
            fi
            if [[ "$1" == "rm" && "$2" == "-f" ]]; then
              container="$3"
              attempts_file="$state/$container.attempts"
              attempts=0
              [[ -f "$attempts_file" ]] && attempts=$(cat "$attempts_file")
              attempts=$((attempts + 1))
              printf '%s' "$attempts" > "$attempts_file"
              if (( attempts < 3 )); then
                echo "Error response from daemon: removal of container $container is already in progress" >&2
                exit 1
              fi
              rm -f "$state/$container"
              exit 0
            fi
            echo "unexpected fake docker invocation: $*" >&2
            exit 99
            """
        )
    )
    docker.chmod(0o755)
    state.mkdir()
    (state / "supernova-web").touch()
    (state / "supernova-worker").touch()

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SUPERNOVA_CONTAINER_REMOVE_RETRIES"] = "5"
    env["SUPERNOVA_CONTAINER_REMOVE_INTERVAL"] = "0.01"

    result = subprocess.run(
        ["bash", str(UP)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert up_marker.exists(), result.stdout + result.stderr
