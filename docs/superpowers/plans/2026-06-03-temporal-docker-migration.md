# Temporal Docker 容器配置迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 Temporal 基础设施管理的最后缺失部分——创建 docker-compose.yml 并为 blackbox CLI 添加 infra 命令组和自动启动能力，使两个 CLI 都能"开箱即用"。

**Architecture:** 核心服务 `temporal_infra.py` 已实现并测试通过。Whitebox CLI 的 `infra` 命令组和 `start` 自动确保也已实现。本计划只需：(1) 创建 docker-compose.yml 使现有代码可运行；(2) 在 blackbox CLI 中对齐 whitebox 的 infra 能力。

**Tech Stack:** Docker Compose, Python 3.12, Click, temporalio, subprocess

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `docker-compose.yml` | Temporal 容器定义（端口、健康检查、数据卷） |
| Modify | `packages/blackbox/src/shannon_blackbox/cli/main.py` | 添加 `infra` 命令组 + `start` 调用 `ensure_infra()` |
| Modify | `packages/blackbox/tests/test_cli.py` | 添加 blackbox infra 命令测试 + ensure_infra 测试 |

---

### Task 1: 创建 docker-compose.yml

**Files:**
- Create: `docker-compose.yml`
- Test: `packages/core/tests/test_temporal_infra.py::TestGetComposeFile::test_returns_docker_compose_yml_in_project_root`

- [ ] **Step 1: Write the docker-compose.yml**

在项目根目录创建 `docker-compose.yml`。容器名 `shannon-py-temporal`，网络 `shannon-py-net`，与原始 TypeScript 项目不冲突。

```yaml
networks:
  default:
    name: shannon-py-net

services:
  temporal:
    image: temporalio/temporal:latest
    container_name: shannon-py-temporal
    command: ["server", "start-dev", "--db-filename", "/home/temporal/temporal.db", "--ip", "0.0.0.0"]
    ports:
      - "127.0.0.1:7233:7233"   # gRPC
      - "127.0.0.1:8233:8233"   # Web UI (built-in)
    volumes:
      - temporal-data:/home/temporal
    healthcheck:
      test: ["CMD", "temporal", "operator", "cluster", "health", "--address", "localhost:7233"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s

volumes:
  temporal-data:
```

- [ ] **Step 2: Run the previously failing test to verify it passes**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py::TestGetComposeFile::test_returns_docker_compose_yml_in_project_root -v`
Expected: PASS

- [ ] **Step 3: Run the full temporal_infra test suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py -v`
Expected: 13 passed

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml for Temporal dev server"
```

---

### Task 2: 为 blackbox CLI 添加 infra 命令组

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`

对齐 whitebox CLI 的 infra 命令，添加 `infra` 组和 `up`、`down`、`status` 子命令，并在 `start` 命令中调用 `ensure_infra()`。

- [ ] **Step 1: Write the failing test for blackbox infra help**

在 `packages/blackbox/tests/test_cli.py` 末尾添加：

```python
def test_infra_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["infra", "--help"])
    assert result.exit_code == 0
    assert "Manage Temporal infrastructure" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_infra_help -v`
Expected: FAIL — `No such command: 'infra'`

- [ ] **Step 3: Add infra imports to blackbox CLI**

在 `packages/blackbox/src/shannon_blackbox/cli/main.py` 顶部添加 import（在现有 import 之后）：

```python
import time

from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
```

注意：保留现有的 `import asyncio`（`run_scan` 仍在使用）和 `from shannon_core.models.agents import ALL_VULN_CLASSES`（`start` 命令仍在使用）。只添加 `import time` 和 temporal_infra 的 import。

- [ ] **Step 4: Add infra command group and subcommands**

在 `packages/blackbox/src/shannon_blackbox/cli/main.py` 的 `workspaces` 命令之后、`main()` 函数之前添加：

```python
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
```

- [ ] **Step 5: Run test to verify infra help passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_infra_help -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py
git commit -m "feat(blackbox): add infra command group with up/down/status"
```

---

### Task 3: 为 blackbox infra 命令添加测试

**Files:**
- Modify: `packages/blackbox/tests/test_cli.py`

- [ ] **Step 1: Add blackbox infra up test**

在 `packages/blackbox/tests/test_cli.py` 末尾添加 import 和测试：

文件顶部，在现有 import 后添加：

```python
from unittest.mock import AsyncMock
```

添加测试：

```python
def test_infra_up():
    with (
        patch("shannon_blackbox.cli.main.start_temporal"),
        patch("shannon_blackbox.cli.main.is_temporal_ready", new_callable=AsyncMock, return_value=True),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "up"])
    assert result.exit_code == 0
    assert "ready" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_infra_up -v`
Expected: PASS

- [ ] **Step 3: Add blackbox infra down test**

```python
def test_infra_down():
    with patch("shannon_blackbox.cli.main.stop_temporal"):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "down"])
    assert result.exit_code == 0
    assert "stopped" in result.output.lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_infra_down -v`
Expected: PASS

- [ ] **Step 5: Add blackbox infra status test**

```python
def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True}

    with patch("shannon_blackbox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_infra_status -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/tests/test_cli.py
git commit -m "test(blackbox): add infra up/down/status CLI tests"
```

---

### Task 4: 修复现有 blackbox start 测试以适配 ensure_infra

**Files:**
- Modify: `packages/blackbox/tests/test_cli.py`

**重要：** 在添加 `ensure_infra()` 调用之前，必须先修补所有现有的 `start` 命令测试，否则它们会因为真实调用 `ensure_infra` 而失败（尝试连接 Docker）。

需要修补的测试：
- `test_start_wires_repo_param`
- `test_start_shows_whitebox_completion_message`
- `test_start_shows_standalone_completion_message`
- `test_start_shows_error_on_failure`

- [ ] **Step 1: Add ensure_infra mock helper and update existing tests**

在 `packages/blackbox/tests/test_cli.py` 中，首先在文件顶部 import 区域添加 `AsyncMock`：

```python
from unittest.mock import AsyncMock, patch
```

然后逐一更新以下 4 个测试，在每个 `with patch(...)` 块中添加 `ensure_infra` mock：

**`test_start_wires_repo_param`**（约第 32 行起）—— 将 `with` 块改为：

```python
def test_start_wires_repo_param():
    """--repo arg should be resolved to an absolute path and passed to run_scan."""
    fake_repo = "/fake/repo"
    expected_repo_path = str(Path(fake_repo).resolve())

    captured_input: BlackboxPipelineInput | None = None

    async def fake_run_scan(input: BlackboxPipelineInput, temporal_address: str) -> BlackboxPipelineState:
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com", "--repo", fake_repo])

    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
    assert captured_input is not None, "run_scan was not called"
    assert isinstance(captured_input, BlackboxPipelineInput)
    assert captured_input.repo_path == expected_repo_path
```

**`test_start_shows_whitebox_completion_message`**（约第 54 行起）：

```python
def test_start_shows_whitebox_completion_message():
    """When whitebox results are found, completion message should mention them."""
    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(
            status="completed",
            has_whitebox_results=True,
            found_whitebox_classes=["injection", "xss"],
        )

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    assert "leveraged whitebox results" in result.output
    assert "injection" in result.output
```

**`test_start_shows_standalone_completion_message`**（约第 71 行起）：

```python
def test_start_shows_standalone_completion_message():
    """When no whitebox results, completion message should say standalone."""
    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    assert "standalone" in result.output
```

**`test_start_shows_error_on_failure`**（约第 85 行起）：

```python
def test_start_shows_error_on_failure():
    """When scan fails, CLI should show error and exit 1."""
    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="failed", errors=["something broke"])

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 1
    assert "something broke" in result.output
```

- [ ] **Step 2: Run existing tests to verify they still pass (without ensure_infra call yet)**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v --ignore-glob="*infra*"`
Expected: All existing tests pass (the mock is in place but ensure_infra call hasn't been added yet)

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/tests/test_cli.py
git commit -m "test(blackbox): prepare start tests for ensure_infra integration"
```

---

### Task 5: blackbox start 命令集成 ensure_infra

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`
- Modify: `packages/blackbox/tests/test_cli.py`

在 blackbox 的 `start` 命令中，在调用 `run_scan` 之前添加 `ensure_infra()` 调用，与 whitebox 对齐。

- [ ] **Step 1: Write the failing test**

在 `packages/blackbox/tests/test_cli.py` 末尾添加：

```python
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

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_ensure.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_start_calls_ensure_infra -v`
Expected: FAIL — `ensure_infra` not called (mock assertion fails)

- [ ] **Step 3: Modify blackbox start command to call ensure_infra**

在 `packages/blackbox/src/shannon_blackbox/cli/main.py` 的 `start` 函数中，在 `click.echo` 和 `run_scan` 之间添加 `ensure_infra` 调用。

当前代码（第 45-46 行）：

```python
    click.echo(f"Starting black-box scan on {url}")
    result = asyncio.run(run_scan(input, temporal_address))
```

替换为：

```python
    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/blackbox/tests/test_cli.py::test_start_calls_ensure_infra -v`
Expected: PASS

- [ ] **Step 5: Run full blackbox CLI test suite to confirm no regressions**

Run: `uv run pytest packages/blackbox/tests/test_cli.py -v`
Expected: All tests pass (existing + new infra + ensure_infra tests)

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py packages/blackbox/tests/test_cli.py
git commit -m "feat(blackbox): auto-ensure Temporal infra in start command"
```

---

### Task 6: 全量验证

**Files:** 无变更

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest packages/core/tests/test_temporal_infra.py packages/whitebox/tests/test_cli.py packages/blackbox/tests/test_cli.py -v`
Expected: All tests pass (13 + 9 + existing + new ≈ 22+ tests)

- [ ] **Step 2: Verify blackbox CLI infra help output**

Run: `uv run shannon-blackbox infra --help`
Expected: 输出包含 `up`、`down`、`status` 子命令

- [ ] **Step 3: Verify whitebox CLI still works**

Run: `uv run shannon-whitebox infra --help`
Expected: 输出包含 `up`、`down`、`status` 子命令（无回归）
