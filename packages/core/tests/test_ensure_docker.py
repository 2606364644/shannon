"""TDD for scripts/ensure-docker.sh pure-logic functions (sourced via bash).

ensure-docker.sh ensures the `buildx` plugin is present at ~/.docker/cli-plugins/
so `docker compose` drives BuildKit instead of the legacy builder. Root cause
(proven by a controlled experiment on this WSL2 host): docker CLI finding no
buildx plugin -> compose falls back to legacy builder -> legacy rejects
`RUN --mount=type=cache` with "the --mount option requires BuildKit". Compose
itself is official and fine (v5.x is the 2025+ mainline, functionally identical
to v2); it is NOT swapped. System-mutating steps (download/overwrite cli-plugins,
runtime detect, rm dangling builder) are gated by the opt-in integration test.
These unit tests cover the pure, side-effect-free platform/arch/URL-mapping
logic exercised by sourcing the script's functions.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # packages/core/tests/ -> repo root
ENSURE_DOCKER = REPO / "scripts" / "ensure-docker.sh"


def _run_func(func: str, *args: str) -> str:
    """Source ensure-docker.sh (functions only; main guarded) and call a pure function.

    Returns trimmed stdout. Asserts the shell call succeeded so a missing
    script/function surfaces as the test's RED signal, not silent empty output.
    """
    argstr = " ".join(shlex.quote(a) for a in args)
    script = f'set -e; source "{ENSURE_DOCKER}"; {func} {argstr}'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, f"shell call failed (rc={r.returncode}):\n{r.stderr}"
    return r.stdout.strip()


# ── _plat_for_uname_s: uname -s → release platform slug ───────────────

def test_plat_linux_maps_to_linux():
    assert _run_func("_plat_for_uname_s", "Linux") == "linux"


def test_plat_darwin_maps_to_darwin():
    assert _run_func("_plat_for_uname_s", "Darwin") == "darwin"


def test_plat_unsupported_maps_to_empty():
    assert _run_func("_plat_for_uname_s", "FreeBSD") == ""


# ── _arch_for_uname_m: uname -m → release arch slug ───────────────────

def test_arch_x86_64_maps_to_amd64():
    assert _run_func("_arch_for_uname_m", "x86_64") == "amd64"


def test_arch_aarch64_maps_to_arm64():
    assert _run_func("_arch_for_uname_m", "aarch64") == "arm64"


def test_arch_arm64_maps_to_arm64():
    assert _run_func("_arch_for_uname_m", "arm64") == "arm64"


def test_arch_unsupported_maps_to_empty():
    assert _run_func("_arch_for_uname_m", "riscv64") == ""


# ── _buildx_download_url: base+plat+arch+ver → URL ────────────────────
# asset 命名 buildx-v<ver>.<plat>-<arch>（点分隔 + 重复版本号）。base 参数化便于镜像前缀。
def test_buildx_download_url_linux_amd64():
    url = _run_func("_buildx_download_url", "https://github.com", "linux", "amd64", "0.35.0")
    assert url == "https://github.com/docker/buildx/releases/download/v0.35.0/buildx-v0.35.0.linux-amd64"


def test_buildx_download_url_darwin_arm64():
    url = _run_func("_buildx_download_url", "https://github.com", "darwin", "arm64", "0.17.1")
    assert url == "https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.darwin-arm64"


# ── Live host gate: ensure-docker.sh keeps buildx present + compose on BuildKit ──
# Opt-in (may download buildx into ~/.docker/cli-plugins on a host missing it).
# Asserts the root-cause fix end-to-end: buildx usable + compose no longer falls
# back to the legacy builder (a minimal RUN --mount build must NOT report
# "requires buildx plugin" / "requires BuildKit"). Skipped unless
# SUPERNOVA_RUN_ENSURE_DOCKER_INTEGRATION=1.
def test_ensure_docker_idempotent_buildx_present():
    if __import__("os").environ.get("SUPERNOVA_RUN_ENSURE_DOCKER_INTEGRATION") != "1":
        __import__("pytest").skip("set SUPERNOVA_RUN_ENSURE_DOCKER_INTEGRATION=1 to run live host gate")
    r = subprocess.run(["bash", str(ENSURE_DOCKER)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"ensure-docker exited {r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    # buildx plugin usable
    bx = subprocess.run(["docker", "buildx", "version"], capture_output=True, text=True)
    assert bx.returncode == 0, f"docker buildx version failed:\n{bx.stderr}"
    # compose drives BuildKit: minimal RUN --mount build must not fall back to legacy
    tmp = __import__("tempfile")
    with tmp.TemporaryDirectory() as d:
        (Path(d) / "Dockerfile").write_text(
            'FROM busybox:latest\nRUN --mount=type=cache,target=/c echo OK\n'
        )
        (Path(d) / "compose.yml").write_text('services:\n  t:\n    build: .\n')
        b = subprocess.run(
            ["docker", "compose", "-f", str(Path(d) / "compose.yml"),
             "--project-directory", d, "build"],
            capture_output=True, text=True, timeout=180,
        )
        combined = b.stdout + b.stderr
        assert "requires buildx plugin" not in combined, f"compose fell back to legacy:\n{combined}"
        assert "requires BuildKit" not in combined, f"legacy builder rejected --mount:\n{combined}"
