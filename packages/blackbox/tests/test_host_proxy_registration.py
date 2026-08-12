"""Task 8 RED→GREEN: assert run_host_proxy_setup/stop_host_proxy are registered
on BOTH workers (CLI blackbox worker.py + WEB runner.py bb_worker).

Without registration, Temporal dispatch of `activities.run_host_proxy_setup` /
`activities.stop_host_proxy` from the workflow would fail at runtime with
"Activity not found on worker". This test guards that regression via AST
inspection of the `Worker(..., activities=[...])` list literal — locally
verifiable (no Temporal dev-server needed).

Orchestration (setup runs before preflight; stop runs in finally) is covered
separately in test_workflow_proxy_orchestration.py (WorkflowEnvironment-based;
may be dev-server-blocked locally — see that file's module docstring).
"""
import ast
from pathlib import Path

import pytest

from supernova_blackbox.pipeline import activities as bb_acts


# ─── activity defn decoration (T7 produced them; guard regression) ────────────

def test_run_host_proxy_setup_is_activity_defn():
    """T7 must have decorated run_host_proxy_setup with @activity.defn."""
    defn = getattr(bb_acts.run_host_proxy_setup, "__temporal_activity_definition", None)
    assert defn is not None, "run_host_proxy_setup missing @activity.defn"


def test_stop_host_proxy_is_activity_defn():
    """T7 must have decorated stop_host_proxy with @activity.defn."""
    defn = getattr(bb_acts.stop_host_proxy, "__temporal_activity_definition", None)
    assert defn is not None, "stop_host_proxy missing @activity.defn"


# ─── helpers: AST-extract the `activities=[...]` list of a Worker(...) call ───

def _worker_activities_names(module_path: Path, worker_var_hint: str) -> set[str]:
    """Return the set of bare-name identifiers passed in the `activities=[...]`
    keyword of the `Worker(...)` call whose assignment target contains
    `worker_var_hint` (e.g. "bb_worker" or just any Worker).

    Falls back to the FIRST Worker(...) call in the module if no target-name
    match is found (single-worker modules like blackbox/worker.py).
    """
    tree = ast.parse(module_path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        tgt_str = " ".join(
            t.id for t in node.targets if isinstance(t, ast.Name)
        )
        if worker_var_hint not in tgt_str:
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "Worker"):
            continue
        for kw in call.keywords:
            if kw.arg == "activities" and isinstance(kw.value, ast.List):
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
    return names


# ─── CLI worker.py registration ───────────────────────────────────────────────

def test_cli_worker_registers_host_proxy_activities():
    """blackbox/worker.py run_scan() Worker must register both host_proxy
    activities. RED before T8 implementation: names absent from activities=[].
    """
    worker_py = Path(bb_acts.__file__).parent.parent / "worker.py"
    assert worker_py.exists(), f"worker.py not found at {worker_py}"
    # blackbox/worker.py has a single `worker = Worker(...)` assignment;
    # use the variable name "worker" as the hint.
    names = _worker_activities_names(worker_py, "worker")
    assert "run_host_proxy_setup" in names, (
        "run_host_proxy_setup not in blackbox/worker.py Worker.activities=[]; "
        "workflow call activities.run_host_proxy_setup would fail at runtime"
    )
    assert "stop_host_proxy" in names, (
        "stop_host_proxy not in blackbox/worker.py Worker.activities=[]; "
        "workflow call activities.stop_host_proxy would fail at runtime"
    )


# ─── WEB runner.py bb_worker registration (bb_ aliases) ───────────────────────

def _runner_py() -> Path:
    """Resolve packages/worker/src/supernova_worker/runner.py from this test file."""
    # tests/test_host_proxy_registration.py → packages/blackbox/tests/
    here = Path(__file__).resolve().parent
    # packages/blackbox/tests → packages
    packages_dir = here.parent.parent
    return packages_dir / "worker" / "src" / "supernova_worker" / "runner.py"


def test_web_runner_registers_host_proxy_activities_bb_aliases():
    """worker/runner.py bb_worker must register both host_proxy activities
    under their bb_ aliases (matches existing bb_ import style in that module).

    RED before T8 implementation: bb_ aliases absent from bb_worker.activities=[].
    """
    runner_py = _runner_py()
    assert runner_py.exists(), f"runner.py not found at {runner_py}"
    names = _worker_activities_names(runner_py, "bb_worker")
    assert "bb_run_host_proxy_setup" in names, (
        "bb_run_host_proxy_setup not in runner.py bb_worker.activities=[]; "
        "workflow call activities.run_host_proxy_setup would fail at runtime "
        "on the WEB worker path (scan_manager-submitted scans)"
    )
    assert "bb_stop_host_proxy" in names, (
        "bb_stop_host_proxy not in runner.py bb_worker.activities=[]; "
        "workflow call activities.stop_host_proxy would fail at runtime "
        "on the WEB worker path"
    )


def test_web_runner_imports_host_proxy_activities_as_bb_aliases():
    """runner.py must import run_host_proxy_setup as bb_run_host_proxy_setup
    (and stop as bb_stop_host_proxy) — the alias is what's registered in the
    Worker.activities list, so a missing import would NameError at module load.
    """
    runner_py = _runner_py()
    tree = ast.parse(runner_py.read_text())
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                "supernova_blackbox.pipeline.activities" in node.module:
            for alias in node.names:
                if alias.asname:
                    aliases.add((alias.name, alias.asname))
    assert ("run_host_proxy_setup", "bb_run_host_proxy_setup") in aliases, (
        "runner.py must `import run_host_proxy_setup as bb_run_host_proxy_setup` "
        "from supernova_blackbox.pipeline.activities"
    )
    assert ("stop_host_proxy", "bb_stop_host_proxy") in aliases, (
        "runner.py must `import stop_host_proxy as bb_stop_host_proxy` "
        "from supernova_blackbox.pipeline.activities"
    )
