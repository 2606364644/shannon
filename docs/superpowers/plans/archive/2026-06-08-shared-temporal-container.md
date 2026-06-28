# Shared Temporal Container + Dynamic Task Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make shannon-py reuse the original shannon project's Temporal container when available, and replace fixed task queue names with dynamic per-scan names.

**Architecture:** Priority-based container detection (port-first, then container name, then fallback). Task queues get `shannon-py-wb-{hex8}` / `shannon-py-bb-{hex8}` naming to avoid collisions with the original project's `shannon-{hex8}` pattern.

**Tech Stack:** Python, temporalio SDK, subprocess (Docker CLI), pytest + pytest-asyncio + unittest.mock

**Spec:** `docs/superpowers/specs/2026-06-08-shared-temporal-container-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `packages/core/src/shannon_core/services/temporal_infra.py` | Container detection, lifecycle, task queue generation |
| `packages/core/tests/test_temporal_infra.py` | Tests for all temporal_infra changes |
| `packages/whitebox/src/shannon_whitebox/worker.py` | Dynamic task queue in whitebox scan |
| `packages/whitebox/tests/test_worker.py` | Test dynamic task queue usage |
| `packages/blackbox/src/shannon_blackbox/worker.py` | Dynamic task queue in blackbox scan |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | Show `source` field in `infra status` |
| `packages/blackbox/tests/test_cli.py` | Test source field in infra status |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | Show `source` field in `infra status` |
| `packages/whitebox/tests/test_cli.py` | Test source field in infra status |

---

### Task 1: Add `generate_task_queue()` to temporal_infra

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py`
- Test: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_temporal_infra.py`:

```python
class TestGenerateTaskQueue:
    def test_format_is_prefix_hex8(self):
        from shannon_core.services.temporal_infra import generate_task_queue
        result = generate_task_queue("shannon-py-wb")
        assert result.startswith("shannon-py-wb-")
        suffix = result.removeprefix("shannon-py-wb-")
        assert len(suffix) == 8
        int(suffix, 16)  # must be valid hex

    def test_generates_unique_names(self):
        from shannon_core.services.temporal_infra import generate_task_queue
        names = {generate_task_queue("shannon-py-wb") for _ in range(100)}
        assert len(names) == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestGenerateTaskQueue -v`
Expected: FAIL with `ImportError: cannot import name 'generate_task_queue'`

- [ ] **Step 3: Write minimal implementation**

Add to `packages/core/src/shannon_core/services/temporal_infra.py`, after the existing imports (add `import secrets` alongside the others) and before the `get_compose_file` function:

```python
import secrets


def generate_task_queue(prefix: str) -> str:
    """Generate a unique task queue name: {prefix}-{8-char-hex}."""
    suffix = secrets.token_hex(4)
    return f"{prefix}-{suffix}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestGenerateTaskQueue -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): add generate_task_queue for dynamic per-scan queue names"
```

---

### Task 2: Add `_shannon_container_exists()` to temporal_infra

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py`
- Test: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_temporal_infra.py`:

```python
class TestShannonContainerExists:
    def test_returns_true_when_container_found(self):
        from shannon_core.services.temporal_infra import _shannon_container_exists
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="shannon-temporal")
            assert _shannon_container_exists() is True
            args = mock_sp.run.call_args[0][0]
            assert "--filter" in args
            assert "name=shannon-temporal" in args

    def test_returns_false_when_container_not_found(self):
        from shannon_core.services.temporal_infra import _shannon_container_exists
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="")
            assert _shannon_container_exists() is False

    def test_returns_false_when_docker_not_installed(self):
        from shannon_core.services.temporal_infra import _shannon_container_exists
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = FileNotFoundError("docker not found")
            assert _shannon_container_exists() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestShannonContainerExists -v`
Expected: FAIL with `ImportError: cannot import name '_shannon_container_exists'`

- [ ] **Step 3: Write minimal implementation**

Add to `packages/core/src/shannon_core/services/temporal_infra.py`, after `generate_task_queue`:

```python
def _shannon_container_exists() -> bool:
    """Check if the original shannon-temporal container exists (running or stopped)."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=shannon-temporal", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        return "shannon-temporal" in result.stdout.strip()
    except FileNotFoundError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestShannonContainerExists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): add _shannon_container_exists for detecting original project container"
```

---

### Task 3: Rewrite `ensure_infra()` with priority-based container reuse

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py` (lines 105–132)
- Test: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: Write the failing tests**

Replace the existing `TestEnsureInfra` class in `packages/core/tests/test_temporal_infra.py` with:

```python
class TestEnsureInfra:
    @pytest.mark.asyncio
    async def test_returns_immediately_if_already_ready(self):
        with patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready:
            mock_ready.return_value = True
            with patch("shannon_core.services.temporal_infra.start_temporal") as mock_start:
                await ensure_infra()
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_starts_shannon_container_when_exists(self):
        """When shannon-temporal container exists but stopped, start it instead of docker-compose."""
        ready_count = 0

        async def fake_ready(address="localhost:7233"):
            nonlocal ready_count
            ready_count += 1
            return ready_count > 1

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=fake_ready),
            patch("shannon_core.services.temporal_infra._shannon_container_exists", return_value=True),
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.start_temporal") as mock_start,
        ):
            await ensure_infra()

        # Should have called docker start, NOT start_temporal (docker compose)
        mock_start.assert_not_called()
        mock_sp.run.assert_called_once_with(
            ["docker", "start", "shannon-temporal"],
            check=True, capture_output=True, text=True,
        )

    @pytest.mark.asyncio
    async def test_starts_own_container_as_fallback(self):
        """When no shannon container exists, fall back to own docker-compose."""
        ready_count = 0

        async def fake_ready(address="localhost:7233"):
            nonlocal ready_count
            ready_count += 1
            return ready_count > 1

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=fake_ready),
            patch("shannon_core.services.temporal_infra._shannon_container_exists", return_value=False),
            patch("shannon_core.services.temporal_infra.start_temporal") as mock_start,
        ):
            await ensure_infra()

        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        async def never_ready(address="localhost:7233"):
            return False

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=never_ready),
            patch("shannon_core.services.temporal_infra._shannon_container_exists", return_value=False),
            patch("shannon_core.services.temporal_infra.start_temporal"),
            patch("shannon_core.services.temporal_infra._READY_POLL_ATTEMPTS", 3),
            patch("shannon_core.services.temporal_infra._READY_POLL_INTERVAL", 0),
        ):
            with pytest.raises(RuntimeError, match="Timed out waiting for Temporal"):
                await ensure_infra()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestEnsureInfra -v`
Expected: FAIL — `test_starts_shannon_container_when_exists` will fail because current `ensure_infra` always calls `start_temporal()`, never `docker start shannon-temporal`.

- [ ] **Step 3: Rewrite `ensure_infra()` implementation**

Replace the existing `ensure_infra` function (lines 105–132) in `packages/core/src/shannon_core/services/temporal_infra.py` with:

```python
async def ensure_infra(
    compose_file: Path | None = None,
    address: str = "localhost:7233",
) -> None:
    """Ensure Temporal infrastructure is available.

    Priority chain:
    1. If Temporal is already reachable, return immediately.
    2. If the original shannon-temporal container exists (stopped), start it.
    3. Otherwise, start shannon-py's own docker-compose as fallback.
    """
    # Step 1: Already reachable?
    if await is_temporal_ready(address):
        logger.info("Temporal already reachable at %s — reusing.", address)
        return

    # Step 2: Original project container exists but stopped?
    if _shannon_container_exists():
        logger.info("Found shannon-temporal container — starting it.")
        try:
            subprocess.run(
                ["docker", "start", "shannon-temporal"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(f"Failed to start shannon-temporal: {e}") from e
    else:
        # Step 3: Start our own
        logger.info("No existing Temporal found — starting shannon-py container.")
        start_temporal(compose_file)

    # Poll until ready
    logger.info("Waiting for Temporal to become ready...")
    for i in range(_READY_POLL_ATTEMPTS):
        if await is_temporal_ready(address):
            logger.info("Temporal is ready!")
            return
        await asyncio.sleep(_READY_POLL_INTERVAL)

    raise RuntimeError(
        "Timed out waiting for Temporal to become ready. "
        "Check `docker compose logs` for errors."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestEnsureInfra -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): ensure_infra reuses shannon-temporal container with priority detection"
```

---

### Task 4: Add `source` field to `get_temporal_status()`

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py` (lines 78–102)
- Test: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: Write the failing tests**

Replace the existing `TestGetTemporalStatus` class in `packages/core/tests/test_temporal_infra.py` with:

```python
class TestGetTemporalStatus:
    @pytest.mark.asyncio
    async def test_returns_running_and_healthy_with_shannon_source(self):
        with (
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready,
        ):
            # First call: docker compose ps (container check)
            # Second call: docker ps --filter name=shannon-temporal (source check)
            # Third call: docker ps --filter name=shannon-py-temporal (source check)
            mock_sp.run.side_effect = [
                MagicMock(stdout="Up 5 minutes"),    # container status
                MagicMock(stdout="Up 5 minutes"),    # shannon-temporal found
            ]
            mock_ready.return_value = True
            result = await get_temporal_status()
        assert result["container"] == "running"
        assert result["healthy"] is True
        assert result["source"] == "shannon-temporal"

    @pytest.mark.asyncio
    async def test_returns_shannon_py_source(self):
        with (
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready,
        ):
            mock_sp.run.side_effect = [
                MagicMock(stdout="Up 5 minutes"),    # container status
                MagicMock(stdout=""),                 # shannon-temporal not found
                MagicMock(stdout="Up 5 minutes"),    # shannon-py-temporal found
            ]
            mock_ready.return_value = True
            result = await get_temporal_status()
        assert result["source"] == "shannon-py-temporal"

    @pytest.mark.asyncio
    async def test_returns_external_source(self):
        with (
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready,
        ):
            mock_sp.run.side_effect = [
                MagicMock(stdout="Up 5 minutes"),
                MagicMock(stdout=""),   # shannon-temporal not found
                MagicMock(stdout=""),   # shannon-py-temporal not found
            ]
            mock_ready.return_value = True
            result = await get_temporal_status()
        assert result["source"] == "external"

    @pytest.mark.asyncio
    async def test_returns_stopped_when_container_not_found(self):
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = FileNotFoundError("docker not found")
            result = await get_temporal_status()
        assert result["container"] == "not found"
        assert result["healthy"] is False
        assert result["source"] == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestGetTemporalStatus -v`
Expected: FAIL — `source` key not in returned dict.

- [ ] **Step 3: Rewrite `get_temporal_status()` implementation**

Replace the existing `get_temporal_status` function (lines 78–102) in `packages/core/src/shannon_core/services/temporal_infra.py` with:

```python
async def get_temporal_status(
    compose_file: Path | None = None,
    address: str = "localhost:7233",
) -> dict:
    """Return Temporal container and health status, including source identification."""
    compose = get_compose_file(compose_file)
    container_status = "unknown"
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "ps", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip().lower()
        if "up" in stdout:
            container_status = "running"
        elif not stdout:
            container_status = "stopped"
        else:
            container_status = stdout
    except FileNotFoundError:
        container_status = "not found"

    healthy = await is_temporal_ready(address)

    # Identify which container is providing the Temporal service
    source = "unknown"
    if healthy:
        for name in ["shannon-temporal", "shannon-py-temporal"]:
            try:
                result = subprocess.run(
                    ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Status}}"],
                    capture_output=True,
                    text=True,
                )
                if "up" in result.stdout.strip().lower():
                    source = name
                    break
            except FileNotFoundError:
                pass
        else:
            source = "external"

    return {"container": container_status, "healthy": healthy, "source": source}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py::TestGetTemporalStatus -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): get_temporal_status identifies source container (shannon/shannon-py/external)"
```

---

### Task 5: Dynamic task queue in whitebox worker

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`
- Test: `packages/whitebox/tests/test_worker.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/whitebox/tests/test_worker.py`:

```python
@pytest.mark.asyncio
async def test_run_scan_uses_dynamic_task_queue(tmp_path):
    """run_scan should generate a unique task queue per scan, not use a fixed name."""
    from shannon_whitebox.pipeline.shared import PipelineState

    repo = tmp_path / "target-repo"
    repo.mkdir()

    input = PipelineInput(
        repo_path=str(repo),
        workspace_name="test-dynamic-tq",
    )

    mock_result = PipelineState(status="completed")

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=mock_result)
    mock_handle.query = AsyncMock(side_effect=Exception("no query in test"))

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    captured_task_queue = None

    def capture_worker(**kwargs):
        nonlocal captured_task_queue
        captured_task_queue = kwargs.get("task_queue")
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", side_effect=capture_worker):
        from shannon_whitebox.worker import run_scan
        await run_scan(input, "localhost:7233")

    # Task queue should have the shannon-py-wb prefix
    assert captured_task_queue is not None
    assert captured_task_queue.startswith("shannon-py-wb-"), f"Expected shannon-py-wb- prefix, got: {captured_task_queue}"
    suffix = captured_task_queue.removeprefix("shannon-py-wb-")
    assert len(suffix) == 8
    int(suffix, 16)  # must be valid hex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_worker.py::test_run_scan_uses_dynamic_task_queue -v`
Expected: FAIL — task queue will be `"shannon-whitebox"` (the current fixed constant).

- [ ] **Step 3: Modify whitebox worker**

In `packages/whitebox/src/shannon_whitebox/worker.py`:

**Add import** (after the existing imports):
```python
from shannon_core.services.temporal_infra import generate_task_queue
```

**Replace line 14** (`TASK_QUEUE = "shannon-whitebox"`) with:
```python
TASK_QUEUE_PREFIX = "shannon-py-wb"
```

**Replace all uses of `TASK_QUEUE`** in the `run_scan` function. The function body changes from referencing `TASK_QUEUE` to generating a dynamic queue. Here is the full replacement for the `run_scan` function:

```python
async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path,
            name=input.workspace_name,
        )

    client = await Client.connect(temporal_address)

    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[WhiteboxScanWorkflow],
        activities=[run_preflight, run_agent, run_vuln_agent, run_code_index, run_rebuild_call_chains],
    )

    async with worker:
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=task_queue,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

            # Convert PipelineState to enriched dict for CLI consumption
            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir
                )
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir
                )

            return result_dict
        except Exception:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_worker.py -v`
Expected: ALL PASS (both old and new tests)

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker.py
git commit -m "feat(whitebox): use dynamic task queue names (shannon-py-wb-{hex8})"
```

---

### Task 6: Dynamic task queue in blackbox worker

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/worker.py`
- Test: `packages/blackbox/tests/test_worker.py` (check if exists, otherwise create)

- [ ] **Step 1: Write the failing test**

Check if `packages/blackbox/tests/test_worker.py` exists. If not, create it. Append the test:

```python
import pytest
from unittest.mock import AsyncMock, patch

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState


@pytest.mark.asyncio
async def test_run_scan_uses_dynamic_task_queue():
    """run_scan should generate a unique task queue per scan with shannon-py-bb prefix."""
    input = BlackboxPipelineInput(
        web_url="http://example.com",
        workspace_name="test-bb-tq",
    )

    mock_result = BlackboxPipelineState(status="completed")

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=mock_result)
    mock_handle.query = AsyncMock(side_effect=Exception("no query in test"))

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    captured_task_queue = None

    def capture_worker(**kwargs):
        nonlocal captured_task_queue
        captured_task_queue = kwargs.get("task_queue")
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker):
        from shannon_blackbox.worker import run_scan
        await run_scan(input, "localhost:7233")

    assert captured_task_queue is not None
    assert captured_task_queue.startswith("shannon-py-bb-"), f"Expected shannon-py-bb- prefix, got: {captured_task_queue}"
    suffix = captured_task_queue.removeprefix("shannon-py-bb-")
    assert len(suffix) == 8
    int(suffix, 16)  # must be valid hex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_worker.py::test_run_scan_uses_dynamic_task_queue -v`
Expected: FAIL — task queue will be `"shannon-blackbox"`.

- [ ] **Step 3: Modify blackbox worker**

In `packages/blackbox/src/shannon_blackbox/worker.py`:

**Add import** (after the existing imports):
```python
from shannon_core.services.temporal_infra import generate_task_queue
```

**Replace line 16** (`TASK_QUEUE = "shannon-blackbox"`) with:
```python
TASK_QUEUE_PREFIX = "shannon-py-bb"
```

**Replace the `run_scan` function** with the following (the only change is `TASK_QUEUE` → `task_queue` local variable):

```python
async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233") -> BlackboxPipelineState:
    client = await Client.connect(temporal_address)

    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[BlackboxScanWorkflow],
        activities=[run_blackbox_preflight, run_recon, run_exploit_agent, assemble_report, run_report_agent],
    )

    async with worker:
        handle = await client.start_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
            task_queue=task_queue,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            return result
        except Exception:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_worker.py::test_run_scan_uses_dynamic_task_queue -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/worker.py packages/blackbox/tests/test_worker.py
git commit -m "feat(blackbox): use dynamic task queue names (shannon-py-bb-{hex8})"
```

---

### Task 7: Show `source` field in CLI `infra status` (both packages)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py` (lines 232–239)
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py` (lines 232–239)
- Test: `packages/whitebox/tests/test_cli.py`
- Test: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 1: Update the whitebox CLI `infra status` command**

In `packages/whitebox/src/shannon_whitebox/cli/main.py`, replace the `status()` function under `@infra.command()` (lines 232–239):

```python
@infra.command()
def status():
    """Check Temporal server status."""
    result = asyncio.run(get_temporal_status())
    container = result.get("container", "unknown")
    healthy = result.get("healthy", False)
    source = result.get("source", "unknown")
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Source:    {source}")
    click.echo(f"Health:    {health_str}")
```

- [ ] **Step 2: Update the whitebox CLI test for `infra status`**

In `packages/whitebox/tests/test_cli.py`, replace the `test_infra_status` function (lines 59–68):

```python
def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True, "source": "shannon-temporal"}

    with patch("shannon_whitebox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()
    assert "shannon-temporal" in result.output
```

- [ ] **Step 3: Update the blackbox CLI `infra status` command**

In `packages/blackbox/src/shannon_blackbox/cli/main.py`, replace the `status()` function under `@infra.command()` (lines 232–239):

```python
@infra.command()
def status():
    """Check Temporal server status."""
    result = asyncio.run(get_temporal_status())
    container = result.get("container", "unknown")
    healthy = result.get("healthy", False)
    source = result.get("source", "unknown")
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Source:    {source}")
    click.echo(f"Health:    {health_str}")
```

- [ ] **Step 4: Update the blackbox CLI test for `infra status`**

In `packages/blackbox/tests/test_cli.py`, replace the `test_infra_status` function (lines 148–157):

```python
def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True, "source": "shannon-temporal"}

    with patch("shannon_blackbox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()
    assert "shannon-temporal" in result.output
```

- [ ] **Step 5: Run all affected tests**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py -v -k "infra_status"`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/blackbox/src/shannon_blackbox/cli/main.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py
git commit -m "feat(cli): infra status shows source container (shannon-temporal/shannon-py-temporal/external)"
```

---

### Task 8: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run all tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_temporal_infra.py packages/whitebox/tests/ packages/blackbox/tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Verify imports work correctly**

Run: `cd /root/shannon-py && python -c "from shannon_core.services.temporal_infra import generate_task_queue, _shannon_container_exists, ensure_infra, get_temporal_status; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Final commit (if any test fixes needed)**

Only if fixes were needed during verification.
