"""TDD for scripts/provision.sh pure-logic functions (sourced via bash).

provision.sh is a system-level orchestrator. Its system-mutating steps
(docker/gitnexus install, chown, setfacl, uv sync) are gated by the container
integration test (spec §8). These unit tests cover the pure, side-effect-free
logic that can be exercised fast by sourcing the script's functions.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # packages/core/tests/ → repo root
PROVISION = REPO / "scripts" / "provision.sh"


def _run_func(func: str, *args: str) -> str:
    """Source provision.sh (functions only; main guarded) and call a pure function.

    Returns trimmed stdout. Asserts the shell call succeeded so a missing
    script/function surfaces as the test's RED signal, not silent empty output.
    """
    argstr = " ".join(shlex.quote(a) for a in args)
    script = f'set -e; source "{PROVISION}"; {func} {argstr}'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, f"shell call failed (rc={r.returncode}):\n{r.stderr}"
    return r.stdout.strip()


# ── _pkg_mgr_for_id: distro id/id_like → package manager ──────────────

def test_pkg_mgr_debian_ubuntu_maps_to_apt():
    assert _run_func("_pkg_mgr_for_id", "ubuntu", "debian") == "apt"


def test_pkg_mgr_rhel_family_maps_to_dnf():
    assert _run_func("_pkg_mgr_for_id", "rocky", "rhel centos") == "dnf"


def test_pkg_mgr_unsupported_distro_maps_to_empty():
    assert _run_func("_pkg_mgr_for_id", "alpine", "") == ""


# ── detect_pkg_mgr: parse an os-release file → package manager ────────

def test_detect_pkg_mgr_reads_os_release_file(tmp_path):
    osr = tmp_path / "os-release"
    osr.write_text('PRETTY_NAME="Debian GNU/Linux"\nID=debian\nID_LIKE=ubuntu\n')
    assert _run_func("detect_pkg_mgr", str(osr)) == "apt"


# ── system-level gate: idempotent re-run on this host (spec §8 layer 1) ──
# Mutating steps (docker/gitnexus/acl/ownership) can't be unit-tested; this
# runs the real orchestrator on the already-provisioned host and asserts a
# no-op + green verify. Skipped unless SUPERNOVA_RUN_PROVISION_INTEGRATION=1
# (it mutates the live host — opt-in).

def test_provision_idempotent_rerun_verify_green():
    if __import__("os").environ.get("SUPERNOVA_RUN_PROVISION_INTEGRATION") != "1":
        __import__("pytest").skip("set SUPERNOVA_RUN_PROVISION_INTEGRATION=1 to run live host gate")
    r = subprocess.run(["bash", str(PROVISION)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"provision exited {r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "all green" in r.stdout.lower(), f"verify not green:\n{r.stdout}"


# ── Regression: WEB container (root) must access repos regardless of .git owner ──
# Bug: fix_ownership once chown'd repos/ to supernova-user; WEB runs as container
# root, so git flagged dubious ownership (exit 128) → WEB scans died. Fix =
# provision no longer chowns repos + Dockerfile sets safe.directory '/app/repos/*'.
def test_web_container_scans_repos_regardless_of_git_owner():
    if __import__("os").environ.get("SUPERNOVA_RUN_PROVISION_INTEGRATION") != "1":
        __import__("pytest").skip("set SUPERNOVA_RUN_PROVISION_INTEGRATION=1 (needs running web container)")
    repos_dir = REPO / "repos"
    sample = next(repos_dir.rglob(".git"), None)
    assert sample is not None, "no repo under repos/ to test"
    repo_rel = sample.parent.relative_to(REPO)  # e.g. repos/frontend/xxx
    r = subprocess.run(
        ["docker", "exec", "supernova-web", "git", "-C", f"/app/{repo_rel}",
         "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"WEB container can't git-access {repo_rel}:\n{r.stderr}"
