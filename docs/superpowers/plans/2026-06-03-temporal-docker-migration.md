# Temporal Docker 容器配置迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 shannon-py 中对齐原始项目的 Temporal 基础设施管理能力，实现"开箱即用"。

**Architecture:** 在 `shannon-core` 中新增 `temporal_infra.py` 服务模块，封装 Temporal 健康检查、自动启动、轮询等待、状态查询。两个 CLI（whitebox/blackbox）新增 `infra` 子命令组，并在 `start` 命令中调用 `ensure_infra()` 自动检测并启动 Temporal。

**Tech Stack:** Python 3.12+, subprocess (docker compose), temporalio Python SDK, Click CLI, pytest + pytest-asyncio

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `packages/core/src/shannon_core/services/temporal_infra.py` | Temporal 基础设施管理核心逻辑 |
| Create | `packages/core/tests/test_temporal_infra.py` | 核心逻辑的单元测试 |
| Modify | `packages/whitebox/src/shannon_whitebox/cli/main.py` | 新增 infra 子命令 + start 调用 ensure_infra |
| Modify | `packages/blackbox/src/shannon_blackbox/cli/main.py` | 同上 |
| Modify | `packages/whitebox/tests/test_cli.py` | infra 子命令测试 |
| Modify | `packages/blackbox/tests/test_cli.py` | infra 子命令测试 |
| Done | `docker-compose.yml` | 已创建，无需修改 |

---

### Task 1: 创建 `temporal_infra.py` 核心模块 — 健康检查与 compose 路径解析

**Files:**
- Create: `packages/core/src/shannon_core/services/temporal_infra.py`
- Test: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: 写 `get_compose_file` 和 `is_temporal_ready` 的失败测试**

```python
# packages/core/tests/test_temporal_infra.py
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.temporal_infra import (
    get_compose_file,
    is_temporal_ready,
)


class TestGetComposeFile:
    def test_returns_docker_compose_yml_in_project_root(self):
        result = get_compose_file()
        assert result.name == "docker-compose.yml"
        assert result.exists()

    def test_custom_path_overrides_default(self, tmp_path):
        custom = tmp_path / "custom-compose.yml"
        custom.write_text("services: {}")
        result = get_compose_file(custom)
        assert result == custom


class TestIsTemporalReady:
    @pytest.mark.asyncio
    async def test_returns_true_when_connect_succeeds(self):
        with patch("shannon_core.services.temporal_infra.Client") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.connect = AsyncMock(return_value=mock_client)
            # get_workflow_service_health succeeds
            mock_client.connection = MagicMock()
            result = await is_temporal_ready("localhost:7233")
        assert result is True
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_connect_fails(self):
        with patch("shannon_core.services.temporal_infra.Client") as mock_client_cls:
            mock_client_cls.connect = AsyncMock(side_effect=Exception("connection refused"))
            result = await is_temporal_ready("localhost:7233")
        assert result is False
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.services.temporal_infra'`

- [ ] **Step 3: 实现 `get_compose_file` 和 `is_temporal_ready`**

```python
# packages/core/src/shannon_core/services/temporal_infra.py
"""Temporal infrastructure management — health checks, start/stop, auto-ensure."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from temporalio.client import Client

logger = logging.getLogger(__name__)

# Resolve project root: this file is at packages/core/src/shannon_core/services/temporal_infra.py
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_CONTAINER_NAME = "shannon-py-temporal"
_READY_POLL_ATTEMPTS = 30
_READY_POLL_INTERVAL = 2  # seconds


def get_compose_file(path: Path | None = None) -> Path:
    """Return the docker-compose.yml path. Defaults to project root."""
    if path is not None:
        return path
    return _PROJECT_ROOT / "docker-compose.yml"


async def is_temporal_ready(address: str = "localhost:7233") -> bool:
    """Check whether the Temporal server is reachable and healthy."""
    try:
        client = await Client.connect(address)
        await client.close()
        return True
    except Exception:
        return False
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): add temporal_infra module with health check and compose path resolver"
```

---

### Task 2: 添加 `start_temporal` / `stop_temporal` / `get_temporal_status`

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py`
- Modify: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: 写 `start_temporal`、`stop_temporal`、`get_temporal_status` 的失败测试**

追加到 `packages/core/tests/test_temporal_infra.py`：

```python
from shannon_core.services.temporal_infra import (
    get_compose_file,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)


class TestStartTemporal:
    def test_calls_docker_compose_up(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            start_temporal(compose)
        mock_sp.run.assert_called_once()
        args = mock_sp.run.call_args[0][0]
        assert "docker" in args
        assert "compose" in args
        assert "up" in args
        assert "-d" in args

    def test_raises_on_failure(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = subprocess.CalledProcessError(1, "docker")
            with pytest.raises(RuntimeError, match="Failed to start Temporal"):
                start_temporal(compose)


class TestStopTemporal:
    def test_calls_docker_compose_down(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            stop_temporal(compose)
        mock_sp.run.assert_called_once()
        args = mock_sp.run.call_args[0][0]
        assert "docker" in args
        assert "compose" in args
        assert "down" in args

    def test_raises_on_failure(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = subprocess.CalledProcessError(1, "docker")
            with pytest.raises(RuntimeError, match="Failed to stop Temporal"):
                stop_temporal(compose)


class TestGetTemporalStatus:
    def test_returns_running_and_healthy(self):
        with (
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready,
        ):
            mock_sp.run.return_value = MagicMock(stdout="Up 5 minutes")
            mock_ready.return_value = True
            # Need to run async in sync context — test via asyncio
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_temporal_status()
            )
        assert result["container"] == "running"
        assert result["healthy"] is True

    def test_returns_stopped_when_container_not_found(self):
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = FileNotFoundError("docker not found")
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_temporal_status()
            )
        assert result["container"] == "not found"
        assert result["healthy"] is False
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py::TestStartTemporal packages/core/tests/test_temporal_infra.py::TestStopTemporal packages/core/tests/test_temporal_infra.py::TestGetTemporalStatus -v`
Expected: FAIL — `ImportError: cannot import name 'start_temporal'`

- [ ] **Step 3: 实现 `start_temporal`、`stop_temporal`、`get_temporal_status`**

追加到 `packages/core/src/shannon_core/services/temporal_infra.py`：

```python
def start_temporal(compose_file: Path | None = None) -> None:
    """Start the Temporal container via docker compose."""
    compose = get_compose_file(compose_file)
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "up", "-d"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Temporal container started")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to start Temporal: {e.stderr}") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "docker command not found. Please install Docker and ensure it is in PATH."
        ) from e


def stop_temporal(compose_file: Path | None = None) -> None:
    """Stop the Temporal container via docker compose."""
    compose = get_compose_file(compose_file)
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "down"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Temporal container stopped")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to stop Temporal: {e.stderr}") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "docker command not found. Please install Docker and ensure it is in PATH."
        ) from e


async def get_temporal_status(
    compose_file: Path | None = None,
    address: str = "localhost:7233",
) -> dict:
    """Return Temporal container and health status."""
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
    return {"container": container_status, "healthy": healthy}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): add start/stop/status to temporal_infra"
```

---

### Task 3: 添加 `ensure_infra` — 自动检测并启动 Temporal

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py`
- Modify: `packages/core/tests/test_temporal_infra.py`

- [ ] **Step 1: 写 `ensure_infra` 的失败测试**

追加到 `packages/core/tests/test_temporal_infra.py`：

```python
from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_compose_file,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)


class TestEnsureInfra:
    @pytest.mark.asyncio
    async def test_returns_immediately_if_already_ready(self):
        with patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready:
            mock_ready.return_value = True
            with patch("shannon_core.services.temporal_infra.start_temporal") as mock_start:
                await ensure_infra()
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_starts_temporal_and_waits_until_ready(self):
        ready_count = 0

        async def fake_ready(address="localhost:7233"):
            nonlocal ready_count
            ready_count += 1
            return ready_count > 1  # ready on 2nd poll

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=fake_ready),
            patch("shannon_core.services.temporal_infra.start_temporal") as mock_start,
        ):
            await ensure_infra()

        mock_start.assert_called_once()
        assert ready_count >= 2  # polled at least twice

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        async def never_ready(address="localhost:7233"):
            return False

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=never_ready),
            patch("shannon_core.services.temporal_infra.start_temporal"),
            patch("shannon_core.services.temporal_infra._READY_POLL_ATTEMPTS", 3),
            patch("shannon_core.services.temporal_infra._READY_POLL_INTERVAL", 0),
        ):
            with pytest.raises(RuntimeError, match="Timed out waiting for Temporal"):
                await ensure_infra()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py::TestEnsureInfra -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_infra'`

- [ ] **Step 3: 实现 `ensure_infra`**

追加到 `packages/core/src/shannon_core/services/temporal_infra.py`：

```python
import asyncio

async def ensure_infra(
    compose_file: Path | None = None,
    address: str = "localhost:7233",
) -> None:
    """Ensure Temporal infrastructure is available.

    1. If Temporal is already ready, return immediately.
    2. Otherwise, start the container via docker compose.
    3. Poll until healthy (30 attempts × 2s interval).
    4. Raise RuntimeError on timeout.
    """
    if await is_temporal_ready(address):
        return

    logger.info("Temporal not ready — starting infrastructure...")
    start_temporal(compose_file)

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

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/test_temporal_infra.py
git commit -m "feat(core): add ensure_infra with auto-start and poll-wait"
```

---

### Task 4: 更新 `__init__.py` 导出

**Files:**
- Modify: `packages/core/src/shannon_core/services/__init__.py`

- [ ] **Step 1: 添加 temporal_infra 的导出**

在 `packages/core/src/shannon_core/services/__init__.py` 中追加：

```python
from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_compose_file,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
```

- [ ] **Step 2: 运行全部 core 测试，确认没有引入回归**

Run: `uv run pytest packages/core/tests/ -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add packages/core/src/shannon_core/services/__init__.py
git commit -m "feat(core): export temporal_infra from services __init__"
```

---

### Task 5: 更新 whitebox CLI — 新增 `infra` 子命令 + `start` 调用 `ensure_infra`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`
- Modify: `packages/whitebox/tests/test_cli.py`

- [ ] **Step 1: 写 `infra` 子命令的失败测试**

追加到 `packages/whitebox/tests/test_cli.py`：

```python
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from shannon_whitebox.cli.main import cli


def test_infra_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["infra", "--help"])
    assert result.exit_code == 0
    assert "Manage Temporal infrastructure" in result.output


def test_infra_up():
    with patch("shannon_whitebox.cli.main.start_temporal") as mock_start:
        with patch("shannon_whitebox.cli.main.is_temporal_ready", new_callable=AsyncMock) as mock_ready:
            mock_ready.return_value = True
            runner = CliRunner()
            result = runner.invoke(cli, ["infra", "up"])
    assert result.exit_code == 0
    assert "started" in result.output.lower() or "ready" in result.output.lower()
    mock_start.assert_called_once()


def test_infra_down():
    with patch("shannon_whitebox.cli.main.stop_temporal") as mock_stop:
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "down"])
    assert result.exit_code == 0
    mock_stop.assert_called_once()


def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True}

    with (
        patch("shannon_whitebox.cli.main.get_temporal_status", side_effect=fake_status),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()


def test_start_calls_ensure_infra():
    """start command should call ensure_infra before run_scan."""
    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {"status": "completed"}

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure) as mock_ensure,
        patch("shannon_whitebox.cli.main.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0
    mock_ensure.assert_called_once()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -v`
Expected: FAIL — `No such command: 'infra'`

- [ ] **Step 3: 修改 whitebox CLI**

重写 `packages/whitebox/src/shannon_whitebox/cli/main.py`：

```python
import asyncio
import click
from pathlib import Path

from dotenv import load_dotenv

from shannon_core.session import SessionManager
from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from shannon_whitebox.pipeline.shared import PipelineInput


@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""
    load_dotenv()


@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (supports resume)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address):
    """Start a white-box security scan."""
    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting white-box scan on {repo}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
        click.echo("Scan completed successfully")
    else:
        click.echo(f"Scan failed: {result.get('error', 'unknown error')}")
        raise SystemExit(1)


@cli.group()
def infra():
    """Manage Temporal infrastructure."""


@infra.command()
def up():
    """Start Temporal server."""
    start_temporal()
    click.echo("Waiting for Temporal to be ready...")
    for _ in range(30):
        if asyncio.run(is_temporal_ready()):
            click.echo("Temporal is ready!")
            return
        import time
        time.sleep(2)
    click.echo("Warning: Temporal may not be ready yet. Check `docker compose logs`.")


@infra.command()
def down():
    """Stop Temporal server."""
    stop_temporal()
    click.echo("Temporal stopped.")


@infra.command()
def status():
    """Check Temporal server status."""
    result = asyncio.run(get_temporal_status())
    container = result.get("container", "unknown")
    healthy = result.get("healthy", False)
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Health: {health_str}")


@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo("No logs found")


@cli.command()
def workspaces():
    """List all workspaces."""
    mgr = SessionManager(Path("workspaces"))
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        url = data.get("web_url", "unknown")
        agents = len(data.get("completed_agents", []))
        click.echo(f"  {ws.name}  url={url}  agents={agents}")


def main():
    cli()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest packages/whitebox/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/tests/test_cli.py
git commit -m "feat(whitebox): add infra subcommand group and auto-ensure in start"
```

---

### Task 6: 更新 blackbox CLI — 新增 `infra` 子命令 + `start` 调用 `ensure_infra`

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Modify: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 1: 写 `infra` 子命令的失败测试**

追加到 `packages/blackbox/tests/test_cli.py`：

```python
def test_infra_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["infra", "--help"])
    assert result.exit_code == 0
    assert "Manage Temporal infrastructure" in result.output


def test_infra_up():
    with patch("shannon_blackbox.cli.main.start_temporal") as mock_start:
        with patch("shannon_blackbox.cli.main.is_temporal_ready", new_callable=AsyncMock) as mock_ready:
            mock_ready.return_value = True
            runner = CliRunner()
            result = runner.invoke(cli, ["infra", "up"])
    assert result.exit_code == 0
    mock_start.assert_called_once()


def test_infra_down():
    with patch("shannon_blackbox.cli.main.stop_temporal") as mock_stop:
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "down"])
    assert result.exit_code == 0
    mock_stop.assert_called_once()


def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True}

    with patch("shannon_blackbox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()


def test_start_calls_ensure_infra():
    """start command should call ensure_infra before run_scan."""
    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", side_effect=fake_ensure) as mock_ensure,
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    mock_ensure.assert_called_once()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: FAIL — `No such command: 'infra'`

- [ ] **Step 3: 修改 blackbox CLI**

重写 `packages/blackbox/src/shannon_blackbox/cli/main.py`：

```python
import asyncio
import time
from pathlib import Path

import click

from dotenv import load_dotenv

from shannon_core.models.agents import ALL_VULN_CLASSES
from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from shannon_core.session import SessionManager


@click.group()
def cli():
    """Shannon Black-Box Scanner - Runtime vulnerability verification."""
    load_dotenv()


@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, repo, output, workspace, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=str(Path(repo).resolve()) if repo else None,
        workspace_name=workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.status == "completed":
        if result.has_whitebox_results:
            classes = result.found_whitebox_classes
            click.echo(f"Scan completed (leveraged whitebox results for: {', '.join(classes)})")
        else:
            click.echo("Scan completed (standalone — no whitebox results found)")
    else:
        error_msg = result.errors[-1] if result.errors else "unknown error"
        click.echo(f"Scan failed: {error_msg}")
        raise SystemExit(1)


@cli.group()
def infra():
    """Manage Temporal infrastructure."""


@infra.command()
def up():
    """Start Temporal server."""
    start_temporal()
    click.echo("Waiting for Temporal to be ready...")
    for _ in range(30):
        if asyncio.run(is_temporal_ready()):
            click.echo("Temporal is ready!")
            return
        time.sleep(2)
    click.echo("Warning: Temporal may not be ready yet. Check `docker compose logs`.")


@infra.command()
def down():
    """Stop Temporal server."""
    stop_temporal()
    click.echo("Temporal stopped.")


@infra.command()
def status():
    """Check Temporal server status."""
    result = asyncio.run(get_temporal_status())
    container = result.get("container", "unknown")
    healthy = result.get("healthy", False)
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Health: {health_str}")


@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo("No logs found")


@cli.command()
def workspaces():
    """List all workspaces."""
    mgr = SessionManager(Path("workspaces"))
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        url = data.get("web_url", "unknown")
        agents = len(data.get("completed_agents", []))
        click.echo(f"  {ws.name}  url={url}  agents={agents}")


def main():
    cli()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): add infra subcommand group and auto-ensure in start"
```

---

### Task 7: 全量测试验证 + 最终提交

- [ ] **Step 1: 运行全部测试**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 2: 验证 CLI 帮助信息**

Run: `uv run shannon-whitebox --help && uv run shannon-whitebox infra --help && uv run shannon-whitebox start --help`
Expected: `infra` 子命令出现在帮助输出中

Run: `uv run shannon-blackbox --help && uv run shannon-blackbox infra --help`
Expected: 同上

- [ ] **Step 3: 验证 docker-compose.yml 存在且内容正确**

Run: `cat docker-compose.yml`
Expected: 容器名 `shannon-py-temporal`，网络 `shannon-py-net`，端口 7233/8233

- [ ] **Step 4: 合并为一个 commit（可选）或保持分步提交**

当前所有变更已在 Task 1-6 中分步提交。如果希望合并为一个 feat commit：

```bash
git rebase -i --autosquash  # 将所有 temporal 相关 commit 合并
```
