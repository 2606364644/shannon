# Shared Temporal Container + Dynamic Task Queue

Date: 2026-06-08
Status: Draft

## Problem

Two projects share the same host:

- `/root/shannon` (TypeScript) — original, uses `shannon-temporal` container on `shannon-net`
- `/root/shannon-py` (Python) — refactored, uses `shannon-py-temporal` container on `shannon-py-net`

Both bind ports `7233` (gRPC) and `8233` (Web UI), so only one can run at a time. When the original project's container is already running, shannon-py fails to start its own.

Additionally, shannon-py uses **fixed** task queue names (`shannon-whitebox`, `shannon-blackbox`), which blocks concurrent scans and diverges from the original project's per-scan isolation pattern.

## Goal

1. shannon-py reuses whatever Temporal server is already reachable at `localhost:7233` — whether it's the original project's container or any other Temporal instance.
2. Only as a fallback, when no Temporal is available, does shannon-py start its own container.
3. Task queues are dynamically generated per scan with a `shannon-py-` prefix, preventing collisions with the original project's `shannon-` prefixed queues.

## Design

### 1. Container Reuse: Priority-Based Detection

Change `temporal_infra.py`'s `ensure_infra()` to follow this priority chain:

```
Step 1: Connect to localhost:7233
  ├─ Success → use it, done
  └─ Fail → continue to Step 2

Step 2: Check if shannon-temporal container exists (stopped)
  ├─ Exists → docker start shannon-temporal, poll until ready
  └─ Not found → continue to Step 3

Step 3: Start shannon-py's own docker-compose
  └─ docker compose up -d, poll until ready
```

**Rationale**: Port `7233` is unique on the host. If anything is listening there, it's a Temporal server — regardless of container name. This is the simplest and most robust check. Container-name detection is only used to decide *which* container to start when nothing is running.

**New helper function** in `temporal_infra.py`:

```python
def _shannon_container_exists() -> bool:
    """Check if the original shannon-temporal container exists (running or stopped)."""
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=shannon-temporal", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return "shannon-temporal" in result.stdout.strip()
```

**Modified `ensure_infra()`**:

```python
async def ensure_infra(compose_file=None, address="localhost:7233"):
    # Step 1: Already reachable?
    if await is_temporal_ready(address):
        logger.info("Temporal already reachable at %s — reusing.", address)
        return

    # Step 2: Original project container exists but stopped?
    if _shannon_container_exists():
        logger.info("Found shannon-temporal container — starting it.")
        subprocess.run(["docker", "start", "shannon-temporal"], check=True, capture_output=True, text=True)
    else:
        # Step 3: Start our own
        logger.info("No existing Temporal found — starting shannon-py container.")
        start_temporal(compose_file)

    # Poll until ready
    for i in range(_READY_POLL_ATTEMPTS):
        if await is_temporal_ready(address):
            logger.info("Temporal is ready!")
            return
        await asyncio.sleep(_READY_POLL_INTERVAL)

    raise RuntimeError("Timed out waiting for Temporal to become ready.")
```

### 2. Dynamic Task Queue Naming

Replace fixed `TASK_QUEUE` constants with a generator function in a shared location.

**New function** in `shannon_core.services.temporal_infra`:

```python
import secrets

def generate_task_queue(prefix: str) -> str:
    """Generate a unique task queue name: {prefix}-{8-char-hex}."""
    suffix = secrets.token_hex(4)  # 8 hex chars
    return f"{prefix}-{suffix}"
```

**Whitebox worker** (`packages/whitebox/src/shannon_whitebox/worker.py`):

```python
# Before:
TASK_QUEUE = "shannon-whitebox"

# After:
from shannon_core.services.temporal_infra import generate_task_queue
TASK_QUEUE_PREFIX = "shannon-py-wb"

# In run_scan():
task_queue = generate_task_queue(TASK_QUEUE_PREFIX)
```

**Blackbox worker** (`packages/blackbox/src/shannon_blackbox/worker.py`):

```python
# Before:
TASK_QUEUE = "shannon-blackbox"

# After:
from shannon_core.services.temporal_infra import generate_task_queue
TASK_QUEUE_PREFIX = "shannon-py-bb"

# In run_scan():
task_queue = generate_task_queue(TASK_QUEUE_PREFIX)
```

**Resulting queue names**:

| Project | Pattern | Example |
|---------|---------|---------|
| shannon (TypeScript) | `shannon-{hex8}` | `shannon-a3f7b2c1` |
| shannon-py whitebox | `shannon-py-wb-{hex8}` | `shannon-py-wb-e9d4c1a7` |
| shannon-py blackbox | `shannon-py-bb-{hex8}` | `shannon-py-bb-5f2e8b3d` |

No collision is possible because the prefixes are distinct.

### 3. CLI `infra status` Enhancement

Update `get_temporal_status()` to report *which* container is providing the Temporal service:

```python
async def get_temporal_status(compose_file=None, address="localhost:7233"):
    # ... existing container check ...
    source = "unknown"
    if healthy:
        # Identify which container is serving
        for name in ["shannon-temporal", "shannon-py-temporal"]:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Status}}"],
                capture_output=True, text=True,
            )
            if "up" in result.stdout.strip().lower():
                source = name
                break
        else:
            source = "external"  # reachable but not a known container
    return {"container": container_status, "healthy": healthy, "source": source}
```

CLI output change:

```
$ shannon-whitebox infra status
Container: running
Source:    shannon-temporal   ← new field
Health:    healthy
```

## Files Changed

| File | Change |
|------|--------|
| `packages/core/src/shannon_core/services/temporal_infra.py` | Add `_shannon_container_exists()`, `generate_task_queue()`; rewrite `ensure_infra()` and `get_temporal_status()` |
| `packages/whitebox/src/shannon_whitebox/worker.py` | Replace `TASK_QUEUE` constant with `TASK_QUEUE_PREFIX` + `generate_task_queue()` call |
| `packages/blackbox/src/shannon_blackbox/worker.py` | Same as whitebox |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Update `infra status` output to show source |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Same as whitebox CLI |

## Out of Scope

- Modifying the original `/root/shannon` project — no changes to it.
- Removing shannon-py's `docker-compose.yml` — kept as fallback.
- Changing port numbers — both projects continue to use `7233`/`8233`.
- Namespace changes — both use Temporal's `default` namespace.

## Decision Record: Temporal Value Assessment

During this design process, we audited what Temporal actually provides to shannon-py:

| Temporal Capability | Used? | Notes |
|---------------------|-------|-------|
| Declarative retry + backoff | Yes | Only genuine value; replaceable with `tenacity` |
| Timeout enforcement | Yes | Replaceable with `asyncio.wait_for` |
| Crash recovery / durability | No | PipelineState is in-memory; lost on crash |
| Signals / Queries | No | `poll_workflow_progress` queries a handler that is never registered — dead code |
| Horizontal scaling | No | Worker and Workflow in same process |
| Activity sandbox | No | Bypassed via `workflow.unsafe.imports_passed_through` |
| Child workflows | No | |
| Saga compensation | No | Uses plain `try/finally` |

**Decision**: Keep Temporal for now (this spec's changes), but record that replacing it with `tenacity` + `asyncio` is a viable future direction. The migration would eliminate the container dependency entirely, remove ~200 lines of activity boilerplate, and simplify the codebase. Revisit when the refactored project is stable.
